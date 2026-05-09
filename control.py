"""
Bot Control Panel v5.0 — run this in a SEPARATE terminal while the bot runs
Usage: python control.py [command]

  pause       → pause the bot (open trades stay open)
  resume      → resume the bot
  status      → show current state
  close       → emergency close ALL open positions right now
  reset       → reset greedy state (direction, avg levels, cycle)
  avgup       → increase max averaging levels by 1 (live tune)
  avgdown     → decrease max averaging levels by 1 (live tune)
  threshold   → show/set the blue-close threshold (e.g: python control.py threshold 0.50)
  emergency   → manually trigger account emergency stop
"""

import sys
import json
import os

STATE_FILE = "bot_state_v50.json"
CONFIG_FILE = "bot_config.py"  # for display only — live changes go via state overrides


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    print("❌ Bot not running (no state file found)")
    sys.exit(1)


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)


def close_all_mt5():
    """Close all XAUUSD positions via MT5 directly."""
    import MetaTrader5 as mt5
    mt5.initialize()
    positions = mt5.positions_get(symbol="XAUUSD")
    closed = 0
    if positions:
        for pos in positions:
            tick       = mt5.symbol_info_tick("XAUUSD")
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price      = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

            # Try all filling modes
            for mode in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]:
                r = mt5.order_send({
                    "action":       mt5.TRADE_ACTION_DEAL,
                    "symbol":       "XAUUSD",
                    "volume":       pos.volume,
                    "type":         close_type,
                    "position":     pos.ticket,
                    "price":        price,
                    "deviation":    50,
                    "magic":        20240101,
                    "comment":      "manual_close",
                    "type_filling": mode,
                })
                if r and r.retcode == mt5.TRADE_RETCODE_DONE:
                    closed += 1
                    break
    mt5.shutdown()
    return closed


cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

# ─────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────

if cmd == "pause":
    s = load_state()
    s["manual_pause"] = True
    save_state(s)
    print("⏸️  Bot PAUSED — open trades stay open, no new entries")

elif cmd == "resume":
    s = load_state()
    s["manual_pause"] = False
    save_state(s)
    print("▶️  Bot RESUMED")

elif cmd == "reset":
    s = load_state()
    s["direction"]          = None
    s["last_grid_price"]    = None
    s["cycle_entry_price"]  = None
    s["greedy_avg_level"]   = 0
    s["phase"]              = "GREEDY"
    s["last_close_time"]    = 0.0
    save_state(s)
    print("🔄 Greedy state reset — bot starts fresh next cycle")
    print("   ⚠️  This does NOT close open positions. Use 'close' for that.")

elif cmd == "close":
    print("🚨 Closing ALL open positions...")
    closed = close_all_mt5()
    print(f"✅ Closed {closed} positions")
    # Also reset state so bot doesn't think it still has a cycle open
    s = load_state()
    s["direction"]        = None
    s["greedy_avg_level"] = 0
    s["phase"]            = "GREEDY"
    s["last_close_time"]  = __import__("time").time()
    save_state(s)
    print("   State reset — bot will start fresh.")

elif cmd == "emergency":
    print("🚨 MANUAL EMERGENCY STOP triggered!")
    closed = close_all_mt5()
    print(f"   Closed {closed} positions")
    s = load_state()
    s["direction"]             = None
    s["greedy_avg_level"]      = 0
    s["phase"]                 = "GREEDY"
    s["last_emergency_time"]   = __import__("time").time()
    s["last_failed_direction"] = s.get("direction")
    s["last_close_time"]       = __import__("time").time()
    save_state(s)
    print("   30s emergency cooldown started. Bot will resume after cooldown.")

elif cmd == "avgup":
    # Dynamically nudge the max averaging levels in the state
    # (bot reads this from CONFIG but we can show/suggest the change)
    s = load_state()
    current = s.get("_override_max_avg_levels", None)
    print("ℹ️  To change max averaging levels, edit bot_config.py:")
    print(f"   'greedy_max_avg_levels': X   (currently in config)")
    print(f"   Then restart the bot.")
    print(f"   Current avg level in this cycle: {s.get('greedy_avg_level', 0)}")

elif cmd == "avgdown":
    s = load_state()
    print("ℹ️  To change max averaging levels, edit bot_config.py:")
    print(f"   'greedy_max_avg_levels': X")
    print(f"   Then restart the bot.")
    print(f"   Current avg level in this cycle: {s.get('greedy_avg_level', 0)}")

elif cmd == "threshold":
    if len(sys.argv) > 2:
        try:
            val = float(sys.argv[2])
            print(f"ℹ️  To change close threshold to ${val:.2f}, edit bot_config.py:")
            print(f"   'greedy_close_threshold': {val}")
            print(f"   Then restart the bot (or the bot will pick it up next cycle if you edit live).")
        except ValueError:
            print("❌ Usage: python control.py threshold 0.50")
    else:
        print("ℹ️  Usage: python control.py threshold [amount]")
        print("   Example: python control.py threshold 0.50")
        print("   Then update bot_config.py with that value.")

elif cmd == "status":
    s = load_state()
    import time

    win_rate    = (s["total_wins"] / s["total_trades"] * 100) if s.get("total_trades", 0) > 0 else 0
    dir_val     = s.get("direction")
    dir_str     = "BUY 📈" if dir_val == 0 else ("SELL 📉" if dir_val == 1 else "— None")
    avg_lvl     = s.get("greedy_avg_level", 0)
    phase       = s.get("phase", "GREEDY")

    # Cooldown remaining
    now          = time.time()
    since_close  = now - s.get("last_close_time", 0)
    since_emerg  = now - s.get("last_emergency_time", 0)
    if s.get("last_emergency_time", 0) > 0 and since_emerg < 30:
        cd_str = f"POST-EMERGENCY {30 - since_emerg:.0f}s remaining"
    elif since_close < 5:
        cd_str = f"{5 - since_close:.1f}s remaining"
    else:
        cd_str = "ready ✅"

    print("\n" + "═"*50)
    print("  🎲 GREEDY SCALPER BOT v5.0 — STATUS")
    print("═"*50)
    print(f"  Phase:        {phase}")
    print(f"  Paused:       {'YES ⏸️' if s.get('manual_pause') else 'NO ▶️'}")
    print(f"  Direction:    {dir_str}")
    print(f"  Avg Level:    {avg_lvl} / (see bot_config.py for max)")
    print(f"  Cooldown:     {cd_str}")
    print(f"  Bot Capital:  ${s.get('bot_balance', 0):.2f}")
    print(f"  Locked:       ${s.get('locked_capital', 0):.2f}")
    print("─"*50)
    print(f"  Total Trades: {s.get('total_trades', 0)}")
    print(f"  Total Wins:   {s.get('total_wins', 0)}")
    print(f"  Win Rate:     {win_rate:.1f}%")
    print(f"  Greedy Wins:  {s.get('total_greedy_wins', 0)}")
    print(f"  Flips caught: {s.get('total_greedy_flips', 0)}")
    print(f"  Reloads:      {s.get('total_reloads', 0)}x")
    print("═"*50)

else:
    print(__doc__)