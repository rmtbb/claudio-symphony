#!/usr/bin/env python3
"""
Claudio Symphony — control CLI.

Quick on/off
  off                              Silence everything (hooks short-circuit)
  on                               Restore (auto-restarts drone if preset has one)
  toggle                           Flip between on and off

Setup
  install / uninstall              Manage hooks in ~/.claude/settings.json
  status                           Show install + drone + preset state (and ON/OFF)
  start / stop                     Drone daemon (no-op for presets without one)
  regen [preset]                   Re-render samples for a preset

Presets
  preset list                      Available presets
  preset use <name>                Switch global default preset (live)
  preset use default               Switch to the shipped default preset (meadow)
  preset current                   Print active preset
  preset reset [name]              Restore preset.json from preset.default.json
  reset                            Full reset: defaults preset + master gain,
                                   restore every preset, clear songs/quant/pins

Per-session routing
  sessions                         List active sessions (last 4h)
  session pin <id|idx> <preset>    Pin one session to a specific preset
  session unpin <id|idx>           Remove the pin
  here <preset>                    Add a cwd rule for the current dir
  rule list                        Show cwd-pattern → preset rules
  rule add <pattern> <preset>      Add or replace a rule (glob or path-prefix)
  rule rm <pattern>                Remove a rule

Live tuning
  web                              Open the browser control panel (stunning, live)
  tune                             Open the curses TUI
  volume <0..1>                    Master gain
  drone-volume <0..1>              Drone gain
  voice <name> gain <0..1>         Per-voice gain (active preset)
  voice <name> mioi <seconds>      Per-voice rate-limit
  voice <name> reverb <wet> [decay] [bright] | off   Per-voice reverb (regens that voice)
  voice <name> delay <ms> [fb] [count] | off         Per-voice delay/echo (live, no regen)
  voice <name> fx                  Show this voice's reverb + delay
  voice <name> play                Preview that voice once
  map <event>[:<tool>] <voice|-|none>   Change event mapping
  mute <event>[:<tool>]            Set mapping to silent
  unmute <event>[:<tool>] [voice]  Restore mapping (default: first voice)
  test [voice]                     Walk all events of active preset
  demo                             60-second showcase of the active preset
  audition                         Hear every preset, optionally pick one
  status-line                      Print one-line live state (for tmux/etc.)

Adaptive routing (extends `rule add`)
  rule add <pattern> <preset> --time HH:MM-HH:MM      Time-of-day rule
  rule add * <preset> --idle-after <seconds>          Idle-only rule

Scale & effect tuning
  scale list                                          Available scales (A_pent, A_lydian, etc.)
  scale use <name> / scale off                        Global scale override
  session scale <id|idx> <name>                       Pin a scale to one session
  preset reverb <0..2>                                Active preset reverb scale (auto-regen)
  event delay <Event> <ms> [feedback] [count]         Per-event delay echo at trigger
  event delay <Event> off                             Remove that event's echo
  event show                                          Show current event effects

MIDI songs + quantization
  song import <file.mid> [name]    Import a MIDI file (Mario, Sonic, anything)
  song import-dir <folder>         Import every *.mid in a folder
  song list                        List imported songs (* = global default)
  song use <name>                  Set as global default
  song off                         Clear global default → Markov picker
  song current                     Show resolution + positions
  song reset [name]                Restart song's pointer (default: global)
  song channel <name> <lead|all|N> Pick which MIDI channel drives the melody
  song info <name>                 Show channel summary + detected lead
  preset song <preset> <name|off>  Per-preset default song (overrides global)
  session song <id|idx> <name|off> Pin a song to one session (overrides preset)
  quant on / off / status          Toggle master quantization
  tempo <bpm>                      Set master quantization tempo
  grid <subdivision>               Beats per cell (0.25=16th, 0.5=8th, 1=quarter)

Support
  coffee                           Show on-chain tip addresses (alias: tip, donate)
"""
import os, sys, json, time, fnmatch
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import song as song_mod  # noqa: E402
import audio  # noqa: E402  (cross-platform playback + process helpers)

PRESETS = HERE / "presets"
STATE = HERE / "state"
LOGS = HERE / "logs"
SETTINGS = Path.home() / ".claude" / "settings.json"
BACKUPS = Path.home() / ".claude" / "backups"
CONFIG = HERE / "config.json"
SESSIONS_FILE = STATE / "sessions.json"
RULES_FILE = STATE / "rules.json"
EVENT_PATH = str(HERE / "event.py")
DRONE_PATH = str(HERE / "drone.py")
TUNE_PATH = str(HERE / "tune.py")
PID_FILE = STATE / "drone.pid"

MARKER = "__claudio_symphony__"

HOOK_EVENTS = [
    "SessionStart", "SessionEnd", "UserPromptSubmit", "Stop",
    "PreToolUse", "PostToolUse", "SubagentStop",
    "Notification", "PreCompact",
]

# ---------- json helpers ----------

def load_json(p, default):
    try: return json.loads(p.read_text()) if p.exists() else default
    except Exception: return default

def save_json(p, d):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    tmp.rename(p)

DEFAULT_PRESET = "meadow"
DEFAULT_CONFIG = {
    "preset": DEFAULT_PRESET,
    "master_gain": 0.55,
    "drone_gain": 0.0,
    "quant": {"enabled": False, "bpm": 120.0, "grid": 0.5},
}

def load_config(): return load_json(CONFIG, {})
def save_config(d): save_json(CONFIG, d)
def active_preset_name(): return load_config().get("preset", DEFAULT_PRESET)
def list_preset_names():
    if not PRESETS.exists(): return []
    return sorted(d.name for d in PRESETS.iterdir() if (d / "preset.json").exists())
def load_preset(name):
    p = PRESETS / name / "preset.json"
    return load_json(p, None) if p.exists() else None
def save_preset(name, d): save_json(PRESETS / name / "preset.json", d)

# ---------- settings.json hook install ----------

def load_settings():
    if SETTINGS.exists(): return json.loads(SETTINGS.read_text())
    return {}

def save_settings(d):
    BACKUPS.mkdir(exist_ok=True)
    if SETTINGS.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        (BACKUPS / f"settings.json.claudio-{ts}").write_bytes(SETTINGS.read_bytes())
    SETTINGS.write_text(json.dumps(d, indent=2) + "\n")

def hook_block_for():
    return {
        "matcher": "*",
        "hooks": [{
            "type": "command",
            "command": EVENT_PATH,
            "async": True,
            "timeout": 1,
            MARKER: True,
        }],
    }

def cmd_install():
    s = load_settings()
    hooks = s.setdefault("hooks", {})
    added = []
    for ev in HOOK_EVENTS:
        existing = hooks.get(ev, [])
        already = any(any(h.get(MARKER) for h in b.get("hooks", [])) for b in existing)
        if already: continue
        existing.append(hook_block_for())
        hooks[ev] = existing
        added.append(ev)
    save_settings(s)
    print(f"installed hooks for: {', '.join(added) if added else '(none — already present)'}")
    print(f"settings written: {SETTINGS}")
    print()
    print("Note: only NEW Claude Code sessions pick up hook changes (settings.json).")
    print("Preset/voice/mapping/session changes ARE live — no Claude restart needed.")

