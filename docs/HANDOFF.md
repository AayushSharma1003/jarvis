# JARVIS — Session Handoff

> Paste this into a new session to continue. It's the single source of "where we are."
> Deeper detail lives in `docs/architecture.md`, `docs/security-model.md`, and the
> Claude memory files (auto-loaded). This is the orientation layer.

## What JARVIS is

A **local-first, voice-activated AI assistant** that runs on the user's own
machine. Cross-platform desktop (macOS, Windows, Linux). Wake word ("Hey Jarvis"
/ "Hey Friday") → speak → a local LLM with tool access (web, files, shell,
clipboard, screenshots) responds in a natural voice while an animated sphere UI
reacts to the audio. Fully local by default (zero API keys, works offline after
install); cloud LLM backends are an optional toggle. Target user: developers and
power users who want an assistant they control and that can *do* things on their
computer. Pitch: Open Interpreter + movie-Jarvis UX + LM Studio polish, in one
downloadable app.

## Who you're working with / how to work

- **User = product owner; you = technical lead.** They explicitly want you to
  **push back** with honest technical judgment, not comply. Propose better when
  you see it. "Cutting scope is a valid answer."
- **Standing authorization** (memory: `standing-authorization`): you have autonomy
  to improvise improvements without asking, ranked: **1 security, 2 reliability,
  3 cross-platform consistency, 4 latency/smoothness, 5 UX, 6 code quality,
  7 community-friendliness.** Small improvements → just do + note in milestone
  summary; medium → do + explain; large (new arch pattern, scope change,
  principle tradeoff) → pause and ask.
- **NEVER without asking:** new unapproved features (small enablers OK), cutting
  agreed features, changing core stack, reversing security decisions, paid
  anything, auto-update/analytics/network-without-user-action, breaking
  cross-platform.
- **Git workflow (memory: `git-commit-block-workflow`): DO NOT run git yourself.**
  At each milestone end, emit a "📦 Milestone Commit" block (files changed,
  2–4 line summary, then a bash block with `cd`, `git add .`, `git status`,
  `git commit -m`, `git log --oneline -5`). Conventional-commit prefixes with a
  milestone tag. Warn on sensitive files. Never `push`/`rm`/`reset`/`checkout`.
  **KNOWN ISSUE:** the user's environment auto-commits the working tree *before*
  the block runs, so commit messages get lost and history is mislabeled (the
  handshake fix landed under a duplicate "walking skeleton" message). Keep
  emitting blocks anyway; it's their env, not yours to fix.
- **Timeline is sequencing, not deadlines.** User has college + research + GATE
  prep + freelance; realistic calendar ~10–14 weeks. Extra time = do it right,
  never add scope. **Zero budget** (no code signing, no paid deps, zero telemetry
  is a hard principle).
- **Hardware:** 8GB M2 Pro MacBook (PRIMARY smoothness target — polish here),
  RTX A6000 48GB Windows box via AnyDesk (pro-tier validation), Linux = CI only.
  If it's not smooth on the 8GB Mac, it's not smooth.

## Approved stack (locked unless something breaks/unmaintained)

- **Shell:** Tauri 2 (Rust). **Frontend:** React 19 + TypeScript + Tailwind v4 +
  Three.js (sphere) + zustand + i18next. **Backend:** Python 3.11+ FastAPI +
  WebSockets, managed by `uv`.
- **LLM:** Ollama for local (detected/installed, **never bundled**); adapter
  pattern for OpenAI-compatible / Anthropic. RAM-tiered model auto-select
  (≤8GB→3B, 16GB→7-8B, 32GB+→14B+).
