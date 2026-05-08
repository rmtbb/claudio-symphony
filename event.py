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
import sys, os, json, time, fnmatch, subprocess, random, re
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

# ---------- melodic picker ----------
#
# Replaces simple round-robin with a Markov-weighted shuffled bag that
# parses midi from filenames "NN_m{midi}.wav". Successive picks favor
# stepwise motion (2nds, 3rds), occasional skips (5ths, 6ths), and every
# ~7 notes drift toward A or E to land a phrase. Result: the sequence
# sounds like an actual melody wandering through the scale, never repeats
# the same pitch back-to-back, and never produces the literal ascending
# scale that pure round-robin gives.

_MIDI_PATTERN = re.compile(r'_m(\d+)\.wav$')

# Weight by interval (semitones) from previous pitch. Tuned for both the
# pentatonic and Lydian voices in the shipped presets — prefers stepwise,
# allows musical leaps, never repeats, never tritones (which won't occur
# in our scales anyway but defensive).
_INTERVAL_WEIGHTS = {
    0: 0.00,   # exact repeat — never
    1: 0.20,   # minor 2nd — awkward
    2: 1.50,   # major 2nd — favored (scale step)
    3: 1.40,   # minor 3rd — favored
    4: 1.20,   # major 3rd
    5: 1.00,   # perfect 4th
    6: 0.00,   # tritone — never
    7: 1.40,   # perfect 5th — favored
    8: 0.40,   # minor 6th — uncommon in pentatonic
    9: 1.10,   # major 6th
    10: 0.30,  # minor 7th
    11: 0.20,  # major 7th
    12: 0.80,  # octave — fine
}

def _interval_weight(prev_midi, next_midi):
    interval = abs(next_midi - prev_midi)
    if interval == 0:
        return 0.0
    if interval > 12:
        base = _INTERVAL_WEIGHTS.get(interval % 12, 0.5)
        oct_jumps = interval // 12
        return base * (0.7 ** (oct_jumps - 1))   # bigger leap, smaller weight
    return _INTERVAL_WEIGHTS.get(interval, 0.5)

def _parse_midi(filename):
    m = _MIDI_PATTERN.search(filename)
    return int(m.group(1)) if m else None

def _melody_state_file(preset_name, voice):
    return preset_state_dir(preset_name) / f"melody-{voice}.json"

def _load_state(p):
    try:
        if p.exists(): return json.loads(p.read_text())
    except Exception: pass
    return {}

def _save_state(p, d):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d))
    tmp.rename(p)

def melodic_pick(preset_name, voice, voice_dir):
    """Pick the next sample for this voice such that successive picks form
    a musical sequence (stepwise motion, occasional leaps, phrase landings,
    no immediate repeats). Falls back to plain shuffled bag for voices whose
    samples don't carry midi in filenames."""
    samples = list_samples(preset_name, voice_dir)
    if not samples: return None

    pitched = []
    for s in samples:
        m = _parse_midi(s.name)
        if m is not None: pitched.append((m, s))

    state_file = _melody_state_file(preset_name, voice)
    state = _load_state(state_file)

    # Unpitched voice (cluster, sparkle, bloom, swell, breath, wood, tap, bird, mokugyo, etc.)
    if not pitched:
        bag = state.get("bag", [])
        if not bag or any(b >= len(samples) for b in bag):
            bag = list(range(len(samples)))
            random.shuffle(bag)
        chosen = bag[0]
        _save_state(state_file, {"bag": bag[1:]})
        return samples[chosen]

    # Pitched voice — Markov-weighted pick from the shuffled bag
    last = state.get("last_pitches", [])
    bag = state.get("bag", [])
    phrase = state.get("phrase_count", 0)

    if not bag or any(b >= len(pitched) for b in bag):
        bag = list(range(len(pitched)))
        random.shuffle(bag)

    weights = []
    for idx in bag:
        midi, _ = pitched[idx]
        w = 1.0
        # Penalize recent repeats (last 3 most strongly)
        for i, recent in enumerate(reversed(last[-3:])):
            if midi == recent:
                w *= (0.05, 0.30, 0.55)[min(i, 2)]
        # Interval shape from immediately previous note
        if last:
            w *= _interval_weight(last[-1], midi)
        # Phrase landing: every ~7 notes, gravity pulls to A (root) or E (5th)
        if phrase >= 6:
            pc = midi % 12
            if pc == 9: w *= 2.5      # any A
            elif pc == 4: w *= 2.0    # any E
        weights.append(w)

    total = sum(weights)
    if total <= 0:
        chosen_bag_idx = random.randrange(len(bag))
    else:
        r = random.random() * total
        acc = 0.0
        chosen_bag_idx = len(bag) - 1
        for i, w in enumerate(weights):
            acc += w
            if acc >= r:
                chosen_bag_idx = i
                break

    sample_idx = bag[chosen_bag_idx]
    midi, sample = pitched[sample_idx]

    new_bag = bag[:chosen_bag_idx] + bag[chosen_bag_idx+1:]
    new_last = (last + [midi])[-4:]
    landed = (phrase >= 6) and (midi % 12 in (9, 4))
    new_phrase = 0 if landed else phrase + 1

    _save_state(state_file, {
        "last_pitches": new_last,
        "bag": new_bag,
        "phrase_count": new_phrase,
    })
    return sample

# Backward-compat alias — older code paths may still reference round_robin_pick
def round_robin_pick(preset_name, voice, voice_dir):
    return melodic_pick(preset_name, voice, voice_dir)

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
    sample = melodic_pick(preset_name, voice, cfg.get("dir", voice))
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
