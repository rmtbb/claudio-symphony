# Claudio Symphony 🎼

> Your Claude Code sessions, scored.

```
                    ╭─────────────────────────────────────╮
                    │                                     │
                    │    every bash command is a breath   │
                    │    every tool call is a soft pluck  │
                    │    every sub-agent finishing,       │
                    │    a tuned bell from across         │
                    │    the cathedral                    │
                    │                                     │
                    ╰─────────────────────────────────────╯
```

A small Python program that listens to Claude Code's hook events and turns them into ambient music. Twenty tool calls in three seconds will sound like one gust of wind, not twenty hammer hits — because that's the difference between music and Slack notifications.

It is not a notification system. It is a room your Claude session is happening inside.

---

## Listen first

Four presets ship in the box. **`meadow` is the default** — bright, friendly, no drone. Pick the room you want to be in; switch any time.

### 🌳  `meadow` — the happy one *(default)*
> *Wooden felt-mallets and kalimba in a sunlit room. A major pentatonic — universal happy mode, no possible dissonance.*

Bright. Satisfying. Defined-but-soft attacks. The mallet "thock" of felt on wood. Kalimba tines for file edits. A small bird-chirp for shell commands. A 9-second major-triad bloom when Claude finishes. Like a music box in a sunny room.

### 🏛️  `cathedral` — the lush one
> *Modal drone bed with a full event palette. Eight voices. Always-on. The Eno / Budd / Frahm lane.*

Low drone never stops. Soft felt-piano notes resolve to themselves as tools complete. Faint air-hisses pass by during bash commands. A tuned bell when a sub-agent finishes. Nothing has a melody. Nothing repeats. The room breathes with the work.

### 🌧️  `rainfall` — the quiet one
> *Sparse drops in a quiet room with rare long swells. Silence is the canvas.*

Most of the time you hear nothing. PostToolUse plays a tiny 250 ms drop — like a single bead of water meeting a still pond. Once every minute or two, a 25-second pad blooms in the distance and recedes. Late-night, focused-work, almost-silent.

### 🎋  `koto` — the contemplative one
> *Plucked silk strings and a temple bowl in the A In Sen scale. Spare. Like working through a tea ceremony.*

A different emotional space from the A-major family above — minor-leaning, deliberate, Japanese. Three voices: koto for melody, bowl for sustains, mokugyo (wooden fish) for percussive accents.

The first three sit in the same A-rooted tonal family, so switching between them mid-session is musically continuous. Switch live with one command.

---

## What you'll hear

| When this happens... | cathedral | meadow | rainfall |
|---|---|---|---|
| Claude starts a tool | airy shimmer | tiny wood tap | (silent) |
| Bash command starts | wind hiss | bird chirp | (silent) |
| File edit completes | bowed glass | kalimba pluck | tiny drop |
| Tool finishes | felt pluck | felt mallet | tiny drop |
| Sub-agent returns | tuned bell | bright chime | small bell |
| Claude finishes a turn | slow pad swell | major-triad bloom | 25-sec swell (rare) |
| Something errored | low E bell | low chime | small bell |
| You start a new session | sparkle cluster | music-box flourish | swell |

All in scale. All consonant. All baked in synthetic-IR convolution reverb.

---

## Install

```bash
git clone https://github.com/rmtbb/claudio-symphony.git
cd claudio-symphony
python3 install.py            # deps + render samples + write starter config
./bin/claudio install         # adds hooks to ~/.claude/settings.json
```

Open a new Claude Code session and listen. That's it.

**Requirements:**
- macOS (uses `afplay` — Linux/Windows support is one small patch in `event.py`, PRs welcome)
- Python 3.9+ with **numpy**
- ~250 MB free for generated samples (one-time render, then static)

---

## Use it

```bash
claudio status                          # what's installed and active
claudio preset list                     # see all available presets
claudio preset use cathedral            # switch live (no Claude restart)
claudio preset use default              # back to the shipped default (meadow)
claudio preset reset meadow             # restore one preset to as-shipped
claudio reset --yes                     # full reset to fresh-install state
claudio off / claudio on                # silence everything / restore
claudio tune                            # interactive TUI for tweaking everything
claudio test                            # walk every voice in the active preset
claudio volume 0.4                      # master gain for all triggered events
claudio start / claudio stop            # drone daemon (cathedral only for now)
```

### `claudio tune` — the interactive tuner

```
─────────────── claudio tune ─────────────────────────────────
 preset: meadow   master: ▰▰▰▰▰▱▱▱▱▱ 0.55              ✓ saved
─ Voices — [gain]  press t for mioi ──────────────────────────
 ▶ mallet     gain ▰▰▰▰▰▱▱▱ 0.55  mioi ▰▰▱▱ 0.35s
   kalimba    gain ▰▰▰▰▰▱▱▱ 0.50  mioi ▰▰▱▱ 0.30s
   chime      gain ▰▰▰▰▱▱▱▱ 0.45  mioi ▰▰▰▱ 3.00s
   bird       gain ▰▰▰▱▱▱▱▱ 0.40  mioi ▰▰▱▱ 0.50s
   wood       gain ▰▰▰▱▱▱▱▱ 0.30  mioi ▰▱▱▱ 0.08s
   bloom      gain ▰▰▰▰▰▱▱▱ 0.55  mioi ▰▰▰▰ 10.00s
   cluster    gain ▰▰▰▰▰▱▱▱ 0.55  mioi ▰▰▰▰ 30.00s
─ Events ─────────────────────────────────────────────────────
   PreToolUse              → wood
       · Bash              → bird
   PostToolUse             → mallet
       · Edit              → kalimba
       · on_failure        → chime
   Stop                    → bloom
   ...
─ Sessions (last 4h) ─────────────────────────────────────────
   33159931        meadow      2m  (default)  /Users/.../BBWiki
   ab12cd34        cathedral   5m  (rule)     /Users/.../shipping-app
─ TAB pane │ ↑↓ row │ ←→ value │ SPC play │ s save │ q ──────
```