def cmd_uninstall():
    s = load_settings()
    hooks = s.get("hooks", {})
    removed = []
    for ev in list(hooks.keys()):
        new_blocks = []
        for block in hooks[ev]:
            new_handlers = [h for h in block.get("hooks", []) if not h.get(MARKER)]
            if new_handlers != block.get("hooks", []):
                if new_handlers:
                    block["hooks"] = new_handlers
                    new_blocks.append(block)
                removed.append(ev)
            else:
                new_blocks.append(block)
        if new_blocks:
            hooks[ev] = new_blocks
        else:
            del hooks[ev]
    if not hooks:
        s.pop("hooks", None)
    save_settings(s)
    print(f"removed claudio hooks from: {', '.join(sorted(set(removed))) if removed else '(none)'}")

# ---------- drone control ----------

def drone_pid():
    try:
        if not PID_FILE.exists(): return None
        pid = int(PID_FILE.read_text().strip())
        return pid if audio.pid_alive(pid) else None
    except Exception:
        return None

def cmd_start():
    pid = drone_pid()
    if pid: print(f"drone already running pid={pid}"); return
    name = active_preset_name()
    preset = load_preset(name)
    if preset is None: print(f"active preset '{name}' not found"); return
    if not preset.get("drone"):
        print(f"preset '{name}' has no continuous drone — nothing to start")
        return
    LOGS.mkdir(exist_ok=True)
    out = LOGS / "drone.out"
    audio.spawn_python(DRONE_PATH, detached=True, log_file=str(out))
    time.sleep(0.4)
    pid = drone_pid()
    print(f"drone started pid={pid}" if pid else f"launch attempted; check {LOGS / 'drone.log'}")

def cmd_stop():
    pid = drone_pid()
    if not pid: print("drone not running"); return
    try:
        audio.terminate_pid(pid)
        if audio.IS_WIN:
            # Windows has no deliverable SIGTERM handler; ask the loop to stop.
            (audio.STATE / "drone.stop").write_text("1")
        time.sleep(0.3)
        audio.stop_drone()          # silence the in-flight drone player now
        print(f"drone stopped pid={pid}")
    except Exception as e:
        print(f"stop failed: {e}")

# ---------- on/off ----------

def cmd_off():
    cfg = load_config()
    if cfg.get("muted"):
        print("🔇 already off")
        return
    cfg["muted"] = True
    save_config(cfg)
    # stop drone if running
    if drone_pid():
        cmd_stop()
    # kill any in-flight players so existing tails go silent now, not at end
    audio.stop_all()
    print("🔇 OFF — run `claudio on` to restore")

def cmd_on():
    cfg = load_config()
    cfg.pop("muted", None)
    save_config(cfg)
    name = cfg.get("preset", "meadow")
    preset = load_preset(name)
    # auto-start drone if active preset has one
    if preset and preset.get("drone") and not drone_pid():
        cmd_start()
    print(f"🔊 ON — preset: {name}")

def cmd_toggle():
    if load_config().get("muted"):
        cmd_on()
    else:
        cmd_off()

# ---------- status ----------

def cmd_status():
    s = load_settings()
    hooks = s.get("hooks", {})
    events = []
    for ev, blocks in hooks.items():
        for b in blocks:
            for h in b.get("hooks", []):
                if h.get(MARKER):
                    events.append(ev); break
    cfg = load_config()
    name = cfg.get("preset", "meadow")
    preset = load_preset(name)
    pid = drone_pid()
    muted = cfg.get("muted", False)
    print(f"state:           {'🔇 OFF' if muted else '🔊 ON'}")
    print(f"active preset:   {name}{' (✓)' if preset else ' (NOT FOUND)'}")
    if preset:
        print(f"  description:   {preset.get('description','')}")
        print(f"  drone:         {preset.get('drone') or 'none'}")
        print(f"  voices:        {', '.join(preset.get('voices', {}).keys())}")
    print(f"available:       {', '.join(list_preset_names()) or '(none)'}")
    print(f"hooks installed: {', '.join(sorted(set(events))) if events else '(none)'}")
    print(f"drone:           {'running pid='+str(pid) if pid else 'stopped'}")
    print(f"master_gain:     {cfg.get('master_gain', preset.get('master_gain', 0.5) if preset else 0.5)}")
    print(f"drone_gain:      {cfg.get('drone_gain', preset.get('drone_gain', 0.45) if preset else 0.45)}")
    rules = load_json(RULES_FILE, {"rules": []}).get("rules", [])
    if rules:
        print("cwd rules:")
        for r in rules:
            print(f"  {r.get('pattern','?'):<60} → {r.get('preset','?')}")
    sessions = load_json(SESSIONS_FILE, {"active": {}}).get("active", {})
    fresh = {sid: s for sid, s in sessions.items() if s.get("last_seen", 0) > time.time() - 4*3600}
    print(f"active sessions: {len(fresh)} (last 4h)  (run `claudio sessions` for details)")
    g = song_mod.global_song()
    if g:
        n_total = len(song_mod.notes_for(g))
        s = song_mod.load_song(g) or {}
        print(f"song (global):   {g} ({song_mod.position(g)}/{n_total} notes, bpm={s.get('bpm')})")
    else:
        print(f"song (global):   (off — Markov picker)")
    if preset and preset.get("song"):
        print(f"  preset song:   {preset['song']} (overrides global for {name})")
    q = song_mod.quant_settings()
    print(f"quant:           {'ON' if q['enabled'] else 'off'}  tempo={q['bpm']} bpm  grid={q['grid']} beats")
    print(f"event log:       {LOGS / 'event.log'}")

# ---------- presets ----------

def cmd_preset(args):
    if not args or args[0] in ("list", "ls"):
        names = list_preset_names()
        active = active_preset_name()
        for n in names:
            preset = load_preset(n)
            tag = " *" if n == active else "  "
            desc = preset.get("description", "") if preset else "(broken)"
            print(f"{tag} {n:<14} {desc}")
        return
    if args[0] in ("current", "show"):
        print(active_preset_name()); return
    if args[0] == "song":
        if len(args) < 3:
            print("usage: claudio preset song <preset> <song-name|off>"); return
        pname, song_name = args[1], args[2]
        preset = load_preset(pname)
        if preset is None:
            print(f"unknown preset '{pname}'"); return
        if song_name in ("off", "none", "-"):
            preset.pop("song", None)
            save_preset(pname, preset)
            print(f"preset '{pname}' song cleared")
            return
        if not song_mod.has_song(song_name):
            print(f"unknown song '{song_name}'. available: {', '.join(song_mod.list_songs()) or '(none)'}")
            return
        preset["song"] = song_name
        save_preset(pname, preset)
        print(f"preset '{pname}' song → {song_name}")
        return
    if args[0] == "reset":
        target = args[1] if len(args) > 1 else active_preset_name()
        return cmd_preset_reset(target)
    if args[0] == "reverb":
        return cmd_preset_reverb(args[1:])
    if args[0] in ("use", "set", "switch"):
        if len(args) < 2: print("usage: claudio preset use <name>"); return
        name = args[1]
        if name == "default":
            name = DEFAULT_PRESET
        if not (PRESETS / name / "preset.json").exists():
            print(f"unknown preset '{name}'. available: {', '.join(list_preset_names())}")
            return
        cfg = load_config()
        cfg["preset"] = name
        cfg.pop("master_gain", None)
        cfg.pop("drone_gain", None)
        save_config(cfg)
        print(f"active preset → {name}")
        if drone_pid():
            print("(stopping drone — preset changed)"); cmd_stop()
        preset = load_preset(name)
        if preset and preset.get("drone"):
            print("(starting drone for new preset)"); cmd_start()
        return
    print(f"unknown preset subcommand: {args[0]}")

