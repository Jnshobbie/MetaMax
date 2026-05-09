"""
XAUUSD Greedy Scalper Bot v5.0 — main.py
─────────────────────────────────────────
MODULES:
  bot_config.py   — All CONFIG constants + phase names
  bot_state.py    — State load/save/default
  bot_mt5.py      — MT5 connect, orders, parallel close, EMA
  bot_filters.py  — H1/range/slope market condition filters
  bot_greedy.py   — "Greedy Scalper" core (stack + average + flip-close)
  main.py         — Orchestration loop (this file)

THE STRATEGY (v5.0 "Greedy Scalper"):
  • OPEN:  Stack positions in EMA trend direction
  • HOLD:  NEVER close while stack is red — add averaging positions
           to pull break-even closer as price moves against us
  • CLOSE: The MILLISECOND combined P&L > $0.10 (blue) → close ALL
  • SAFE:  One hard stop — account equity drops 20% → kill all

─────────────────────────────────────────
"""

import MetaTrader5 as mt5
import threading
import time
import os
from datetime import datetime

from bot_config  import CONFIG, PHASE_GREEDY, STATE_FILE
from bot_state   import load_state, save_state, default_state
from bot_mt5     import (
    connect, detect_filling_mode, EMA_TIMEFRAME,
    get_bot_positions, get_lot_for_balance, get_total_lots,
    in_session, get_trend_direction, close_all_positions
)
from bot_filters import get_filter_cache, invalidate_filter_cache
from bot_greedy  import open_greedy_stack, check_greedy


# ─────────────────────────────────────────
#  CAPITAL LOCK
# ─────────────────────────────────────────
def check_capital_lock(state):
    bot_bal = state["bot_balance"]
    locked  = state["locked_capital"]
    if bot_bal >= locked * CONFIG["lock_profit_at"]:
        new_locked = bot_bal / 2
        if new_locked > locked:
            print(f"\n🔒 CAPITAL LOCK! Locking ${new_locked:.2f} | "
                  f"Trading with ${bot_bal - new_locked:.2f}")
            state["locked_capital"] = new_locked
            state["bot_balance"]    = bot_bal - new_locked
            save_state(state)


# ─────────────────────────────────────────
#  KILL SWITCH
# ─────────────────────────────────────────
def check_kill_switch(state):
    positions = get_bot_positions()
    floating  = sum(p.profit for p in positions)
    threshold = -(state["bot_balance"] * CONFIG["kill_switch_ratio"])
    if floating <= threshold:
        print(f"\n💀 KILL SWITCH | Float: ${floating:.2f} | Threshold: ${threshold:.2f}")
        close_all_positions()
        state["manual_pause"]   = True
        state["reload_pending"] = True
        save_state(state)
        return True
    return False


# ─────────────────────────────────────────
#  RELOAD LISTENER
# ─────────────────────────────────────────
def reload_listener(state):
    info      = mt5.account_info()
    acct_bal  = info.balance
    locked    = state["locked_capital"]
    available = max(acct_bal - locked - state["bot_balance"], 0)

    print(f"\n{'═'*55}")
    print(f"  🚨 BOT CAPITAL DEPLETED — YOUR ACTION REQUIRED")
    print(f"{'═'*55}")
    print(f"  Account Balance:  ${acct_bal:.2f}")
    print(f"  Locked (safe):    ${locked:.2f}")
    print(f"  Available:        ${available:.2f}")
    print(f"{'─'*55}")

    while True:
        try:
            raw    = input("  💰 Reload amount ($, 0 = stop bot): ").strip()
            amount = float(raw)
            if amount < 0:
                print("  ❌ Must be 0 or positive.")
                continue
            if amount == 0:
                print("  🛑 Bot deactivated.")
                os._exit(0)
            if amount > available:
                print(f"  ⚠️  Max available: ${available:.2f}")
                continue
            state["bot_balance"]    = amount
            state["manual_pause"]   = False
            state["reload_pending"] = False
            state["total_reloads"] += 1
            state["reload_history"].append({
                "time":   datetime.now().strftime("%H:%M:%S"),
                "amount": amount
            })
            save_state(state)
            print(f"\n  ✅ Reloaded ${amount:.2f} — bot resuming!")
            break
        except (ValueError, KeyboardInterrupt):
            os._exit(0)


