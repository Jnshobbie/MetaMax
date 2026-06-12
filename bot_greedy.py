"""
bot_greedy.py — Dual Direction Greedy Scalper
──────────────────────────────────────────────
DUAL DIRECTION: Bot runs TWO independent stacks simultaneously.
  - BUY stack:  6 positions fired when M15 EMA says uptrend
  - SELL stack: 6 positions fired when M15 EMA says downtrend
  Both stacks are managed independently. A BUY stack bleeding red
  while a SELL stack is printing blue is NORMAL — the SELL snipes
  its profits immediately, offsetting the BUY drawdown.

ENTRY LOGIC (per stack):
  - Check EMA direction for that stack's side
  - If no stack of that direction exists → fire 6 burst simultaneously
  - Never open a second BUY stack while one BUY stack is already open
  - Never open a second SELL stack while one SELL stack is already open

CLOSE LOGIC (per stack, independent):
  A) Any individual position in stack turns blue → snipe immediately
  B) Combined stack P&L >= $0.10 → close entire stack
  C) Account equity drops 20% → kill ALL positions both stacks

DEEP LEVELS: Each stack independently adds deep levels every 200pts
──────────────────────────────────────────────
"""

import MetaTrader5 as mt5
import threading
import time
from bot_config import CONFIG, PHASE_GREEDY
from bot_state  import save_state
from bot_mt5    import (
    get_bot_positions, get_lot_for_balance, get_greedy_avg_lot,
    place_order, close_positions_parallel, get_trend_direction,
)
from bot_filters import check_market_filters, invalidate_filter_cache

BUY  = mt5.ORDER_TYPE_BUY   # 0
SELL = mt5.ORDER_TYPE_SELL  # 1


# ─────────────────────────────────────────
#  HELPERS — split positions by direction
# ─────────────────────────────────────────
def _buy_positions(positions):
    return [p for p in positions if p.type == BUY]

def _sell_positions(positions):
    return [p for p in positions if p.type == SELL]


# ─────────────────────────────────────────
#  ENTRY COOLDOWN (per direction)
# ─────────────────────────────────────────
def _can_enter(state):
    now        = time.time()
    elapsed    = now - state.get("last_close_time", 0)
    if elapsed < CONFIG["reentry_cooldown"]:
        return False, f"Cooldown: {CONFIG['reentry_cooldown'] - elapsed:.1f}s"

    last_emerg = state.get("last_emergency_time", 0)
    if last_emerg > 0:
        since = now - last_emerg
        if since < CONFIG["emergency_cooldown"]:
            return False, f"Post-emergency: {CONFIG['emergency_cooldown'] - since:.0f}s"

    return True, ""


# ─────────────────────────────────────────
#  ACCOUNT EMERGENCY STOP
# ─────────────────────────────────────────
def _account_emergency(state):
    info      = mt5.account_info()
    equity    = info.equity
    balance   = info.balance
    drop_pct  = CONFIG["greedy_account_stop_pct"]
    threshold = balance * (1.0 - drop_pct)

    if equity <= threshold:
        print(f"\n🚨🚨 ACCOUNT EMERGENCY STOP 🚨🚨")
        print(f"   Equity: ${equity:.2f} | Threshold: ${threshold:.2f} ({drop_pct*100:.0f}% drop)")
        positions = get_bot_positions()
        closed    = close_positions_parallel(positions)
        total_pnl = sum(p.profit for p in positions)
        print(f"   Closed {closed} positions (both stacks) | P&L: ${total_pnl:.2f}")

        now = time.time()
        state["last_emergency_time"]    = now
        state["last_close_time"]        = now
        state["buy_stack_open"]         = False
        state["buy_deep_level"]         = 0
        state["buy_last_deep_price"]    = None
        state["sell_stack_open"]        = False
        state["sell_deep_level"]        = 0
        state["sell_last_deep_price"]   = None
        state["direction"]              = None
        state["phase"]                  = PHASE_GREEDY
        invalidate_filter_cache()
        save_state(state)
        return True
    return False


