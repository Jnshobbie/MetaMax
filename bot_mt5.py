"""
bot_mt5.py — MT5 connection, order helpers, parallel close engine
"""

import MetaTrader5 as mt5
import threading
import time
from datetime import datetime
from bot_config import CONFIG

FILLING_MODE  = None
EMA_TIMEFRAME = None


# ─────────────────────────────────────────
#  CONNECT
# ─────────────────────────────────────────
def connect():
    global EMA_TIMEFRAME
    if not mt5.initialize(
        login=CONFIG["login"],
        password=CONFIG["password"],
        server=CONFIG["server"]
    ):
        print(f"❌ MT5 connect failed: {mt5.last_error()}")
        return False
    EMA_TIMEFRAME = mt5.TIMEFRAME_M1   # Smart scalper runs on M1
    info = mt5.account_info()
    print(f"✅ Connected | Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f}")
    server_time = datetime.fromtimestamp(mt5.symbol_info_tick(CONFIG["symbol"]).time)
    print(f"   Server time: {server_time.strftime('%Y-%m-%d %H:%M:%S')} (broker time)")
    print(f"   Local time:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (your PC)")
    return True


# ─────────────────────────────────────────
#  FILLING MODE DETECTION
# ─────────────────────────────────────────
def detect_filling_mode():
    global FILLING_MODE
    override = CONFIG.get("filling_mode_override")
    if override:
        mode_map = {
            "RETURN": mt5.ORDER_FILLING_RETURN,
            "IOC":    mt5.ORDER_FILLING_IOC,
            "FOK":    mt5.ORDER_FILLING_FOK,
        }
        if override in mode_map:
            FILLING_MODE = mode_map[override]
            print(f"\n🔍 Filling mode: {override} (skipping test trade)")
            return True

    symbol_info = mt5.symbol_info(CONFIG["symbol"])
    if not symbol_info:
        print("❌ Cannot detect filling mode — symbol not found")
        return False

    tick = mt5.symbol_info_tick(CONFIG["symbol"])
    filling_names = {
        mt5.ORDER_FILLING_RETURN: "RETURN",
        mt5.ORDER_FILLING_IOC:    "IOC",
        mt5.ORDER_FILLING_FOK:    "FOK",
    }

    print("\n🔍 Detecting supported order filling mode...")
    for mode, name in filling_names.items():
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       CONFIG["symbol"],
            "volume":       0.01,
            "type":         mt5.ORDER_TYPE_BUY,
            "price":        tick.ask,
            "tp":           round(tick.ask + 500 * symbol_info.point, 2),
            "deviation":    50,
            "magic":        CONFIG["magic"],
            "comment":      "fill_test",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mode,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"  ✅ Filling mode: {name} works!")
            FILLING_MODE = mode
            time.sleep(0.3)
            pos = mt5.positions_get(symbol=CONFIG["symbol"])
            if pos:
                for p in pos:
                    if p.magic == CONFIG["magic"] and p.comment == "fill_test":
                        mt5.order_send({
                            "action":       mt5.TRADE_ACTION_DEAL,
                            "symbol":       CONFIG["symbol"],
                            "volume":       p.volume,
                            "type":         mt5.ORDER_TYPE_SELL,
                            "position":     p.ticket,
                            "price":        mt5.symbol_info_tick(CONFIG["symbol"]).bid,
                            "deviation":    50,
                            "magic":        CONFIG["magic"],
                            "comment":      "fill_test_close",
                            "type_filling": mode,
                        })
            return True
        else:
            code = result.retcode if result else "None"
            print(f"  ❌ {name} failed (retcode: {code}) — trying next...")

    print("❌ No filling mode worked")
    return False


# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────
def get_bot_positions():
    pos = mt5.positions_get(symbol=CONFIG["symbol"])
    return [p for p in pos if p.magic == CONFIG["magic"]] if pos else []


def get_lot_for_balance(balance):
    """Get lot size. force_lot in config overrides everything."""
    if CONFIG.get("force_lot") is not None:
        return CONFIG["force_lot"]
    lot = CONFIG["lot_phases"][0]["lot"]
    for phase in reversed(CONFIG["lot_phases"]):
        if balance >= phase["min_balance"]:
            lot = phase["lot"]
            break
    return min(lot, CONFIG["lot_hard_cap"])


