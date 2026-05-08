#!/usr/bin/env python3
"""
Meadow preset — sample renderer.

Aesthetic: wooden felt-mallets and kalimba in a sunlit room. Bright partials
over warm bodies. A=432 Hz, A major pentatonic (A B C# E F#) — no half-steps
anywhere in the scale, so every random combination is resolved and consonant.

Voices:
  mallet  — felt mallet on warm wood (the satisfying voice)
  kalimba — plucked metal tine, slight inharmonicity
  chime   — bright FM bell, friendly not solemn
  bird    — pitched 80 ms upward chirp (synthetic tin whistle)
  wood    — tiny noise-burst tap with small pitched ring
  bloom   — slow major-triad pad swell ('you did good')
  cluster — quick 4-5 note ascending pentatonic mallet flourish
"""
import sys, math, wave
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from synth import (
    SR, A4, freq, t_axis, adsr, soft_clip, lowpass_fft,
    reverb_stereo, to_stereo, write_wav,
)

OUT = HERE / "samples"
for sub in ("mallet", "kalimba", "chime", "bird", "wood", "bloom", "cluster"):
    (OUT / sub).mkdir(parents=True, exist_ok=True)

# A major pentatonic in midi (no half-steps): A B C# E F#
# Across octaves 3-5: 57, 59, 61, 64, 66, 69, 71, 73, 76, 78, 81
PENTATONIC_LOW  = [57, 59, 61, 64, 66, 69, 71, 73, 76, 78, 81]   # A3..A5
PENTATONIC_MID  = [64, 66, 69, 71, 73, 76, 78, 81]               # E4..A5
PENTATONIC_HIGH = [73, 76, 78, 81, 83, 85, 88, 90]               # C#5..F#6

# ---------- voices ----------

def voice_mallet(midi):
    """Felt mallet on warm wood. The satisfying voice.

    Three layers stacked:
      1. Filtered noise burst at attack — the 'thock' of felt on wood.
      2. Body resonance: sine fundamental + slightly detuned octave +
         non-integer high partial for wood color (1.0, 2.01, 4.1, 7.0).
      3. Tiny pitch glide down at the very start (~0.5 cents) — feels
         like the mallet 'gives' against the wood. Below threshold of
         consciousness, but ear notices.
    """
    f = freq(midi)
    dur = 1.6
    n = int(dur * SR); t = t_axis(dur)
    rng = np.random.default_rng(int(midi))

    # Noise burst, narrow-banded around 2*f for wood character
    noise = rng.standard_normal(n)
    band = lowpass_fft(noise, max(f*4, 800), order=2) - lowpass_fft(noise, f*1.5, order=2)
    noise_env = np.exp(-t * 90)
    attack_layer = 0.18 * band * noise_env

    # Body: sine + non-integer partials (wood, not metal)
    body = (
        np.sin(2*np.pi*f*t)             * np.exp(-t*4) +
        0.40 * np.sin(2*np.pi*f*2.01*t) * np.exp(-t*7) +
        0.18 * np.sin(2*np.pi*f*4.1*t)  * np.exp(-t*14) +
        0.08 * np.sin(2*np.pi*f*7.0*t)  * np.exp(-t*22)
    )
    # Subtle pitch glide down (~half a cent, decays in 16ms) — the felt give
    pitch_env = 1 - 0.0005 * np.exp(-t * 60)
    body = body * pitch_env

    sig = attack_layer + body
    env = adsr(n, a=0.004, d=0.55, s_level=0.0, r=0.85)
    out = sig[:env.size] * env[:sig.size] * 0.55
    return out

def voice_kalimba(midi):
    """Plucked metal tine. Slight inharmonicity (1.0, 2.05, 3.1, 5.0)
    — these ratios are characteristic of a struck metal lamella, sit just
    barely off integer multiples so you hear them as 'metal-warm' rather
    than 'pure piano'."""
    f = freq(midi)
    dur = 2.8
    n = int(dur * SR); t = t_axis(dur)
    rng = np.random.default_rng(int(midi) + 100)

    sig = (
        np.sin(2*np.pi*f*t)              * np.exp(-t*1.4) +
        0.50 * np.sin(2*np.pi*f*2.05*t)  * np.exp(-t*2.4) +
        0.25 * np.sin(2*np.pi*f*3.10*t)  * np.exp(-t*4.0) +
        0.12 * np.sin(2*np.pi*f*5.00*t)  * np.exp(-t*6.0)
    )
    # Tiny detune for liveness
    sig += 0.15 * np.sin(2*np.pi*f*1.003*t) * np.exp(-t*2.0)
    # Pluck noise (very short, very subtle)
    sig += rng.standard_normal(n) * np.exp(-t*120) * 0.04

    env = adsr(n, a=0.003, d=1.6, s_level=0.0, r=1.1)
    out = sig[:env.size] * env[:sig.size] * 0.45
    return out