# ─────────────────────────────────────────
#  RESET ONE STACK
# ─────────────────────────────────────────
def _reset_stack(state, direction, pnl=0.0):
    """Reset state for one direction (BUY or SELL) after its stack closes."""
    if pnl > 0:
        state["bot_balance"]       += pnl
        state["total_wins"]        += 1
        state["total_greedy_wins"] += 1

    if direction == BUY:
        state["buy_stack_open"]      = False
        state["buy_deep_level"]      = 0
        state["buy_last_deep_price"] = None
        state["buy_entry_price"]     = None
    else:
        state["sell_stack_open"]      = False
        state["sell_deep_level"]      = 0
        state["sell_last_deep_price"] = None
        state["sell_entry_price"]     = None

    # Only update close time if BOTH stacks are now closed
    all_positions = get_bot_positions()
    remaining_dir = [p for p in all_positions
                     if p.type == direction]
    if not remaining_dir:
        state["last_close_time"] = time.time()

    invalidate_filter_cache()
    save_state(state)


# ─────────────────────────────────────────
#  SIMULTANEOUS BURST ENGINE
# ─────────────────────────────────────────
def _fire_burst_simultaneously(direction, base_lot, n_levels, label):
    results = [None] * n_levels
    threads = []

    def _open_one(idx):
        lot    = get_greedy_avg_lot(base_lot, idx)
        result = place_order(direction, lot, f"{label}_B{idx+1}", grid_level=0)
        results[idx] = result

    for i in range(n_levels):
        t = threading.Thread(target=_open_one, args=(i,), daemon=True)
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    return sum(1 for r in results if r is not None)


# ─────────────────────────────────────────
#  OPEN A STACK (BUY or SELL)
# ─────────────────────────────────────────
def _open_stack(state, direction, ema_timeframe):
    """Open a burst stack for one direction if not already open."""
    dir_key   = "buy" if direction == BUY else "sell"
    stack_key = f"{dir_key}_stack_open"

    if state.get(stack_key):
        print(f"  ⏸️  {dir_key.upper()} stack already flagged open in state")
        return False

    current_positions = len(get_bot_positions())
    if current_positions >= CONFIG["max_open_positions"]:
        print(f"  ⚠️  Max positions reached ({current_positions}/{CONFIG['max_open_positions']})")
        return False

    bot_bal  = state["bot_balance"]
    base_lot = get_lot_for_balance(bot_bal)
    n_burst  = CONFIG["greedy_burst_levels"]
    dir_str  = "BUY 📈" if direction == BUY else "SELL 📉"
    label    = "buy" if direction == BUY else "sell"

    print(f"  💰 Bot balance: ${bot_bal:.2f} | Base lot: {base_lot}")
    lot_preview = [round(get_greedy_avg_lot(base_lot, i), 2) for i in range(n_burst)]
    print(f"\n🚀 {dir_str} BURST | Firing {n_burst} positions SIMULTANEOUSLY")
    print(f"   Lots: {lot_preview}")

    opened = _fire_burst_simultaneously(direction, base_lot, n_burst, label)
    print(f"   ✅ Opened {opened}/{n_burst} {dir_str} positions")

    if opened > 0:
        tick = mt5.symbol_info_tick(CONFIG["symbol"])
        state[stack_key]                    = True
        state[f"{dir_key}_deep_level"]      = 0
        state[f"{dir_key}_last_deep_price"] = tick.bid
        state[f"{dir_key}_entry_price"]     = tick.bid
        state["last_stack_direction"]       = int(direction)
        state["total_trades"]              += opened
        save_state(state)
        return True
    print(f"  ❌ All {n_burst} orders failed — check MT5 connection")
    return False


