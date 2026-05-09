"""
bot_filters.py — Market condition filters for XAUUSD Greedy Scalper Bot
H1 trend alignment, range/volatility check, EMA slope strength.
"""

import MetaTrader5 as mt5
import time
from bot_config import CONFIG

# ─────────────────────────────────────────
#  FILTER RESULT CACHE
# ─────────────────────────────────────────
_filter_cache = {
    "timestamp":    0.0,
    "result":       None,
    "reason":       "",
    "h1_trend":     None,
    "m15_trend":    None,
    "avg_body":     0.0,
    "ema_slope":    0.0,
}


def get_filter_cache():
    return _filter_cache


def invalidate_filter_cache():
    _filter_cache["timestamp"] = 0.0


def _calc_ema(closes, period):
    if len(closes) < period:
        return None
    ema = closes[0]
    k   = 2 / (period + 1)
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema


def check_market_filters(ema_timeframe):
    """
    Run all market condition filters.
    Returns (ok_to_trade, reason_string).
    Results cached for filter_cache_seconds.
    """
    global _filter_cache

    now = time.time()

    # Skip cache if all filters are disabled — just return True immediately
    all_off = (
        not CONFIG["h1_filter_enabled"] and
        not CONFIG["range_filter_enabled"] and
        not CONFIG["trend_strength_enabled"]
    )
    if all_off:
        _filter_cache["timestamp"] = now
        _filter_cache["result"]    = True
        _filter_cache["reason"]    = "All filters OFF ✅"
        _filter_cache["h1_trend"]  = None
        _filter_cache["m15_trend"] = None
        return True, "All filters OFF ✅"

    if now - _filter_cache["timestamp"] < CONFIG["filter_cache_seconds"]:
        return _filter_cache["result"], _filter_cache["reason"]

    symbol = CONFIG["symbol"]
    point  = mt5.symbol_info(symbol).point if mt5.symbol_info(symbol) else 0.01
    reasons_fail = []
    reasons_pass = []

    # ── FILTER 1: H1 TREND CHECK ──────────────────────────
    if CONFIG["h1_filter_enabled"]:
        h1_period = CONFIG["h1_ema_period"]
        h1_rates  = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, h1_period + 10)

        if h1_rates is not None and len(h1_rates) >= h1_period:
            h1_closes  = [r["close"] for r in h1_rates]
            h1_ema     = _calc_ema(h1_closes, h1_period)
            h1_current = h1_closes[-1]
            h1_trend   = mt5.ORDER_TYPE_BUY if h1_current > h1_ema else mt5.ORDER_TYPE_SELL
            h1_label   = "📈" if h1_trend == mt5.ORDER_TYPE_BUY else "📉"

            m15_period = CONFIG["ema_period"]
            m15_rates  = mt5.copy_rates_from_pos(symbol, ema_timeframe, 0, m15_period + 10)
            m15_trend  = None
            m15_label  = "?"

            if m15_rates is not None and len(m15_rates) >= m15_period:
                m15_closes  = [r["close"] for r in m15_rates]
                m15_ema     = _calc_ema(m15_closes, m15_period)
                m15_current = m15_closes[-1]
                m15_trend   = mt5.ORDER_TYPE_BUY if m15_current > m15_ema else mt5.ORDER_TYPE_SELL
                m15_label   = "📈" if m15_trend == mt5.ORDER_TYPE_BUY else "📉"

            _filter_cache["h1_trend"]  = h1_trend
            _filter_cache["m15_trend"] = m15_trend

            if m15_trend is not None and h1_trend != m15_trend:
                msg = (f"H1 EMA{h1_period}: {h1_ema:.2f} | H1:{h1_label} M15:{m15_label} | ❌ CONFLICT")
                reasons_fail.append(msg)
            else:
                msg = (f"H1 EMA{h1_period}: {h1_ema:.2f} | H1:{h1_label} M15:{m15_label} | ✅ ALIGNED")
                reasons_pass.append(msg)
        else:
            reasons_pass.append("H1 data unavailable — skipping H1 filter")

    # ── FILTER 2: RANGE FILTER ────────────────────────────
    if CONFIG["range_filter_enabled"]:
        n_candles = CONFIG["range_filter_candles"]
        min_body  = CONFIG["range_filter_min_body_pts"]
        m15_rates = mt5.copy_rates_from_pos(symbol, ema_timeframe, 0, n_candles + 2)

        if m15_rates is not None and len(m15_rates) >= n_candles:
            bodies   = [abs(r["close"] - r["open"]) / point for r in m15_rates[-n_candles:]]
            avg_body = sum(bodies) / len(bodies)
            _filter_cache["avg_body"] = avg_body

            if avg_body < min_body:
                msg = f"Range: avg body {avg_body:.1f}pt < {min_body}pt | ❌ RANGING"
                reasons_fail.append(msg)
            else:
                msg = f"Range: avg body {avg_body:.1f}pt ≥ {min_body}pt | ✅ VOLATILE"
                reasons_pass.append(msg)
        else:
            reasons_pass.append("Range filter: insufficient data — skipping")

    # ── FILTER 3: TREND STRENGTH ──────────────────────────
    if CONFIG["trend_strength_enabled"]:
        lookback   = CONFIG["trend_strength_lookback"]
        min_slope  = CONFIG["trend_strength_min_slope"]
        m15_period = CONFIG["ema_period"]
        needed     = m15_period + lookback + 5
        m15_rates  = mt5.copy_rates_from_pos(symbol, ema_timeframe, 0, needed)

        if m15_rates is not None and len(m15_rates) >= needed:
            closes   = [r["close"] for r in m15_rates]
            ema_now  = _calc_ema(closes, m15_period)
            ema_past = _calc_ema(closes[:-lookback], m15_period)

            if ema_now is not None and ema_past is not None:
                slope = abs(ema_now - ema_past) / lookback
                _filter_cache["ema_slope"] = slope

                if slope < min_slope:
                    msg = f"Slope {slope:.2f}pt/bar < {min_slope}pt/bar | ❌ FLAT"
                    reasons_fail.append(msg)
                else:
                    msg = f"Slope {slope:.2f}pt/bar ≥ {min_slope}pt/bar | ✅ TRENDING"
                    reasons_pass.append(msg)
            else:
                reasons_pass.append("Trend strength: EMA calc failed — skipping")
        else:
            reasons_pass.append("Trend strength: insufficient data — skipping")

    # ── COMBINE ───────────────────────────────────────────
    ok_to_trade = len(reasons_fail) == 0
    reason_str  = " | ".join(reasons_fail + reasons_pass) if (reasons_fail or reasons_pass) \
                  else "All filters OFF — open market ✅"

    _filter_cache["timestamp"] = now
    _filter_cache["result"]    = ok_to_trade
    _filter_cache["reason"]    = reason_str

    return ok_to_trade, reason_str