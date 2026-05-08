#!/usr/bin/env python3
"""
Claudio Symphony — installer.

Run from a fresh clone:

    python3 install.py

This will:
  1. Verify dependencies (Python 3, numpy, /usr/bin/afplay).
  2. Render samples for every preset under presets/.
  3. Print the next steps (./bin/claudio install / start / tune).

Re-run any time to regenerate samples (existing samples are overwritten).
"""
import sys, shutil, subprocess, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRESETS = HERE / "presets"

def step(msg):
    print(f"\n→ {msg}")

def fatal(msg):
    print(f"\n✗ {msg}", file=sys.stderr)
    sys.exit(1)

step("Checking Python version")
if sys.version_info < (3, 9):
    fatal(f"Python 3.9+ required (you have {sys.version.split()[0]})")
print(f"  Python {sys.version.split()[0]} ✓")

step("Checking numpy")
try:
    import numpy as np
    print(f"  numpy {np.__version__} ✓")
except ImportError:
    fatal("numpy not installed. Try: pip install numpy")

step("Checking afplay (macOS)")
if not shutil.which("afplay"):
    print("  ✗ /usr/bin/afplay not found.")
    print("    Claudio currently uses macOS's afplay. Linux/Windows support")
    print("    is a small patch — PRs welcome at:")
    print("    https://github.com/rmtbb/claudio-symphony")
    sys.exit(1)
print("  afplay ✓")

step("Rendering presets")
preset_dirs = sorted(p for p in PRESETS.iterdir()
                     if (p / "render.py").exists())
if not preset_dirs:
    fatal(f"No presets found under {PRESETS}/")
for p in preset_dirs:
    print(f"  · {p.name} ...")
    r = subprocess.run([sys.executable, str(p / "render.py")],
                       cwd=str(p), capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout); print(r.stderr, file=sys.stderr)
        fatal(f"render failed for preset '{p.name}'")
    n_wavs = sum(1 for _ in (p / "samples").rglob("*.wav"))
    print(f"    {n_wavs} samples")

claudio = HERE / "bin" / "claudio"
print()
print("─" * 60)
print(" Done. Next steps:")
print("─" * 60)
print()
print(f"  Add hooks to ~/.claude/settings.json:")
print(f"    {claudio} install")
print()
print(f"  Start the drone (cathedral preset has a continuous bed):")
print(f"    {claudio} start")
print()
print(f"  Open the interactive tuner:")
print(f"    {claudio} tune")
print()
print(f"  See all options:")
print(f"    {claudio}")
print()