def voice_chime(midi):
    """Bright friendly FM bell. Carrier:modulator 1:2.76 (an 'open' ratio
    that gives bell character without the dark inharmonic feel of 1:1.41)."""
    f = freq(midi)
    dur = 4.0
    n = int(dur * SR); t = t_axis(dur)
    fm_index = 3.5 * np.exp(-t * 1.4)
    sig = np.sin(2*np.pi*f*t + fm_index * np.sin(2*np.pi*f*2.76*t))
    sig += 0.15 * np.sin(2*np.pi*f*3*t) * np.exp(-t*2.0)
    env = adsr(n, a=0.008, d=2.5, s_level=0.0, r=0.5)
    out = sig[:env.size] * env[:sig.size] * 0.40
    return out

def voice_bird(seed):
    """80 ms pitched chirp — like a tin whistle's quick rise. Pitch chosen
    randomly from upper pentatonic, sweep up by 2-5 semitones, slight vibrato."""
    rng = np.random.default_rng(seed)
    start_midi = int(rng.choice([76, 78, 81, 83]))
    end_midi = start_midi + rng.uniform(2.0, 5.0)
    chirp_dur = 0.085
    full_dur = 0.5
    n = int(full_dur * SR)
    chirp_n = int(chirp_dur * SR)

    f0 = freq(start_midi); f1 = freq(end_midi)
    f_curve = np.linspace(f0, f1, chirp_n)
    phase = 2*np.pi * np.cumsum(f_curve) / SR
    chirp = np.sin(phase)
    chirp_t = np.arange(chirp_n) / SR
    chirp *= (1 + 0.012 * np.sin(2*np.pi*30*chirp_t))   # tiny vibrato
    chirp_env = np.exp(-chirp_t * 18)
    chirp = chirp * chirp_env

    sig = np.zeros(n)
    sig[:chirp_n] = chirp
    # Body envelope to ensure clean tail
    env = adsr(n, a=0.002, d=0.06, s_level=0.0, r=0.06)
    return sig * env * 0.32

def voice_wood(seed):
    """Tiny percussive tap — filtered noise + small pitched ringing.
    Almost subliminal. 30 ms total energy."""
    rng = np.random.default_rng(seed)
    dur = 0.3
    n = int(dur * SR); t = t_axis(dur)
    noise = rng.standard_normal(n)
    band = lowpass_fft(noise, 4000, order=2) - lowpass_fft(noise, 1500, order=2)
    band_env = np.exp(-t * 80)
    ring_freq = 2200 + rng.uniform(-300, 300)
    ring = np.sin(2*np.pi*ring_freq*t) * np.exp(-t*45)
    sig = 0.7 * band * band_env + 0.18 * ring
    env = adsr(n, a=0.001, d=0.06, s_level=0.0, r=0.06)
    return sig[:env.size] * env[:sig.size] * 0.28

def voice_bloom(chord_midis):
    """Slow major-triad pad swell. 9 sec total. Multiple sines + small saw
    layer + slight detune + gentle vibrato. Warm filtered for body."""
    dur = 9.0
    n = int(dur * SR); t = t_axis(dur)
    sig = np.zeros(n)
    for m in chord_midis:
        f = freq(m)
        partials = np.zeros(n)
        for h in range(1, 5):
            partials += (1.0/h) * np.sin(2*np.pi*f*h*t)
        partials *= 0.35
        sine = np.sin(2*np.pi*f*t)
        det = np.sin(2*np.pi*f*1.004*t)
        vib = 1 + 0.0028 * np.sin(2*np.pi*0.30*t + m*0.11)
        sig += (0.7*sine + 0.4*det + 0.4*partials) * vib
    sig /= max(1, len(chord_midis))
    sig = lowpass_fft(sig, 1500, order=3)
    env = adsr(n, a=3.0, d=0.5, s_level=0.85, hold=2.0, r=4.0)
    out = sig[:env.size] * env[:sig.size] * 0.50
    return out

def voice_cluster(seed):
    """Quick 4-5 note ascending pentatonic mallet flourish.
    The music-box wind-up. Each note 70 ms apart, stacked into a chord
    via the natural mallet tail — feels like one gesture, not five hits."""
    rng = np.random.default_rng(seed)
    n_notes = int(rng.integers(4, 6))
    starts = sorted(rng.choice(PENTATONIC_HIGH, size=n_notes, replace=False))
    if rng.random() < 0.25:
        starts = list(reversed(starts))   # occasional descending

    spacing = 0.075
    note_dur = 1.6
    total = spacing * (n_notes - 1) + note_dur + 1.0
    n = int(total * SR)
    out = np.zeros(n)
    for i, m in enumerate(starts):
        offset = int(i * spacing * SR)
        note = voice_mallet(int(m))
        end = offset + note.size
        if end > out.size:
            note = note[:out.size - offset]
            end = out.size
        # slight gain ramp so first note is loudest
        gain = 0.85 - 0.07 * i
        out[offset:end] += note * gain
    return out

