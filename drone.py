#!/usr/bin/env python3
"""
Claudio Symphony — preset-aware drone player.

Reads the active preset; if preset.drone is null, exits cleanly (no drone).
Otherwise loops the preset's drone WAV via afplay until idle-timeout.
Single-instance via PID file.
"""
import os, sys, time, signal, subprocess, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRESETS = HERE / "presets"
STATE = HERE / "state"
LOGS = HERE / "logs"
CONFIG = HERE / "config.json"
PID_FILE = STATE / "drone.pid"
ACTIVE_PRESET_FILE = STATE / "drone-preset.txt"
HEARTBEAT_FILE = STATE / "heartbeat"
LOG_FILE = LOGS / "drone.log"
STATE.mkdir(exist_ok=True); LOGS.mkdir(exist_ok=True)

IDLE_TIMEOUT_S = 10 * 60   # exit after 10 min of no events

def log(msg):
    try:
        with LOG_FILE.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass

def read_config():
    try:
        if CONFIG.exists():
            return json.loads(CONFIG.read_text())
    except Exception:
        pass
    return {}

def active_preset_name():
    return read_config().get("preset", "cathedral")

def load_preset(name):
    p = PRESETS / name / "preset.json"
    if not p.exists(): return None
    try: return json.loads(p.read_text())
    except Exception: return None

def existing_pid_alive():
    if not PID_FILE.exists(): return None
    try: pid = int(PID_FILE.read_text().strip())
    except Exception: return None
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid

def write_pid():
    PID_FILE.write_text(str(os.getpid()))

def cleanup_pid():
    try:
        if PID_FILE.exists() and int(PID_FILE.read_text().strip()) == os.getpid():
            PID_FILE.unlink()
    except Exception:
        pass

def heartbeat_age():
    try:
        return time.time() - float(HEARTBEAT_FILE.read_text().strip())
    except Exception:
        return None

def main():
    name = active_preset_name()
    preset = load_preset(name)
    if preset is None:
        log(f"preset {name} missing; exiting")
        sys.exit(1)

    drone_file = preset.get("drone")
    if not drone_file:
        log(f"preset {name} has no drone; exiting cleanly")
        print(f"preset '{name}' has no continuous drone; nothing to play.", file=sys.stderr)
        sys.exit(0)

    drone_path = PRESETS / name / "samples" / drone_file
    if not drone_path.exists():
        log(f"drone file missing: {drone_path}")
        sys.exit(1)

    existing = existing_pid_alive()
    if existing and existing != os.getpid():
        log(f"already running pid={existing}, exiting")
        print(f"drone already running pid={existing}", file=sys.stderr)
        sys.exit(1)
    write_pid()
    ACTIVE_PRESET_FILE.write_text(name)

    if not HEARTBEAT_FILE.exists():
        HEARTBEAT_FILE.write_text(str(time.time()))

    def shutdown(*_):
        log("shutdown signal")
        cleanup_pid()
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    cfg = read_config()
    drone_gain = float(cfg.get("drone_gain", preset.get("drone_gain", 0.45)))
    log(f"drone start preset={name} pid={os.getpid()} gain={drone_gain}")

    try:
        while True:
            age = heartbeat_age()
            if age is not None and age > IDLE_TIMEOUT_S:
                log(f"idle {age:.0f}s, exiting")
                break
            # If user switched presets while drone running, exit so
            # `claudio start` can re-spawn for the new preset.
            try:
                if ACTIVE_PRESET_FILE.read_text().strip() != active_preset_name():
                    log("preset changed; exiting")
                    break
            except Exception:
                pass
            try:
                subprocess.run(
                    ["/usr/bin/afplay", "-v", f"{drone_gain:.3f}", str(drone_path)],
                    check=False,
                )
            except Exception as e:
                log(f"afplay error: {e}")
                time.sleep(2)
    finally:
        cleanup_pid()
        log("drone stop")

if __name__ == "__main__":
    main()