# ─────────────────────────────────────────
#  DEEP LEVEL ENGINE (per stack)
# ─────────────────────────────────────────
def _try_deep_level(state, direction, positions):
    dir_key    = "buy" if direction == BUY else "sell"
    deep_level = state.get(f"{dir_key}_deep_level", 0)
    max_deep   = CONFIG["greedy_deep_levels"]

    if deep_level >= max_deep:
        return
    if len(get_bot_positions()) >= CONFIG["max_open_positions"]:
        return

    symbol_info = mt5.symbol_info(CONFIG["symbol"])
    tick        = mt5.symbol_info_tick(CONFIG["symbol"])
    point       = symbol_info.point
    step_pts    = CONFIG["greedy_deep_step"]
    last_price  = state.get(f"{dir_key}_last_deep_price") or tick.bid

    current_price = tick.bid if direction == BUY else tick.ask
    moved_pts     = (last_price - current_price) / point if direction == BUY \
                    else (current_price - last_price) / point

    if moved_pts >= step_pts:
        bot_bal   = state["bot_balance"]
        base_lot  = get_lot_for_balance(bot_bal)
        deep_lot  = get_greedy_avg_lot(base_lot, CONFIG["greedy_burst_levels"] + deep_level)
        dir_str   = "BUY 📈" if direction == BUY else "SELL 📉"
        level_num = CONFIG["greedy_burst_levels"] + deep_level + 1

        print(f"\n📉 DEEP {dir_str} L{level_num} | {deep_lot} lots | "
              f"{moved_pts:.0f}pts moved | Dragging BE closer...")

        result = place_order(direction, deep_lot, f"{dir_key}_D{level_num}", grid_level=0)
        if result:
            state[f"{dir_key}_deep_level"]      = deep_level + 1
            state[f"{dir_key}_last_deep_price"] = current_price
            state["total_trades"]              += 1
            save_state(state)


# ─────────────────────────────────────────
#  MANAGE ONE STACK
# ─────────────────────────────────────────
def _manage_stack(state, direction, positions):
    """
    Full lifecycle management for one direction's stack.
    Returns True if action was taken (close/snipe).
    """
    if not positions:
        return False

    dir_key    = "buy" if direction == BUY else "sell"
    dir_str    = "BUY 📈" if direction == BUY else "SELL 📉"
    threshold  = CONFIG["greedy_close_threshold"]
    burst_lvls = CONFIG["greedy_burst_levels"]
    deep_level = state.get(f"{dir_key}_deep_level", 0)
    max_deep   = CONFIG["greedy_deep_levels"]

    total_pnl  = sum(p.profit for p in positions)
    total_lots = sum(p.volume for p in positions)

    # ── FULL STACK FLIP ──────────────────────────────────────
    # Only close ALL if every position is out of the red (no deeply red ones)
    red_positions = [p for p in positions if p.profit < 0]
    all_clear     = len(red_positions) == 0
    if total_pnl >= threshold and all_clear:
        print(f"\n💥 {dir_str} FULL FLIP | +${total_pnl:.2f} | "
              f"{len(positions)} positions | CLOSING ALL...")
        closed = close_positions_parallel(positions)
        print(f"   ✅ Closed {closed} | Banked: +${total_pnl:.2f}")
        state["total_greedy_flips"] += 1
        _reset_stack(state, direction, pnl=total_pnl)
        return True

    # ── INDIVIDUAL BLUE SNIPER ───────────────────────────────
    blue = [p for p in positions if p.profit >= threshold]
    if blue:
        blue_pnl = sum(p.profit for p in blue)
        print(f"\n💰 {dir_str} SNIPE | {len(blue)} blue | "
              f"+${blue_pnl:.2f} | {len(positions)-len(blue)} red remain...")
        closed = close_positions_parallel(blue)
        print(f"   ✅ Sniped {closed} | Banked: +${blue_pnl:.2f}")
        state["total_trades"]      += closed
        state["total_greedy_wins"] += closed
        state["total_wins"]        += closed
        remaining = [p for p in get_bot_positions() if p.type == direction]
        state[f"{dir_key}_deep_level"] = max(0, len(remaining) - burst_lvls)
        save_state(state)
        return True

    # ── DEEP LEVEL ───────────────────────────────────────────
    _try_deep_level(state, direction, positions)

    # ── STATUS (throttled — only print when P&L moves $1+) ──────
    symbol_info = mt5.symbol_info(CONFIG["symbol"])
    tick        = mt5.symbol_info_tick(CONFIG["symbol"])
    point       = symbol_info.point if symbol_info else 0.01

    last_pnl_key = f"{dir_key}_last_logged_pnl"
    last_pnl     = state.get(last_pnl_key)
    should_log   = (last_pnl is None) or (abs(total_pnl - last_pnl) >= 1.0)

    if should_log and total_lots > 0:
        weighted_entry = sum(p.price_open * p.volume for p in positions) / total_lots
        be_dist        = abs(weighted_entry - tick.bid) / point
        be_dir         = "needs ↑" if direction == BUY else "needs ↓"
        color          = "🔴" if total_pnl < 0 else "🟡"
        print(f"\n{color} {dir_str} | P&L: ${total_pnl:.2f} | Lots: {total_lots:.2f} | "
              f"{len(positions)} pos (deep:{deep_level}/{max_deep}) | "
              f"BE: {weighted_entry:.2f} ({be_dist:.0f}pts {be_dir})")
        state[last_pnl_key] = total_pnl

    return False


