"""
bot_greedy.py — Smart Small Account Scalper
─────────────────────────────────────────────
STRATEGY:
  1. Wait for new M1 candle to close
  2. EMA 9/21 sets direction (gate) — RSI + candle body must confirm
  3. Enter 1 position with TP + SL
  4. TP hit → bank profit, back to normal
  5. SL hit → log loss, enter recovery mode
  6. Recovery: next signal → enter with TP = 3x last loss amount
  7. Recovery TP hit → clear loss memory, back to normal

NO burst. NO deep levels. NO holding red. One clean trade at a time.
─────────────────────────────────────────────
"""

import MetaTrader5 as mt5
import time
from datetime import datetime

from bot_config import CONFIG, PHASE_SMART, PHASE_GREEDY
from bot_state  import save_state
from bot_mt5    import (
    get_bot_positions, get_lot_for_balance,
    close_positions_parallel, FILLING_MODE,
)
from bot_filters import get_signal, is_new_candle, invalidate_filter_cache

BUY  = mt5.ORDER_TYPE_BUY   # 0
SELL = mt5.ORDER_TYPE_SELL  # 1

_last_entry_candle = 0


# ─────────────────────────────────────────
#  ACCOUNT EMERGENCY STOP
# ─────────────────────────────────────────
def _account_emergency(state):
    info = mt5.account_info()
    if info is None:
        return False
    equity    = info.equity
    balance   = info.balance
    threshold = balance * (1.0 - CONFIG["greedy_account_stop_pct"])

    if equity <= threshold:
        print(f"\n🚨 EMERGENCY STOP | Equity: ${equity:.2f} < ${threshold:.2f}")
        positions = get_bot_positions()
        if positions:
            close_positions_parallel(positions)
        state["last_emergency_time"] = time.time()
        state["position_open"]       = False
        state["position_ticket"]     = None
        save_state(state)
        return True
    return False


# ─────────────────────────────────────────
#  DAILY LOSS CHECK
# ─────────────────────────────────────────
def _check_daily_limit(state):
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("daily_loss_date") != today:
        state["daily_loss_date"]  = today
        state["daily_loss_today"] = 0.0
        save_state(state)

    limit = CONFIG["daily_loss_limit"]
    lost  = state.get("daily_loss_today", 0.0)
    if lost >= limit:
        print(f"  📵 Daily loss limit hit: -${lost:.2f} / -${limit:.2f} — pausing until tomorrow")
        return True
    return False


