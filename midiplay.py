#!/usr/bin/env python3
"""
Claudio Symphony — MIDI jukebox / performer (the easter egg).

The normal "song" feature (song.py) feeds a MIDI melody one note at a time as
REAL hook events fire. This is the opposite: it *performs* a whole MIDI file in
real time, right now, using the active preset's voices — turning the whole
event palette into an instrument.

The fun part is the correlation: each MIDI channel is mapped to an event TYPE
(PostToolUse, Stop, PreToolUse, …). Whichever voice that event normally plays is
the voice that channel performs with. So you literally hear — and *see* — the
"PostToolUse voice" carry the melody while the "SessionStart voice" lays down a
bass line. As each note fires we touch the same activity markers event.py
touches, so the Events tab and the constellation bloom in sync with the song.

Because every note is fired through event.play(), an open recording window
captures the performance automatically — record a MIDI played through your
favourite preset and share it.

Pure stdlib. Reuses event.py for sample-selection + playback + recording, and
song.py for the SMF parser. Runs as a foreground or detached process; webui.py
and `claudio play` drive it.
"""
import sys, os, json, time, signal, random, threading
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import event as ev          # noqa: E402  sample selection + play() + recording capture
import song as song_mod     # noqa: E402  SMF parser + song library
import timeline as tl       # noqa: E402  session timeline reader (replay source)

STATE = HERE / "state"
PLAY_DIR = STATE / "midiplay"
ACTIVE = PLAY_DIR / "active.json"          # presence = a performance is running
PROGRESS = PLAY_DIR / "progress.json"      # frequently-updated playhead

# Canonical "musical prominence" order. The busiest / lead MIDI channel takes
# the first event with a mapped voice, the next channel the second, and so on.
# Lead-ish melodic events first, ambient / rare events last.
PERFORM_EVENT_ORDER = [
    "PostToolUse", "Stop", "PreToolUse", "UserPromptSubmit",
    "Notification", "SubagentStop", "SessionStart", "SessionEnd", "PreCompact",
]

# Fork-bomb guard: never let a pathological/dense MIDI spawn more than this many
# afplay processes inside a short window. Chords are fine; a 64-voice cluster on
# one tick is not.
_BURST_WINDOW_S = 0.12
_BURST_MAX = 18


# ---------- atomic json ----------

def _save(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d))
    tmp.rename(p)


def _load(p, default=None):
    try:
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return default


# ---------- mapping: MIDI channels → event types → preset voices ----------

def event_voice(preset, event_name):
    """The voice the active preset plays for an event's `default`, or None."""
    spec = (preset.get("events", {}) or {}).get(event_name) or {}
    return spec.get("default")


def mappable_events(preset):
    """Events that actually resolve to a voice, in performance-priority order,
    then any extra events the preset defines that we didn't list."""
    out = [e for e in PERFORM_EVENT_ORDER if event_voice(preset, e)]
    for e in (preset.get("events", {}) or {}):
        if e not in out and event_voice(preset, e):
            out.append(e)
    return out


def auto_map(song, preset):
    """Assign each MIDI channel an event type. The detected lead channel goes
    first (most prominent event), remaining channels follow by note-count.
    Returns {channel: event_name}. Channels cycle if there are more channels
    than mapped events."""
    summary = song_mod.channel_summary(song)          # [(ch, count, median), …] count desc
    chans = [ch for ch, _, _ in summary]
    lead = song_mod.lead_channel(song)
    if lead is not None and lead in chans:
        chans.remove(lead)
        chans.insert(0, lead)
    events = mappable_events(preset)
    if not events or not chans:
        return {}
    return {ch: events[i % len(events)] for i, ch in enumerate(chans)}


