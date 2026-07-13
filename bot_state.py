"""
bot_state.py — State persistence for XAUUSD Smart Scalper
"""

import json
import os
from bot_config import STATE_FILE, PHASE_SMART


def default_state():
    return {
        # ── Core ────────────────────────────────
        "phase":                PHASE_SMART,
        "manual_pause":         False,
        "mode":                 "auto",

        # ── Active trade ────────────────────────
        "position_open":        False,
        "position_ticket":      None,
        "position_tickets":     [],
        "last_entry_time":      0.0,
        "last_close_time":      0.0,
        "last_emergency_time":  0.0,

        # ── Recovery mode ───────────────────────
        "recovery_mode":        False,
        "last_loss_amount":     0.0,

        # ── Daily loss tracker ──────────────────
        "daily_loss_today":     0.0,
        "daily_loss_date":      "",

        # ── Stats ───────────────────────────────
        "total_trades":         0,
        "total_wins":           0,
    }


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
        # Add any missing keys from default (safe upgrades)
        for k, v in default_state().items():
            if k not in s:
                s[k] = v
        return s
    return default_state()


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)