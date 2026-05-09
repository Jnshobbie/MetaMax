"""
bot_config.py — XAUUSD Greedy Scalper Bot
All configuration constants live here.
"""

CONFIG = {
    "login":    5049264119,
    "password": "@6LyPcCg",
    "server":   "MetaQuotes-Demo",

    "symbol": "XAUUSD",
    "magic":  20240101,

    # ── Capital management ──────────────────
    "starting_capital": 50.0,
    "lock_profit_at":   2.0,

    # ── Lot sizing phases (CAPPED LOW intentionally) ────────────
    # Strategy: grow fast when blue, lose small when red.
    # Base lot stays tiny so a bad streak never wipes big gains.
    # The burst x1.5 multiplier already scales across 6 positions
    # so we never need a big base lot to make meaningful money.
    # To unlock higher lots manually, edit the numbers below.
    "lot_phases": [
    {"min_balance": 0,     "lot": 0.05},
    {"min_balance": 200,   "lot": 0.10},
    {"min_balance": 500,   "lot": 0.45},
    {"min_balance": 1000,  "lot": 0.75},
    {"min_balance": 3000,  "lot": 2.36},
],

    # ── Martingale / Grid ────────────────────
    "martingale_multiplier":  3.0,
    "max_grid_levels":        3,
    "grid_step_points":       300,
    "take_profit_points":     500,
    "take_profit_l4_points":  700,

    # ── GREEDY MODE ─────────────────────────────────────────────────
    # No hard stop-loss. Never close in red.
    # Close trigger: combined P&L > threshold OR any individual turns blue.
    "greedy_mode":            True,
    "greedy_close_threshold": 0.10,   # $0.10 = close all (or snipe individual)

    # ── BURST (levels 1-6, ALL opened SIMULTANEOUSLY on entry) ──────
    # The moment the bot enters, it fires ALL 6 positions at the same time.
    # Each level uses a larger lot so break-even is pulled in from the start.
    # Lot sizes: base, base*1.5, base*2.25, base*3.37, base*5.06, base*7.59
    # Example with base=0.03: 0.03, 0.05, 0.07, 0.10, 0.15, 0.23 lots
    "greedy_burst_levels":    6,      # positions fired simultaneously at entry
    "greedy_avg_multiplier":  1.5,    # lot multiplier per burst level

    # ── DEEP LEVELS (levels 7-10, added every 200pts if still in red) ─
    # After the burst is open, if price KEEPS going against us,
    # add one heavy position every 200pts to keep dragging break-even.
    "greedy_deep_levels":     4,      # levels 7, 8, 9, 10
    "greedy_deep_step":       200,    # points against us to trigger each deep level

    # ── Emergency account protection ────────────────────────────────
    "greedy_account_stop_pct": 0.20,  # equity drops 20% of balance -> kill all

    # ── PRECISION fallback (if greedy_mode = False) ──────────────────
    "scout_profit_trigger":  3.0,
    "scout_loss_cap":        -2.0,
    "burst_loss_cap":        -5.0,
    "ignition_trigger":      4.0,
    "compression_layers":    [8, 9, 10],
    "compression_lots":      [0.04, 0.05, 0.05],
    "compression_delay":     0.05,
    "harvest_trail_tiers": [
        {"peak_above": 100, "trail_pct": 0.80},
        {"peak_above": 40,  "trail_pct": 0.85},
        {"peak_above": 15,  "trail_pct": 0.90},
        {"peak_above": 0,   "trail_pct": 0.95},
    ],
    "velocity_timeout":      2.0,
    "velocity_min_peak":     8.0,
    "harvest_floor_min":     3.0,
    "harvest_emergency_stop": -18.0,

    # ── Trend filter (M15 EMA) ──────────────
    "ema_period": 50,

    # ── FILTERS (all OFF by default — toggle via app or set True here) ──
    # H1 trend alignment check
    "h1_ema_period":   21,
    "h1_filter_enabled": False,   # OFF by default

    # Range/volatility filter
    "range_filter_enabled":      False,  # OFF by default
    "range_filter_candles":      10,
    "range_filter_min_body_pts": 20,

    # EMA slope / trend strength
    "trend_strength_enabled":    False,  # OFF by default
    "trend_strength_lookback":   5,
    "trend_strength_min_slope":  0.5,

    # Filter cache
    "filter_cache_seconds":      60,

    # ── SESSION MODE ────────────────────────
    "trading_mode": "always",
    "sessions": [
        {"name": "Asian",    "start_h": 0,  "end_h": 8},
        {"name": "London",   "start_h": 8,  "end_h": 12},
        {"name": "New York", "start_h": 13, "end_h": 21},
        {"name": "Late",     "start_h": 21, "end_h": 24},
    ],

    # ── Speed ───────────────────────────────
    "poll_interval":         0.01,
    "status_interval":       10,
    "heartbeat_interval":    10,
    "failed_burst_cooldown": 3.0,

    # ── Cooldowns ───────────────────────────
    "reentry_cooldown":          1.0,
    "emergency_cooldown":       30.0,
    "same_direction_extra_wait": 15.0,

    # ── Safety ──────────────────────────────
    "max_open_positions": 20,
    "kill_switch_ratio":  4.0,
}

# Phase constants
PHASE_MICRO_SCOUT  = "MICRO_SCOUT"
PHASE_BURST        = "BURST"
PHASE_COMPRESSING  = "COMPRESSING"
PHASE_HARVESTING   = "HARVESTING"
PHASE_GREEDY       = "GREEDY"

STATE_FILE = "bot_state_v50.json"