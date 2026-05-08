#!/usr/bin/env python3
"""
Cathedral preset — sample renderer.

The cathedral voices and gen_all() live in the top-level synth.py (since
they were the original implementation). This thin wrapper sets the output
directory to this preset's samples/ folder and invokes the generator.
"""
import os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# Tell synth.py to write samples here, then import + run.
os.environ["CLAUDIO_SAMPLES_DIR"] = str(HERE / "samples")
sys.path.insert(0, str(ROOT))

import synth  # noqa: E402

if __name__ == "__main__":
    synth.gen_all()