# ---------- reset ----------

def cmd_preset_reset(name):
    src = PRESETS / name / "preset.default.json"
    dst = PRESETS / name / "preset.json"
    if not src.exists():
        print(f"no shipped default for '{name}' (looking for {src.name})")
        return
    dst.write_bytes(src.read_bytes())
    print(f"preset '{name}' restored from {src.name}")


def cmd_reset(args):
    """Full reset to shipped defaults — equivalent of a fresh install
    state. Asks for confirmation unless --yes is passed."""
    confirm = ("--yes" in args) or ("-y" in args)
    if not confirm:
        print("This will:")
        print(f"  · restore preset.json for ALL presets from preset.default.json")
        print(f"  · rewrite config.json to shipped defaults "
              f"(preset={DEFAULT_PRESET}, master_gain=0.55, drone_gain=0.0)")
        print(f"  · clear song positions, channel overrides, global song")
        print(f"  · clear session pins (sessions.json) and cwd rules (rules.json)")
        print(f"  · NOT touch installed hooks, samples, imported songs, or logs")
        print()
        print("Re-run with --yes to confirm: claudio reset --yes")
        return
    # restore presets
    for name in list_preset_names():
        cmd_preset_reset(name)
    # config
    save_config(dict(DEFAULT_CONFIG))
    print(f"config.json → shipped defaults")
    # song state
    song_state = STATE / "song.json"
    if song_state.exists(): song_state.unlink()
    print(f"song state cleared")
    # session pins
    if SESSIONS_FILE.exists(): SESSIONS_FILE.unlink()
    if RULES_FILE.exists(): RULES_FILE.unlink()
    print(f"session pins + cwd rules cleared")
    # bounce drone
    if drone_pid():
        cmd_stop()
    preset = load_preset(DEFAULT_PRESET)
    if preset and preset.get("drone"):
        cmd_start()
    print()
    print(f"✓ reset complete — active preset: {DEFAULT_PRESET}")


# ---------- sessions / rules ----------

def _load_active_sessions():
    return load_json(SESSIONS_FILE, {"active": {}}).get("active", {})

def _resolve_session(target):
    """Resolve target (numeric index or id-prefix) to a session_id."""
    sessions = _load_active_sessions()
    rows = sorted(sessions.items(), key=lambda kv: -kv[1].get("last_seen", 0))
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(rows): return rows[idx][0]
        return None
    matches = [sid for sid, _ in rows if sid.startswith(target)]
    if len(matches) == 1: return matches[0]
    if len(matches) > 1:
        print(f"ambiguous prefix '{target}'; matches {len(matches)} sessions")
    return None

def cmd_sessions():
    sessions = _load_active_sessions()
    cutoff = time.time() - 4 * 3600
    fresh = sorted(
        ((sid, s) for sid, s in sessions.items() if s.get("last_seen", 0) > cutoff),
        key=lambda kv: -kv[1].get("last_seen", 0),
    )
    if not fresh:
        print("no active sessions seen in last 4h")
        return
    print(f"{'#':>2}  {'sid':<10} {'pin':<3} {'preset':<12} {'ago':<6} {'src':<12} cwd")
    for i, (sid, s) in enumerate(fresh, 1):
        ago = max(0, int((time.time() - s.get("last_seen", 0)) / 60))
        pin = "📌" if s.get("preset_pinned") else " "
        preset = s.get("preset_resolved", "?")
        src = (s.get("preset_source", "") or "")[:11]
        cwd = s.get("cwd", "?")
        print(f"{i:>2}  {sid[:8]}    {pin}    {preset:<12} {ago:>3}m    {src:<12} {cwd}")

def cmd_session(args):
    if not args:
        cmd_sessions(); return
    sub = args[0]; rest = args[1:]
    if sub in ("list", "ls"):
        cmd_sessions(); return
    if sub == "pin":
        if len(rest) < 2: print("usage: claudio session pin <id|index> <preset>"); return
        sid = _resolve_session(rest[0])
        if not sid: print(f"no session matches '{rest[0]}'"); return
        preset = rest[1]
        if not (PRESETS / preset / "preset.json").exists():
            print(f"unknown preset '{preset}'"); return
        d = load_json(SESSIONS_FILE, {"active": {}})
        d.setdefault("active", {}).setdefault(sid, {})["preset_pinned"] = preset
        save_json(SESSIONS_FILE, d)
        print(f"pinned {sid[:8]} → {preset}")
        return
    if sub == "scale":
        if len(rest) < 2:
            print("usage: claudio session scale <id|index> <scale|off>"); return
        sid = _resolve_session(rest[0])
        if not sid: print(f"no session matches '{rest[0]}'"); return
        d = load_json(SESSIONS_FILE, {"active": {}})
        rec = d.setdefault("active", {}).setdefault(sid, {})
        target = rest[1]
        if target in ("off", "none", "-"):
            rec.pop("scale_override", None)
            save_json(SESSIONS_FILE, d)
            print(f"session {sid[:8]} scale cleared")
            return
        if target not in _scale_names():
            print(f"unknown scale '{target}'. available: {', '.join(_scale_names())}")
            return
        rec["scale_override"] = target
        save_json(SESSIONS_FILE, d)
        print(f"session {sid[:8]} scale → {target}")
        return
    if sub == "song":
        if len(rest) < 2:
            print("usage: claudio session song <id|index> <song-name|off>"); return
        sid = _resolve_session(rest[0])
        if not sid: print(f"no session matches '{rest[0]}'"); return
        d = load_json(SESSIONS_FILE, {"active": {}})
        rec = d.setdefault("active", {}).setdefault(sid, {})
        target = rest[1]
        if target in ("off", "none", "-"):
            rec.pop("song_pinned", None)
            save_json(SESSIONS_FILE, d)
            print(f"session {sid[:8]} song cleared")
            return
        if not song_mod.has_song(target):
            print(f"unknown song '{target}'"); return
        rec["song_pinned"] = target
        save_json(SESSIONS_FILE, d)
        print(f"session {sid[:8]} song → {target}")
        return
    if sub == "unpin":
        if not rest: print("usage: claudio session unpin <id|index>"); return
        sid = _resolve_session(rest[0])
        if not sid: print(f"no session matches '{rest[0]}'"); return
        d = load_json(SESSIONS_FILE, {"active": {}})
        if sid in d.get("active", {}):
            d["active"][sid].pop("preset_pinned", None)
            save_json(SESSIONS_FILE, d)
            print(f"unpinned {sid[:8]}")
        return
    print(f"unknown session subcommand: {sub}")

