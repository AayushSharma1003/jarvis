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
6. **CPU% lies on Apple Silicon:** a mostly-idle background thread runs on
   efficiency cores at ~1/3 clock, so the same work reads ~3× the CPU% you
   measured in a hot benchmark loop. Budget always-on work against *measured
   idle* numbers (`ps -o cputime` deltas), not hot-loop math. Also: per-chunk
   `asyncio.to_thread` at 30 Hz costs several % — the wake worker is one
   long-lived thread for this reason.
7. **You can voice-test without a human:** synthesize with Kokoro, `afplay`
   through the speakers, and the real mic hears it — full wake→STT→LLM→TTS
   and barge-in verified this way. Latency numbers need a quiet machine (dev
   servers running inflate 1.3s → 1.8s).

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
2. ✅ **Voice loop** — DONE (pending a live mic test by the user). Mic button/⌘M
   → backend capture → Silero endpointing → whisper (Metal) at endpoint (NOT
   streaming STT — measured unnecessary at 140ms/utterance) → LLM (voice-mode
   prompt: short openers) → clause-chunked Kokoro fp32 → playback w/ barge-in
   stop. `jarvis doctor --latency` measures **1.17–1.41s** end-of-speech→
   first-audio vs the 1.5s budget on the 8GB M2. ~500MB model fetch
   (`scripts/fetch_models.py`, sha256-pinned). 61 backend tests; voice
   orchestration tested over WS with fake hardware (`VoiceIO` boundary).
   Gotchas that cost time: int8 Kokoro is 2.4× slower than fp32 on Apple
   Silicon; CoreML EP fragments the graph (don't); waiting for a full first
   sentence blew the budget (3.92s) before clause/word-cap chunking.
   NSMicrophoneUsageDescription lives in app/src-tauri/Info.plist.
3. **Always-on + feel** — IN PROGRESS. ✅ **M3.1 wake word DONE** (2026-07-18):
   always-on "Hey Jarvis" at **2.4% idle CPU** (budget <3%), persistent UI
   toggle (state.toml), wake-word barge-in (interrupts playback instantly),
   verified acoustically E2E (speaker→mic: wake → question → spoken reply →
   barge-in mid-speech). The openWakeWord chain is **vendored** in
   wake/detector.py (3 onnx sessions; bit-exact parity vs the reference lib)
   — the pip package would drag scipy/sklearn into the bundle. VAD-gated
   pipeline (wake/pipeline.py): adaptive energy gate → Silero → chain, so
   the expensive embedding model sleeps in silence. 78 backend tests.
   ✅ **M3.2 sphere UI DONE** (2026-07-19): the signature orb —
   app/src/components/sphere/{Sphere,SphereFallback2D,SphereOrb,params,
   useAudioLevels}. Vanilla three.js (no R3F), ~6k shader-displaced points,
   Fresnel shell (NOT transmission — approved perf deviation), half-res
   UnrealBloom, navy in-scene vignette, four states from shared
   STATE_PARAMS. **Adaptive placement**: mini-orb docked in the header
   center while chatting, glides to 240px center stage during voice states
   / empty chat (one canvas, CSS-transitioned container). Canvas-2D
   fallback is live behavior-identical (same params module). Renderer
   selection: WebGL probe + persisted `jarvis.sphere.fallback` localStorage
   flag; watchdog trips on **render-call duration** (ema >12ms → 2D).
   Measured in-browser: **1.8ms CPU/frame** at full size, speaking state.
   three.js code-split (chat shell 261kB, orb chunk 540kB lazy).
   NOT yet verified: live run in the Tauri WKWebView on the M2 (user test).
   Remaining: RAM tiering surfacing, onboarding v1 (M3.3).
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

## Sphere UI (built in M3.2 — hard-won gotchas)

Design target: **docs/design/sphere.md** + **docs/design/sphere-refs/**.
Things that cost time — don't rediscover:
1. **CanvasTexture needs `colorSpace = SRGBColorSpace`** or the OutputPass
   brightens it — the "seamless" backdrop rendered lighter than the page.
2. **UnrealBloom writes alpha=1**: a transparent canvas turns into an opaque
   square. Solution: opaque canvas cleared to the app bg (#18181b zinc-900,
   MUST stay in sync with ChatView) + in-scene radial navy vignette that
   fades to that color — edges dissolve, and the rounded-full container clip
   lands exactly where the vignette hits zero.
3. **Watchdog on render-call duration, never frame cadence** — rAF throttling
   (occluded window, battery, embedded webviews) makes cadence lie and would
   permanently flag capable GPUs into the 2D fallback (happened in dev).
4. **Docked-size compensation**: fixed-pixel additive points saturate white in
   a 32px canvas — uSize and brightness scale with canvas height, bloom
   disabled under 100px.
5. Dev affordances: `window.__jarvisStore` (DEV only) drives
   voiceState/voiceLevel by hand; host div exposes `data-render-ms` (ema).

## Dev commands

```sh
cd backend && uv sync && uv run pytest && uv run jarvis doctor
cd app && npm install && npm run tauri dev      # full app (debug runs backend via uv)
# frontend-only: start backend with JARVIS_WS_TOKEN=x JARVIS_PORT=8765 uv run jarvis-backend,
#   then VITE_JARVIS_PORT=8765 VITE_JARVIS_TOKEN=x npm run dev
# Rust: export PATH="/opt/homebrew/opt/rustup/bin:$PATH" first (rustup via brew)
```

## Chat history — stored but not navigable (decision point before Phase 3)

**History IS persisted.** Every turn (typed and spoken) is written to the
immutable SQLite tree at `~/Library/Application Support/jarvis/jarvis.sqlite3`;
`jarvis doctor` shows the conversation count. The *backend* already exposes
`conversations.list` and `conversation.history` over WS, and `Store` has
`set_title()`, `set_active_leaf()`, `siblings()`, `path(leaf)` — branching-ready.

**Why the user can't see past chats:** the *frontend* has no conversation-list
UI. `App.tsx` renders only `<ChatView/>`; `state/conversation.ts` boots with
`conversationId: null` and never sends `conversations.list`, so every launch
starts a fresh conversation. Nothing is lost — it's just not surfaced.

**Gaps to close for full chat management (user asked for this explicitly):**
- **List + switch + new chat** — pure frontend: a sidebar that calls the
  existing `conversations.list` / `conversation.history`; "new chat" = reset
  `conversationId` to null. No backend work.
- **Rename** — backend `set_title()` exists; needs a `conversation.rename` WS
  message + dispatch + a UI affordance.
- **Delete** — **not implemented anywhere.** Note the tension: the store is
  deliberately append-only ("no update/delete for turns/messages" — the
  immutability promise in architecture.md). Deleting a whole *conversation*
  (the container) is defensible user-data control, not a breach of that promise,
  but it needs a deliberate call + `DELETE ... CASCADE` (schema already has FKs).
- Storage cost is a **non-issue**: text only, no audio stored; ~1KB/turn, so
  heavy daily use is tens of MB/year — rounding error against the ~500MB voice
  models and multi-GB LLM. Local-forever is the right default; delete is a
  privacy/control feature, not a space-pressure one.

This is Phase 5 in the original plan (with branching UI). **Recommendation:**
pull *basic* conversation management (list/switch/new/rename/delete) forward to
its own small milestone before or alongside Phase 3 — it's a glaring everyday
gap — and leave *branch navigation* (the sibling/tree UI) in Phase 5. User to
decide sequencing.

## Immediate next action

**M3.2 (sphere) built and browser-verified** — see phase plan above for what
landed and the sphere gotchas section for the traps. **Pending: the user's
live run** (`npm run tauri dev`) to feel the orb during a real voice turn on
the 8GB M2 — states were exercised via the dev store handle, not a live mic.

**Next: M3.3 — RAM tiering surfacing + onboarding v1** (the remaining Phase 3
scope; slips first per the user). Alternative next milestone if the user
prefers: chat history / conversation management (see section above) — still
queued, user chose Phase 3 first.