def get_total_lots(positions):
    return round(sum(p.volume for p in positions), 2)


def place_order(order_type, lot, comment="bot", sl=None, tp=None):
    """
    Place a single market order with optional TP and SL.
    Uses the detected filling mode.
    """
    global FILLING_MODE
    if FILLING_MODE is None:
        print("  ❌ place_order: no filling mode detected yet")
        return None

    symbol_info = mt5.symbol_info(CONFIG["symbol"])
    if not symbol_info:
        return None
    if not symbol_info.visible:
        mt5.symbol_select(CONFIG["symbol"], True)

    tick  = mt5.symbol_info_tick(CONFIG["symbol"])
    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       CONFIG["symbol"],
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "deviation":    50,
        "magic":        CONFIG["magic"],
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": FILLING_MODE,
    }
    if tp is not None:
        request["tp"] = tp
    if sl is not None:
        request["sl"] = sl

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        dir_str = "BUY " if order_type == mt5.ORDER_TYPE_BUY else "SELL"
        print(f"  ✅ {dir_str} {lot} lots @ {price:.2f}"
              + (f" | TP: {tp:.2f}" if tp else "")
              + (f" | SL: {sl:.2f}" if sl else ""))
        return result
    else:
        code = result.retcode if result else mt5.last_error()
        print(f"  ❌ Order failed | retcode: {code}")
        return None


# ─────────────────────────────────────────
#  PARALLEL CLOSE ENGINE
# ─────────────────────────────────────────
def _close_single(pos, results, idx):
    global FILLING_MODE
    tick       = mt5.symbol_info_tick(CONFIG["symbol"])
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price      = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

    modes = [FILLING_MODE] if FILLING_MODE else []
    for m in [mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK]:
        if m not in modes:
            modes.append(m)

    for mode in modes:
        result = mt5.order_send({
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       CONFIG["symbol"],
            "volume":       pos.volume,
            "type":         close_type,
            "position":     pos.ticket,
            "price":        price,
            "deviation":    50,
            "magic":        CONFIG["magic"],
            "comment":      "close",
            "type_filling": mode,
        })
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            results[idx] = True
            return
    results[idx] = False


def close_positions_parallel(positions):
    """Close all given positions simultaneously. Returns count closed."""
    if not positions:
        return 0
    results = [False] * len(positions)
    threads = []
    for i, pos in enumerate(positions):
        t = threading.Thread(target=_close_single, args=(pos, results, i), daemon=True)
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.0)
    return sum(1 for r in results if r)


def close_all_positions():
    return close_positions_parallel(get_bot_positions())


def close_position_single(pos):
    results = [False]
    _close_single(pos, results, 0)
    return results[0]


# ─────────────────────────────────────────
#  SESSION CHECK
# ─────────────────────────────────────────
def in_session():
    if CONFIG["trading_mode"] == "always":
        return True, "24/7 Mode"
    tick = mt5.symbol_info_tick(CONFIG["symbol"])
    if not tick:
        return False, None
    server_h = datetime.fromtimestamp(tick.time).hour
    for s in CONFIG["sessions"]:
        if s["start_h"] <= server_h < s["end_h"]:
            return True, s["name"]
    return False, None


# ─────────────────────────────────────────
#  TREND (legacy shim — new signals in bot_filters.py)
# ─────────────────────────────────────────
def get_trend_direction():
    """Legacy shim kept for import compat. Real signals now in bot_filters.get_signal()."""
    rates = mt5.copy_rates_from_pos(CONFIG["symbol"], mt5.TIMEFRAME_M1, 0, 30)
    if rates is None or len(rates) < 2:
        return mt5.ORDER_TYPE_SELL, None
    closes  = [r["close"] for r in rates]
    trend   = mt5.ORDER_TYPE_BUY if closes[-1] > closes[-2] else mt5.ORDER_TYPE_SELL
    return trend, closes[-1]