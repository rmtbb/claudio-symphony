# Claudio Symphony — Sonic Framework

A composer's brief for an ambient generative system that sonifies the moment-to-moment life of Claude Code sessions. The performers are stochastic: tool calls, agent stops, file edits, bash spawns, sub-agent births and deaths. Our job is to build an instrument and a tuning system such that *no matter what they play*, the room sounds beautiful.

---

## 1. Sonic Identity / Aesthetic Direction

**The chosen lane: deep modal drone bed + soft Rhodes-like plucks + tuned metal bell harmonics + breathy granular texture, all tuned to a single fixed key, processed through a long convolution reverb (a stone hall or cathedral impulse).**

Imagine the slow rotation of Eno's *Music for Airports 1/1*, the bell-tone economy of Harold Budd's *The Pearl*, and the air-around-the-notes of Nils Frahm's *Felt*. Add a touch of Buchla West Coast bell tone — inharmonic but tuned, with bloom rather than attack. Everything bathed in a long pre-delayed reverb so individual events smear into a continuous wash.

This works for hours because:

- **No rhythmic grid.** Events arrive when they arrive. The ear stops trying to predict downbeats and settles into landscape mode.
- **Long release tails (3–8 seconds).** Density manifests as *thickness* of pad, not as a stack of discrete hits.
- **Single tonal center.** No modulations, no surprises. The brain can let go.
- **No clear melodic hook.** Memorability is the enemy of background music. We want texture, not tunes.

**Alternatives considered:**

- *Granular field recording (wind, water, distant traffic)* — Beautiful but lacks event-articulation. You couldn't tell a tool-call from an error. Rejected: the events need to be perceptible enough that the developer subconsciously parses them.
- *Plucked koto / kalimba in pentatonic* — Very pleasant, but the attack transients are too defined. Rapid-fire events would feel like raindrops on a tin roof — pretty for 4 minutes, exhausting at hour 3. Rejected.
- *Pure sine-wave drones with no events* — Too static. The developer wants ambient *feedback*, not just ambient sound. The whole point is that the music breathes with the work. Rejected.

The chosen lane sits between these: enough articulation to feel alive, enough wash to disappear.

---

## 2. Harmonic / Tonal Foundation

**Scale: A Lydian, drone-anchored on A, with selective use of the natural overtone series above A1.**

Pitch set (concert pitch, A = 440 Hz reference, but we tune the root to **A = 432 Hz** for a slightly softer, less-bright character that suits long listening):

```
A1   (root drone, ~54.0 Hz at 432-tuning)
A2   (octave drone)
E3   (perfect fifth, sub-bass anchor)
A3
B3   (Lydian color: 2nd)
C#4
D#4  (the Lydian #4 — the magic note)
E4
F#4
G#4
A4
B4
C#5
D#5
E5
F#5
A5
C#6  (high bell ceiling)
E6
```

**Why Lydian and not pentatonic or just-intonation drone?**

Pentatonic was the obvious safe choice — every note pleasant against every other. But pentatonic in random combinations sounds *too* familiar, almost wind-chime cliché. Lydian retains pentatonic's consonance (no minor 2nds, no tritone *against the root*) while adding the #4, which gives an open, suspended, slightly mysterious quality. Think of it as pentatonic with two extra colors that don't clash.

