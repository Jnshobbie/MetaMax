"""
XAUUSD Smart Scalper Bot — main.py
────────────────────────────────────
Strategy: Signal-first, clean entries, accept losses fast, recover 3x.
EMA 9/21 = gate | RSI 14 + candle body = confirmation
One trade at a time. Daily loss limit. No burst, no holding red.
"""

import MetaTrader5 as mt5
import time
import os
from datetime import datetime

from bot_config  import CONFIG, PHASE_SMART, STATE_FILE
from bot_state   import load_state, save_state
from bot_mt5     import (
    connect, detect_filling_mode,
    get_bot_positions, get_lot_for_balance,
    in_session, close_all_positions
)
from bot_greedy  import open_greedy_stack, check_greedy, BUY


# ─────────────────────────────────────────
#  STATUS DISPLAY
# ─────────────────────────────────────────
def print_status(state):
    info      = mt5.account_info()
    positions = get_bot_positions()
    win_rate  = (state.get("total_wins", 0) / state.get("total_trades", 1) * 100) \
                if state.get("total_trades", 0) > 0 else 0

    tick       = mt5.symbol_info_tick(CONFIG["symbol"])
    price_str  = f"{tick.bid:.2f}" if tick else "N/A"
    in_rec     = state.get("recovery_mode", False)
    last_loss  = state.get("last_loss_amount", 0.0)
    daily_lost = state.get("daily_loss_today", 0.0)

    pos_str = "No position — waiting for signal"
    if positions:
        p         = positions[0]
        dir_label = "BUY 📈" if p.type == BUY else "SELL 📉"
        pos_str   = (f"{dir_label} | Lot:{p.volume} | P&L:${p.profit:+.2f} | "
                     f"TP:{p.tp:.2f} | SL:{p.sl:.2f}")

    mode_str = (f"🔁 RECOVERY (need +${last_loss * CONFIG['tp_recovery_multiplier']:.2f})"
                if in_rec else "✅ NORMAL")

    print("\n" + "═"*57)
    print(f"  🧠 XAUUSD SMART SCALPER | {datetime.now().strftime('%H:%M:%S')}")
    print("═"*57)
    print(f"  Account:  ${info.balance:.2f} | Equity: ${info.equity:.2f}")
    print(f"  Price:    {price_str}")
    print(f"  Mode:     {mode_str}")
    print(f"  Position: {pos_str}")
    print(f"  Daily:    Lost ${daily_lost:.2f} / limit ${CONFIG['daily_loss_limit']:.2f}")
    print(f"  Stats:    {state.get('total_trades',0)} trades | "
          f"{state.get('total_wins',0)} wins | WR:{win_rate:.1f}%")
    print("═"*57)


# ─────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────
def bot_loop():
    if not connect():
        return

    import bot_mt5 as bmt5
    if not detect_filling_mode():
        print("\n❌ Cannot trade — no working filling mode found.")
        mt5.shutdown()
        return

    ema_tf = bmt5.EMA_TIMEFRAME
    state  = load_state()

    # Ensure all required state fields exist
    state["phase"]            = PHASE_SMART
    state["position_open"]    = state.get("position_open", False)
    state["position_ticket"]  = state.get("position_ticket", None)
    state["recovery_mode"]    = state.get("recovery_mode", False)
    state["last_loss_amount"] = state.get("last_loss_amount", 0.0)
    state["daily_loss_today"] = state.get("daily_loss_today", 0.0)
    state["daily_loss_date"]  = state.get("daily_loss_date", "")
    state["total_trades"]     = 0
    state["total_wins"]       = 0
    save_state(state)

    # Clear orphan position flags if no open positions found
    if not get_bot_positions():
        state["position_open"]   = False
        state["position_ticket"] = None
        save_state(state)

    print(f"\n🧠 XAUUSD Smart Scalper — Small Account Mode")
    print(f"   Signals:     EMA {CONFIG['ema_fast']}/{CONFIG['ema_slow']} (gate) + RSI {CONFIG['rsi_period']} + Candle confirm")
    print(f"   Entry:       1 position | Wait for M1 candle close")
    print(f"   TP / SL:     {CONFIG['tp_normal_points']}pts / {CONFIG['sl_points']}pts")
    print(f"   Recovery:    3x last loss TP after any SL hit")
    print(f"   Daily limit: Stop if down ${CONFIG['daily_loss_limit']:.2f}/day")
    print(f"   Emergency:   Equity drops {CONFIG['greedy_account_stop_pct']*100:.0f}% → kill all")
    print(f"   Lot:         {CONFIG.get('force_lot') or 'auto'} (cap {CONFIG['lot_hard_cap']})\n")

    last_status    = 0
    last_heartbeat = 0

    while True:
        try:
            state = load_state()

            if state.get("manual_pause"):
                time.sleep(0.5)
                continue

            active, session_name = in_session()
            now = time.time()

            # ── Heartbeat ──────────────────────────────────────
            if now - last_heartbeat >= CONFIG["heartbeat_interval"]:
                positions = get_bot_positions()
                tick      = mt5.symbol_info_tick(CONFIG["symbol"])
                in_rec    = state.get("recovery_mode", False)

                if not active:
                    hb = "⏸️  Outside session"
                elif positions:
                    p  = positions[0]
                    hb = (f"{'BUY 📈' if p.type == 0 else 'SELL 📉'} | "
                          f"P&L: ${p.profit:+.2f} | TP: {p.tp:.2f} | SL: {p.sl:.2f}")
                else:
                    tag = "🔁 RECOVERY" if in_rec else "✅ NORMAL"
                    hb  = f"🔍 {tick.bid:.2f} | {tag} | Waiting for signal..."

                print(f"[{datetime.now().strftime('%H:%M:%S')}] {hb}")
                last_heartbeat = now

            if not active:
                time.sleep(5)
                continue

            # ── Status print ───────────────────────────────────
            if now - last_status >= CONFIG["status_interval"]:
                print_status(state)
                last_status = now

            # ── Core logic ─────────────────────────────────────
            positions = get_bot_positions()

            if positions or state.get("position_open"):
                check_greedy(state, ema_tf)
            else:
                open_greedy_stack(state, ema_tf)

            time.sleep(CONFIG["poll_interval"])

        except KeyboardInterrupt:
            print("\n🛑 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)

    mt5.shutdown()


if __name__ == "__main__":
    bot_loop()