def cmd_here(args):
    if not args: print("usage: claudio here <preset>"); return
    preset = args[0]
    if not (PRESETS / preset / "preset.json").exists():
        print(f"unknown preset '{preset}'"); return
    cwd = os.environ.get("CLAUDIO_CWD") or os.getcwd()
    cmd_rule(["add", cwd, preset])

def _format_rule(r):
    parts = [f"  {r.get('pattern','?'):<48} → {r.get('preset','?'):<10}"]
    extras = []
    if "time" in r:           extras.append(f"time={r['time']}")
    if "idle_after_s" in r:   extras.append(f"idle≥{r['idle_after_s']}s")
    if extras:                parts.append("  " + "  ".join(extras))
    return "".join(parts)


def cmd_rule(args):
    if not args or args[0] in ("list", "ls"):
        rules = load_json(RULES_FILE, {"rules": []}).get("rules", [])
        if not rules: print("(no rules)"); return
        print("rules are evaluated top-to-bottom; first match wins")
        for r in rules:
            print(_format_rule(r))
        return
    sub = args[0]
    rest = list(args[1:])
    if sub == "add":
        # parse positional + flag args.
        # syntax: claudio rule add <pattern> <preset> [--time HH:MM-HH:MM] [--idle-after N]
        time_val = None
        idle_val = None
        positional = []
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--time" and i + 1 < len(rest):
                time_val = rest[i + 1]; i += 2; continue
            if a in ("--idle-after", "--idle") and i + 1 < len(rest):
                idle_val = int(rest[i + 1]); i += 2; continue
            positional.append(a); i += 1
        if len(positional) < 2:
            print("usage: claudio rule add <pattern> <preset> [--time HH:MM-HH:MM] [--idle-after N]")
            return
        pattern, preset = positional[0], positional[1]
        if not (PRESETS / preset / "preset.json").exists():
            print(f"unknown preset '{preset}'"); return
        new_rule = {"pattern": pattern, "preset": preset}
        if time_val:
            try:
                start_s, end_s = time_val.split("-")
                for s in (start_s, end_s):
                    h, m = s.split(":")
                    int(h); int(m)
            except Exception:
                print(f"--time must be HH:MM-HH:MM (got '{time_val}')"); return
            new_rule["time"] = time_val
        if idle_val is not None:
            if idle_val < 30:
                print(f"--idle-after below 30s is too jumpy; pick a higher value"); return
            new_rule["idle_after_s"] = idle_val
        d = load_json(RULES_FILE, {"rules": []})
        # de-dup by pattern + time + idle (allow multiple rules for same pattern with different conditions)
        key = (new_rule["pattern"], new_rule.get("time"), new_rule.get("idle_after_s"))
        rules = [r for r in d.get("rules", [])
                 if (r.get("pattern"), r.get("time"), r.get("idle_after_s")) != key]
        rules.append(new_rule)
        d["rules"] = rules
        save_json(RULES_FILE, d)
        print(f"rule added:")
        print(_format_rule(new_rule))
        return
    if sub in ("rm", "remove"):
        if not rest: print("usage: claudio rule rm <pattern>"); return
        pattern = rest[0]
        d = load_json(RULES_FILE, {"rules": []})
        before = len(d.get("rules", []))
        d["rules"] = [r for r in d.get("rules", []) if r.get("pattern") != pattern]
        save_json(RULES_FILE, d)
        print(f"removed {before - len(d['rules'])} rule(s) matching '{pattern}'")
        return
    print(f"unknown rule subcommand: {sub}")

# ---------- voice / mapping subcommands ----------

def _require_voice(preset, name):
    voices = preset.get("voices", {})
    if name not in voices:
        print(f"unknown voice '{name}'. available: {', '.join(voices)}")
        return None
    return voices[name]

def _regen_voice(pname, vname):
    """Re-render a single voice's samples (used after a reverb change)."""
    render = PRESETS / pname / "render.py"
    if not render.exists():
        print(f"  (preset '{pname}' has no render.py; reverb change saved but "
              f"samples not regenerated)"); return
    audio.spawn_python(render, [vname])


def cmd_voice(args):
    if len(args) < 2:
        print("usage: claudio voice <name> <gain|mioi|reverb|delay|fx|play> [value]"); return
    name = args[0]; sub = args[1]; rest = args[2:]
    pname = active_preset_name()
    preset = load_preset(pname)
    if preset is None: print(f"preset {pname} not found"); return
    v = _require_voice(preset, name)
    if v is None: return
    if sub == "reverb":
        # claudio voice <name> reverb <wet> [decay] [brightness] | off
        # baked at render → regenerates just this voice's samples.
        rv = v.setdefault("reverb", {})
        if rest and rest[0] in ("off", "dry", "none", "0"):
            rv["wet"] = 0.0
        elif not rest:
            print("usage: claudio voice <name> reverb <wet 0..1> [decay s] [brightness 0..1] | off"); return
        else:
            rv["wet"] = round(max(0.0, min(1.0, float(rest[0]))), 3)
            if len(rest) > 1: rv["decay"] = round(max(0.1, min(8.0, float(rest[1]))), 2)
            if len(rest) > 2: rv["brightness"] = round(max(0.0, min(1.0, float(rest[2]))), 2)
        save_preset(pname, preset)
        print(f"{name}.reverb = {rv}  (regenerating…)")
        _regen_voice(pname, name)
        return
    if sub == "delay":
        # claudio voice <name> delay <ms> [feedback] [count] | off
        # live playback echo — no re-render needed.
        if rest and rest[0] in ("off", "none", "0"):
            v.pop("delay", None)
            save_preset(pname, preset)
            print(f"{name}.delay = off"); return
        if not rest:
            print("usage: claudio voice <name> delay <ms> [feedback 0..0.85] [count 1..8] | off"); return
        d = v.setdefault("delay", {})
        d["ms"] = int(max(40, min(2000, float(rest[0]))))
        if len(rest) > 1: d["feedback"] = round(max(0.0, min(0.85, float(rest[1]))), 2)
        if len(rest) > 2: d["count"] = int(max(1, min(8, float(rest[2]))))
        d.setdefault("feedback", 0.30); d.setdefault("count", 3)
        save_preset(pname, preset)
        print(f"{name}.delay = {d}  (live — no regen)")
        return
    if sub in ("fx", "show"):
        rv = v.get("reverb"); d = v.get("delay")
        rtxt = (f"wet={rv.get('wet')} decay={rv.get('decay','?')}s "
                f"bright={rv.get('brightness','?')}" if isinstance(rv, dict) else "(default)")
        dtxt = (f"{d.get('ms')}ms fb={d.get('feedback')} x{d.get('count')}"
                if isinstance(d, dict) else "off")
        print(f"{name}: reverb {rtxt}  |  delay {dtxt}")
        return
    if sub == "gain":
        if not rest: print("usage: claudio voice <name> gain <0..1>"); return
        v["gain"] = round(max(0.0, min(1.0, float(rest[0]))), 3)
        save_preset(pname, preset)
        print(f"{name}.gain = {v['gain']}")
        return
    if sub == "mioi":
        if not rest: print("usage: claudio voice <name> mioi <seconds>"); return
        v["mioi"] = round(max(0.01, min(120.0, float(rest[0]))), 3)
        save_preset(pname, preset)
        print(f"{name}.mioi = {v['mioi']}s")
        return
    if sub == "play":
        # fire one trigger via event.py with a fake event that maps to this voice
        # easier: play a random sample directly via the audio backend
        import random
        d = PRESETS / pname / "samples" / v.get("dir", name)
        samples = sorted(p for p in d.iterdir() if p.suffix == ".wav") if d.exists() else []
        if not samples: print(f"no samples in {d}"); return
        cfg = load_config()
        master = float(cfg.get("master_gain", preset.get("master_gain", 0.5)))
        gain = max(0.0, min(1.0, v.get("gain", 0.5) * master))
        sample = random.choice(samples)
        audio.play_simple(sample, gain)
        print(f"playing {name} → {sample.name} @ v={gain:.2f}")
        return
    print(f"unknown voice subcommand: {sub}")

