"""
bot_state.py — State persistence for XAUUSD Greedy Scalper Bot
"""

import json
import os
from bot_config import CONFIG, STATE_FILE, PHASE_GREEDY


def default_state():
    return {
        "bot_balance":          CONFIG["starting_capital"],
        "locked_capital":       CONFIG["starting_capital"],
        "total_trades":         0,
        "total_wins":           0,
        "grid_level":           0,
        "last_grid_price":      None,
        "direction":            None,
        "manual_pause":         False,
        "mode":                 "auto",
        "reload_pending":       False,
        "total_reloads":        0,
        "reload_history":       [],
        "last_close_time":      0.0,
        "last_emergency_time":  0.0,
        "last_failed_direction":None,
        "cycle_entry_price":    None,

        # Phase
        "phase":                PHASE_GREEDY,
        "compression_fired":    False,
        "harvest_peak":         0.0,
        "harvest_peak_time":    0.0,

        # ── BUY stack state ──────────────────────
        "buy_stack_open":       False,
        "buy_deep_level":       0,
        "buy_last_deep_price":  None,
        "buy_entry_price":      None,

        # ── SELL stack state ─────────────────────
        "sell_stack_open":      False,
        "sell_deep_level":      0,
        "sell_last_deep_price": None,
        "sell_entry_price":     None,

        # ── Alternation tracker ──────────────────
        # None = first entry (use EMA), 0 = last was BUY, 1 = last was SELL
        "last_stack_direction": None,

        # Stats
        "total_harvests":         0,
        "total_cuts":             0,
        "total_velocity_exits":   0,
        "total_scout_rejections": 0,
        "total_greedy_wins":      0,
        "total_greedy_flips":     0,
    }


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
        for k, v in default_state().items():
            if k not in s:
                s[k] = v
        return s
    return default_state()


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)