# ─────────────────────────────────────────
#  PLACE ONE TRADE WITH TP + SL
# ─────────────────────────────────────────
def _place_smart_trade(state, direction):
    """
    Opens 1 position with calculated TP and SL.
    Uses the globally detected FILLING_MODE — no hardcoded IOC.
    """
    import bot_mt5 as bmt5

    filling = bmt5.FILLING_MODE
    if filling is None:
        print("  ❌ No filling mode detected — cannot place order")
        return False

    symbol  = CONFIG["symbol"]
    info    = mt5.symbol_info(symbol)
    tick    = mt5.symbol_info_tick(symbol)
    point   = info.point
    balance = mt5.account_info().balance
    lot     = min(get_lot_for_balance(balance), CONFIG["lot_hard_cap"])

    in_recovery = state.get("recovery_mode", False)
    last_loss   = state.get("last_loss_amount", 0.0)
    sl_pts      = CONFIG["sl_points"]
    tp_pts      = CONFIG["tp_normal_points"]

    # Recovery TP: calculate points needed to make 3x last loss
    # XAUUSD: profit = lot * volume_step_ratio * pts * point
    # Simpler: profit_per_pt ≈ lot * 10 (for XAUUSD where 1pt = $0.01 at 0.01 lot)
    if in_recovery and last_loss > 0:
        target_profit   = last_loss * CONFIG["tp_recovery_multiplier"]
        # 1 point on XAUUSD = lot * 1.0 dollars (at standard lot = 100oz)
        # At 0.01 lot: 1 point ($0.01 price move) = $0.01 profit
        # So pts_needed = target / (lot * point_value_per_lot)
        # point_value_per_lot for XAUUSD = contract_size * point = 100 * 0.01 = 1.0
        contract_size   = info.trade_contract_size  # typically 100 for XAUUSD
        profit_per_pt   = lot * contract_size * point
        if profit_per_pt > 0:
            tp_pts = max(int(target_profit / profit_per_pt), tp_pts)
        print(f"  🎯 RECOVERY | Last loss: -${last_loss:.2f} | "
              f"Target: +${target_profit:.2f} | TP: {tp_pts}pts")

    if direction == BUY:
        price = tick.ask
        tp    = round(price + tp_pts * point, info.digits)
        sl    = round(price - sl_pts * point, info.digits)
    else:
        price = tick.bid
        tp    = round(price - tp_pts * point, info.digits)
        sl    = round(price + sl_pts * point, info.digits)

    # ── Drift check — skip if price already moved against signal ──
    drift_limit = CONFIG.get("entry_drift_limit_pts", 300)
    rates = mt5.copy_rates_from_pos(CONFIG["symbol"], mt5.TIMEFRAME_M1, 1, 1)
    if rates is not None and len(rates) > 0:
        candle_close = rates[0]["close"]
        drift = (price - candle_close) / point if direction == BUY else (candle_close - price) / point
        if drift > drift_limit:
            print(f"  ⏭️  Drift check: price moved {drift:.0f}pts against signal since candle close — skip")
            return False

    dir_str = "BUY 📈" if direction == BUY else "SELL 📉"
    print(f"\n🎯 {dir_str} | Lot: {lot} | Entry: {price:.2f} | "
          f"TP: {tp:.2f} (+{tp_pts}pts) | SL: {sl:.2f} (-{sl_pts}pts)")

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         direction,
        "price":        price,
        "tp":           tp,
        "sl":           sl,
        "deviation":    50,
        "magic":        CONFIG["magic"],
        "comment":      "smart_recovery" if in_recovery else "smart_normal",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }

    NUM_POSITIONS = 1  # open 3 × 0.01 lots = 0.03 total (~$0.24 TP, ~$0.15 SL)
    tickets = []
    for i in range(NUM_POSITIONS):
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            tickets.append(result.order)
        else:
            code = result.retcode if result else "None"
            print(f"  ❌ Order {i+1} failed | retcode: {code}")

    if tickets:
        print(f"  ✅ {len(tickets)}/{NUM_POSITIONS} orders placed | Tickets: {tickets}")
        state["position_open"]        = True
        state["position_ticket"]      = tickets[0]
        state["position_tickets"]     = tickets
        state["position_entry_price"] = price
        state["position_direction"]   = direction
        state["last_entry_time"]      = time.time()
        state["total_trades"]         = state.get("total_trades", 0) + 1
        save_state(state)
        return True
    else:
        print(f"  ❌ All orders failed")
        return False


# ─────────────────────────────────────────
#  CHECK IF OPEN POSITION CLOSED (TP/SL hit)
# ─────────────────────────────────────────
def _check_open_position(state):
    """
    Monitors the open position.
    If closed (TP or SL hit by MT5), updates state.
    Returns True if still open, False if closed.
    """
    ticket    = state.get("position_ticket")
    tickets   = state.get("position_tickets", [ticket] if ticket else [])
    positions = get_bot_positions()
    still_open = any(p.ticket in tickets for p in positions) if tickets else False

    if not still_open and state.get("position_open"):
        # Find the closing deal in history
        time.sleep(1.0)  # wait for broker to register the deal
        from_time = int(state.get("last_entry_time", time.time() - 300)) - 10
        to_time   = int(time.time()) + 10
        deals     = mt5.history_deals_get(from_time, to_time)

        pnl = None
        if deals:
            # Primary: match by position_id (MT5 closing deals carry position_id = opening ticket)
            for d in reversed(list(deals)):
                if d.magic != CONFIG["magic"]:
                    continue
                if d.entry != 1:  # 1 = EXIT deal
                    continue
                if ticket and d.position_id == ticket:
                    pnl = d.profit + d.swap + d.commission
                    break

            # Fallback: most recent EXIT deal with our magic
            if pnl is None:
                for d in reversed(list(deals)):
                    if d.magic == CONFIG["magic"] and d.entry == 1:
                        pnl = d.profit + d.swap + d.commission
                        break

        if pnl is None:
            # Last resort: compare close price vs entry price (handles manual closes)
            entry_price = state.get("position_entry_price")
            direction   = state.get("position_direction")
            # Try to get close price from the most recent deal by position_id
            close_price = None
            if deals and ticket:
                for d in reversed(list(deals)):
                    if d.position_id == ticket:
                        close_price = d.price
                        break
            if entry_price and close_price and direction is not None:
                if direction == BUY:
                    pnl = 1.0 if close_price > entry_price else -1.0
                else:
                    pnl = 1.0 if close_price < entry_price else -1.0
                result_str = "WIN" if pnl > 0 else "LOSS"
                print(f"  ⚠️  Deal P&L not found — determined by price: entry={entry_price:.2f} close={close_price:.2f} → {result_str}")
            else:
                pnl = 0.0
                print(f"  ⚠️  Could not determine outcome — counting as neutral (no stats update)")

        if pnl > 0:
            win_type = "RECOVERY WIN 🎉" if state.get("recovery_mode") else "Normal win ✅"
            print(f"\n✅ TRADE CLOSED | +${pnl:.2f} | {win_type}")
            state["total_wins"]       = state.get("total_wins", 0) + 1
            state["recovery_mode"]    = False
            state["last_loss_amount"] = 0.0
        elif pnl == 0.0:
            print(f"\n⚪ TRADE CLOSED | Outcome unknown — stats unchanged")
        else:
            loss_amt = abs(pnl)
            print(f"\n❌ LOSS | -${loss_amt:.2f} | Entering recovery mode (next TP = ${loss_amt * CONFIG['tp_recovery_multiplier']:.2f})")
            state["recovery_mode"]    = True
            state["last_loss_amount"] = loss_amt
            state["daily_loss_today"] = state.get("daily_loss_today", 0.0) + loss_amt
            print(f"  📊 Daily loss: -${state['daily_loss_today']:.2f} / limit -${CONFIG['daily_loss_limit']:.2f}")

        state["position_open"]   = False
        state["position_ticket"] = None
        state["last_close_time"] = time.time()
        invalidate_filter_cache()
        save_state(state)
        return False

    return still_open