def _parse_event_key(token):
    """ 'PostToolUse' → ('PostToolUse', 'default')
        'PostToolUse:Edit' → ('PostToolUse', 'Edit')
        'PostToolUse:on_failure' → ('PostToolUse', 'on_failure') """
    if ":" in token:
        ev, key = token.split(":", 1)
        return ev, key
    return token, "default"

def _set_mapping(ev, key, voice_or_none):
    pname = active_preset_name()
    preset = load_preset(pname)
    if preset is None: print(f"preset {pname} not found"); return False
    if voice_or_none is not None and voice_or_none not in preset.get("voices", {}):
        print(f"unknown voice '{voice_or_none}'. available: {', '.join(preset.get('voices', {}))}")
        return False
    spec = preset.setdefault("events", {}).setdefault(ev, {})
    if key == "default":
        spec["default"] = voice_or_none
    elif key == "on_failure":
        spec["on_failure"] = voice_or_none
    else:
        bt = spec.setdefault("by_tool", {})
        if voice_or_none is None:
            bt.pop(key, None)
        else:
            bt[key] = voice_or_none
    save_preset(pname, preset)
    return True

def cmd_map(args):
    if len(args) < 2: print("usage: claudio map <event>[:<tool>] <voice|none>"); return
    ev, key = _parse_event_key(args[0])
    voice = args[1]
    if voice in ("-", "none", "null", "silent"): voice = None
    if _set_mapping(ev, key, voice):
        print(f"map {ev}/{key} → {voice if voice is not None else '(silent)'}")

def cmd_mute(args):
    if not args: print("usage: claudio mute <event>[:<tool>]"); return
    ev, key = _parse_event_key(args[0])
    if _set_mapping(ev, key, None):
        print(f"muted {ev}/{key}")

def cmd_unmute(args):
    if not args: print("usage: claudio unmute <event>[:<tool>] [voice]"); return
    ev, key = _parse_event_key(args[0])
    pname = active_preset_name()
    preset = load_preset(pname)
    if preset is None: return
    voices = list(preset.get("voices", {}).keys())
    if not voices: print("preset has no voices"); return
    voice = args[1] if len(args) > 1 else voices[0]
    if _set_mapping(ev, key, voice):
        print(f"unmuted {ev}/{key} → {voice}")

# ---------- gain ----------

def cmd_volume(v):
    cfg = load_config()
    cfg["master_gain"] = max(0.0, min(1.0, float(v)))
    save_config(cfg)
    print(f"master_gain = {cfg['master_gain']}")

def cmd_drone_volume(v):
    cfg = load_config()
    cfg["drone_gain"] = max(0.0, min(1.0, float(v)))
    save_config(cfg)
    print(f"drone_gain = {cfg['drone_gain']} (restart drone to apply)")

# ---------- regen ----------

def cmd_regen(args):
    name = args[0] if args else active_preset_name()
    render = PRESETS / name / "render.py"
    legacy = HERE / "synth.py"
    if render.exists():
        audio.spawn_python(render)
    elif name == "cathedral" and legacy.exists():
        # legacy synth writes into samples/ at top level; need to redirect
        # to presets/cathedral/samples — but this is rarely needed since
        # cathedral was bootstrapped already. Tell the user to use rainfall path.
        print(f"cathedral has no render.py; samples already rendered at "
              f"{PRESETS / 'cathedral' / 'samples'}")
    else:
        print(f"no renderer for preset '{name}'")

# ---------- test demo ----------

def fire_event(payload):
    audio.spawn_python(EVENT_PATH, stdin_bytes=json.dumps(payload).encode())

def clear_mioi(preset_name):
    sd = STATE / preset_name
    if sd.exists():
        for p in sd.glob("last-*.txt"):
            try: p.unlink()
            except FileNotFoundError: pass

def cmd_test(voice=None):
    name = active_preset_name()
    preset = load_preset(name)
    if preset is None: print(f"preset '{name}' not found"); return
    clear_mioi(name)
    events = preset.get("events", {})
    sequence = []
    if "SessionStart" in events:     sequence.append({"hook_event_name": "SessionStart", "session_id": "test"})
    if "UserPromptSubmit" in events: sequence.append({"hook_event_name": "UserPromptSubmit", "session_id": "test"})
    if "PreToolUse" in events:
        sequence.append({"hook_event_name": "PreToolUse", "tool_name": "Read", "session_id": "test"})
        for t in (events["PreToolUse"].get("by_tool") or {}):
            sequence.append({"hook_event_name": "PreToolUse", "tool_name": t, "session_id": "test"})
    if "PostToolUse" in events:
        sequence.append({"hook_event_name": "PostToolUse", "tool_name": "Read", "session_id": "test"})
        for t in (events["PostToolUse"].get("by_tool") or {}):
            sequence.append({"hook_event_name": "PostToolUse", "tool_name": t, "session_id": "test"})
    if "SubagentStop" in events:     sequence.append({"hook_event_name": "SubagentStop", "session_id": "test"})
    if "Stop" in events:             sequence.append({"hook_event_name": "Stop", "session_id": "test"})
    if "SessionEnd" in events:       sequence.append({"hook_event_name": "SessionEnd", "session_id": "test"})

    if voice:
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location("event_mod", str(HERE / "event.py"))
        mod = module_from_spec(spec); spec.loader.exec_module(mod)
        sequence = [p for p in sequence
                    if mod.resolve_voice(preset, p["hook_event_name"], p) == voice]
        if not sequence:
            print(f"no events in preset '{name}' map to voice '{voice}'"); return

    for p in sequence:
        ev = p["hook_event_name"]; tool = p.get("tool_name", "")
        print(f"  {ev}{('('+tool+')') if tool else ''}")
        clear_mioi(name)
        fire_event(p)
        time.sleep(2.5)
    print("done.")

# ---------- song / quant ----------

def _channel_label(song, name):
    ch = song_mod.get_channel(name)
    if ch is None:
        lead = song_mod.lead_channel(song)
        return f"lead (auto={lead})"
    if ch == "all":
        return "all"
    return f"ch{ch}"