def plan(song_name, preset_name, mapping=None):
    """Describe how `song_name` would be performed by `preset_name`: per-channel
    note counts / register and the event+voice each channel maps to. Used by the
    web UI to show (and let the user edit) the track↔event correlation before
    pressing play."""
    song = song_mod.load_song(song_name)
    preset = ev.load_preset(preset_name)
    if not song or not preset:
        return None
    summary = song_mod.channel_summary(song)
    amap = auto_map(song, preset)
    mapping = mapping or {}
    lead = song_mod.lead_channel(song)
    rows = []
    for ch, count, median in summary:
        evname = mapping.get(str(ch), mapping.get(ch, amap.get(ch)))
        if evname in ("__none__", ""):
            evname = None
        rows.append({
            "channel": ch,
            "notes": count,
            "median_midi": median,
            "register": _register_label(median),
            "is_lead": ch == lead,
            "event": evname,
            "voice": event_voice(preset, evname) if evname else None,
        })
    return {
        "song": song_name,
        "preset": preset_name,
        "bpm": song.get("bpm", 120.0),
        "total_notes": len(song.get("notes") or []),
        "events": mappable_events(preset),
        "channels": rows,
        "duration": _song_duration(song),
    }


def _register_label(midi):
    if midi is None:
        return ""
    if midi < 48:
        return "bass"
    if midi < 60:
        return "low"
    if midi < 72:
        return "mid"
    return "high"


def _song_duration(song, bpm=None):
    notes = song.get("notes") or []
    if not notes:
        return 0.0
    bpm = float(bpm or song.get("bpm") or 120.0)
    last_beat = max(n["beat"] for n in notes)
    return last_beat * (60.0 / bpm)


# ---------- firing one note ----------

def _fire(preset_name, preset, master, event_name, voice, target_midi, velocity):
    """Select a sample for `voice` landing on `target_midi`, play it through
    event.play() (which also feeds the recording timeline), and touch the
    activity markers so the UI blooms. Velocity scales gain subtly."""
    voices = preset.get("voices", {})
    cfg = voices.get(voice)
    if cfg is None:
        return
    samples = ev.list_samples(preset_name, cfg.get("dir", voice))
    if not samples:
        return

    pitched = []
    for s in samples:
        m = ev._parse_midi(s.name)
        if m is not None:
            pitched.append((m, s))

    if pitched:
        path, shift = ev._nearest_pitched(pitched, int(target_midi))
    else:
        # Unpitched voice (cluster / bird / wood): bag-pick, optionally shift a
        # tonal-anchored noise burst toward the requested pitch (clamped so we
        # don't warp the timbre too far), else play it dry.
        path = random.choice(samples)
        anchor = cfg.get("tonal_anchor_midi")
        if anchor is not None:
            rng = int(cfg.get("tonal_range", 12) or 12)
            shift = max(-rng, min(rng, int(target_midi) - int(anchor)))
        else:
            shift = 0

    # velocity (1..127) → 0.55..1.0 gain scale — present but gentle
    vscale = 0.55 + 0.45 * (max(1, min(127, int(velocity))) / 127.0)
    gain = float(cfg.get("gain", 0.5)) * master * vscale
    echo = cfg.get("delay") if isinstance(cfg.get("delay"), dict) else None
    ev.play(path, gain, shift_semitones=shift,
            rate_jitter=bool(cfg.get("rate_jitter", False)), echo=echo)

    # Light up the same markers a real hook touches: voice orb (last-<voice>)
    # and event dot (evt-<event>) bloom in sync. We deliberately do NOT bump
    # cnt-<event>.bin — jukebox plays shouldn't pollute the "most fired" stats.
    try:
        sd = ev.preset_state_dir(preset_name)
        now = str(time.time())
        (sd / f"last-{voice}.txt").write_text(now)
        (sd / f"evt-{event_name}.txt").write_text(now)
    except Exception:
        pass


# ---------- the performance loop ----------

def _build_schedule(song, mapping, bpm):
    """Flatten the song into [(time_s, channel, event, midi, velocity), …] for
    the mapped channels only, sorted by time."""
    beat_s = 60.0 / float(bpm)
    sched = []
    for n in song.get("notes") or []:
        ch = n["channel"]
        evname = mapping.get(ch)
        if not evname:
            continue
        sched.append((n["beat"] * beat_s, ch, evname, n["midi"], n.get("velocity", 96)))
    sched.sort(key=lambda x: x[0])
    return sched