# ─────────────────────────────────────────
#  MAIN ENTRY POINT — called every poll tick
# ─────────────────────────────────────────
def open_greedy_stack(state, ema_timeframe=None):
    """Called when no position is open — wait for signal and enter."""
    global _last_entry_candle

    if _check_daily_limit(state):
        return False

    if _account_emergency(state):
        return False

    # Cooldown after last close
    if time.time() - state.get("last_close_time", 0) < CONFIG["reentry_cooldown"]:
        return False

    # Emergency cooldown
    last_emerg = state.get("last_emergency_time", 0)
    if last_emerg > 0 and (time.time() - last_emerg) < CONFIG["emergency_cooldown"]:
        return False

    # Only act when a new M1 candle has just closed
    if not is_new_candle():
        return False

    direction, score, reason = get_signal()
    min_score = CONFIG["signal_strength_min"]

    print(f"\n📊 SIGNAL | {reason}")

    if direction is None or score < min_score:
        print(f"  ⏭️  Score {score}/{min_score} — skip")
        return False

    return _place_smart_trade(state, direction)


def check_greedy(state, ema_timeframe=None):
    """Called every poll tick when a position IS open."""
    if _account_emergency(state):
        return
    if _check_open_position(state):
        _trail_stop(state)


def _trail_stop(state):
    """
    Trailing stop logic — runs while position is open.
    +80pts → move SL to breakeven
    +120pts → trail SL 50pts behind price
    """
    import bot_mt5 as bmt5

    ticket    = state.get("position_ticket")
    direction = state.get("position_direction")
    entry     = state.get("position_entry_price")
    if not ticket or direction is None or not entry:
        return

    positions = get_bot_positions()
    tickets = state.get("position_tickets", [ticket] if ticket else [])
    pos = next((p for p in positions if p.ticket in tickets), None)
    if not pos:
        return

    symbol = CONFIG["symbol"]
    info   = mt5.symbol_info(symbol)
    tick   = mt5.symbol_info_tick(symbol)
    point  = info.point

    current_price = tick.bid if direction == BUY else tick.ask
    profit_pts    = ((current_price - entry) / point) if direction == BUY else ((entry - current_price) / point)

    new_sl = None

    if profit_pts >= 1200:
        # Trail SL 50pts behind current price
        trail_sl = (current_price - 50 * point) if direction == BUY else (current_price + 50 * point)
        trail_sl = round(trail_sl, 2)
        # Only move SL if it's better than current SL
        if direction == BUY and trail_sl > pos.sl:
            new_sl = trail_sl
        elif direction == SELL and trail_sl < pos.sl:
            new_sl = trail_sl

    elif profit_pts >= 800:
        # Move SL to breakeven
        be_sl = round(entry, 2)
        if direction == BUY and be_sl > pos.sl:
            new_sl = be_sl
        elif direction == SELL and be_sl < pos.sl:
            new_sl = be_sl

    if new_sl is None:
        return

    # Send SL modification
    filling = bmt5.FILLING_MODE
    result  = mt5.order_send({
        "action":   mt5.TRADE_ACTION_SLTP,
        "symbol":   symbol,
        "position": ticket,
        "sl":       new_sl,
        "tp":       pos.tp,
    })
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        label = "breakeven" if profit_pts < 120 else f"trailing ({new_sl:.2f})"
        print(f"  🔒 SL moved to {label} | profit: +{profit_pts:.0f}pts")