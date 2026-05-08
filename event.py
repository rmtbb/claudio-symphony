#!/usr/bin/env python3
"""
Claudio Symphony — preset-aware hook entry with per-session routing.

Reads JSON from stdin, resolves the preset to use for this event using
(in order):
  1. explicit pin on this session_id (state/sessions.json)
  2. first-matching cwd rule (state/rules.json)
  3. global default (config.json -> preset)

Then maps the event through the preset's mapping table, applies per-voice
MIOI rate-limiting + pressure accumulation, and launches afplay detached.

Always exits 0.
"""
import sys, os, json, time, fnmatch, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRESETS = HERE / "presets"
STATE = HERE / "state"
LOGS = HERE / "logs"
CONFIG = HERE / "config.json"
SESSIONS_FILE = STATE / "sessions.json"
RULES_FILE = STATE / "rules.json"
LOG = LOGS / "event.log"
STATE.mkdir(exist_ok=True); LOGS.mkdir(exist_ok=True)

DEFAULT_PRESET = "cathedral"
SESSION_TTL_S = 4 * 3600   # prune sessions idle longer than this

def log(msg):
    try:
        with LOG.open("a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

# ---------- atomic JSON helpers ----------

def _load(p, default):
    try:
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return default

def _save(p, d):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.rename(p)

def read_config(): return _load(CONFIG, {})
def load_sessions(): return _load(SESSIONS_FILE, {"active": {}})
def save_sessions(d): _save(SESSIONS_FILE, d)
def load_rules(): return _load(RULES_FILE, {"rules": []})

def active_preset_name():
    return read_config().get("preset", DEFAULT_PRESET)

def load_preset(name):
    p = PRESETS / name / "preset.json"
    if not p.exists():
        log(f"preset.json missing for {name}")
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log(f"preset {name} parse error: {e}")
        return None

def preset_state_dir(name):
    d = STATE / name
    d.mkdir(parents=True, exist_ok=True)
    return d

# ---------- preset resolution ----------

def cwd_rule_match(cwd, pattern):
    """Glob if pattern has wildcards; otherwise prefix match on path components."""
    if not pattern: return False
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatch(cwd, pattern)
    pat = pattern.rstrip("/")
    return cwd == pat or cwd.startswith(pat + "/")

def resolve_preset(session_id, cwd):
    sessions = load_sessions().get("active", {})
    sess = sessions.get(session_id, {})
    if sess.get("preset_pinned"):
        return sess["preset_pinned"], "pin"
    if cwd:
        for r in load_rules().get("rules", []):
            if cwd_rule_match(cwd, r.get("pattern", "")):
                return r.get("preset"), f"rule:{r.get('pattern')}"
    return active_preset_name(), "default"

def update_session_record(session_id, cwd, event_name, resolved_preset, source):
    if not session_id:
        return
    sessions = load_sessions()
    active = sessions.setdefault("active", {})
    # prune stale
    cutoff = time.time() - SESSION_TTL_S
    for sid in list(active.keys()):
        if active[sid].get("last_seen", 0) < cutoff and sid != session_id:
            del active[sid]
    rec = active.setdefault(session_id, {})
    now = time.time()
    rec.setdefault("first_seen", now)
    rec["last_seen"] = now
    if cwd: rec["cwd"] = cwd
    rec["preset_resolved"] = resolved_preset
    rec["preset_source"] = source
    if event_name == "SessionEnd":
        rec["ended"] = True
    sessions["active"] = active
    try: save_sessions(sessions)
    except Exception as e: log(f"sessions write: {e}")

# ---------- sample selection + playback ----------

def list_samples(preset_name, voice_dir):
    d = PRESETS / preset_name / "samples" / voice_dir
    if not d.exists(): return []
    return sorted(p for p in d.iterdir() if p.suffix == ".wav")

def round_robin_pick(preset_name, voice, voice_dir):
    samples = list_samples(preset_name, voice_dir)
    if not samples: return None
    state_file = preset_state_dir(preset_name) / f"rr-{voice}.txt"
    try: idx = int(state_file.read_text().strip())
    except Exception: idx = 0
    pick = samples[idx % len(samples)]
    state_file.write_text(str((idx + 1) % len(samples)))
    return pick

def check_mioi(preset_name, voice, mioi_s):
    sd = preset_state_dir(preset_name)
    last_file = sd / f"last-{voice}.txt"
    pressure_file = sd / f"pressure-{voice}.txt"
    now = time.time()
    try: last = float(last_file.read_text().strip())
    except Exception: last = 0.0
    if now - last < mioi_s:
        try: p = int(pressure_file.read_text().strip())
        except Exception: p = 0
        pressure_file.write_text(str(p + 1))
        return False, 0.0
    try: p = int(pressure_file.read_text().strip())
    except Exception: p = 0
    pressure_file.write_text("0")
    last_file.write_text(str(now))
    pressure_db = min(3.0, 0.5 * p)
    return True, pressure_db

def play(sample_path, gain_linear):
    if sample_path is None: return
    v = max(0.0, min(1.0, gain_linear))
    try:
        subprocess.Popen(
            ["/usr/bin/afplay", "-v", f"{v:.3f}", str(sample_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as e:
        log(f"afplay launch failed: {e}")

def trigger(preset_name, preset, voice):
    if voice is None: return
    voices = preset.get("voices", {})
    cfg = voices.get(voice)
    if cfg is None:
        log(f"unknown voice '{voice}' in preset {preset_name}")
        return
    ok, pressure_db = check_mioi(preset_name, voice, cfg.get("mioi", 0.5))
    if not ok: return
    sample = round_robin_pick(preset_name, voice, cfg.get("dir", voice))
    if sample is None: return
    cfg_master = read_config().get("master_gain")
    master = float(cfg_master if cfg_master is not None
                   else preset.get("master_gain", 0.5))
    base_lin = float(cfg.get("gain", 0.5)) * master
    pressure_lin = 10 ** (pressure_db / 20.0)
    play(sample, base_lin * pressure_lin)

# ---------- event mapping ----------

def is_failure(payload):
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("error"):
            return True
    if payload.get("tool_error"):
        return True
    return False

def resolve_voice(preset, event_name, payload):
    events = preset.get("events", {})
    spec = events.get(event_name)
    if not spec:
        return None
    if event_name == "PostToolUse" and is_failure(payload):
        return spec.get("on_failure") or spec.get("default")
    tool = payload.get("tool_name", "")
    by_tool = spec.get("by_tool") or {}
    if tool and tool in by_tool:
        return by_tool[tool]
    return spec.get("default")

# ---------- main handler ----------

def handle(payload):
    event = payload.get("hook_event_name") or payload.get("event") or "unknown"
    tool = payload.get("tool_name", "")
    session = payload.get("session_id", "")
    cwd = payload.get("cwd", "")

    preset_name, source = resolve_preset(session, cwd)
    preset = load_preset(preset_name)
    update_session_record(session, cwd, event, preset_name, source)

    if preset is None:
        log(f"no preset loaded ({preset_name}); silent")
        return

    log(f"[{preset_name}/{source}] event={event} tool={tool} session={session[:8]}")
    try:
        (STATE / "heartbeat").write_text(str(time.time()))
    except Exception:
        pass

    voice = resolve_voice(preset, event, payload)
    trigger(preset_name, preset, voice)

def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        log(f"json parse error: {e}; raw={raw[:200]!r}")
        return 0
    try: handle(payload)
    except Exception as e: log(f"handle error: {e}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