# ---------- generation ----------

def render_with_pan(mono, wet, decay_s, predelay_ms, brightness, pan=0.0):
    st = reverb_stereo(mono, wet=wet, decay_s=decay_s,
                       predelay_ms=predelay_ms, brightness=brightness)
    if pan != 0.0:
        pan = float(np.clip(pan, -1.0, 1.0))
        lg = math.cos((pan + 1) * math.pi / 4)
        rg = math.sin((pan + 1) * math.pi / 4)
        st = st.copy()
        st[:, 0] *= 2 * lg
        st[:, 1] *= 2 * rg
    return st

def gen_all():
    print("[meadow] rendering...")

    # mallet — workhorse voice, 11 pitches in pentatonic, alternating pan
    for i, m in enumerate(PENTATONIC_LOW):
        print(f"  mallet m{m}")
        pan = (-0.3, -0.15, 0.0, 0.15, 0.3, 0.15, 0.0, -0.15, -0.3, 0.0, 0.2)[i % 11]
        # short bright reverb: 1.8 s, more brightness than cathedral
        st = render_with_pan(voice_mallet(m), wet=0.32, decay_s=1.8,
                             predelay_ms=22, brightness=0.6, pan=pan)
        write_wav(OUT / "mallet" / f"{i:02d}_m{m}.wav", st, target_peak=0.85)

    # kalimba — 8 pitches mid-high
    for i, m in enumerate(PENTATONIC_MID):
        print(f"  kalimba m{m}")
        pan = (-0.4, -0.2, 0.0, 0.2, 0.4, 0.2, 0.0, -0.2)[i % 8]
        st = render_with_pan(voice_kalimba(m), wet=0.35, decay_s=2.2,
                             predelay_ms=25, brightness=0.55, pan=pan)
        write_wav(OUT / "kalimba" / f"{i:02d}_m{m}.wav", st, target_peak=0.80)

    # chime — 4 pitches
    for i, m in enumerate([73, 76, 78, 81]):
        print(f"  chime m{m}")
        pan = (-0.2, 0.0, 0.0, 0.2)[i]
        st = render_with_pan(voice_chime(m), wet=0.45, decay_s=2.8,
                             predelay_ms=40, brightness=0.55, pan=pan)
        write_wav(OUT / "chime" / f"{i:02d}_m{m}.wav", st, target_peak=0.75)

    # bird — 4 chirp variants
    for i in range(4):
        print(f"  bird {i}")
        pan = (-0.5, 0.5, -0.3, 0.3)[i]
        st = render_with_pan(voice_bird(seed=42 + i), wet=0.28, decay_s=1.0,
                             predelay_ms=10, brightness=0.7, pan=pan)
        write_wav(OUT / "bird" / f"{i:02d}.wav", st, target_peak=0.65)

    # wood — 3 tap variants
    for i in range(3):
        print(f"  wood {i}")
        pan = (-0.25, 0.0, 0.25)[i]
        st = render_with_pan(voice_wood(seed=200 + i), wet=0.20, decay_s=0.6,
                             predelay_ms=8, brightness=0.5, pan=pan)
        write_wav(OUT / "wood" / f"{i:02d}.wav", st, target_peak=0.50)

    # bloom — 3 chord voicings (A-rooted majors/sixths)
    chords = [
        [57, 61, 64, 69],     # A3 C#4 E4 A4    — root position
        [57, 64, 71, 73],     # A3 E4 B4 C#5    — open fifth + 9 + 3 (bright)
        [57, 61, 66, 69],     # A3 C#4 F#4 A4   — A6 chord (warm sweet)
    ]
    for i, c in enumerate(chords):
        print(f"  bloom {i} {c}")
        st = render_with_pan(voice_bloom(c), wet=0.50, decay_s=4.0,
                             predelay_ms=50, brightness=0.45, pan=0.0)
        write_wav(OUT / "bloom" / f"{i:02d}.wav", st, target_peak=0.78)

    # cluster — 3 ascending mallet flourishes
    for i in range(3):
        print(f"  cluster {i}")
        st = render_with_pan(voice_cluster(seed=300 + i), wet=0.42, decay_s=2.5,
                             predelay_ms=30, brightness=0.6, pan=0.0)
        write_wav(OUT / "cluster" / f"{i:02d}.wav", st, target_peak=0.80)

    print("[meadow] done.")

if __name__ == "__main__":
    gen_all()