- **Voice (Phase 2+):** wake word **openWakeWord** (ships `hey_jarvis`; "Hey
  Friday" trained once offline via `scripts/train_wake_word.py`). VAD **Silero
  (ONNX)**. STT **whisper.cpp via pywhispercpp** (Metal/CUDA/CPU — NOT
  faster-whisper, which is CPU-only on Mac). TTS **Kokoro via kokoro-onnx**.
- **ONE ML runtime story:** onnxruntime (wake/VAD/Kokoro) + whisper.cpp.
  **No PyTorch, no ctranslate2** — adding a torch-dragging dep is a regression.
- **Storage:** SQLite (immutable turn-grouped message tree, branching-ready) +
  TOML config.
- **Packaging:** Tauri sidecar + PyInstaller **onedir** (never onefile). CI
  builds installers for all 3 OSes on tag push; releases **unsigned** +
  SHA-256 checksums.

## Security model (NON-NEGOTIABLE — see docs/security-model.md)

Tool risk levels safe/ask/dangerous; **shell always confirms** (no classifier,
no denylist). Filesystem **sandboxed to user roots, symlink-resolved**.
**Taint tracking:** once untrusted content (web/unknown files) enters, every
side-effectful tool call escalates to confirmation. `web_fetch` **SSRF guards**.
Extensions declare permissions in a manifest, approved on load, live OUTSIDE the
sandbox. Backend binds 127.0.0.1 + token + Origin check. **Zero telemetry, no
phone-home** (even the model catalog refresh is manual). Clipboard is `ask`.

## What's DONE — Phase 1 (walking skeleton), COMPLETE & verified

Working, installable text-chat app end-to-end on the 8GB Mac:
- Tauri shell spawns/supervises the Python sidecar (JSON ready-line handshake,
  token via env, parent-PID watchdog, kill-on-exit).
- FastAPI WebSocket server: Origin allowlist + first-message token auth,
  streaming chat, stop/interrupt, models.list, history.
- Ollama adapter (streaming, machine-readable error CODES for i18n), RAM-tier
  model auto-select.
- **Immutable turn-grouped SQLite message tree** — branching-ready from day 1
  (branch/sibling/path tested); UI for branching comes in Phase 5.
- React chat UI: streaming, model picker, stop, reconnect-with-backoff, full
  i18n (backend emits codes, frontend translates — no hardcoded UI strings).
- `jarvis doctor` CLI (all green on the user's machine).
- CI (lint+test+check) + release matrix (3 OS → draft release + SHA256SUMS).
- **32 backend tests, 2 Rust tests, all green.** Warm TTFT 407ms / llama3.2:3b.
- **Handshake bug fixed** (see gotchas). Verified by reproducing then passing a
  `JARVIS_STARTUP_DELAY=5` slow-start run.

## Hard-won gotchas (don't rediscover these)

1. **Tauri 2 capabilities:** `app/src-tauri/capabilities/default.json`
   (`core:default`) is REQUIRED or the webview gets zero IPC permissions and
   `event.listen` is silently denied → "Backend didn't start in time". Handshake
   is now listen + 1s-poll so events are never a single point of failure.
2. **SQLite** opened with `check_same_thread=False` (serialized mode); default
   thread-binding breaks under the async server / test client.
3. **React StrictMode** double-mounts effects → guard `init()` synchronously or
   you open two WebSockets and double-apply deltas.
4. **PyInstaller onedir, never onefile** (slow start, orphaned procs).
5. **Debugging the handshake:** `JARVIS_DEBUG=1` echoes raw sidecar stdout; all
   steps log `[sidecar]`/`[frontend]` to the `tauri dev` terminal (webview
   console is invisible there — that's why the original bug was silent).

## Repo map

```
app/            Tauri 2 shell (src-tauri/) + React frontend (src/)
                capabilities/default.json ← the handshake fix, don't delete
backend/        Python sidecar (jarvis_backend/: server audio wake stt tts llm
                agent tools security extensions storage doctor)
extensions/     default set: timers-reminders (x-platform ref), calendar-macos
docs/           architecture.md, security-model.md, extensions.md, latency.md,
                unsigned-install.md, HANDOFF.md (this), design/sphere.md
docs/design/sphere-refs/  the sphere UI reference images (gif + avif)
scripts/        install.sh/.ps1, build_sidecar.py + sidecar.spec, fetch_models.py,
                train_wake_word.py
catalog/models.toml   curated model catalog (bundled data, manual refresh)
```

## Phase plan (sequencing, not deadlines)

1. ✅ **Walking skeleton** — DONE.
2. ⏭️ **Voice loop (NEXT)** — push-to-talk hotkey → Silero VAD endpointing →
   whisper.cpp streaming STT → LLM → sentence-chunked Kokoro TTS playback, with
   latency instrumentation wired into `jarvis doctor --latency`. Adds
   onnxruntime + a ~200MB dev model fetch (`scripts/fetch_models.py`); first
   real macOS mic-permission flow. Metric: end-of-speech→first-audio <1.5s (8GB).
3. **Always-on + feel** — openWakeWord always-on (<3% CPU), wake-word barge-in,
   **the sphere UI** (4 states, audio-reactive — see docs/design/sphere.md),
   RAM tiering, onboarding v1.
4. **Agency + security** — permission engine + taint + sandbox, tools ship WITH
   their security layer, extension loader + approval gate.
5. **Extended scope** — branching UI, `jarvis install <url>`, model catalog UI,
   default extensions, wake-word training + "Hey Friday", opt-in VAD barge-in.
6. **Ship** — installers, onboarding polish, docs, tagged unsigned release.
- **Post-v1:** AEC milestone (macOS Voice Processing AU then WebRTC AEC3), voice
  cloning TTS eval (Chatterbox-Turbo tier), auto-update (blocked on signing).

## Barge-in tiers (approved)

v1 default = wake-word + hotkey interrupt (no AEC needed). v1 opt-in = full VAD
barge-in with a headphones/beamforming-mic warning. Proper AEC = post-v1
milestone, doesn't block v1.

## Sphere UI (Phase 3, user-supplied references)

Glass sphere on near-black with a cyan→purple audio-reactive particle waveform
inside, Fresnel rim glow, bloom, floor reflection. Full target + Three.js plan:
**docs/design/sphere.md**; images in **docs/design/sphere-refs/**. Mandatory
canvas-2D fallback for flaky Linux WebGL. Smoothness on 8GB > fidelity.

## Dev commands

```sh
cd backend && uv sync && uv run pytest && uv run jarvis doctor
cd app && npm install && npm run tauri dev      # full app (debug runs backend via uv)
# frontend-only: start backend with JARVIS_WS_TOKEN=x JARVIS_PORT=8765 uv run jarvis-backend,
#   then VITE_JARVIS_PORT=8765 VITE_JARVIS_TOKEN=x npm run dev
# Rust: export PATH="/opt/homebrew/opt/rustup/bin:$PATH" first (rustup via brew)
```

## Immediate next action

Start **Phase 2, voice loop**, at the highest-risk piece first (whisper.cpp via
pywhispercpp working on Apple Silicon with Metal, and the sidecar audio capture
path). Confirm the plan with the user, then build STT → VAD → playback
incrementally, keeping the text-chat path working throughout.