def cmd_song(args):
    if not args or args[0] in ("list", "ls"):
        names = song_mod.list_songs()
        global_name = song_mod.global_song()
        if not names:
            print("(no songs imported — `claudio song import <file.mid>`)")
            return
        print(f"{'':2} {'name':<24} {'notes':>6}  {'bpm':>6}  channel")
        for n in names:
            tag = " *" if n == global_name else "  "
            s = song_mod.load_song(n) or {}
            print(f"{tag} {n:<24} {len(s.get('notes') or []):>6}  {s.get('bpm', 0):>6}  {_channel_label(s, n)}")
        return
    sub = args[0]; rest = args[1:]
    if sub == "import":
        if not rest:
            print("usage: claudio song import <file.mid> [name]"); return
        try:
            name, parsed = song_mod.import_midi_file(rest[0], rest[1] if len(rest) > 1 else None)
        except Exception as e:
            print(f"import failed: {e}"); return
        lead = song_mod.lead_channel(parsed)
        print(f"imported '{name}': {len(parsed['notes'])} notes  bpm={parsed['bpm']}  lead=ch{lead}")
        return
    if sub in ("import-dir", "importdir"):
        if not rest:
            print("usage: claudio song import-dir <folder>"); return
        folder = Path(rest[0]).expanduser()
        if not folder.is_dir():
            print(f"not a folder: {folder}"); return
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".mid")
        if not files:
            print(f"no .mid files in {folder}"); return
        for f in files:
            try:
                name, parsed = song_mod.import_midi_file(f)
                lead = song_mod.lead_channel(parsed)
                print(f"  ✓ {name:<24} {len(parsed['notes'])} notes  bpm={parsed['bpm']}  lead=ch{lead}")
            except Exception as e:
                print(f"  ✗ {f.name}: {e}")
        return
    if sub == "use":
        if not rest:
            print("usage: claudio song use <name>"); return
        if not song_mod.set_global(rest[0]):
            print(f"unknown song '{rest[0]}'. available: {', '.join(song_mod.list_songs()) or '(none)'}")
            return
        print(f"global song → {rest[0]} (events cycle through its notes)")
        return
    if sub in ("off", "stop", "disable"):
        song_mod.disable_global()
        print("global song off — back to Markov picker (preset/session pins still apply)")
        return
    if sub in ("current", "status", "show"):
        g = song_mod.global_song()
        print(f"global default: {g or '(none)'}")
        if g:
            s = song_mod.load_song(g) or {}
            n = len(song_mod.notes_for(g))
            print(f"  position:     {song_mod.position(g)} / {n}")
            print(f"  channel:      {_channel_label(s, g)}")
            print(f"  bpm (file):   {s.get('bpm')}")
        # preset overrides
        from importlib import import_module
        for name in list_preset_names():
            preset = load_preset(name)
            ps = (preset or {}).get("song")
            if ps:
                print(f"  preset {name}: {ps}")
        # session pins
        sessions = load_json(SESSIONS_FILE, {"active": {}}).get("active", {})
        for sid, rec in sessions.items():
            sp = rec.get("song_pinned")
            if sp:
                print(f"  session {sid[:8]}: {sp}")
        return
    if sub == "reset":
        target = rest[0] if rest else song_mod.global_song()
        if not target:
            print("usage: claudio song reset <name> (no global song set)"); return
        song_mod.reset_position(target)
        print(f"song '{target}' position → 0")
        return
    if sub == "channel":
        if len(rest) < 2:
            print("usage: claudio song channel <name> <lead|all|N>"); return
        name, ch_str = rest[0], rest[1]
        if not song_mod.has_song(name):
            print(f"unknown song '{name}'"); return
        if ch_str in ("lead", "auto"):
            song_mod.set_channel(name, "lead")
        elif ch_str == "all":
            song_mod.set_channel(name, "all")
        else:
            try:
                song_mod.set_channel(name, int(ch_str))
            except ValueError:
                print("channel must be 'lead', 'all', or an integer 0-15"); return
        s = song_mod.load_song(name)
        print(f"{name}.channel = {_channel_label(s, name)}  ({len(song_mod.notes_for(name))} notes after filter)")
        return
    if sub == "info":
        if not rest:
            print("usage: claudio song info <name>"); return
        name = rest[0]
        s = song_mod.load_song(name)
        if not s:
            print(f"unknown song '{name}'"); return
        lead = song_mod.lead_channel(s)
        print(f"{name}: {len(s.get('notes') or [])} notes  bpm={s.get('bpm')}  ppq={s.get('ppq')}")
        print(f"  selected channel: {_channel_label(s, name)}")
        print(f"  detected lead:    ch{lead}")
        print(f"  channel breakdown:")
        for ch, ct, med in song_mod.channel_summary(s):
            marker = " ← lead" if ch == lead else ""
            print(f"    ch{ch:>2}  {ct:>5} notes  median midi={med}{marker}")
        return
    print(f"unknown song subcommand: {sub}")


def _print_quant():
    q = song_mod.quant_settings()
    state = "ON" if q["enabled"] else "off"
    print(f"quant: {state}  tempo={q['bpm']} bpm  grid={q['grid']} beats")


def cmd_quant(args):
    if not args or args[0] in ("status", "show"):
        _print_quant(); return
    sub = args[0]
    if sub == "on":
        song_mod.set_quant(enabled=True);  _print_quant(); return
    if sub == "off":
        song_mod.set_quant(enabled=False); _print_quant(); return
    if sub == "toggle":
        cur = song_mod.quant_settings()["enabled"]
        song_mod.set_quant(enabled=not cur); _print_quant(); return
    print(f"unknown quant subcommand: {sub}")


def cmd_tempo(args):
    if not args:
        _print_quant(); return
    try:
        bpm = float(args[0])
    except ValueError:
        print("usage: claudio tempo <bpm>"); return
    song_mod.set_quant(bpm=bpm)
    _print_quant()


def cmd_grid(args):
    if not args:
        _print_quant(); return
    aliases = {
        "quarter": 1.0, "q": 1.0, "1/4": 1.0,
        "8th": 0.5, "eighth": 0.5, "1/8": 0.5,
        "16th": 0.25, "sixteenth": 0.25, "1/16": 0.25,
        "32nd": 0.125, "1/32": 0.125,
        "half": 2.0, "1/2": 2.0,
    }
    raw = args[0]
    if raw in aliases:
        grid = aliases[raw]
    else:
        try: grid = float(raw)
        except ValueError:
            print("usage: claudio grid <0.25|0.5|1.0|16th|8th|quarter|...>")
            return
    song_mod.set_quant(grid=grid)
    _print_quant()


# ---------- scale override ----------

# Imports SCALES dict from event.py — single source of truth.
def _scale_names():
    sys.path.insert(0, str(HERE))
    import event as _ev
    return list(_ev.SCALES.keys())