def perform(song_name, preset_name=None, bpm=None, tempo=1.0, loop=False,
            mapping=None, on_tick=None, _stop=None):
    """Play `song_name` through `preset_name` in real time. Blocks until the
    song ends (or `loop` forever) or `_stop` is set. `mapping` overrides the
    auto channel→event map ({channel:int → event:str}). `tempo` multiplies the
    file's bpm (>1 faster). `on_tick(idx, total, elapsed)` is called as notes
    fire."""
    preset_name = preset_name or ev.active_preset_name()
    song = song_mod.load_song(song_name)
    preset = ev.load_preset(preset_name)
    if not song or not preset:
        return False
    eff_bpm = float(bpm or song.get("bpm") or 120.0) * float(tempo or 1.0)
    full_map = auto_map(song, preset)
    if mapping:
        for k, v in mapping.items():
            try:
                ch = int(k)
            except (ValueError, TypeError):
                continue
            if v in (None, "", "__none__"):     # explicit silence wins over auto-map
                full_map.pop(ch, None)
            else:
                full_map[ch] = v
    sched = _build_schedule(song, full_map, eff_bpm)
    if not sched:
        return False

    master = float(ev.read_config().get("master_gain",
                   preset.get("master_gain", 0.5)))
    stop = _stop if _stop is not None else {"stop": False}
    recent = deque()       # afplay onset times, for the burst guard
    total = len(sched)

    while not stop.get("stop"):
        t0 = time.time()
        for idx, (t, ch, evname, midi, vel) in enumerate(sched):
            if stop.get("stop"):
                break
            target = t0 + t
            while True:
                dt = target - time.time()
                if dt <= 0 or stop.get("stop"):
                    break
                time.sleep(min(dt, 0.05))
            if stop.get("stop"):
                break
            now = time.time()
            while recent and now - recent[0] > _BURST_WINDOW_S:
                recent.popleft()
            if len(recent) >= _BURST_MAX:
                continue                      # drop a note rather than fork-bomb
            recent.append(now)
            # voice may have changed if the preset was edited mid-play; re-read
            voice = event_voice(preset, evname)
            if voice:
                _fire(preset_name, preset, master, evname, voice, midi, vel)
            if on_tick:
                on_tick(idx + 1, total, now - t0)
        if not loop or stop.get("stop"):
            break
    return True


# ---------- lifecycle (foreground process owns active.json) ----------

def status():
    meta = _load(ACTIVE)
    out = {"active": bool(meta)}
    if meta:
        out.update(meta)
        prog = _load(PROGRESS) or {}
        out["progress"] = prog
    return out


def stop_running():
    """Signal a running performer to halt (or clean up a dead one)."""
    meta = _load(ACTIVE)
    if not meta:
        return False
    pid = meta.get("pid")
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
            return True
        except ProcessLookupError:
            pass
        except Exception:
            pass
    try:
        ACTIVE.unlink()
    except Exception:
        pass
    return True


def run(song_name, preset_name=None, bpm=None, tempo=1.0, loop=False, mapping=None):
    """Foreground entry: claim active.json, perform, then clean up. Honors
    SIGTERM / SIGINT (web Stop button, Ctrl-C)."""
    preset_name = preset_name or ev.active_preset_name()
    if not song_mod.has_song(song_name):
        print(f"unknown song '{song_name}'")
        return False
    p = plan(song_name, preset_name, mapping)
    if not p or not p.get("channels"):
        print("nothing to play (no mappable channels / voices)")
        return False

    eff_bpm = float(bpm or p["bpm"]) * float(tempo or 1.0)
    duration = _song_duration(song_mod.load_song(song_name), eff_bpm)
    PLAY_DIR.mkdir(parents=True, exist_ok=True)
    _save(ACTIVE, {
        "pid": os.getpid(), "song": song_name, "preset": preset_name,
        "bpm": round(eff_bpm, 2), "tempo": float(tempo or 1.0), "loop": bool(loop),
        "total_notes": p["total_notes"], "duration": round(duration, 2),
        "channels": p["channels"], "start": time.time(),
    })
    _save(PROGRESS, {"idx": 0, "total": p["total_notes"], "elapsed": 0.0,
                     "last_event": None, "last_channel": None})

    stop = {"stop": False}
    def _sig(_s, _f): stop["stop"] = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    last_write = [0.0]
    def _tick(idx, total, elapsed):
        # throttle progress writes to ~12/s
        if elapsed - last_write[0] >= 0.08 or idx >= total:
            last_write[0] = elapsed
            _save(PROGRESS, {"idx": idx, "total": total, "elapsed": round(elapsed, 2),
                             "playing": True})

    try:
        perform(song_name, preset_name, bpm=bpm, tempo=tempo, loop=loop,
                mapping=mapping, on_tick=_tick, _stop=stop)
    finally:
        try: ACTIVE.unlink()
        except Exception: pass
        try: _save(PROGRESS, {"playing": False})
        except Exception: pass
    return True


