#!/usr/bin/env python3
"""
Claudio Symphony — local web UI server (pure stdlib, no deps).

A small threaded HTTP server that serves the browser control panel in web/ and
exposes a tiny JSON API over the SAME files the CLI + hooks use (config.json,
presets/<name>/preset.json, state/). So anything you change here is live, and
the live-activity view reads the very markers event.py touches when Claude works.

Run:  python3 webui.py [--port 8788] [--open]
  or: claudio web
"""
import os, sys, json, time, random, signal, threading, subprocess, urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PRESETS = HERE / "presets"
STATE = HERE / "state"
CONFIG = HERE / "config.json"
RULES_FILE = STATE / "rules.json"
SESSIONS_FILE = STATE / "sessions.json"
EVENT_PY = str(HERE / "event.py")
RECORD_PY = str(HERE / "record.py")
REC_DIR = STATE / "recording"
REC_ACTIVE = REC_DIR / "active.json"
REC_EVENTS = REC_DIR / "events.jsonl"
OUT_DIR = HERE / "recordings"
WEB = HERE / "web"
SR_HINT = 44100

try:
    import song as song_mod
except Exception:
    song_mod = None

# A-rooted scales (mirrors event.py SCALES keys) — for the global/per-session override.
SCALE_NAMES = ["A_major", "A_pent", "A_lydian", "A_lydian_pent", "A_dorian",
               "A_aeolian", "A_in_sen", "A_phrygian", "A_yo", "A_hijaz"]

# ---------- json + domain helpers (mirror cli.py, no import side effects) ----------

def load_json(p, default):
    try: return json.loads(p.read_text()) if p.exists() else default
    except Exception: return default

def save_json(p, d):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    tmp.rename(p)

def load_config(): return load_json(CONFIG, {})
def save_config(d): save_json(CONFIG, d)
def active_preset_name(): return load_config().get("preset", "meadow")

def list_preset_names():
    if not PRESETS.exists(): return []
    return sorted(d.name for d in PRESETS.iterdir() if (d / "preset.json").exists())

def load_preset(name):
    p = PRESETS / name / "preset.json"
    return load_json(p, None)

def save_preset(name, d): save_json(PRESETS / name / "preset.json", d)

def clamp(x, lo, hi): return max(lo, min(hi, x))

ALL_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
              "SubagentStop", "Stop", "SessionEnd", "Notification", "PreCompact"]

# ---------- audio (play on the host, like Claudio itself) ----------

def voice_dir(preset, voice):
    cfg = (load_preset(preset) or {}).get("voices", {}).get(voice, {})
    return PRESETS / preset / "samples" / cfg.get("dir", voice)

def samples_in(d):
    return sorted(p for p in d.iterdir() if p.suffix == ".wav") if d.exists() else []

