#!/usr/bin/env python3
"""
Claudio plugin bootstrap — runs once on SessionStart (alongside event.py) when
Claudio is installed as a Claude Code plugin.

A fresh plugin clone has no rendered samples (they're generated, not committed),
so this makes the plugin actually make sound without the user running install.py
by hand: if the active preset has no samples yet, it renders that one preset in
a fully detached background process. It NEVER blocks the session (returns in a
few ms; the render runs on its own), is idempotent (skips when samples exist),
and self-limits its numpy bootstrap to a single attempt so it can't loop.

If numpy isn't available and can't be installed, it writes one clear note to
logs/SETUP_NEEDED.txt and stays silent — graceful, never noisy.
"""
import sys, os, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # plugin / repo root (bin/..)
PRESETS = ROOT / "presets"
STATE = ROOT / "state"
LOGS = ROOT / "logs"
PIP_MARK = STATE / ".pip-attempted"


def active_preset():
    try:
        return json.loads((ROOT / "config.json").read_text()).get("preset", "meadow")
    except Exception:
        return "meadow"


def has_samples(name):
    d = PRESETS / name / "samples"
    return d.is_dir() and next(d.rglob("*.wav"), None) is not None


def have_numpy():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def main():
    STATE.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    name = active_preset()
    if has_samples(name):
        return 0                                    # already playable — nothing to do

    if not have_numpy():
        if not PIP_MARK.exists():                   # one best-effort install, ever
            PIP_MARK.write_text("1")
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "numpy"],
                               timeout=180, check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        if not have_numpy():
            (LOGS / "SETUP_NEEDED.txt").write_text(
                "Claudio needs numpy to synthesize its sounds.\n"
                "Run this once, then start a new session:\n\n"
                f"    python3 -m pip install numpy && python3 \"{ROOT / 'install.py'}\"\n")
            return 0

    render = PRESETS / name / "render.py"
    if render.exists():
        try:
            kw = {}
            if hasattr(os, "setsid"):
                kw["start_new_session"] = True       # detach from the 1s hook timeout
            subprocess.Popen([sys.executable, str(render)], cwd=str(ROOT),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)                                  # bootstrap must never fail a session