# ---------- session replay (the "mini-track" player) ----------
#
# Replays a captured session score (timeline.py) through any preset. Unlike the
# jukebox (which drives pitches from a MIDI), this re-runs your actual workflow:
# each captured hook event re-resolves to the chosen preset's voice and the
# melodic picker re-rolls a fitting note — so you can audition the SAME session
# through different presets and tune it until it sounds great, then render a WAV.

def _replay_fire(preset_name, preset, master, mioi_last, e, tool, fail):
    """Re-resolve one captured event to a voice in `preset` and play it. Uses an
    in-memory MIOI (so replay is deterministic and never touches live rate-limit
    state), the normal melodic picker (re-rolls pitch to fit the preset), and the
    same bloom markers the jukebox uses (evt-/last-, never cnt-)."""
    payload = {"hook_event_name": e, "tool_name": tool or ""}
    if fail:
        payload["tool_error"] = True
    voice = ev.resolve_voice(preset, e, payload)
    if not voice:
        return
    cfg = preset.get("voices", {}).get(voice)
    if not cfg:
        return
    now = time.time()
    if now - mioi_last.get(voice, 0.0) < float(cfg.get("mioi", 0.5)):
        return                      # in-memory rate-limit, shaped by THIS preset
    mioi_last[voice] = now
    iw = ev._normalize_weights(preset.get("interval_weights"))
    pr = preset.get("phrase_roots")
    scale = preset.get("scale_pitches") if cfg.get("tonal_anchor_midi") is not None else None
    sample, shift = ev.melodic_pick(
        preset_name, voice, cfg.get("dir", voice),
        interval_weights=iw, phrase_roots=pr, scale_pitches=scale,
        tonal_anchor=cfg.get("tonal_anchor_midi"), tonal_range=cfg.get("tonal_range", 12))
    if sample is None:
        return
    gain = float(cfg.get("gain", 0.5)) * master
    echo = cfg.get("delay") if isinstance(cfg.get("delay"), dict) else None
    ev.play(sample, gain, shift_semitones=shift,
            rate_jitter=bool(cfg.get("rate_jitter", False)), echo=echo)
    try:
        sd = ev.preset_state_dir(preset_name)
        (sd / f"last-{voice}.txt").write_text(str(now))
        (sd / f"evt-{e}.txt").write_text(str(now))
    except Exception:
        pass


def perform_score(events, preset_name, preset, tempo=1.0, loop=False,
                  max_gap=2.5, on_tick=None, _stop=None):
    """Replay a list of captured events in real time through `preset`."""
    sched = tl.replay_schedule(events, tempo=tempo, max_gap=max_gap)
    if not sched:
        return False
    master = float(ev.read_config().get("master_gain", preset.get("master_gain", 0.5)))
    stop = _stop if _stop is not None else {"stop": False}
    recent = deque()
    total = len(sched)
    while not stop.get("stop"):
        mioi_last = {}
        t0 = time.time()
        for idx, (t, e, tool, fail) in enumerate(sched):
            if stop.get("stop"):
                break
            target = t0 + t
            while True:
                dt = target - time.time()
                if dt <= 0 or stop.get("stop"):
                    break
                time.sleep(min(dt, 0.05))
            if stop.get("stop"):
                break
            now = time.time()
            while recent and now - recent[0] > _BURST_WINDOW_S:
                recent.popleft()
            if len(recent) >= _BURST_MAX:
                continue
            recent.append(now)
            _replay_fire(preset_name, preset, master, mioi_last, e, tool, fail)
            if on_tick:
                on_tick(idx + 1, total, now - t0)
        if not loop or stop.get("stop"):
            break
    return True


