#!/usr/bin/env python3
"""
Claudio Symphony — interactive tuner (curses TUI).

Three panes (TAB to cycle):
  Voices    — gain + MIOI per voice; SPACE plays a sample from that voice.
  Events    — event → voice mapping (incl. by-tool overrides + on_failure).
              SPACE plays the mapped voice; m mutes/unmutes.
  Sessions  — active sessions (last 4h). ←/→ cycles a per-session preset pin.

Globals:
  p — cycle active preset
  s — save changes back to preset.json + config.json (stays open)
  ./, — master gain ±0.05
  q — quit (auto-saves any pending changes, like vim :wq)
  Q — quit WITHOUT saving (capital Q, discards edits)

In Voices pane, ←/→ adjusts the focused column. Use g (gain) or t (mioi/time)
to switch which column ←/→ tweaks.
"""
import curses, json, time, os, sys, subprocess, math, random
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRESETS = HERE / "presets"
STATE = HERE / "state"
CONFIG = HERE / "config.json"
SESSIONS_FILE = STATE / "sessions.json"
EVENT_PY = HERE / "event.py"

def load_json(p, default):
    try: return json.loads(p.read_text()) if p.exists() else default
    except Exception: return default

def save_json(p, d):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    tmp.rename(p)

def list_preset_names():
    if not PRESETS.exists(): return []
    return sorted(d.name for d in PRESETS.iterdir() if (d / "preset.json").exists())

def list_samples(preset_name, voice_dir):
    d = PRESETS / preset_name / "samples" / voice_dir
    if not d.exists(): return []
    return sorted(p for p in d.iterdir() if p.suffix == ".wav")

# ---------- TUI ----------

