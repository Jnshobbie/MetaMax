"""
bot_config.py — XAUUSD Smart Scalper (Small Account Mode)
Strategy: Signal-first, clean entries, accept losses fast, recover 3x.
"""

CONFIG = {
    "login":    16736819,
    "password": "(y6C6cH^",
    "server":   "Weltrade-Real",

    "symbol": "XAUUSD_i",
    "magic":  20240101,
    "filling_mode_override": "FOK",   # skip test trade — IOC confirmed working

    # ── Lot sizing — small account, grows carefully ─────────────
    # IMPORTANT: Set force_lot to your real account's appropriate lot.
    # This overrides lot_phases entirely — use when demo balance != real balance.
    # Set to None to use lot_phases based on actual account balance instead.
    "force_lot":  0.01,          # ← SET THIS (0.01 for $5 real account)

    "lot_phases": [
        {"min_balance": 0,    "lot": 0.01},
        {"min_balance": 20,   "lot": 0.02},
        {"min_balance": 40,   "lot": 0.03},
        {"min_balance": 75,   "lot": 0.05},
        {"min_balance": 120,  "lot": 0.08},
    ],
    "lot_hard_cap": 0.08,

    # ── Signal thresholds ───────────────────────────────────────
    "ema_fast":   9,        # EMA fast period (M1)
    "ema_slow":   21,       # EMA slow period (M1)
    "rsi_period": 14,
    "rsi_buy_max":  60,     # Only BUY if RSI < 60
    "rsi_sell_min": 20,     # Only SELL if RSI > 20

    # ── Entry rules ─────────────────────────────────────────────
    "wait_for_candle_close": True,   # Enter only after M1 candle closes
    "signal_strength_min": 3,        # Min signals that must agree (out of 3)
    "entry_drift_limit_pts": 300,  # skip if price drifted 300pts against signal

    # ── Take profit / Stop loss ─────────────────────────────────
    "tp_normal_points":   1500,       # Normal TP: 15 pips on XAUUSD
    "sl_points":          1200,       # Stop loss: 10 pips — accept loss, move on
    "tp_recovery_multiplier": 3.0,   # Recovery TP = 3x last loss amount

    # ── Recovery mode ───────────────────────────────────────────
    # After a loss, next trade uses 3x TP. Once hit, back to normal.
    "recovery_mode": False,          # toggled automatically by bot
    "last_loss_amount": 0.0,         # tracked automatically

    # ── Daily loss limit (replaces bot cap / lock safe) ─────────
    "daily_loss_limit":  2.00,       # Stop trading today if down $2
    "daily_loss_today":  0.0,        # tracked automatically (reset midnight)

    # ── Emergency stop ──────────────────────────────────────────
    "greedy_account_stop_pct": 0.40, # Kill all if equity drops 40% of balance

    # ── Speed ───────────────────────────────────────────────────
    "poll_interval":      0.05,
    "status_interval":    15,
    "heartbeat_interval": 10,
    "reentry_cooldown":   3.0,       # Seconds after close before re-entering
    "emergency_cooldown": 60.0,

    # ── Safety ──────────────────────────────────────────────────
    "max_open_positions": 3,         # Max 1 active trade + 2 buffer

    # ── Filter cache ────────────────────────────────────────────
    "filter_cache_seconds": 0,       # No cache — check every candle close

    # ── Session ─────────────────────────────────────────────────
    "trading_mode": "always",
    "sessions": [
        {"name": "London",   "start_h": 8,  "end_h": 12},
        {"name": "New York", "start_h": 13, "end_h": 21},
    ],
}

# Phase constants
PHASE_SMART   = "SMART"
PHASE_GREEDY  = "GREEDY"   # kept for compat

STATE_FILE = "bot_state_v50.json"