def cmd_scale(args):
    """Apply a per-config (current shell) scale override. Affects all sessions
    that don't have their own pin. Resolution: session pin > config > preset default."""
    if not args or args[0] in ("list", "ls"):
        names = _scale_names()
        cur = load_config().get("scale_override")
        for n in names:
            tag = " *" if n == cur else "  "
            print(f"{tag} {n}")
        return
    sub = args[0]
    if sub in ("off", "stop", "disable", "clear"):
        cfg = load_config(); cfg.pop("scale_override", None); save_config(cfg)
        print("scale override cleared")
        return
    if sub in ("show", "current", "status"):
        cur = load_config().get("scale_override")
        print(f"global scale: {cur or '(off — preset default)'}")
        sessions = load_json(SESSIONS_FILE, {"active": {}}).get("active", {})
        for sid, rec in sessions.items():
            if rec.get("scale_override"):
                print(f"  session {sid[:8]}: {rec['scale_override']}")
        return
    if sub == "use" or sub in _scale_names():
        # `claudio scale use <name>` or shorthand `claudio scale <name>`
        name = args[1] if sub == "use" and len(args) > 1 else sub
        if name not in _scale_names():
            print(f"unknown scale '{name}'. available: {', '.join(_scale_names())}")
            return
        cfg = load_config(); cfg["scale_override"] = name; save_config(cfg)
        print(f"global scale → {name}")
        return
    print(f"unknown scale subcommand: {sub}")


# ---------- preset reverb scale ----------

def cmd_preset_reverb(args):
    """Set or read the active preset's reverb_scale multiplier. Auto-regens.
    1.0 = unchanged from rendered defaults; 0.5 = half wet; 1.5 = 50% wetter."""
    pname = active_preset_name()
    preset = load_preset(pname)
    if preset is None:
        print(f"no active preset"); return
    if not args:
        print(f"{pname}.reverb_scale = {preset.get('reverb_scale', 1.0)}")
        return
    try:
        scale = max(0.0, min(2.0, float(args[0])))
    except ValueError:
        print("usage: claudio preset reverb <0..2>"); return
    preset["reverb_scale"] = round(scale, 3)
    save_preset(pname, preset)
    print(f"{pname}.reverb_scale = {scale}  (regenerating samples...)")
    cmd_regen([pname])


# ---------- per-event delay ----------

def cmd_event(args):
    """Set per-event-mapping effects (currently only delay/echo).
    Examples:
      claudio event delay Stop 320 0.30 3      # ms / feedback / count
      claudio event delay PostToolUse off      # remove the echo
      claudio event show                       # show current event effects
    """
    if not args or args[0] in ("show", "list", "ls"):
        pname = active_preset_name()
        preset = load_preset(pname)
        if preset is None: return
        events = preset.get("events", {})
        any_effects = False
        for ev, spec in events.items():
            if not isinstance(spec, dict): continue
            eff = spec.get("effect")
            if eff:
                any_effects = True
                d = eff.get("delay")
                if d:
                    print(f"  {ev}: delay {d.get('ms','?')}ms  fb={d.get('feedback','?')}  count={d.get('count','?')}")
        if not any_effects:
            print(f"(no event effects on '{pname}')")
        return
    sub = args[0]
    rest = args[1:]
    if sub == "delay":
        if len(rest) < 2:
            print("usage: claudio event delay <Event> <ms|off> [feedback] [count]")
            return
        ev = rest[0]
        pname = active_preset_name()
        preset = load_preset(pname)
        if preset is None: return
        events = preset.setdefault("events", {})
        spec = events.setdefault(ev, {"default": None})
        if not isinstance(spec, dict):
            spec = {"default": spec}
            events[ev] = spec
        if rest[1] in ("off", "none", "-", "0"):
            if "effect" in spec and "delay" in spec["effect"]:
                spec["effect"].pop("delay", None)
                if not spec["effect"]:
                    spec.pop("effect", None)
            save_preset(pname, preset)
            print(f"{ev}: delay cleared")
            return
        try:
            ms = max(40, min(2000, int(rest[1])))
            fb = float(rest[2]) if len(rest) > 2 else 0.30
            count = int(rest[3]) if len(rest) > 3 else 3
        except ValueError:
            print("usage: claudio event delay <Event> <ms> [feedback 0..0.85] [count 0..8]")
            return
        fb = max(0.0, min(0.85, fb))
        count = max(0, min(8, count))
        spec.setdefault("effect", {})["delay"] = {"ms": ms, "feedback": round(fb, 3), "count": count}
        save_preset(pname, preset)
        print(f"{ev}: delay {ms}ms  fb={fb}  count={count}")
        return
    print(f"unknown event subcommand: {sub}")


# ---------- demo / audition / status-line ----------

# Demo script: ~60 s of varied events with musical pacing. Designed to
# trigger every voice family in any preset and feel like a real session,
# not a checklist. Tools chosen so by_tool overrides actually fire.
_DEMO_SCRIPT = [
    ("SessionStart",     None,           1.6, "session begins"),
    ("UserPromptSubmit", None,           1.1, "you ask a question"),
    ("PreToolUse",       "Read",         0.3, "claude opens a file"),
    ("PostToolUse",      "Read",         1.6, "  …reads it"),
    ("PreToolUse",       "Bash",         0.3, "shell command"),
    ("PostToolUse",      "Bash",         1.4, "  …completes"),
    ("PreToolUse",       "Edit",         0.4, "file edit incoming"),
    ("PostToolUse",      "Edit",         2.4, "  …saved"),
    ("PreToolUse",       "Write",        0.4, "writing a new file"),
    ("PostToolUse",      "Write",        2.0, "  …written"),
    ("PreToolUse",       "Read",         0.3, "another read"),
    ("PostToolUse",      "Read",         3.0, "  …done"),
    ("SubagentStop",     None,           4.0, "subagent finishes"),
    ("PreToolUse",       "MultiEdit",    0.4, "batch edit"),
    ("PostToolUse",      "MultiEdit",    2.5, "  …complete"),
    ("PreToolUse",       "Bash",         0.3, "test command"),
    ("PostToolUse",      "Bash",         3.5, "  …passes"),
    ("Stop",             None,           5.0, "claude finishes"),
    ("SessionEnd",       None,           0.0, "session over"),
]


def _fire_event_payload(payload):
    audio.spawn_python(EVENT_PATH, stdin_bytes=json.dumps(payload).encode())


def cmd_demo(args):
    """60-second showcase. Fires events with musical gaps; selectively clears
    MIOI before bloom/cluster events so they actually sound."""
    pname = active_preset_name()
    preset = load_preset(pname)
    if preset is None:
        print(f"preset '{pname}' not found"); return
    voices_with_long_mioi = {n for n, v in preset.get("voices", {}).items()
                              if v.get("mioi", 0.5) >= 4.0}
    print(f"demo: {pname} ({preset.get('description','')[:60]})")
    print("─" * 70)
    clear_mioi(pname)
    started = time.time()
    for ev, tool, gap, label in _DEMO_SCRIPT:
        # Resolve the voice this event will pick — used for the timeline label
        # AND for pre-clearing MIOI on long-cooldown voices so they actually fire.
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location("event_mod", str(HERE / "event.py"))
        mod = module_from_spec(spec); spec.loader.exec_module(mod)
        payload = {"hook_event_name": ev, "session_id": "demo"}
        if tool: payload["tool_name"] = tool
        voice = mod.resolve_voice(preset, ev, payload)
        if voice in voices_with_long_mioi:
            # let the slow voice fire even mid-demo
            (STATE / pname / f"last-{voice}.txt").unlink(missing_ok=True)
        elapsed = time.time() - started
        ev_label = f"{ev}{f'({tool})' if tool else ''}"
        print(f"  {int(elapsed):>2}s  {ev_label:<28} → {voice or '-':<10}  {label}")
        _fire_event_payload(payload)
        time.sleep(gap)
    print("─" * 70)
    print(f"demo complete ({int(time.time() - started)}s).")


