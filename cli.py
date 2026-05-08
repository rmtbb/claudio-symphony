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
  preset current                   Print active preset

Per-session routing
  sessions                         List active sessions (last 4h)
  session pin <id|idx> <preset>    Pin one session to a specific preset
  session unpin <id|idx>           Remove the pin
  here <preset>                    Add a cwd rule for the current dir
  rule list                        Show cwd-pattern → preset rules
  rule add <pattern> <preset>      Add or replace a rule (glob or path-prefix)
  rule rm <pattern>                Remove a rule

Live tuning
  tune                             Open the curses TUI (recommended)
  volume <0..1>                    Master gain
  drone-volume <0..1>              Drone gain
  voice <name> gain <0..1>         Per-voice gain (active preset)
  voice <name> mioi <seconds>      Per-voice rate-limit
  voice <name> play                Preview that voice once
  map <event>[:<tool>] <voice|-|none>   Change event mapping
  mute <event>[:<tool>]            Set mapping to silent
  unmute <event>[:<tool>] [voice]  Restore mapping (default: first voice)
  test [voice]                     Walk all events of active preset
"""
import os, sys, json, time, fnmatch, subprocess, signal
from pathlib import Path

HERE = Path(__file__).resolve().parent
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

def load_config(): return load_json(CONFIG, {})
def save_config(d): save_json(CONFIG, d)
def active_preset_name(): return load_config().get("preset", "cathedral")
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
        os.kill(pid, 0)
        return pid
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
    subprocess.Popen(
        ["/usr/bin/env", "python3", DRONE_PATH],
        stdout=open(out, "a"), stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True,
    )
    time.sleep(0.4)
    pid = drone_pid()
    print(f"drone started pid={pid}" if pid else f"launch attempted; check {LOGS / 'drone.log'}")

def cmd_stop():
    pid = drone_pid()
    if not pid: print("drone not running"); return
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.3)
        subprocess.run(["pkill", "-f", "afplay .*drone.wav"], check=False)
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
    # kill any in-flight afplay so existing tails go silent now, not at end
    subprocess.run(["pkill", "-f", "afplay"], check=False)
    print("🔇 OFF — run `claudio on` to restore")

def cmd_on():
    cfg = load_config()
    cfg.pop("muted", None)
    save_config(cfg)
    name = cfg.get("preset", "cathedral")
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
    name = cfg.get("preset", "cathedral")
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
    if args[0] in ("use", "set", "switch"):
        if len(args) < 2: print("usage: claudio preset use <name>"); return
        name = args[1]
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

def cmd_rule(args):
    if not args or args[0] in ("list", "ls"):
        rules = load_json(RULES_FILE, {"rules": []}).get("rules", [])
        if not rules: print("(no rules)"); return
        for r in rules:
            print(f"  {r.get('pattern','?'):<60} → {r.get('preset','?')}")
        return
    sub = args[0]
    rest = args[1:]
    if sub == "add":
        if len(rest) < 2: print("usage: claudio rule add <pattern> <preset>"); return
        pattern, preset = rest[0], rest[1]
        if not (PRESETS / preset / "preset.json").exists():
            print(f"unknown preset '{preset}'"); return
        d = load_json(RULES_FILE, {"rules": []})
        rules = [r for r in d.get("rules", []) if r.get("pattern") != pattern]
        rules.append({"pattern": pattern, "preset": preset})
        d["rules"] = rules
        save_json(RULES_FILE, d)
        print(f"rule: {pattern} → {preset}")
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

def cmd_voice(args):
    if len(args) < 2:
        print("usage: claudio voice <name> <gain|mioi|play> [value]"); return
    name = args[0]; sub = args[1]; rest = args[2:]
    pname = active_preset_name()
    preset = load_preset(pname)
    if preset is None: print(f"preset {pname} not found"); return
    v = _require_voice(preset, name)
    if v is None: return
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
        # easier: call afplay directly on a random sample
        import random
        d = PRESETS / pname / "samples" / v.get("dir", name)
        samples = sorted(p for p in d.iterdir() if p.suffix == ".wav") if d.exists() else []
        if not samples: print(f"no samples in {d}"); return
        cfg = load_config()
        master = float(cfg.get("master_gain", preset.get("master_gain", 0.5)))
        gain = max(0.0, min(1.0, v.get("gain", 0.5) * master))
        sample = random.choice(samples)
        subprocess.Popen(
            ["/usr/bin/afplay", "-v", f"{gain:.3f}", str(sample)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
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
        subprocess.run(["/usr/bin/env", "python3", str(render)], check=False)
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
    p = subprocess.Popen(
        ["/usr/bin/env", "python3", EVENT_PATH],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    p.communicate(json.dumps(payload).encode())

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

# ---------- tune (TUI) ----------

def cmd_tune():
    # exec so curses gets a clean tty handoff
    os.execvp("/usr/bin/env", ["/usr/bin/env", "python3", TUNE_PATH])

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
    elif cmd == "off":                  cmd_off()
    elif cmd == "on":                   cmd_on()
    elif cmd == "toggle":               cmd_toggle()
    else:
        print(__doc__)

if __name__ == "__main__":
    main(sys.argv[1:])