Move sliders with arrow keys, SPACE to preview, `m` to mute, `s` to save, `q` to save+quit. Changes are live — the next event picks them up without restarting Claude.

### Per-session routing — different presets per terminal

Three lookup tiers, in order:

1. **Session pin** (`claudio session pin <id> <preset>`) — that one terminal, no matter what
2. **CWD rule** (`claudio rule add /Users/me/quiet rainfall`) — every future session in that dir
3. **Global default** (`claudio preset use meadow`) — the catch-all

```bash
claudio sessions
#  #  sid       pin  preset      ago    src       cwd
#  1  33159931       meadow      0m     default   /Users/me/Projects/shipping-app
#  2  ab12cd34  📌    rainfall    3m     pin       /Users/me/Projects/quiet-stuff

claudio rule add "/Users/me/Projects/quiet*" rainfall
claudio session pin 1 cathedral
claudio here meadow      # add a rule for the current cwd
```

### Power-user CLI

```bash
claudio voice mallet gain 0.45            # change a voice's gain in active preset
claudio voice mallet mioi 0.5             # rate-limit (min seconds between hits)
claudio voice mallet play                 # preview at current settings
claudio map PostToolUse:Edit pluck        # remap by tool
claudio map PostToolUse:on_failure -      # silence the failure variant
claudio mute UserPromptSubmit             # silence an event
claudio unmute UserPromptSubmit wood      # restore (defaults to first voice)
```

All changes write back to `presets/<active>/preset.json` and take effect on the next event.

---

## Make your own preset

Each preset is three things:

```
presets/<your-preset>/
├── preset.json    # voice configs + event → voice mapping
├── render.py      # generates the WAV samples
└── samples/       # output (gitignored — generated by render.py)
```

`preset.json` is straightforward — voice names, MIOIs, event mappings. `render.py` imports the shared DSP helpers from the top-level `synth.py` (FFT-convolved reverb, ADSR envelopes, FFT lowpass, sample writers, A=432 frequency math) and writes WAV files into `samples/<voice>/`.

There's a [composer's brief](docs/SONIC_FRAMEWORK.md) covering scale choices, palette design, the anti-machine-gun strategies (per-voice MIOI, pressure accumulators, voice stealing, granular accumulation, reverb-as-glue), and a starter mapping table you can crib from.

If you build something cool, open a PR.

---

## How it actually works

```
Claude Code event
       │
       │  hook fires (async, timeout 1s — never blocks the agent)
       ▼
~/.claude/settings.json
       │
       │  invokes:
       ▼
event.py  (~50ms total)
       │
       │  ┌─ reads JSON from stdin
       │  ├─ resolves preset:  pin → cwd-rule → default
       │  ├─ maps event → voice via preset.json
       │  ├─ checks per-voice MIOI lockfile (drops the event if too soon,
       │  │   accumulates "pressure" that bumps the next note's amplitude)
       │  ├─ picks a sample (round-robin across pitches in scale)
       │  └─ launches `afplay` detached
       ▼
   (your speakers)
```

`event.py` is stateless except for tiny per-voice last-fired timestamp files. `drone.py` is the only long-running process and it auto-exits after 10 minutes of no event activity. There's no daemon to manage, no port to conflict with anything, no central config server.

The whole thing fits in ~1500 lines of Python with one external dep (numpy).

---

## The composer's notes

Three principles that shaped every design choice:

**1. Inverse-frequency-to-prominence.** Rare events get the foreground; common events whisper. The session-end pad swell is loud-feeling because it happens once every few minutes. Tool starts get the airiest, most disposable voice because they happen 5–30 times per minute. Get this wrong and the system is either too noisy or too quiet to feel responsive.

**2. No tempo, no melody, no notification DNA.** The ear stops trying to predict downbeats and settles into landscape mode. No memorable hook means no annoyance after hour 3. No bright sine-with-fast-decay anywhere — the moment it sounds like a notification, the developer's nervous system flags every event as a possible interruption.

**3. The reverb tail is the glue.** A 6-second tail on every voice means dense events smear into a wash rather than stack as discrete hits. Same patch handles the gentle session and the chaotic session — the wash gets thicker, not louder.

Read [the full composer's brief](docs/SONIC_FRAMEWORK.md) — it's worth your time even if you never touch the code.

---

## Why?

Slack pings condition you to flinch. A cathedral does not. A bell that's tuned to the room you're working in becomes a fact about the room, not an interruption. The hope is that after twenty minutes you stop parsing the sound as music and start parsing it as *room* — and the room breathes with your work.

---

## Credits

- The two design briefs in [`docs/`](docs/) — `SONIC_FRAMEWORK.md` and `TRIGGER_SURFACE.md` — were drafted by Claude in a single session as the spec for this project. They're worth reading on their own.
- DSP synthesis: pure numpy, no scipy, no external audio libraries. Synthetic-IR FFT convolution for the reverb.
- Inspired by Brian Eno (*Music for Airports*), Harold Budd (*The Pearl*), Stars of the Lid, Nils Frahm (*Felt*), William Basinski, Susumu Yokota.
- Tuning: A = 432 Hz throughout. Drone fundamentals are strictly just-intoned (3:2 root-to-fifth); upper voices are equal-tempered.

---

## License

MIT. Do whatever you want with it. If you build a preset you love, we'd love to see it.

---

```
   tune the drone first
   get the reverb right
   add the pluck
   everything else is decoration on those three foundations
```