**Consonance guarantee:** Every interval in A Lydian against the A drone is consonant or pleasantly tense (the #4 sounds shimmery, not dissonant, when the root is droning under it). There are no half-step clashes against the tonic. Random subsets of these notes voiced simultaneously produce stacked-fourths and open-fifth voicings — the exact harmonic territory that ECM-era jazz and ambient music live in.

**Tuning detail:** The drone layer (A1, A2, E3) uses **strict 3:2 just-intonation** between root and fifth. The upper voices are equal-tempered for practical sample-library reasons, but the most prominent low overtones are pure, which gives the bed a "sitting" quality that equal-tempered drones lack.

---

## 3. Sound Categories → Event Categories

Eight voices. Listed from *most prominent / rarest events* to *least prominent / highest-frequency events*. The cardinal rule: **rarer events get the foreground; common events whisper.**

| # | Voice | Character | Mapped event class | Rough frequency budget |
|---|---|---|---|---|
| 1 | **Sub Drone** | A1 fundamental, evolves over minutes | Session start / overall "presence" — always audible, breathing | Continuous |
| 2 | **Low Pad Swell** | Slow attack (4s), filtered saw + sine | Session-level: agent stop, long task complete | 1 every few minutes |
| 3 | **Tuned Bell** | Inharmonic FM bell, long decay | Errors, sub-agent spawned, significant milestones | 1–5 per minute |
| 4 | **Soft Pluck** | Damped Rhodes / felt-piano single note | Tool call *completion* (the satisfying resolve) | 5–30 per minute |
| 5 | **Mid Harmonic** | Bowed-glass / soft mallet | File edits / writes | 2–10 per minute |
| 6 | **Air / Breath** | Granular noise wash, tuned | Bash commands, shell activity | High frequency, blends |
| 7 | **High Shimmer** | Tiny bell-like grains, top octave | Tool call *starting* (faint, anticipatory) | Very high frequency |
| 8 | **Sparkle** | Brief upper-partial ping | Rare/special: PR created, deploy, etc. | A few per session |

**Reasoning on the mapping:**

- *Tool starts* are the highest-frequency event. They get the airiest, most disposable voice (High Shimmer) — almost subliminal, like dust in light. Five in three seconds should sound like one breath.
- *Tool completes* are also frequent but carry more meaning ("something finished"). They get the Soft Pluck — the most musical voice — but with strict rate-limiting (see §4) and pitch round-robin so consecutive plucks form a slow arpeggio rather than a machine-gun.
- *Errors* are rare and matter. They get the Tuned Bell — clearly audible, but tuned to the scale, never alarming.
- *Drone* is always-on. The session itself is the ground.

This inverse-frequency-to-prominence mapping is the single most important decision in the framework. Get this wrong and the system becomes either too noisy or too quiet to feel responsive.

---

## 4. Temporal / Rhythmic Behavior

The "machine gun" problem is the central engineering challenge. Twenty tool calls in three seconds must sound like *one gentle gust*, not twenty hammer hits.

**Strategies, layered:**

1. **Per-voice minimum inter-onset interval (MIOI).** Each voice has a floor below which it refuses to retrigger:
   - Sub Drone: continuous (n/a)
   - Low Pad Swell: 8 s
   - Tuned Bell: 4 s
   - Soft Pluck: 600 ms
   - Mid Harmonic: 400 ms
   - Air/Breath: 80 ms (but see grain accumulation below)
   - High Shimmer: 50 ms
   - Sparkle: 30 s

2. **Event swallowing within MIOI.** If an event arrives during cooldown, it is *not queued* — it is dropped, but it increments a "pressure" counter that subtly raises the next trigger's amplitude (up to +3 dB) and brightness. This converts density into *intensity of the next note* rather than a queue of pent-up hits.

3. **Voice stealing with crossfade.** Polyphony cap of 6 simultaneous Soft Plucks; oldest voice fades out over 200 ms when stolen. Never a click.

4. **Long attack on the bigger voices.** Low Pad Swell has a 3–4 second attack — by definition cannot machine-gun. Bell has 15 ms attack but 6 s decay — the decays overlap into a wash.

5. **Granular accumulation for Air/Breath.** Instead of triggering a sample per bash command, each event injects a *grain* into a continuously-running granular cloud. 50 grains/sec sounds like wind, not 50 separate sounds.

6. **No quantize-to-grid.** Events fire on arrival. Quantization would impose a tempo, and since real event timing is arrhythmic, quantizing would feel either laggy (snap to next 1/8) or robotic. Free time is correct here.

7. **Reverb-tail-as-glue.** A 6-second convolution reverb on every voice except Sub Drone. Dense events smear into the tail. Sparse events feel held in space. Same patch handles both.

8. **Repeat suppression.** If the same event class fires within 200 ms of itself with the same pitch, the second one is dropped or pitch-shifted to the next scale degree. Prevents two identical bell strikes.

---

## 5. Spatial / Mix Design

**Target loudness: −23 LUFS integrated, true peak −6 dBTP.**

This is *quieter than music streaming services* by ~9 LU. It must sit *under* the developer's typing, podcast, or spoken thought. If they notice it, we've lost.

**Stereo placement (static per voice, not panning around):**

| Voice | Pan | Reverb send | Notes |
|---|---|---|---|
| Sub Drone | Center, mono-summed below 120 Hz | Light (dry-ish) | Anchor |
| Low Pad Swell | Wide stereo (Haas-spread) | Heavy | Engulfs |
| Tuned Bell | Slight L (−15) | Heavy, long pre-delay 80 ms | Distinct |
| Soft Pluck | Round-robin pan ±25 with each note | Medium | Movement |
| Mid Harmonic | Slight R (+15) | Medium | Distinct from pluck |
| Air/Breath | Wide stereo, decorrelated grains | Medium | Atmosphere |
| High Shimmer | Wide stereo, random ±60 per grain | Heavy | Sparkle dust |
| Sparkle | Center | Heavy with shimmer reverb | Special |

**Bus structure:**
- All voices → a single ambient bus → soft bus compressor (2:1, slow attack, slow release, gentle 1–2 dB GR on peaks) → −18 dBFS peak limiter as safety.
- Convolution reverb on a parallel send (cathedral or concert hall IR, 6s decay, low-cut at 200 Hz on the return so the wash doesn't muddy the drone).
- High-shelf cut at −2 dB above 8 kHz to remove any digital sheen.

The compressor is critical: under chaos (20 events/sec) it gently ducks the whole bed by 1–2 dB so the bus never gets loud, even if voice count spikes. The developer's attention is protected.

---

## 6. Variation & Non-Repetition

The enemy of a 6-hour listen is recognition. Every layer must drift.

- **Pitch round-robin within scale.** Soft Pluck cycles through a shuffled bag of scale degrees, refilling when empty. Never plays the same note twice in a row, almost never the same three-note shape twice in a session.
- **Sample round-robin.** 8 velocity-layers × 4 round-robin samples per voice (32 samples per pitch, minimum). No two consecutive plucks sound mechanically identical.
- **Slow filter LFO on Sub Drone.** A 90-second sine sweeps the cutoff between 400 Hz and 1.2 kHz. Imperceptible per-second, obvious over an hour.
- **Pad chord rotation.** Low Pad Swell picks from 4 voicings (A-E-B, A-E-F#, A-C#-E, A-D#-F#-A) round-robin with shuffling.
- **Time-of-day shading.** Morning (before 11): brighter EQ, more shimmer; afternoon: neutral; evening: low-pass slowly closes 1 kHz over the afternoon, warmer; night: drone drops to A0 octave below, bells become rarer.
- **Session "movements."** Every ~25 minutes, the system silently advances through 4 sub-modes: *Calm* (sparse), *Working* (full palette), *Busy* (granular emphasis, less pluck), *Wind-down* (fade to drone + occasional bell). Movements rotate based on event density, not clock — so the music actually reflects the rhythm of work.
- **Drift in inharmonicity of bells.** Each bell strike has a ±2% random detuning of upper partials, never the fundamental. Keeps bells alive without being out of tune.

---

## 7. Synthesis / Sample Sourcing Recommendations

Pragmatic, in implementation order:

**Drone & Pad (synthesis, real-time):** Use Tone.js (browser) or a small Rust/Go DSP layer with `oscillator → low-pass → reverb send`. Two detuned saws + sine fundamental, 6 s attack, no release. This is cheap and sounds great. Avoid sample-based drones — they loop audibly.

**Soft Pluck:** Sampled. Best sources:
- Spitfire LABS *Soft Piano* (free) — felt-muted upright, exactly the right character. Convert to 32 SFZ-mapped samples.
- Orchestral Tools *Layers* (free) — has pad-pluck hybrids.
- Or: record a real felt piano yourself, single notes A1–C7, 4 dynamics, 4 round-robins.

**Tuned Bell:** Synthesis preferred. 6-operator FM bell (DX7 algorithm 5 patch territory) or two-operator FM in Tone.js with a carrier:modulator ratio of 1:1.41 (inharmonic), modulation index decaying from 8 to 0 over 4 seconds. Alternatively, sample a real handpan or singing bowl tuned to A; Freesound has CC0 handpan recordings tuned to D and F — pitch-shift to A.

**Mid Harmonic:** Bowed crotales or glass-harmonica samples. Spitfire LABS *Glass & Steel* is gold here. Free.

**Air/Breath (granular):** Record 30 seconds of your own quiet exhale, or use Freesound CC0 "wind through grass" recordings. Run through a granular engine (Tone.js `GrainPlayer`, or `sfizz` with grain extension). 50–200 ms grain size, randomized position, pitched to scale degrees by playback rate.

**High Shimmer:** Tiny FM bell grains, 100 ms each, pitched to top-octave Lydian notes, heavy reverb. Pure synthesis. Or use Spitfire LABS *Music Box*.

**Convolution Reverb IR:** Look for "St. Andrew's Church" or "Hamilton Mausoleum" IRs (CC-licensed on OpenAIR Lib, openairlib.net). 6-second tails. Critical to the whole sound.

**Engine choices:**
- *In a desktop app:* SuperCollider, or a small Rust DSP using `fundsp` / `cpal`, or embed `sfizz` for SFZ playback.
- *In a web/Electron context:* Tone.js + a few SFZ files served as audio buffers. Tone.js handles polyphony, scheduling, and reverb adequately.
- *Quickest path:* Use Pure Data or a small Reaper project driven by OSC from a tiny event-listener daemon.

---

## 8. Starter Score (Prescriptive Mapping)

Implementer's seed. Each row: `event → voice → pitch behavior → envelope (A/D/S/R) → effects/notes`.

| Event | Voice | Pitch behavior | Envelope (s) | Effects / Notes |
|---|---|---|---|---|
| Session start | Sub Drone (fade in) | A1 + A2 + E3 just-intoned | A 12, R ∞ | Light reverb; runs for entire session |
| Session end | Sub Drone (fade out) | hold then fade | R 20 | Slow ritardando of pad, no new triggers |
| Tool call start | High Shimmer | random from {A5, C#6, E6, F#6}, weighted toward A | A 0.005, D 0.4, S 0, R 0.6 | Heavy reverb; pan random ±60; MIOI 50 ms; pressure→amp |
| Tool call complete | Soft Pluck | round-robin bag of A Lydian, octaves 3–5 | A 0.02, D 1.5, S 0, R 3.0 | Medium reverb; pan round-robin ±25; MIOI 600 ms |
| File edit / write | Mid Harmonic | round-robin from {A4, B4, C#5, E5, F#5} | A 0.2, D 2.0, S 0.3, R 4.0 | Medium reverb; slight R pan; MIOI 400 ms |
| Bash command exec | Air/Breath grain | grain pitched to random scale degree A3–A4 | grain 200 ms, internal envelope | Continuous granular cloud; new event = inject 1–3 grains |
| Sub-agent spawned | Tuned Bell | A4 with detuned upper partials | A 0.015, D 6.0, S 0, R 6.0 | Heavy reverb, pre-delay 80 ms; MIOI 4 s |
| Error / failure | Tuned Bell (low) | E3 (fifth, not root — feels grounded, not alarming) | A 0.02, D 8.0, S 0, R 8.0 | Heavy reverb; slight low-pass to soften |
| Long task complete (>30s) | Low Pad Swell | A-E-B voicing, octave above current drone | A 4.0, D 0, S 1.0, R 8.0 | Heavy reverb; wide Haas spread |
| Agent stop (idle) | Low Pad Swell | A-C#-E voicing | A 6.0, D 0, S 0.6, R 12.0 | Slowly fades; signals "rest" |
| PR created / deploy | Sparkle | A5 + C#6 + E6 cluster, slightly arpeggiated over 1.5s | A 0.01, D 4.0, S 0, R 6.0 | Shimmer reverb; rare event, MIOI 30 s |
| Movement transition (every ~25 min) | Pad chord rotation | next voicing in cycle | A 8.0, R 15.0 | Internal, not event-driven |

---

## 9. What to AVOID

A list of anti-patterns, each one of which would individually ruin the project:

- **No notification dings.** No bright sine-with-fast-decay. No iOS/Slack/email-app DNA anywhere. The moment it sounds like a notification, the developer's nervous system flags every event as a possible interruption.
- **No sharp transients.** Every voice has at least 5 ms attack, most have 15–200 ms. Click-free is non-negotiable.
- **No notes outside A Lydian.** Ever. Every random pitch must be drawn from the defined pitch set. One stray note breaks the spell.
- **No clear melodic hooks.** If you can hum it after one listen, it's wrong. Round-robin and randomization within the scale should produce *no* repeating motif over 5+ minutes.
- **No tempo.** No metronome, no quantize, no rhythmic loops. Events arrive when they arrive. Imposing a grid would feel either lagged or robotic.
- **No vocal samples.** The brain attends to voices irresistibly. Even wordless humans-singing samples will pull focus.
- **No stereo panning automation per note (autopan).** Static placement only. Movement comes from stereo decorrelation in reverb, not from panning sweeps.
- **No sidechain ducking to events.** It will feel pumpy. Use the gentle bus compressor instead.
- **No build-ups, no drops, no swells timed to milestones.** This is not film scoring. It is weather. It does not know what you are about to do.
- **No "achievement" sounds.** The temptation to reward a successful deploy with a major-key triad is strong. Resist. The Sparkle voice is the maximum permissible celebration, and even it must stay in scale.
- **Avoid ultra-low (<40 Hz) and ultra-high (>12 kHz) energy.** The first muddies on laptop speakers; the second causes listening fatigue. Keep the spectrum sitting between 60 Hz and 8 kHz with gentle rolloffs.
- **Do not make the bell the loudest voice.** Tempting because errors feel important. But a loud bell every error will train the developer to flinch. Keep it audible but soft — the *novelty* of the timbre, not the volume, carries the meaning.

---

## Closing Note

The implementer's job is not to make this *interesting*. Interesting music demands attention. Our job is to make a sonic environment that the developer's auditory system gradually stops parsing as music and starts parsing as *room*. The room then breathes with their work. They notice it the way they notice rain: only when it stops, and only with a small sense of loss.

Tune the drone first. Get the reverb right. Add the pluck. Everything else is decoration on those three foundations.

— *Composer's score, Claudio Symphony, May 2026*