def _audition_blurbs():
    return {
        "meadow":    "bright, happy, felt-mallets in a sunlit room",
        "cathedral": "modal drone bed; airy plucks; the lush one",
        "rainfall":  "sparse drops, near-silence — silence as canvas",
        "koto":      "plucked silk strings + temple bowl, japanese",
    }


def cmd_audition(args):
    """Play a 12-second slice of every preset in turn so you can pick one.
    Doesn't change config.json unless --pick is passed and the user chooses.
    Safe to run any time."""
    blurbs = _audition_blurbs()
    order = ["meadow", "cathedral", "rainfall", "koto"]
    available = [n for n in order if n in list_preset_names()]
    if not available:
        print("no presets installed"); return
    cur = active_preset_name()
    print("audition — listen to each preset, then pick one")
    print("─" * 70)
    cfg_save = load_config()
    try:
        for n in available:
            cfg = load_config(); cfg["preset"] = n
            cfg["muted"] = False
            save_config(cfg)
            print(f"  ▶ {n:<10}  {blurbs.get(n, '')}")
            clear_mioi(n)
            preset = load_preset(n)
            for ev in ("SessionStart", "UserPromptSubmit",
                       "PreToolUse", "PostToolUse",
                       "PreToolUse", "PostToolUse", "Stop"):
                payload = {"hook_event_name": ev, "session_id": "audition"}
                if ev in ("PreToolUse", "PostToolUse"):
                    payload["tool_name"] = "Read"
                _fire_event_payload(payload)
                time.sleep(1.4)
            time.sleep(1.0)
        print("─" * 70)
        try:
            choice = input(f"pick one [1-{len(available)}, Enter=keep '{cur}']: ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = ""
        if choice.isdigit():
            i = int(choice) - 1
            if 0 <= i < len(available):
                target = available[i]
                cfg = load_config(); cfg["preset"] = target
                save_config(cfg)
                print(f"active preset → {target}")
                return
        # restore previous
        save_config(cfg_save)
        print(f"kept '{cur}'")
    except Exception as e:
        save_config(cfg_save)
        raise


def cmd_status_line(args):
    """Print ONE line summarizing live state. Suitable for tmux:
    `set -g status-right \"#(claudio status-line)\"`
    Reads existing state files; no per-event file write."""
    cfg = load_config()
    name = cfg.get("preset", DEFAULT_PRESET)
    muted = cfg.get("muted", False)
    # last fired voice across all voices for this preset
    sd = STATE / name
    last_voice, last_ts = None, 0.0
    if sd.exists():
        for f in sd.glob("last-*.txt"):
            try:
                ts = float(f.read_text().strip())
                if ts > last_ts:
                    last_ts = ts
                    last_voice = f.stem.replace("last-", "")
            except Exception:
                pass
    age = int(time.time() - last_ts) if last_ts else None
    voice_field = f"{last_voice}{f' {age}s' if age is not None else ''}" if last_voice else "—"
    state = "🔇" if muted else "🔊"
    sym = song_mod.global_song()
    pieces = [f"{state} {name}", f"voice:{voice_field}"]
    if sym:
        pieces.append(f"♪{sym}")
    q = song_mod.quant_settings()
    if q.get("enabled"):
        pieces.append(f"⏱ {int(q.get('bpm', 120))}bpm")
    print(" │ ".join(pieces))


# ---------- tune (TUI) ----------

def cmd_tune():
    # exec so curses gets a clean tty handoff (spawn-and-exit on Windows)
    audio.exec_python(TUNE_PATH)

# ---------- web UI ----------

def cmd_web(args):
    """Launch the browser control panel (pure-stdlib local server)."""
    web = str(HERE / "webui.py")
    port = "8788"; do_open = True
    rest = []
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args): port = args[i + 1]
        elif a == "--no-open": do_open = False
    args = ["--port", port]
    if do_open: args.append("--open")
    audio.exec_python(web, args)

# ---------- support ----------

def cmd_coffee(args):
    d = load_json(HERE / "donate.json", None)
    if not d or not d.get("methods"):
        print("No donate.json found.")
        return
    print()
    print(f"  ☕  {d.get('title', 'Buy me a coffee')}")
    if d.get("blurb"):
        print(f"      {d['blurb']}")
    print()
    for m in d["methods"]:
        accepts = m.get("accepts", [])
        extra = f"   ({', '.join(accepts[1:])})" if len(accepts) > 1 else ""
        print(f"  {m['label']:<10} {m['symbol']:<5} {m['address']}{extra}")
    print()
    print("  Native coin first; the listed tokens ride the same address (same chain only).")
    print("  QR codes + one-tap copy live in the web panel:  claudio web  →  ☕ Tip")
    print()

# ---------- main ----------

def main(argv):
    if not argv: argv = ["status"]
    cmd = argv[0]; args = argv[1:]
    if   cmd == "install":              cmd_install()
    elif cmd == "uninstall":            cmd_uninstall()
    elif cmd == "start":                cmd_start()
    elif cmd == "stop":                 cmd_stop()
    elif cmd == "status":               cmd_status()
    elif cmd == "test":                 cmd_test(args[0] if args else None)
    elif cmd == "volume" and args:      cmd_volume(args[0])
    elif cmd == "drone-volume" and args:cmd_drone_volume(args[0])
    elif cmd == "preset":               cmd_preset(args)
    elif cmd == "regen":                cmd_regen(args)
    elif cmd == "sessions":             cmd_sessions()
    elif cmd == "session":              cmd_session(args)
    elif cmd == "here":                 cmd_here(args)
    elif cmd in ("rule", "rules"):      cmd_rule(args)
    elif cmd == "voice":                cmd_voice(args)
    elif cmd == "map":                  cmd_map(args)
    elif cmd == "mute":                 cmd_mute(args)
    elif cmd == "unmute":               cmd_unmute(args)
    elif cmd == "tune":                 cmd_tune()
    elif cmd in ("web", "ui"):          cmd_web(args)
    elif cmd == "off":                  cmd_off()
    elif cmd == "on":                   cmd_on()
    elif cmd == "toggle":               cmd_toggle()
    elif cmd == "reset":                cmd_reset(args)
    elif cmd == "demo":                 cmd_demo(args)
    elif cmd == "audition":             cmd_audition(args)
    elif cmd in ("status-line", "statusline"): cmd_status_line(args)
    elif cmd == "scale":                cmd_scale(args)
    elif cmd == "event":                cmd_event(args)
    elif cmd in ("song", "songs"):      cmd_song(args)
    elif cmd == "quant":                cmd_quant(args)
    elif cmd == "tempo":                cmd_tempo(args)
    elif cmd == "grid":                 cmd_grid(args)
    elif cmd in ("coffee", "tip", "donate"): cmd_coffee(args)
    else:
        print(__doc__)

if __name__ == "__main__":
    main(sys.argv[1:])
