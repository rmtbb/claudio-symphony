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
import sys, os, json, time, fnmatch, subprocess, random, re, shlex
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import song as song_mod  # noqa: E402  (sibling module, must come after sys.path)
PRESETS = HERE / "presets"
STATE = HERE / "state"
LOGS = HERE / "logs"
CONFIG = HERE / "config.json"
SESSIONS_FILE = STATE / "sessions.json"
RULES_FILE = STATE / "rules.json"
LOG = LOGS / "event.log"
REC_ACTIVE = STATE / "recording" / "active.json"     # presence = recording window open
REC_EVENTS = STATE / "recording" / "events.jsonl"    # one line per sound played
STATE.mkdir(exist_ok=True); LOGS.mkdir(exist_ok=True)

DEFAULT_PRESET = "meadow"
SESSION_TTL_S = 4 * 3600   # prune sessions idle longer than this

def log(msg):
    try:
        with LOG.open("a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

# ---------- recording capture (dependency-free; mixing lives in record.py) ----------

def _rec_window():
    """If a recording is open, return (start_epoch, duration_s); else None.
    One cheap stat in the common (not-recording) case."""
    try:
        if not REC_ACTIVE.exists():
            return None
        m = json.loads(REC_ACTIVE.read_text())
        return (float(m["start"]), float(m["duration"]))
    except Exception:
        return None

def _rec_log(sample_path, gain_v, rate, t_play):
    """Append one sound to the recording timeline. O_APPEND keeps concurrent
    hook processes from clobbering each other (lines are < PIPE_BUF)."""
    try:
        line = json.dumps({"t": round(float(t_play), 4), "wav": str(sample_path),
                           "v": round(float(gain_v), 4), "r": round(float(rate or 1.0), 5)})
        with REC_EVENTS.open("a") as f:
            f.write(line + "\n")
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
    """Glob if pattern has wildcards; otherwise prefix match on path components.
    Empty/missing pattern matches any cwd (for time-only or idle-only rules)."""
    if not pattern or pattern == "*":
        return True
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatch(cwd, pattern)
    pat = pattern.rstrip("/")
    return cwd == pat or cwd.startswith(pat + "/")

def _parse_hhmm(s):
    """Parse 'HH:MM' → minutes since midnight; None on bad input."""
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None

def _in_time_window(now, window):
    """`window` is 'HH:MM-HH:MM'. End-before-start means overnight (e.g.,
    21:00-09:00 spans midnight). Match precision is the minute, not the second
    — avoids second-boundary flapping when bursts straddle the rule edge."""
    try:
        start_s, end_s = window.split("-")
    except ValueError:
        return False
    start = _parse_hhmm(start_s.strip())
    end = _parse_hhmm(end_s.strip())
    if start is None or end is None:
        return False
    cur = now.hour * 60 + now.minute
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end

def _idle_match(idle_after_s):
    """Return True if last hook event was longer than `idle_after_s` ago,
    using the heartbeat file already written on every event."""
    try:
        hb = (STATE / "heartbeat").read_text().strip()
        return (time.time() - float(hb)) >= float(idle_after_s)
    except Exception:
        # No heartbeat file yet — consider the system idle (covers fresh installs).
        return True

def _rule_match(rule, cwd, now_dt):
    """All conditions on a rule are AND-combined. Empty rule → always matches."""
    pat = rule.get("pattern", "")
    if pat and not cwd_rule_match(cwd, pat):
        return False
    if "time" in rule and not _in_time_window(now_dt, rule["time"]):
        return False
    if "idle_after_s" in rule and not _idle_match(rule["idle_after_s"]):
        return False
    return True

def resolve_preset(session_id, cwd):
    sessions = load_sessions().get("active", {})
    sess = sessions.get(session_id, {})
    if sess.get("preset_pinned"):
        return sess["preset_pinned"], "pin"
    import datetime
    now_dt = datetime.datetime.now()
    for r in load_rules().get("rules", []):
        if _rule_match(r, cwd or "", now_dt):
            tag = r.get("pattern") or "*"
            extras = []
            if "time" in r: extras.append(f"time={r['time']}")
            if "idle_after_s" in r: extras.append(f"idle={r['idle_after_s']}s")
            label = ",".join([tag] + extras) if extras else tag
            return r.get("preset"), f"rule:{label}"
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
# Pitch-class scales for per-session overrides. Each entry is (pcs, roots).
# pcs = which pitch classes (mod 12) belong to this scale.
# roots = preferred phrase landings (typically tonic + dominant).
# All A-rooted so switching mid-session stays continuous with the shipping presets.
SCALES = {
    "A_major":         ([9, 11, 1, 2, 4, 6, 8],          [9, 4]),    # A B C# D E F# G#
    "A_pent":          ([9, 11, 1, 4, 6],                [9, 4]),    # A B C# E F#
    "A_lydian":        ([9, 11, 1, 3, 4, 6, 8],          [9, 4]),    # A B C# D# E F# G#
    "A_lydian_pent":   ([9, 11, 1, 3, 6],                [9, 6]),    # A B C# D# F#
    "A_dorian":        ([9, 11, 0, 2, 4, 6, 7],          [9, 4]),    # A B C D E F# G
    "A_aeolian":       ([9, 11, 0, 2, 4, 5, 7],          [9, 4]),    # A B C D E F G
    "A_in_sen":        ([9, 10, 0, 4, 5],                [9, 4]),    # A Bb C E F
    "A_phrygian":      ([9, 10, 0, 2, 4, 5, 7],          [9, 4]),    # A Bb C D E F G
    "A_yo":            ([9, 11, 2, 4, 7],                [9, 4]),    # A B D E G — Japanese major-pent
    "A_hijaz":         ([9, 10, 1, 2, 4, 5, 7],          [9, 4]),    # A Bb C# D E F G — flamenco
}


def resolve_scale(session_id):
    """Returns (pcs, roots) or (None, None). Resolution: session pin > config > none.
    Preset's scale_pitches/phrase_roots are used as default — we only return non-None
    when an explicit override is set."""
    if session_id:
        sess = load_sessions().get("active", {}).get(session_id, {})
        name = sess.get("scale_override")
        if name and name in SCALES:
            return SCALES[name] + (name,)
    name = read_config().get("scale_override")
    if name and name in SCALES:
        return SCALES[name] + (name,)
    return None, None, None


def expand_pcs_to_midis(pcs, low=57, high=88):
    """Expand pitch classes to MIDI integers across a range. Used when applying
    a scale override to tonal-overlay voices that need scale_pitches as midis."""
    return [m for m in range(low, high + 1) if m % 12 in pcs]


_INTERVAL_WEIGHTS = {
    0: 0.00,   # exact repeat — never
    1: 0.20,   # minor 2nd — awkward in major-family scales
    2: 1.50,   # major 2nd — favored (scale step)
    3: 1.40,   # minor 3rd — favored
    4: 1.20,   # major 3rd
    5: 1.00,   # perfect 4th
    6: 0.00,   # tritone — never
    7: 1.00,   # perfect 5th — meaningful leap, not default step (was 1.40, lowered after review)
    8: 0.40,   # minor 6th — uncommon in pentatonic
    9: 1.10,   # major 6th
    10: 0.30,  # minor 7th
    11: 0.20,  # major 7th
    12: 0.80,  # octave — fine
}

def _normalize_weights(raw):
    """JSON dict has str keys; convert to int-keyed for interval lookup."""
    if not raw: return None
    try:
        return {int(k): float(v) for k, v in raw.items()}
    except (ValueError, TypeError):
        return None

def _interval_weight(prev_midi, next_midi, table=None):
    table = table or _INTERVAL_WEIGHTS
    interval = abs(next_midi - prev_midi)
    if interval == 0:
        return 0.0
    if interval > 12:
        base = table.get(interval % 12, 0.5)
        oct_jumps = interval // 12
        return base * (0.7 ** (oct_jumps - 1))   # bigger leap, smaller weight
    return table.get(interval, 0.5)

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

def _nearest_pitched(pitched, target_midi):
    """pitched = [(midi, path), ...]; return (path, semitone_shift) for the
    sample whose midi is closest to target. Shift is target-sample so playback
    rate = 2**(shift/12) lands the chosen sample on the requested pitch."""
    best = min(pitched, key=lambda mp: abs(mp[0] - target_midi))
    return best[1], target_midi - best[0]


def _markov_pitch(candidates, state, weight_table, roots):
    """Pick a target MIDI from `candidates` using the same Markov weighting
    + phrase-landing logic as the main picker. Returns (chosen_midi, updated_state).
    Used by the pitched mode AND by the tonal-overlay mode for unpitched voices
    (wood/bird/mokugyo) that get rate-shifted onto scale pitches."""
    last = state.get("last_pitches", [])
    phrase = state.get("phrase_count", 0)
    weights = []
    for midi in candidates:
        w = 1.0
        for i, recent in enumerate(reversed(last[-3:])):
            if midi == recent:
                w *= (0.05, 0.30, 0.55)[min(i, 2)]
        if last:
            w *= _interval_weight(last[-1], midi, weight_table)
        if phrase >= 6:
            pc = midi % 12
            if len(roots) > 0 and pc == roots[0]: w *= 2.5
            elif len(roots) > 1 and pc == roots[1]: w *= 2.0
            elif len(roots) > 2 and pc == roots[2]: w *= 1.6
        weights.append(w)
    total = sum(weights)
    if total <= 0:
        idx = random.randrange(len(candidates))
    else:
        r = random.random() * total
        acc = 0.0
        idx = len(candidates) - 1
        for i, w in enumerate(weights):
            acc += w
            if acc >= r:
                idx = i; break
    chosen = int(candidates[idx])
    new_last = (last + [chosen])[-4:]
    landed = (phrase >= 6) and (chosen % 12 in roots)
    new_phrase = 0 if landed else phrase + 1
    return chosen, {"last_pitches": new_last, "phrase_count": new_phrase}


def melodic_pick(preset_name, voice, voice_dir, song_name=None,
                  interval_weights=None, phrase_roots=None,
                  scale_pitches=None, tonal_anchor=None, tonal_range=12,
                  scale_override_pcs=None, scale_override_roots=None):
    """Pick the next sample for this voice. Returns (sample_path, shift_semitones).
    shift_semitones is non-zero only in song mode for chromatic transposition.

    Three modes:
      - **song mode** (`song_name` is set): pulls the next note from that
        song's pointer; nearest sample is chosen and the residual is corrected
        via afplay -r so playback lands on the requested pitch.
      - **Markov mode** (default): stepwise motion, occasional leaps, phrase
        landings, no immediate repeats.
      - **bag mode**: voices whose samples don't carry midi in filenames just
        get a shuffled bag (cluster, sparkle, bloom, swell, breath, wood, etc.).

    `interval_weights`: optional per-preset semitone→weight dict (already
        normalized to int keys). Falls back to the global `_INTERVAL_WEIGHTS`.
    `phrase_roots`: optional list of pitch classes that the picker pulls
        toward when a phrase has run ~7 notes. Defaults to [9, 4] (A, E).
    """
    weight_table = interval_weights or _INTERVAL_WEIGHTS
    # Phrase roots: scale override wins over preset default.
    roots = scale_override_roots or phrase_roots or [9, 4]
    # Scale-pitches for tonal-overlay voices: scale override expands to a midi
    # list within the preset's existing scale range; otherwise use preset's
    # scale_pitches as-is.
    if scale_override_pcs is not None:
        if scale_pitches:
            low, high = min(scale_pitches), max(scale_pitches)
        else:
            low, high = 57, 88
        scale_pitches = expand_pcs_to_midis(scale_override_pcs, low, high)
    samples = list_samples(preset_name, voice_dir)
    if not samples: return None, 0

    pitched = []
    for s in samples:
        m = _parse_midi(s.name)
        if m is not None: pitched.append((m, s))

    # Scale override: filter pitched bag to only midis whose pitch class is in
    # the override scale. Falls through to unfiltered if no in-scale samples
    # exist (rather than going silent).
    if pitched and scale_override_pcs is not None:
        in_scale = [(m, p) for m, p in pitched if m % 12 in scale_override_pcs]
        if in_scale:
            pitched = in_scale

    # Song-mode override: a MIDI file is driving the melody. Each voice trigger
    # advances that song's pointer by one note. Voices share the song.
    if pitched and song_name and song_mod.has_song(song_name):
        target = song_mod.next_note(song_name)
        if target is not None:
            return _nearest_pitched(pitched, int(target))

    state_file = _melody_state_file(preset_name, voice)
    state = _load_state(state_file)

    # Tonal-overlay mode for unpitched voices: a noise-burst voice (wood,
    # bird, mokugyo) opts in by setting `tonal_anchor_midi` in preset.json
    # AND the preset declares `scale_pitches`. We bag-pick a sample, Markov-
    # pick a target pitch from scale_pitches, and shift via afplay -r so the
    # noise burst rings at that scale degree. Cheap way to make every sound
    # in the system land on the key without re-rendering.
    if not pitched and tonal_anchor is not None and scale_pitches:
        # Clamp candidates to within tonal_range semitones of anchor so
        # noise-burst voices don't get shifted by ±24 (which would warp the
        # rendered duration and timbre too aggressively).
        anchor = int(tonal_anchor)
        rng = int(tonal_range) if tonal_range else 12
        candidates = [p for p in scale_pitches if abs(int(p) - anchor) <= rng]
        if not candidates:
            candidates = list(scale_pitches)  # safety: at least pick something
        bag = state.get("bag", [])
        if not bag or any(b >= len(samples) for b in bag):
            bag = list(range(len(samples)))
            random.shuffle(bag)
        sample_idx = bag[0]
        target, mstate = _markov_pitch(candidates, state, weight_table, roots)
        new_state = {"bag": bag[1:], **mstate}
        _save_state(state_file, new_state)
        shift = target - anchor
        return samples[sample_idx], shift

    # Unpitched voice without tonal-overlay (cluster, sparkle, bloom, swell, breath etc.)
    if not pitched:
        bag = state.get("bag", [])
        if not bag or any(b >= len(samples) for b in bag):
            bag = list(range(len(samples)))
            random.shuffle(bag)
        chosen = bag[0]
        _save_state(state_file, {"bag": bag[1:]})
        return samples[chosen], 0

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
            w *= _interval_weight(last[-1], midi, weight_table)
        # Phrase landing: every ~7 notes, gravity pulls to the preset's roots.
        # Default A and E; per-preset roots override (e.g., E Phrygian → [4, 11]).
        if phrase >= 6:
            pc = midi % 12
            if len(roots) > 0 and pc == roots[0]: w *= 2.5
            elif len(roots) > 1 and pc == roots[1]: w *= 2.0
            elif len(roots) > 2 and pc == roots[2]: w *= 1.6
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
    landed = (phrase >= 6) and (midi % 12 in roots)
    new_phrase = 0 if landed else phrase + 1

    _save_state(state_file, {
        "last_pitches": new_last,
        "bag": new_bag,
        "phrase_count": new_phrase,
    })
    return sample, 0

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

def play(sample_path, gain_linear, shift_semitones=0, delay_s=0.0,
          rate_jitter=False, echo=None):
    """Launch a detached afplay. `shift_semitones` triggers afplay's -r rate
    so song-mode notes land on the requested pitch even when the nearest
    sampled pitch is off. `delay_s` (used by quantization) defers playback
    via a `sh -c sleep ...; afplay ...` trampoline so the hook returns
    immediately and the play lands on the next grid boundary.
    `rate_jitter` adds ±0.4% (≈±7 cents) of pitch wobble — below conscious
    pitch perception, but the ear reads it as 'this is alive' rather than
    'mechanical recall'. Opt-in per voice via preset.json `rate_jitter: true`.
    NEVER enable on bell/bowl/pad/bloom/sparkle/drone — sustained voices accumulate
    audible drift, and chord-based voices beat against each other.

    `echo` is an optional dict {ms, feedback, count} that spawns N additional
    afplay copies at decreasing gain spaced by `ms` — gives a feedback-delay
    feel at trigger time WITHOUT re-rendering. Per-event-mapping, so different
    alert types (Stop, SubagentStop, Notification) can have different echoes."""
    if sample_path is None: return
    v = max(0.0, min(1.0, gain_linear))
    cmd_base = ["/usr/bin/afplay", "-v"]
    base_rate = 2 ** (shift_semitones / 12.0) if shift_semitones else 1.0
    if rate_jitter:
        base_rate *= 1.0 + random.uniform(-0.004, 0.004)
    rate = max(0.25, min(4.0, base_rate)) if (shift_semitones or rate_jitter) else None

    rec = _rec_window()           # capture this play() into the recording, if one is open
    t0 = time.time()

    def _spawn(gain_v, extra_delay):
        cmd = list(cmd_base) + [f"{gain_v:.3f}"]
        if rate is not None:
            cmd += ["-r", f"{rate:.5f}"]
        cmd.append(str(sample_path))
        try:
            total_delay = delay_s + extra_delay
            if total_delay > 0.005:
                quoted = " ".join(shlex.quote(c) for c in cmd)
                full = ["/bin/sh", "-c", f"sleep {total_delay:.4f}; exec {quoted}"]
            else:
                full = cmd
            subprocess.Popen(
                full,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
        except Exception as e:
            log(f"afplay launch failed: {e}")
        if rec is not None:
            t_play = (t0 - rec[0]) + delay_s + extra_delay
            if 0.0 <= t_play <= rec[1]:
                _rec_log(sample_path, gain_v, rate, t_play)

    _spawn(v, 0.0)
    if echo and isinstance(echo, dict):
        ms = max(40, min(2000, int(echo.get("ms", 320))))
        fb = max(0.0, min(0.85, float(echo.get("feedback", 0.30))))
        count = max(0, min(8, int(echo.get("count", 3))))
        for i in range(1, count + 1):
            echo_gain = v * (fb ** i)
            if echo_gain < 0.005:
                break
            _spawn(echo_gain, (ms / 1000.0) * i)

def trigger(preset_name, preset, voice, song_name=None, quant_override=None,
             scale_override_pcs=None, scale_override_roots=None,
             event_effect=None):
    if voice is None: return
    voices = preset.get("voices", {})
    cfg = voices.get(voice)
    if cfg is None:
        log(f"unknown voice '{voice}' in preset {preset_name}")
        return
    ok, pressure_db = check_mioi(preset_name, voice, cfg.get("mioi", 0.5))
    if not ok: return
    iw = _normalize_weights(preset.get("interval_weights"))
    pr = preset.get("phrase_roots")
    # Tonal-overlay opt-in: voice has tonal_anchor_midi and preset has scale_pitches.
    # Lets noise-burst voices (wood/bird/mokugyo) land on key.
    scale = preset.get("scale_pitches") if cfg.get("tonal_anchor_midi") is not None else None
    anchor = cfg.get("tonal_anchor_midi")
    trange = cfg.get("tonal_range", 12)
    sample, shift = melodic_pick(
        preset_name, voice, cfg.get("dir", voice),
        song_name=song_name,
        interval_weights=iw,
        phrase_roots=pr,
        scale_pitches=scale,
        tonal_anchor=anchor,
        tonal_range=trange,
        scale_override_pcs=scale_override_pcs,
        scale_override_roots=scale_override_roots,
    )
    if sample is None: return
    cfg_master = read_config().get("master_gain")
    master = float(cfg_master if cfg_master is not None
                   else preset.get("master_gain", 0.5))
    base_lin = float(cfg.get("gain", 0.5)) * master
    pressure_lin = 10 ** (pressure_db / 20.0)
    delay = song_mod.quant_delay_for(quant_override) if quant_override is not None else song_mod.quant_delay()
    rate_jitter = bool(cfg.get("rate_jitter", False))
    # Per-event-mapping echo (delay): events block in preset.json may carry
    # an `effect.delay` dict like {ms: 320, feedback: 0.30, count: 3}.
    # Applied at trigger time so the same voice can have different feels for
    # different alerts (e.g. Stop with long echo, PreToolUse with none).
    echo = None
    if event_effect and isinstance(event_effect, dict):
        d = event_effect.get("delay")
        if isinstance(d, dict):
            echo = d
    # Per-VOICE delay fallback: a voice may carry its own `delay` dict
    # ({ms, feedback, count}) in preset.json, applied live at playback (no
    # re-render). An event-mapping echo, if present, takes precedence so a
    # voice can still feel different per alert.
    if echo is None:
        vd = cfg.get("delay")
        if isinstance(vd, dict):
            echo = vd
    play(sample, base_lin * pressure_lin, shift_semitones=shift, delay_s=delay,
         rate_jitter=rate_jitter, echo=echo)

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

    # Global mute — short-circuit before any work. Toggled via `claudio off/on`.
    if read_config().get("muted"):
        return

    preset_name, source = resolve_preset(session, cwd)
    preset = load_preset(preset_name)
    update_session_record(session, cwd, event, preset_name, source)

    if preset is None:
        log(f"no preset loaded ({preset_name}); silent")
        return

    # Resolve song: session pin > preset's "song" > global.
    sess_rec = load_sessions().get("active", {}).get(session, {})
    song_name = (sess_rec.get("song_pinned")
                 or preset.get("song")
                 or song_mod.global_song())
    if song_name and not song_mod.has_song(song_name):
        song_name = None

    log(f"[{preset_name}/{source}] event={event} tool={tool} session={session[:8]} song={song_name or '-'}")
    try:
        (STATE / "heartbeat").write_text(str(time.time()))
    except Exception:
        pass
    # Per-event activity marker — lets the TUI show a growing dot when each
    # event TYPE fires (mirrors the per-voice last-<voice>.txt). Written on
    # every handled event, even if it maps to silence, so unmapped events
    # still light up and can be mapped.
    try:
        _ed = STATE / preset_name
        _ed.mkdir(parents=True, exist_ok=True)
        (_ed / f"evt-{event}.txt").write_text(str(time.time()))
        # Frequency counter: append one byte so the file SIZE == fire count.
        # O_APPEND of a single byte is atomic, so concurrent hook processes
        # never clobber each other (a read-modify-write counts.json would).
        with (_ed / f"cnt-{event}.bin").open("ab") as _cf:
            _cf.write(b"\x01")
    except Exception:
        pass

    # Per-preset quant override: preset.json may carry a "quant" block that
    # overrides the global config.json quant settings on a per-preset basis,
    # so e.g. rainfall can be slowly-quantized while meadow runs ungated.
    preset_quant = preset.get("quant")

    # Per-session scale override: lets the user pin a different scale (e.g.
    # A_lydian when the active preset is meadow) for one session without
    # forking the whole preset. Filters pitched bag, overrides phrase_roots
    # and scale_pitches. Resolution: session pin > config-wide > none.
    override_pcs, override_roots, override_name = resolve_scale(session)

    # Per-event effect block: preset.json events.<name> may carry an
    # `effect: {delay: {ms, feedback, count}}` block, applied at trigger time.
    spec = preset.get("events", {}).get(event, {}) if isinstance(preset.get("events"), dict) else {}
    event_effect = spec.get("effect") if isinstance(spec, dict) else None

    voice = resolve_voice(preset, event, payload)
    trigger(preset_name, preset, voice, song_name=song_name,
            quant_override=preset_quant,
            scale_override_pcs=override_pcs,
            scale_override_roots=override_roots,
            event_effect=event_effect)

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
