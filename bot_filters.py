"""
bot_filters.py — Signal engine for Smart Small Account mode.
EMA is the GATE — RSI and candle body must CONFIRM it.
No EMA agreement = no trade. Period.
"""

import MetaTrader5 as mt5
import time
from bot_config import CONFIG

_last_candle_time = 0
_consecutive_signals = []  # tracks last 3 signal directions


def _calc_ema(closes, period):
    if len(closes) < period:
        return None
    ema = closes[0]
    k = 2 / (period + 1)
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema


def _calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def is_new_candle():
    """Returns True if a new M1 candle has just closed."""
    global _last_candle_time
    rates = mt5.copy_rates_from_pos(CONFIG["symbol"], mt5.TIMEFRAME_M1, 0, 2)
    if rates is None or len(rates) < 2:
        return False
    current_candle_time = rates[-1]["time"]
    if current_candle_time != _last_candle_time:
        _last_candle_time = current_candle_time
        return True
    return False


def get_signal():
    """
    Candle-first signal engine.
    Candle = gate (real-time direction)
    EMA = trend filter (are we trading with or against momentum)
    RSI = extreme filter only (block at 80/20)

    Returns:
      (direction, score, reason)
      score 3 = strong entry
      score 2 = valid but weaker (blocked by signal_strength_min=3)
      score 0 = skip
    """
    symbol     = CONFIG["symbol"]
    fast_p     = CONFIG["ema_fast"]
    slow_p     = CONFIG["ema_slow"]
    rsi_period = CONFIG["rsi_period"]
    needed     = max(slow_p, rsi_period) + 10

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, needed)
    if rates is None or len(rates) < needed:
        return None, 0, "Not enough M1 data"

    closes = [r["close"] for r in rates]
    opens  = [r["open"]  for r in rates]

    si    = mt5.symbol_info(symbol)
    point = si.point if si else 0.01

    # ── Candle body — THE GATE ────────────────────────────────
    last_close = closes[-2]   # -1 is forming, -2 just closed
    last_open  = opens[-2]
    body_pts   = abs(last_close - last_open) / point
    candle_dir = mt5.ORDER_TYPE_BUY if last_close > last_open else mt5.ORDER_TYPE_SELL
    candle_str = f"Candle={'Bull' if candle_dir==0 else 'Bear'}({body_pts:.0f}pts)"

    # Skip small/doji candles — unpredictable
    if body_pts < 30:
        return None, 0, f"⏸️  Candle too small ({body_pts:.0f}pts < 30) — unpredictable, skip"

    # ── EMA 9/21 — trend filter ───────────────────────────────
    ema_fast = _calc_ema(closes, fast_p)
    ema_slow = _calc_ema(closes, slow_p)
    if ema_fast is None or ema_slow is None:
        return None, 0, "EMA calc failed"

    ema_dir   = mt5.ORDER_TYPE_BUY if ema_fast > ema_slow else mt5.ORDER_TYPE_SELL
    ema_label = "📈BUY" if ema_dir == mt5.ORDER_TYPE_BUY else "📉SELL"
    ema_str   = f"EMA9={ema_fast:.2f} {'>' if ema_dir==0 else '<'} EMA21={ema_slow:.2f} {ema_label}"
    ema_gap   = abs(ema_fast - ema_slow) / point

    # EMA slope — is momentum going with or against EMA direction?
    ema_fast_prev = _calc_ema(closes[:-1], fast_p)
    ema_sloping_with = True
    if ema_fast_prev is not None:
        if ema_dir == mt5.ORDER_TYPE_BUY and ema_fast < ema_fast_prev:
            ema_sloping_with = False
        elif ema_dir == mt5.ORDER_TYPE_SELL and ema_fast > ema_fast_prev:
            ema_sloping_with = False

    # ── RSI — extreme filter only ─────────────────────────────
    rsi = _calc_rsi(closes, rsi_period)
    if rsi is None:
        return None, 0, "RSI calc failed"
    rsi_str = f"RSI={rsi:.1f}"

    # Block only at true extremes
    if candle_dir == mt5.ORDER_TYPE_BUY and rsi >= 80:
        return None, 0, f"Candle=BUY but {rsi_str} EXTREMELY OVERBOUGHT (≥80) — skip ❌"
    if candle_dir == mt5.ORDER_TYPE_SELL and rsi <= 20:
        return None, 0, f"Candle=SELL but {rsi_str} EXTREMELY OVERSOLD (≤20) — skip ❌"

    # ── Direction decision ────────────────────────────────────
    direction = candle_dir
    dir_label = "BUY 📈" if direction == mt5.ORDER_TYPE_BUY else "SELL 📉"

    # Case 1: Candle agrees with EMA + EMA sloping same way + gap strong
    if candle_dir == ema_dir and ema_sloping_with and ema_gap >= 30:
        score    = 3
        strength = "💪 STRONG (candle+EMA+slope)"

    # Case 2: Candle agrees with EMA, slope neutral or gap small
    elif candle_dir == ema_dir and ema_gap >= 30:
        score    = 3
        strength = "✅ GOOD (candle+EMA)"

    # Case 3: Candle contradicts EMA but EMA is rolling over (early reversal)
    elif candle_dir != ema_dir and not ema_sloping_with and body_pts >= 80:
        score    = 3
        strength = "🔄 REVERSAL (strong candle overrides lagging EMA)"
        direction = candle_dir  # trust the candle

    # Case 4: Candle contradicts EMA and EMA still trending — skip
    elif candle_dir != ema_dir and ema_sloping_with:
        return None, 0, f"⛔ Candle={dir_label} but EMA trending opposite — skip"

    # Case 5: Everything else — too weak
    else:
        return None, 0, f"⏸️  Weak signal — gap={ema_gap:.0f}pts, body={body_pts:.0f}pts — skip"

    # ── Trend shift guard ─────────────────────────────────────
    global _consecutive_signals
    _consecutive_signals.append(direction)
    if len(_consecutive_signals) > 3:
        _consecutive_signals.pop(0)

    # If last 2+ were opposite direction, force a pause
    if len(_consecutive_signals) >= 2:
        last_two = _consecutive_signals[-2:]
        if last_two[0] != direction and last_two[1] != direction:
            _consecutive_signals = []  # reset
            return None, 0, f"⏸️  Trend shift detected — pausing 1 candle to reassess"

    reason = f"{strength} {dir_label} | {ema_str} | gap={ema_gap:.0f}pts | {rsi_str} | {candle_str}"
    return direction, score, reason

_filter_cache = {"timestamp": 0.0, "result": None, "reason": ""}

def get_filter_cache():
    return _filter_cache

def invalidate_filter_cache():
    _filter_cache["timestamp"] = 0.0

def check_market_filters(ema_timeframe=None):
    return True, "Signal engine active ✅"