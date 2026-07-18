# The Sphere UI

The signature visual: an audio-reactive orb that reacts to Jarvis's voice, like
Siri / the movie Jarvis. Reference images live in [sphere-refs/](sphere-refs/)
(`sphere-speaking.gif` is the animated target; `.avif` is a still).

## The visual target (from the user's references)

A **translucent glass sphere**, centered on a near-black navy background
(~`#0d0d16`), with a soft **floor reflection** beneath it. Suspended *inside*
the glass is a flowing, undulating **particle waveform** — a 3D ribbon/surface
of fine dotted lines that ripples like an audio frequency spectrum wrapped in
space. The waveform is a **cyan → blue → purple/magenta gradient**
(~`#22d3ee` → `#3b82f6` → `#a855f7`). The glass has bright **Fresnel rim
lighting** (cyan/purple edge glow), overall **bloom**, and faint floating
**sparkle particles** inside. The reference shows the *speaking/active* state:
high-amplitude, energetic motion.

## The four states (product decision)

| State | Motion |
|---|---|
| **idle** | slow breathing scale, low-amplitude drift, dimmer |
| **listening** | gentle upward pull, particles converge, brightening |
| **thinking** | slow swirl / orbit, no audio coupling |
| **speaking** | the reference: waveform amplitude driven by live TTS audio levels |

## Implementation approach (Phase 3 — not built yet)

- **Glass sphere**: `MeshPhysicalMaterial` with `transmission: 1`, low
  `roughness`, `thickness`, `iridescence` for the rim sheen. One directional +
  rim light for the Fresnel edge.
- **Inner waveform**: a `Points` system (or displaced lat/long mesh) whose
  vertices are displaced by a noise field; amplitude = audio level. Vertex
  colors carry the cyan→purple gradient. This is the audio-reactive core.
- **Audio coupling**: [`useAudioLevels.ts`](../../app/src/components/sphere/useAudioLevels.ts)
  (stubbed) exposes a smoothed 0–1 level from the TTS playback stream; the
  sphere reads it per-frame. For input (listening), the same hook can tap mic RMS.
- **Bloom + reflection**: `postprocessing` `UnrealBloomPass`; a mirrored plane
  or a simple radial-gradient sprite for the floor glow.
- **THE HARD CONSTRAINT**: WebKitGTK on Linux has flaky WebGL, and this must run
  smoothly on an **8GB M2 Pro**. [`SphereFallback2D.tsx`](../../app/src/components/sphere/SphereFallback2D.tsx)
  (a canvas-2D waveform) is **mandatory**, not optional — detect WebGL/perf and
  fall back. Keep the particle count tuned for the 8GB target; if the sphere
  costs latency or frames, simplify it. Smoothness > fidelity.

## Reuse note

Consider the `threejs-webgl` / `lightweight-3d-effects` skills available in this
environment when building this — they cover transmission materials, bloom, and
particle systems. Vanta.js-style backgrounds are NOT the right tool (the effect
is a foreground object, not a background).