def run_score(session_id, preset_name=None, tempo=1.0, loop=False, max_gap=2.5):
    """Foreground entry to replay a captured session. Claims the same active.json
    the jukebox uses (one performance at a time), honors SIGTERM/SIGINT."""
    preset_name = preset_name or ev.active_preset_name()
    score = tl.read_session(session_id)
    if not score and Path(session_id).exists():       # also accept a .score.json path
        score = tl.load_score_file(session_id)
    if not score or not score.get("events"):
        print(f"no timeline for session '{session_id}'")
        return False
    preset = ev.load_preset(preset_name)
    if not preset:
        print(f"no preset '{preset_name}'")
        return False
    events = score["events"]
    duration = tl.replay_duration(events, tempo, max_gap)
    PLAY_DIR.mkdir(parents=True, exist_ok=True)
    _save(ACTIVE, {
        "pid": os.getpid(), "kind": "replay", "session": session_id,
        "preset": preset_name, "tempo": float(tempo or 1.0), "loop": bool(loop),
        "total_notes": len(events), "duration": round(duration, 2), "start": time.time(),
    })
    _save(PROGRESS, {"idx": 0, "total": len(events), "elapsed": 0.0, "playing": True})

    stop = {"stop": False}
    def _sig(_s, _f): stop["stop"] = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    last_write = [0.0]
    def _tick(idx, total, elapsed):
        if elapsed - last_write[0] >= 0.08 or idx >= total:
            last_write[0] = elapsed
            _save(PROGRESS, {"idx": idx, "total": total, "elapsed": round(elapsed, 2), "playing": True})
    try:
        perform_score(events, preset_name, preset, tempo=tempo, loop=loop,
                      max_gap=max_gap, on_tick=_tick, _stop=stop)
    finally:
        try: ACTIVE.unlink()
        except Exception: pass
        try: _save(PROGRESS, {"playing": False})
        except Exception: pass
    return True


# ---------- CLI ----------

def _parse_map_arg(s):
    """'3=PostToolUse,0=SessionStart' → {3:'PostToolUse', 0:'SessionStart'}."""
    out = {}
    for part in (s or "").split(","):
        part = part.strip()
        if "=" in part:
            ch, evname = part.split("=", 1)
            try: out[int(ch.strip())] = evname.strip()
            except ValueError: pass
    return out


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("usage: midiplay.py <song> [--preset NAME] [--bpm N] [--tempo X] "
              "[--loop] [--map ch=Event,...]\n"
              "       midiplay.py stop | status | plan <song> [--preset NAME]")
        return 0
    cmd = argv[0]
    if cmd == "stop":
        stop_running(); return 0
    if cmd == "replay":
        if len(argv) < 2:
            print("usage: midiplay.py replay <session_id> [--preset N] [--tempo X] [--loop] [--max-gap S]")
            return 1
        sid = argv[1]
        preset = None; tempo = 1.0; loop = False; max_gap = 2.5
        i = 2
        while i < len(argv):
            a = argv[i]
            if a == "--preset" and i + 1 < len(argv): preset = argv[i + 1]; i += 2
            elif a == "--tempo" and i + 1 < len(argv): tempo = float(argv[i + 1]); i += 2
            elif a == "--max-gap" and i + 1 < len(argv): max_gap = float(argv[i + 1]); i += 2
            elif a == "--loop": loop = True; i += 1
            else: i += 1
        run_score(sid, preset, tempo=tempo, loop=loop, max_gap=max_gap)
        return 0
    if cmd == "status":
        print(json.dumps(status(), indent=2)); return 0
    if cmd == "plan":
        if len(argv) < 2:
            print("usage: midiplay.py plan <song> [--preset NAME]"); return 1
        preset = None
        if "--preset" in argv:
            preset = argv[argv.index("--preset") + 1]
        print(json.dumps(plan(argv[1], preset or ev.active_preset_name()), indent=2))
        return 0

    song_name = cmd
    preset = bpm = mapping = None
    tempo = 1.0
    loop = False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--preset" and i + 1 < len(argv): preset = argv[i + 1]; i += 2
        elif a == "--bpm" and i + 1 < len(argv): bpm = float(argv[i + 1]); i += 2
        elif a == "--tempo" and i + 1 < len(argv): tempo = float(argv[i + 1]); i += 2
        elif a == "--map" and i + 1 < len(argv): mapping = _parse_map_arg(argv[i + 1]); i += 2
        elif a == "--loop": loop = True; i += 1
        else: i += 1
    run(song_name, preset, bpm=bpm, tempo=tempo, loop=loop, mapping=mapping)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