# ─────────────────────────────────────────
#  STATUS DISPLAY
# ─────────────────────────────────────────
def print_status(state):
    import bot_mt5 as bmt5
    ema_tf    = bmt5.EMA_TIMEFRAME

    info       = mt5.account_info()
    positions  = get_bot_positions()
    floating   = sum(p.profit for p in positions)
    total_lots = get_total_lots(positions)
    active, session_name = in_session()
    win_rate   = (state["total_wins"] / state["total_trades"] * 100) \
                 if state["total_trades"] > 0 else 0
    dir_val    = state.get("direction")
    dir_str    = "BUY 📈" if dir_val == 0 else ("SELL 📉" if dir_val == 1 else "—")

    tick        = mt5.symbol_info_tick(CONFIG["symbol"])
    server_time = datetime.fromtimestamp(tick.time).strftime("%H:%M:%S") if tick else "N/A"

    # Filter display — call it directly so status always reflects reality
    from bot_filters import check_market_filters as _cmf
    _f_ok, _f_reason = _cmf(ema_tf)
    fc       = get_filter_cache()
    h1_str   = ("📈" if fc["h1_trend"] == 0 else "📉") if fc["h1_trend"] is not None else "—"
    m15_str  = ("📈" if fc["m15_trend"] == 0 else "📉") if fc["m15_trend"] is not None else "—"
    body_str = f"{fc['avg_body']:.1f}pt" if fc["avg_body"] > 0 else "—"
    if not CONFIG["h1_filter_enabled"] and not CONFIG["range_filter_enabled"] and not CONFIG["trend_strength_enabled"]:
        fok_str = "✅ OFF (open)"
    else:
        fok_str = "✅ PASS" if _f_ok else "❌ BLOCKED"

    max_deep  = CONFIG["greedy_deep_levels"]

    # Per-stack info
    buy_pos   = [p for p in positions if p.type == 0]
    sell_pos  = [p for p in positions if p.type == 1]
    buy_pnl   = sum(p.profit for p in buy_pos)
    sell_pnl  = sum(p.profit for p in sell_pos)
    buy_deep  = state.get("buy_deep_level", 0)
    sell_deep = state.get("sell_deep_level", 0)

    buy_str  = (f"{len(buy_pos)} pos | ${buy_pnl:+.2f} | deep:{buy_deep}/{max_deep}"
                if buy_pos else "no stack")
    sell_str = (f"{len(sell_pos)} pos | ${sell_pnl:+.2f} | deep:{sell_deep}/{max_deep}"
                if sell_pos else "no stack")

    print("\n" + "═"*57)
    print(f"  🎲 XAUUSD DUAL GREEDY v5.0 | {datetime.now().strftime('%H:%M:%S')} | {server_time}")
    print("═"*57)
    print(f"  Account:   ${info.balance:.2f}  |  Equity: ${info.equity:.2f}")
    print(f"  Bot Cap:   ${state['bot_balance']:.2f}  |  Floating: ${floating:+.2f}")
    print(f"  🔒 Locked: ${state['locked_capital']:.2f}")
    print(f"  📈 BUY:    {buy_str}")
    print(f"  📉 SELL:   {sell_str}")
    print(f"  Total:     {len(positions)} positions | {total_lots:.2f} lots")
    print(f"  Close at:  +${CONFIG['greedy_close_threshold']:.2f} | "
          f"Burst: {CONFIG['greedy_burst_levels']} | Deep: every {CONFIG['greedy_deep_step']}pts")
    print(f"  Emergency: equity drops {CONFIG['greedy_account_stop_pct']*100:.0f}% → kill all")
    print(f"  Filters:   {fok_str} | H1:{h1_str} M15:{m15_str} | Body:{body_str}")
    print(f"  Trades:    {state['total_trades']} | Wins: {state['total_wins']} | WR: {win_rate:.1f}%")
    print(f"  Greedy:    Wins: {state.get('total_greedy_wins',0)} | "
          f"Flips: {state.get('total_greedy_flips',0)}")
    print(f"  Session:   {'✅ ' + session_name if active else '⏸️  No session'} | "
          f"Mode: {CONFIG['trading_mode'].upper()}")
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

    state = load_state()
    state["phase"] = PHASE_GREEDY
    save_state(state)

    # Clear any orphan positions on startup
    if not get_bot_positions():
        state["direction"]        = None
        state["greedy_burst_open"] = False
        state["greedy_deep_level"] = 0
        state["greedy_last_deep_price"] = None
        save_state(state)

    print(f"\n🎲 XAUUSD Greedy Scalper Bot v5.0")
    print(f"   Mode:         GREEDY — stack & never close in red")
    print(f"   Close trigger: +${CONFIG['greedy_close_threshold']:.2f} combined (any blue = CLOSE ALL)")
    print(f"   Burst:        {CONFIG['greedy_burst_levels']} positions simultaneously | lot ×{CONFIG['greedy_avg_multiplier']}")
    print(f"   Deep levels:  every {CONFIG['greedy_deep_step']}pts | max {CONFIG['greedy_deep_levels']} extra levels")
    print(f"   Emergency:    equity drops {CONFIG['greedy_account_stop_pct']*100:.0f}% → kill all")
    print(f"   Filters:      H1 {'ON' if CONFIG['h1_filter_enabled'] else 'OFF'} | "
          f"Range {'ON' if CONFIG['range_filter_enabled'] else 'OFF'} | "
          f"Slope {'ON' if CONFIG['trend_strength_enabled'] else 'OFF'}")
    print(f"   ℹ️  '❌ RETURN failed' on startup is NORMAL.\n")

    last_status    = 0
    last_heartbeat = 0

    while True:
        try:
            state = load_state()

            if state.get("reload_pending"):
                t = threading.Thread(target=reload_listener, args=(state,), daemon=True)
                t.start()
                t.join()
                state = load_state()
                continue

            if state["manual_pause"]:
                time.sleep(0.5)
                continue

            active, session_name = in_session()
            now = time.time()

            # ── Heartbeat ──────────────────────────────
            if now - last_heartbeat >= CONFIG["heartbeat_interval"]:
                positions = get_bot_positions()
                floating  = sum(p.profit for p in positions) if positions else 0
                tick      = mt5.symbol_info_tick(CONFIG["symbol"])
                deep_lvl  = state.get("greedy_deep_level", 0)

                if not active:
                    hb = f"⏸️  Waiting for session..."
                elif positions:
                    hb = (f"🎲 {len(positions)} pos | Red: ${floating:.2f} | "
                          f"Deep: {deep_lvl}/{CONFIG['greedy_deep_levels']}")
                else:
                    all_off = not CONFIG["h1_filter_enabled"] and not CONFIG["range_filter_enabled"] and not CONFIG["trend_strength_enabled"]
                    fok = "✅ OFF" if all_off else ("✅" if get_filter_cache()["result"] else "❌")
                    hb  = f"🔍 {tick.bid:.2f} | Filters:{fok} | No positions"

                print(f"[{datetime.now().strftime('%H:%M:%S')}] {hb}")
                last_heartbeat = now

            if not active:
                time.sleep(5)
                continue

            # ── Status print ───────────────────────────
            if now - last_status >= CONFIG["status_interval"]:
                print_status(state)
                last_status = now

            # ── Safety checks ──────────────────────────
            if check_kill_switch(state):
                state = load_state()
                continue

            check_capital_lock(state)
            state = load_state()

            # ── SINGLE STACK ALTERNATING CORE ────────
            positions = get_bot_positions()

            if positions:
                # Stack is open — manage it
                check_greedy(state, ema_tf)
            else:
                # No stack open — open next one (alternates BUY→SELL→BUY)
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