class TuneUI:
    PANE_VOICES, PANE_EVENTS, PANE_SESSIONS = 0, 1, 2

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.config = load_json(CONFIG, {})
        names = list_preset_names()
        self.preset_name = self.config.get("preset", "cathedral")
        if self.preset_name not in names and names:
            self.preset_name = names[0]
        self.preset = self._load_preset(self.preset_name)

        self.preset_dirty = False
        self.config_dirty = False

        self.pane = self.PANE_VOICES
        self.row_v = 0
        self.col_v = 0  # 0=gain 1=mioi
        self.row_e = 0
        self.row_s = 0

        self.message = ""
        self.message_until = 0

    def _load_preset(self, name):
        return load_json(PRESETS / name / "preset.json", {})

    def status(self, m, dur=2.5):
        self.message = m
        self.message_until = time.time() + dur

    # ---------- data accessors ----------

    def voices(self):
        return list(self.preset.get("voices", {}).items())

    def voice_names(self):
        return list(self.preset.get("voices", {}).keys())

    def events_flat(self):
        rows = []
        for ev, spec in self.preset.get("events", {}).items():
            spec = spec or {}
            rows.append((ev, "default", spec.get("default")))
            for t, v in (spec.get("by_tool") or {}).items():
                rows.append((ev, t, v))
            if "on_failure" in spec:
                rows.append((ev, "on_failure", spec.get("on_failure")))
        return rows

    def sessions(self):
        d = load_json(SESSIONS_FILE, {"active": {}}).get("active", {})
        cutoff = time.time() - 4 * 3600
        return sorted(
            ((sid, s) for sid, s in d.items() if s.get("last_seen", 0) > cutoff),
            key=lambda kv: -kv[1].get("last_seen", 0),
        )

    def master_gain(self):
        m = self.config.get("master_gain")
        if m is None:
            m = self.preset.get("master_gain", 0.5)
        return float(m)

    # ---------- drawing ----------

    def _slider(self, v, n=10):
        v = max(0.0, min(1.0, v))
        filled = int(round(v * n))
        return "▰" * filled + "▱" * (n - filled)

    def _mioi_bar(self, s, n=8):
        # log scale 0.05..30 sec
        x = max(0.0, min(1.0, math.log10(max(s, 0.05) / 0.05) / math.log10(30 / 0.05)))
        filled = int(round(x * n))
        return "▰" * filled + "▱" * (n - filled)

    def _addstr(self, y, x, s, attr=0):
        """Safe addstr — clip to screen, swallow errors at corners."""
        h, w = self.stdscr.getmaxyx()
        if y < 0 or y >= h: return
        max_w = w - x - 1
        if max_w <= 0: return
        try:
            self.stdscr.addstr(y, x, s[:max_w], attr)
        except curses.error:
            pass

    def draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        # Try to set up a red color pair for the dirty indicator
        try:
            curses.init_pair(1, curses.COLOR_RED, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            red = curses.color_pair(1) | curses.A_BOLD
            green = curses.color_pair(2) | curses.A_BOLD
        except Exception:
            red = curses.A_BOLD
            green = curses.A_BOLD

        # Header
        title = " claudio tune "
        self._addstr(0, 0, title.center(w, "─"), curses.A_BOLD)
        master = self.master_gain()
        is_dirty = self.preset_dirty or self.config_dirty
        sub = (f" preset: {self.preset_name}  "
               f"  master: {self._slider(master)} {master:.2f}")
        self._addstr(1, 1, sub)
        if is_dirty:
            tag = "● MODIFIED — press s to save, q to save+quit"
        else:
            saved_at = getattr(self, "_saved_at", 0)
            if time.time() - saved_at < 3:
                tag = f"✓ saved at {time.strftime('%H:%M:%S', time.localtime(saved_at))}"
            else:
                tag = "  (no changes)"
        # right-align the tag in the header
        x = max(1, w - len(tag) - 2)
        self._addstr(1, x, tag, red if is_dirty else green if "saved" in tag else curses.A_DIM)

        # Voices pane
        y = 3
        attr = curses.A_BOLD | (curses.A_REVERSE if self.pane == self.PANE_VOICES else 0)
        col_hint = "[gain]  press t for mioi" if self.col_v == 0 else "gain   [mioi]  press g for gain"
        header = f" Voices — {col_hint} "
        self._addstr(y, 1, header.ljust(w - 2, "─"), attr); y += 1
        for i, (name, cfg) in enumerate(self.voices()):
            sel = (self.pane == self.PANE_VOICES and self.row_v == i)
            arrow = "▶" if sel else " "
            gain = cfg.get("gain", 0.5)
            mioi = cfg.get("mioi", 0.5)
            gain_attr = curses.A_BOLD if (sel and self.col_v == 0) else 0
            mioi_attr = curses.A_BOLD if (sel and self.col_v == 1) else 0
            row_attr = curses.A_REVERSE if sel else 0
            line = f" {arrow} {name:<10}  gain {self._slider(gain)} {gain:>4.2f}    mioi {self._mioi_bar(mioi)} {mioi:>6.2f}s"
            self._addstr(y, 1, line.ljust(w-2), row_attr | (gain_attr if self.col_v == 0 else mioi_attr))
            y += 1
        y += 1

        # Events pane
        attr = curses.A_BOLD | (curses.A_REVERSE if self.pane == self.PANE_EVENTS else 0)
        self._addstr(y, 1, " Events ".ljust(w - 2, "─"), attr); y += 1
        ev_rows = self.events_flat()
        # render with simple scroll if too many rows
        max_e_rows = max(4, h - y - 6 - len(self.sessions()))
        start = max(0, self.row_e - max_e_rows + 2) if len(ev_rows) > max_e_rows else 0
        for i in range(start, min(start + max_e_rows, len(ev_rows))):
            ev, key, val = ev_rows[i]
            sel = (self.pane == self.PANE_EVENTS and self.row_e == i)
            arrow = "▶" if sel else " "
            indent = "  " if key == "default" else "      · "
            label = ev if key == "default" else key
            v = "(silent)" if val is None else val
            line = f" {arrow}{indent}{label:<22}→ {v}"
            row_attr = curses.A_REVERSE if sel else 0
            self._addstr(y, 1, line.ljust(w-2), row_attr)
            y += 1
        y += 1

        # Sessions pane
        attr = curses.A_BOLD | (curses.A_REVERSE if self.pane == self.PANE_SESSIONS else 0)
        self._addstr(y, 1, " Sessions (last 4h) ".ljust(w - 2, "─"), attr); y += 1
        sessions = self.sessions()
        if not sessions:
            self._addstr(y, 1, "   (no active sessions yet — fire any tool call from any Claude Code terminal)")
            y += 1
        for i, (sid, s) in enumerate(sessions):
            if y >= h - 2: break
            sel = (self.pane == self.PANE_SESSIONS and self.row_s == i)
            arrow = "▶" if sel else " "
            ago = max(0, int((time.time() - s.get("last_seen", 0)) / 60))
            cwd = s.get("cwd", "?")
            short = cwd if len(cwd) <= w - 50 else "…" + cwd[-(w-51):]
            pinned = s.get("preset_pinned")
            tag = "📌pin" if pinned else "    "
            preset = s.get("preset_resolved", "?")
            src = s.get("preset_source", "")
            line = f" {arrow} {sid[:8]} {tag}  {preset:<10}  {ago:>3}m  ({src})  {short}"
            row_attr = curses.A_REVERSE if sel else 0
            self._addstr(y, 1, line.ljust(w-2), row_attr)
            y += 1

        # Message
        if self.message and time.time() < self.message_until:
            self._addstr(h - 2, 1, self.message.ljust(w - 2), curses.A_DIM)

        # Footer
        keys = " TAB pane │ ↑↓ row │ ←→ value │ g/t col │ SPC play │ p preset │ m mute │ s save │ q save+quit │ Q discard "
        self._addstr(h - 1, 0, keys.center(w, "─"), curses.A_BOLD)

        self.stdscr.refresh()

    # ---------- mutations ----------

    def adjust_master(self, delta):
        cur = self.master_gain()
        self.config["master_gain"] = round(max(0.0, min(1.0, cur + delta)), 3)
        self.config_dirty = True

    def adjust_voice(self, voice_idx, col, delta):
        names = self.voice_names()
        if not (0 <= voice_idx < len(names)): return
        v = self.preset["voices"][names[voice_idx]]
        if col == 0:
            v["gain"] = round(max(0.0, min(1.0, v.get("gain", 0.5) + delta * 0.05)), 3)
        else:
            cur = v.get("mioi", 0.5)
            factor = 1.2 if delta > 0 else (1.0 / 1.2)
            v["mioi"] = round(max(0.01, min(120.0, cur * factor)), 3)
        self.preset_dirty = True

    def cycle_event_voice(self, row, delta):
        rows = self.events_flat()
        if not (0 <= row < len(rows)): return
        ev, key, val = rows[row]
        choices = [None] + self.voice_names()  # None == silent
        try: idx = choices.index(val)
        except ValueError: idx = 0
        new_val = choices[(idx + delta) % len(choices)]
        spec = self.preset["events"].setdefault(ev, {})
        if key == "default":
            spec["default"] = new_val
        elif key == "on_failure":
            spec["on_failure"] = new_val
        else:
            bt = spec.setdefault("by_tool", {})
            if new_val is None: bt.pop(key, None)
            else: bt[key] = new_val
        self.preset_dirty = True

    def mute_event(self, row):
        rows = self.events_flat()
        if not (0 <= row < len(rows)): return
        ev, key, val = rows[row]
        spec = self.preset["events"].setdefault(ev, {})
        if val is None:
            # restore default to first voice
            first = self.voice_names()[0] if self.voice_names() else None
            if first is None: return
            if key == "default":   spec["default"] = first
            elif key == "on_failure": spec["on_failure"] = first
            else: spec.setdefault("by_tool", {})[key] = first
            self.status(f"unmuted {ev}/{key} → {first}")
        else:
            if key == "default":   spec["default"] = None
            elif key == "on_failure": spec["on_failure"] = None
            else: spec.setdefault("by_tool", {}).pop(key, None)
            self.status(f"muted {ev}/{key}")
        self.preset_dirty = True

    def cycle_session_pin(self, row, delta):
        sessions = self.sessions()
        if not (0 <= row < len(sessions)): return
        sid, s = sessions[row]
        choices = [None] + list_preset_names()
        cur = s.get("preset_pinned")
        try: idx = choices.index(cur)
        except ValueError: idx = 0
        new = choices[(idx + delta) % len(choices)]
        d = load_json(SESSIONS_FILE, {"active": {}})
        if sid in d.get("active", {}):
            if new is None:
                d["active"][sid].pop("preset_pinned", None)
                self.status(f"unpinned {sid[:8]}")
            else:
                d["active"][sid]["preset_pinned"] = new
                self.status(f"pinned {sid[:8]} → {new}")
            save_json(SESSIONS_FILE, d)

    def cycle_preset(self):
        names = list_preset_names()
        if not names: return
        if self.preset_dirty:
            self.status("save (s) or discard your edits before switching presets")
            return
        try: idx = names.index(self.preset_name)
        except ValueError: idx = 0
        new = names[(idx + 1) % len(names)]
        self.preset_name = new
        self.preset = self._load_preset(new)
        self.config["preset"] = new
        self.config.pop("master_gain", None)
        self.config_dirty = True
        self.row_v = self.row_e = self.row_s = 0
        self.status(f"preset → {new}")

    def play_voice(self, voice):
        v_cfg = self.preset.get("voices", {}).get(voice)
        if not v_cfg:
            self.status(f"no voice '{voice}'"); return
        samples = list_samples(self.preset_name, v_cfg.get("dir", voice))
        if not samples:
            self.status(f"no samples for {voice}"); return
        gain = v_cfg.get("gain", 0.5) * self.master_gain()
        gain = max(0.0, min(1.0, gain))
        sample = random.choice(samples)
        try:
            subprocess.Popen(
                ["/usr/bin/afplay", "-v", f"{gain:.3f}", str(sample)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
            self.status(f"play {voice} ({sample.name})")
        except Exception as e:
            self.status(f"play failed: {e}")

    def fire_event_voice(self, row):
        rows = self.events_flat()
        if not (0 <= row < len(rows)): return
        ev, key, val = rows[row]
        if val is None:
            self.status(f"{ev}/{key} is silent"); return
        self.play_voice(val)

    def save(self):
        wrote = []
        if self.preset_dirty:
            save_json(PRESETS / self.preset_name / "preset.json", self.preset)
            self.preset_dirty = False
            wrote.append(f"presets/{self.preset_name}/preset.json")
        if self.config_dirty:
            save_json(CONFIG, self.config)
            self.config_dirty = False
            wrote.append("config.json")
        self._saved_at = time.time()
        if wrote:
            self.status(f"✓ saved {' + '.join(wrote)}", dur=3.0)
        else:
            self.status("nothing to save")

    # ---------- key dispatch ----------

    def handle(self, key):
        if key == -1:
            return True  # timeout/refresh
        # 'q' = save + quit (always exits)
        if key == ord('q'):
            if self.preset_dirty or self.config_dirty:
                self.save()
            return False
        # 'Q' = quit WITHOUT saving (capital)
        if key == ord('Q'):
            return False
        # ESC = save + quit (same as q)
        if key == 27:
            if self.preset_dirty or self.config_dirty:
                self.save()
            return False
        if key == ord('\t'):
            self.pane = (self.pane + 1) % 3; return True
        if key in (curses.KEY_BTAB,):
            self.pane = (self.pane - 1) % 3; return True
        if key == ord('s'): self.save(); return True
        if key == ord('p'): self.cycle_preset(); return True
        if key == ord('m') and self.pane == self.PANE_EVENTS:
            self.mute_event(self.row_e); return True

        if key == curses.KEY_UP:
            if   self.pane == self.PANE_VOICES:   self.row_v = max(0, self.row_v - 1)
            elif self.pane == self.PANE_EVENTS:   self.row_e = max(0, self.row_e - 1)
            elif self.pane == self.PANE_SESSIONS: self.row_s = max(0, self.row_s - 1)
            return True
        if key == curses.KEY_DOWN:
            if self.pane == self.PANE_VOICES:
                self.row_v = min(max(0, len(self.voices()) - 1), self.row_v + 1)
            elif self.pane == self.PANE_EVENTS:
                self.row_e = min(max(0, len(self.events_flat()) - 1), self.row_e + 1)
            elif self.pane == self.PANE_SESSIONS:
                self.row_s = min(max(0, len(self.sessions()) - 1), self.row_s + 1)
            return True
        if key == curses.KEY_LEFT:
            if   self.pane == self.PANE_VOICES:   self.adjust_voice(self.row_v, self.col_v, -1)
            elif self.pane == self.PANE_EVENTS:   self.cycle_event_voice(self.row_e, -1)
            elif self.pane == self.PANE_SESSIONS: self.cycle_session_pin(self.row_s, -1)
            return True
        if key == curses.KEY_RIGHT:
            if   self.pane == self.PANE_VOICES:   self.adjust_voice(self.row_v, self.col_v, +1)
            elif self.pane == self.PANE_EVENTS:   self.cycle_event_voice(self.row_e, +1)
            elif self.pane == self.PANE_SESSIONS: self.cycle_session_pin(self.row_s, +1)
            return True
        if key == ord('g') and self.pane == self.PANE_VOICES:
            self.col_v = 0; return True
        if key == ord('t') and self.pane == self.PANE_VOICES:
            self.col_v = 1; return True
        if key in (ord(','), ord('-'), ord('_')):
            self.adjust_master(-0.05); return True
        if key in (ord('.'), ord('+'), ord('=')):
            self.adjust_master(+0.05); return True
        if key == ord(' '):
            if self.pane == self.PANE_VOICES:
                names = self.voice_names()
                if 0 <= self.row_v < len(names):
                    self.play_voice(names[self.row_v])
            elif self.pane == self.PANE_EVENTS:
                self.fire_event_voice(self.row_e)
            return True
        return True

    def run(self):
        curses.curs_set(0)
        try:
            curses.start_color(); curses.use_default_colors()
        except Exception: pass
        # 500ms timeout so the UI auto-refreshes (sessions panel is live)
        self.stdscr.timeout(500)
        while True:
            self.draw()
            try: key = self.stdscr.getch()
            except KeyboardInterrupt: key = ord('q')
            if not self.handle(key):
                break

def main():
    if not sys.stdout.isatty():
        print("claudio tune requires an interactive terminal", file=sys.stderr)
        sys.exit(2)
    curses.wrapper(lambda s: TuneUI(s).run())

if __name__ == "__main__":
    main()