# ─────────────────────────────────────────
#  MAIN ENTRY — one stack at a time, alternating
# ─────────────────────────────────────────
def open_greedy_stack(state, ema_timeframe):
    """
    Opens ONE stack at a time, alternating BUY → SELL → BUY → SELL.
    """
    can, reason = _can_enter(state)
    if not can:
        print(f"  ⏸️  Entry blocked: {reason}")
        return False

    # Don't open if any stack is already open
    if state.get("buy_stack_open") or state.get("sell_stack_open"):
        # Only warn if the flag is STALE (flagged open but no real positions)
        real_positions = get_bot_positions()
        has_buys  = any(p.type == BUY  for p in real_positions)
        has_sells = any(p.type == SELL for p in real_positions)
        if state.get("buy_stack_open") and not has_buys:
            print(f"  ⚠️  buy_stack_open was stale — auto-clearing")
            state["buy_stack_open"]      = False
            state["buy_deep_level"]      = 0
            state["buy_last_deep_price"] = None
            save_state(state)
        elif state.get("sell_stack_open") and not has_sells:
            print(f"  ⚠️  sell_stack_open was stale — auto-clearing")
            state["sell_stack_open"]      = False
            state["sell_deep_level"]      = 0
            state["sell_last_deep_price"] = None
            save_state(state)
        else:
            # Stack genuinely open — silently wait (no spam)
            return False
        return False

    ok, filter_reason = check_market_filters(ema_timeframe)
    if not ok:
        print(f"\n⏳ FILTER BLOCK | {filter_reason}")
        return False

    # Determine direction
    last = state.get("last_stack_direction")

    if last is None:
        print(f"  🔍 First entry — checking EMA direction...")
        direction, ema_val = get_trend_direction()
        if direction is None:
            print(f"  ⚠️  EMA returned None — cannot determine direction, skipping")
            return False
        print(f"  ✅ EMA direction: {'BUY 📈' if direction == BUY else 'SELL 📉'}")
    else:
        direction = SELL if last == BUY else BUY
        dir_str   = "BUY 📈" if direction == BUY else "SELL 📉"
        print(f"\n🔄 ALTERNATING → {dir_str} (last was {'BUY' if last == BUY else 'SELL'})")

    print(f"  🚀 Opening stack...")
    return _open_stack(state, direction, ema_timeframe)


# ─────────────────────────────────────────
#  MAIN GREEDY LOOP
# ─────────────────────────────────────────
def check_greedy(state, ema_timeframe):
    """
    Called every poll tick.
    Manages BUY stack and SELL stack independently.
    """
    if state["phase"] != PHASE_GREEDY:
        return

    positions = get_bot_positions()
    if not positions:
        # Both stacks closed — reset and wait
        state["buy_stack_open"]       = False
        state["buy_deep_level"]       = 0
        state["buy_last_deep_price"]  = None
        state["sell_stack_open"]      = False
        state["sell_deep_level"]      = 0
        state["sell_last_deep_price"] = None
        state["last_close_time"]      = time.time()
        invalidate_filter_cache()
        save_state(state)
        return

    # ── ACCOUNT EMERGENCY (kills everything) ─────────────────
    if _account_emergency(state):
        return

    # ── MANAGE THE ONE OPEN STACK ────────────────────────────
    buy_positions  = _buy_positions(positions)
    sell_positions = _sell_positions(positions)

    # Only one of these will have positions at any time
    if buy_positions:
        _manage_stack(state, BUY, buy_positions)
    elif sell_positions:
        _manage_stack(state, SELL, sell_positions)

    # ── UPDATE STACK FLAGS based on what's still open ────────
    remaining       = get_bot_positions()
    remaining_buys  = _buy_positions(remaining)
    remaining_sells = _sell_positions(remaining)

    if not remaining_buys and state.get("buy_stack_open"):
        state["buy_stack_open"] = False
        save_state(state)

    if not remaining_sells and state.get("sell_stack_open"):
        state["sell_stack_open"] = False
        save_state(state)