def play_sample(path, gain):
    try:
        subprocess.Popen(
            ["/usr/bin/afplay", "-v", f"{clamp(gain,0,1):.3f}", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
        return True
    except Exception:
        return False

def master_gain(preset_obj):
    cfg = load_config()
    m = cfg.get("master_gain")
    return float(m if m is not None else preset_obj.get("master_gain", 0.5))

def play_voice(preset, voice):
    p = load_preset(preset) or {}
    v = p.get("voices", {}).get(voice, {})
    smp = samples_in(voice_dir(preset, voice))
    if not smp: return False
    g = clamp(float(v.get("gain", 0.5)) * master_gain(p), 0, 1)
    return play_sample(random.choice(smp), g)

def regen_voice(preset, voice):
    render = PRESETS / preset / "render.py"
    if not render.exists(): return False, "no render.py for this preset"
    def _run():
        try:
            subprocess.run(["/usr/bin/env", "python3", str(render), voice],
                           cwd=str(HERE), check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()
    return True, "regenerating"

# ---------- state shaping ----------

def preset_card(name):
    p = load_preset(name) or {}
    return {
        "name": name,
        "description": p.get("description", ""),
        "voice_count": len(p.get("voices", {})),
        "scale_len": len(p.get("scale_pitches", []) or []),
        "has_drone": bool(p.get("drone")),
        "master_gain": p.get("master_gain", 0.5),
    }

SESSION_TTL = 4 * 3600

def sessions_list():
    d = load_json(STATE / "sessions.json", {}).get("active", {}) or {}
    now = time.time()
    out = []
    for sid, rec in d.items():
        ls = float(rec.get("last_seen", 0) or 0)
        if now - ls > SESSION_TTL:
            continue
        cwd = rec.get("cwd", "") or ""
        base = cwd.rstrip("/").split("/")[-1] if cwd else ""
        out.append({
            "id": sid, "short": (sid[:6] if sid else "—"),
            "cwd": cwd, "base": base or (cwd or "—"),
            "preset": rec.get("preset_resolved"),
            "source": rec.get("preset_source", "?"),
            "pinned": rec.get("preset_pinned"),
            "scale": rec.get("scale_override"),
            "song": rec.get("song_pinned"),
            "age": round(now - ls, 1), "ended": bool(rec.get("ended")),
        })
    out.sort(key=lambda s: s["age"])
    return out

def rules_list():
    return (load_json(RULES_FILE, {"rules": []}) or {}).get("rules", []) or []

def music_state():
    cfg = load_config()
    q = cfg.get("quant") or {}
    songs, gsong = [], None
    if song_mod:
        try:
            songs = [s[0] if isinstance(s, (list, tuple)) else s for s in song_mod.list_songs()]
        except Exception:
            songs = []
        try: gsong = song_mod.global_song()
        except Exception: gsong = None
    return {
        "scales": SCALE_NAMES, "scale_global": cfg.get("scale_override"),
        "quant": {"enabled": bool(q.get("enabled")), "bpm": float(q.get("bpm", 120.0)), "grid": float(q.get("grid", 0.5))},
        "songs": songs, "song_global": gsong,
    }

def fire_test(preset_hint=None):
    """Fire one of each event through event.py (uses the active routing)."""
    seq = [("SessionStart", {}), ("UserPromptSubmit", {}),
           ("PreToolUse", {"tool_name": "Read"}), ("PreToolUse", {"tool_name": "Edit"}),
           ("PostToolUse", {"tool_name": "Bash"}), ("SubagentStop", {}),
           ("Stop", {}), ("Notification", {}), ("SessionEnd", {})]
    def _run():
        for name, extra in seq:
            payload = {"hook_event_name": name, "session_id": "webtest", **extra}
            try:
                p = subprocess.Popen(["/usr/bin/env", "python3", EVENT_PY],
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                p.communicate(json.dumps(payload).encode(), timeout=3)
            except Exception:
                pass
            time.sleep(1.4)
    threading.Thread(target=_run, daemon=True).start()

def full_state():
    cfg = load_config()
    active = cfg.get("preset", "meadow")
    return {
        "active": active,
        "muted": bool(cfg.get("muted")),
        "master_gain": master_gain(load_preset(active) or {}),
        "drone_gain": float(cfg.get("drone_gain", 0.0)),
        "presets": [preset_card(n) for n in list_preset_names()],
        "sessions": sessions_list(),
        "rules": rules_list(),
        "music": music_state(),
    }

def preset_detail(name):
    p = load_preset(name)
    if p is None: return None
    voices = []
    for vn, vc in (p.get("voices", {}) or {}).items():
        rv = vc.get("reverb") if isinstance(vc.get("reverb"), dict) else {}
        dl = vc.get("delay") if isinstance(vc.get("delay"), dict) else None
        voices.append({
            "name": vn, "gain": vc.get("gain", 0.5), "mioi": vc.get("mioi", 0.5),
            "rate_jitter": bool(vc.get("rate_jitter")),
            "reverb": {"wet": rv.get("wet", 0.0), "decay": rv.get("decay"),
                       "brightness": rv.get("brightness")},
            "delay": dl, "regenable": (PRESETS / name / "render.py").exists(),
        })
    # events: always include all 9
    evmap = p.get("events", {}) or {}
    events = []
    for ev in ALL_EVENTS + [e for e in evmap if e not in ALL_EVENTS]:
        spec = evmap.get(ev) or {}
        events.append({
            "event": ev, "default": spec.get("default"),
            "by_tool": spec.get("by_tool") or {},
            "on_failure": spec.get("on_failure", "__none__") if "on_failure" in spec else None,
            "delay": (spec.get("effect") or {}).get("delay"),
        })
    return {
        "name": name, "description": p.get("description", ""),
        "master_gain": p.get("master_gain", 0.5),
        "reverb_scale": p.get("reverb_scale", 1.0),
        "scale_pitches": p.get("scale_pitches", []),
        "voice_names": list((p.get("voices", {}) or {}).keys()),
        "voices": voices, "events": events,
    }

def activity(name):
    sd = STATE / name
    now = time.time()
    def ts(path):
        try: return float(path.read_text().strip())
        except Exception: return 0.0
    p = load_preset(name) or {}
    voices = {vn: ts(sd / f"last-{vn}.txt") for vn in (p.get("voices", {}) or {})}
    events = {ev: ts(sd / f"evt-{ev}.txt") for ev in ALL_EVENTS}
    return {"now": now, "muted": bool(load_config().get("muted")),
            "active": active_preset_name(),
            "heartbeat": ts(STATE / "heartbeat"),
            "voices": voices, "events": events,
            "sessions": sessions_list()}

# ---------- HTTP ----------

CT = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
      ".js": "text/javascript; charset=utf-8", ".json": "application/json",
      ".wav": "audio/wav", ".m4a": "audio/mp4",
      ".svg": "image/svg+xml", ".woff2": "font/woff2"}

def record_status():
    out = {"active": False, "recordings": [], "max": 300, "default": 30}
    if OUT_DIR.exists():
        for p in sorted(OUT_DIR.glob("*"), reverse=True):
            if p.suffix in (".wav", ".m4a"):
                out["recordings"].append({
                    "name": p.name, "size": p.stat().st_size,
                    "url": "/recordings/" + urllib.parse.quote(p.name)})
    try:
        if REC_ACTIVE.exists():
            m = json.loads(REC_ACTIVE.read_text())
            elapsed = time.time() - m["start"]
            events = 0
            try: events = sum(1 for _ in REC_EVENTS.open())
            except Exception: pass
            out.update(active=True, duration=m["duration"], events=events,
                       remaining=max(0.0, round(m["duration"] - elapsed, 1)))
    except Exception:
        pass
    return out

class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudioWeb/1.0"
    def log_message(self, *a): pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n: return {}
        try: return json.loads(self.rfile.read(n) or b"{}")
        except Exception: return {}

    # ---- GET ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        if path == "/" or path == "/index.html":
            return self._file(WEB / "index.html")
        if path.startswith("/static/"):
            return self._file(WEB / path[len("/static/"):])
        if path == "/api/state":
            return self._send(200, full_state())
        if path == "/api/preset":
            name = (q.get("name") or [active_preset_name()])[0]
            d = preset_detail(name)
            return self._send(200, d) if d else self._send(404, {"error": "not found"})
        if path == "/api/activity":
            name = (q.get("name") or [active_preset_name()])[0]
            return self._send(200, activity(name))
        if path == "/api/donate":
            return self._send(200, load_json(HERE / "donate.json", {"methods": []}))
        if path == "/api/record/status":
            return self._send(200, record_status())
        if path.startswith("/recordings/"):
            fn = urllib.parse.unquote(path[len("/recordings/"):])
            f = OUT_DIR / fn
            if f.name != fn or not f.exists():     # block path traversal / missing
                return self._send(404, {"error": "not found"})
            return self._file(f)
        if path == "/sample":
            preset = (q.get("preset") or [active_preset_name()])[0]
            voice = (q.get("voice") or [""])[0]
            smp = samples_in(voice_dir(preset, voice))
            if not smp: return self._send(404, {"error": "no samples"})
            return self._file(random.choice(smp))
        return self._send(404, {"error": "not found"})

    def _file(self, p):
        p = Path(p)
        if not p.exists() or not p.is_file():
            return self._send(404, {"error": "not found"})
        ctype = CT.get(p.suffix, "application/octet-stream")
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- POST ----
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        b = self._json_body()
        path = u.path
        try:
            if path == "/api/preset/use":
                cfg = load_config(); cfg["preset"] = str(b["name"]); save_config(cfg)
                return self._send(200, {"ok": True, "active": cfg["preset"]})
            if path == "/api/mute":
                cfg = load_config(); cfg["muted"] = bool(b.get("muted")); save_config(cfg)
                return self._send(200, {"ok": True, "muted": cfg["muted"]})
            if path == "/api/master":
                cfg = load_config(); cfg["master_gain"] = round(clamp(float(b["gain"]), 0, 1), 3); save_config(cfg)
                return self._send(200, {"ok": True, "master_gain": cfg["master_gain"]})
            if path == "/api/drone":
                cfg = load_config(); cfg["drone_gain"] = round(clamp(float(b["gain"]), 0, 1), 3); save_config(cfg)
                return self._send(200, {"ok": True, "drone_gain": cfg["drone_gain"]})
            if path == "/api/voice":
                return self._voice(b)
            if path == "/api/voice/reverb":
                return self._voice_reverb(b)
            if path == "/api/voice/delay":
                return self._voice_delay(b)
            if path == "/api/voice/play":
                preset = b.get("preset", active_preset_name())
                ok = play_voice(preset, b["voice"])
                return self._send(200, {"ok": ok})
            if path == "/api/map":
                return self._map(b)
            if path == "/api/reverb_scale":
                return self._reverb_scale(b)
            if path == "/api/audition":
                return self._audition(b)
            if path == "/api/session/pin":
                return self._session_set(b, "preset_pinned", b.get("preset"))
            if path == "/api/session/scale":
                return self._session_set(b, "scale_override", b.get("scale"))
            if path == "/api/session/song":
                return self._session_set(b, "song_pinned", b.get("song"))
            if path == "/api/rule/add":
                return self._rule_add(b)
            if path == "/api/rule/rm":
                return self._rule_rm(b)
            if path == "/api/scale":
                cfg = load_config(); name = b.get("name")
                if name and name in SCALE_NAMES: cfg["scale_override"] = name
                else: cfg.pop("scale_override", None)
                save_config(cfg)
                return self._send(200, {"ok": True, "scale_global": cfg.get("scale_override")})
            if path == "/api/quant":
                cfg = load_config(); q = cfg.setdefault("quant", {})
                if "enabled" in b: q["enabled"] = bool(b["enabled"])
                if b.get("bpm") is not None: q["bpm"] = round(clamp(float(b["bpm"]), 30, 300), 1)
                if b.get("grid") is not None: q["grid"] = float(b["grid"])
                save_config(cfg)
                return self._send(200, {"ok": True, "quant": q})
            if path == "/api/song":
                if not song_mod: return self._send(200, {"ok": False, "msg": "no song module"})
                name = b.get("name")
                if name: song_mod.set_global(name)
                else: song_mod.disable_global()
                return self._send(200, {"ok": True, "song_global": song_mod.global_song()})
            if path == "/api/regen":
                name = b.get("name", active_preset_name())
                render = PRESETS / name / "render.py"
                if render.exists():
                    threading.Thread(target=lambda: subprocess.run(
                        ["/usr/bin/env", "python3", str(render)], cwd=str(HERE), check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
                return self._send(200, {"ok": render.exists()})
            if path == "/api/preset/reset":
                return self._preset_reset(b)
            if path == "/api/test":
                fire_test(b.get("name"))
                return self._send(200, {"ok": True})
            if path == "/api/record/start":
                if REC_ACTIVE.exists():
                    return self._send(200, {"ok": False, "msg": "already recording"})
                try: secs = int(b.get("seconds", 30))
                except Exception: secs = 30
                secs = max(1, min(300, secs))
                REC_DIR.mkdir(parents=True, exist_ok=True)
                cmd = ["/usr/bin/env", "python3", RECORD_PY, "run", str(secs)]
                if b.get("drone"):
                    cmd.append("--drone")
                subprocess.Popen(cmd, cwd=str(HERE),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, close_fds=True)
                return self._send(200, {"ok": True, "seconds": secs, "drone": bool(b.get("drone"))})
            if path == "/api/record/stop":
                try:
                    m = json.loads(REC_ACTIVE.read_text())
                    os.kill(int(m["pid"]), signal.SIGTERM)
                except Exception:
                    pass
                return self._send(200, {"ok": True})
        except KeyError as e:
            return self._send(400, {"error": f"missing {e}"})
        except Exception as e:
            return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def _voice(self, b):
        preset = b.get("preset", active_preset_name())
        p = load_preset(preset); v = p["voices"][b["voice"]]
        field = b["field"]
        if field == "gain":
            v["gain"] = round(clamp(float(b["value"]), 0, 1), 3)
        elif field == "mioi":
            v["mioi"] = round(clamp(float(b["value"]), 0.01, 120), 3)
        elif field == "rate_jitter":
            if bool(b["value"]): v["rate_jitter"] = True
            else: v.pop("rate_jitter", None)
        else:
            return self._send(400, {"error": "bad field"})
        save_preset(preset, p)
        return self._send(200, {"ok": True})

    def _voice_reverb(self, b):
        preset = b.get("preset", active_preset_name())
        p = load_preset(preset); v = p["voices"][b["voice"]]
        rv = v.setdefault("reverb", {})
        rv["wet"] = round(clamp(float(b["wet"]), 0, 1), 3)
        if b.get("decay") is not None: rv["decay"] = round(clamp(float(b["decay"]), 0.1, 8), 2)
        if b.get("brightness") is not None: rv["brightness"] = round(clamp(float(b["brightness"]), 0, 1), 2)
        save_preset(preset, p)
        ok, msg = regen_voice(preset, b["voice"])
        return self._send(200, {"ok": True, "regen": ok, "msg": msg})

    def _voice_delay(self, b):
        preset = b.get("preset", active_preset_name())
        p = load_preset(preset); v = p["voices"][b["voice"]]
        if b.get("off"):
            v.pop("delay", None)
        else:
            d = v.setdefault("delay", {})
            d["ms"] = int(clamp(float(b["ms"]), 40, 2000))
            d["feedback"] = round(clamp(float(b.get("feedback", d.get("feedback", 0.30))), 0, 0.85), 2)
            d["count"] = int(clamp(float(b.get("count", d.get("count", 3))), 1, 8))
        save_preset(preset, p)
        return self._send(200, {"ok": True})  # live, no regen

    def _map(self, b):
        preset = b.get("preset", active_preset_name())
        p = load_preset(preset); ev = b["event"]; key = b.get("key", "default")
        voice = b.get("voice")
        if voice in ("", "__none__", None) and b.get("voice", "__keep__") != "__keep__":
            voice = None
        spec = p.setdefault("events", {}).setdefault(ev, {})
        if key == "default":
            spec["default"] = voice
        elif key == "on_failure":
            if voice is None: spec.pop("on_failure", None)
            else: spec["on_failure"] = voice
        else:  # by_tool key
            bt = spec.setdefault("by_tool", {})
            if voice is None: bt.pop(key, None)
            else: bt[key] = voice
        save_preset(preset, p)
        return self._send(200, {"ok": True})

    def _reverb_scale(self, b):
        preset = b.get("preset", active_preset_name())
        p = load_preset(preset)
        p["reverb_scale"] = round(clamp(float(b["value"]), 0, 2), 3)
        save_preset(preset, p)
        # full regen in background
        render = PRESETS / preset / "render.py"
        if render.exists():
            threading.Thread(target=lambda: subprocess.run(
                ["/usr/bin/env", "python3", str(render)], cwd=str(HERE), check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), daemon=True).start()
        return self._send(200, {"ok": True, "regen": render.exists()})

    def _session_set(self, b, key, value):
        sid = b["id"]
        d = load_json(SESSIONS_FILE, {"active": {}})
        rec = d.setdefault("active", {}).setdefault(sid, {})
        if value in (None, "", "__none__"): rec.pop(key, None)
        else: rec[key] = value
        save_json(SESSIONS_FILE, d)
        return self._send(200, {"ok": True})

    def _rule_add(self, b):
        pattern = (b.get("pattern") or "").strip()
        preset = b.get("preset")
        if not pattern or not preset or not (PRESETS / preset / "preset.json").exists():
            return self._send(400, {"error": "need valid pattern + preset"})
        rule = {"pattern": pattern, "preset": preset}
        if b.get("time"): rule["time"] = b["time"]
        if b.get("idle_after_s"): rule["idle_after_s"] = int(b["idle_after_s"])
        d = load_json(RULES_FILE, {"rules": []})
        key = (rule["pattern"], rule.get("time"), rule.get("idle_after_s"))
        rules = [r for r in d.get("rules", []) if (r.get("pattern"), r.get("time"), r.get("idle_after_s")) != key]
        rules.append(rule); d["rules"] = rules
        save_json(RULES_FILE, d)
        return self._send(200, {"ok": True, "rules": rules})

    def _rule_rm(self, b):
        pat = b.get("pattern")
        d = load_json(RULES_FILE, {"rules": []})
        d["rules"] = [r for r in d.get("rules", []) if r.get("pattern") != pat]
        save_json(RULES_FILE, d)
        return self._send(200, {"ok": True, "rules": d["rules"]})

    def _preset_reset(self, b):
        name = b["name"]
        default = PRESETS / name / "preset.default.json"
        if default.exists():
            save_preset(name, json.loads(default.read_text()))
            return self._send(200, {"ok": True, "msg": "restored from default"})
        return self._send(200, {"ok": False, "msg": "no preset.default.json for this preset"})

    def _audition(self, b):
        preset = b["name"]
        p = load_preset(preset) or {}
        # prefer the Stop voice, else a melodic-ish default, else first voice
        ev = p.get("events", {})
        pick = (ev.get("Stop", {}) or {}).get("default") \
            or (ev.get("PostToolUse", {}) or {}).get("default") \
            or next(iter(p.get("voices", {})), None)
        ok = play_voice(preset, pick) if pick else False
        return self._send(200, {"ok": ok, "voice": pick})


def main():
    port = 8788
    do_open = False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args): port = int(args[i + 1])
        if a == "--open": do_open = True
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Claudio web UI → {url}  (Ctrl-C to stop)")
    if do_open:
        try: subprocess.Popen(["/usr/bin/open", url])
        except Exception: pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
