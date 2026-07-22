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
   servers running inflate 1.3s → 1.8s). Barge-in over the app's own speech
   needs `afplay -v 2`.
8. **WKWebView suspension kills background JS:** with the window occluded or on
   another Space, macOS suspends the WebContent process (RSS ~600KB) — frozen
   JS can't answer wake.detected, while WebKit's networking process keeps the
   WS TCP ESTABLISHED so it *looks* connected. Fixed with
   `"backgroundThrottling": "disabled"` in tauri.conf.json (window options).
   Also: every webview reload leaves the old WS connection behind as an
   authenticated zombie — never assume connection count == live UIs.
9. **wake.detected must be broadcast** (it was `connections[-1]`-only): any
   newer client — zombie page, diagnostic script, future second window —
   silently stole the wake. handle_wake now cancels all generations and
   broadcasts; dead pages simply never answer with voice.start.
10. **Whisper transcribes ambient noise as "[BLANK_AUDIO]"** (and friends),
    which passed `if not text` and became a real LLM turn.
    `join_speech_segments()` in stt/transcriber.py drops segments that are
    entirely bracketed annotations → such turns end as no_speech.
11. **Kokoro's load silently starves the microphone.** Loading the Kokoro
    onnxruntime session + its first synthesis takes ~2.2 s and saturates every
    core; while it runs, PortAudio delivers only **33-38%** of input chunks —
    and sets **no overflow flag**, so nothing warns you. Measured by bisecting
    the load with a chunk counter (whisper and Silero are ~100%, innocent).
    That is why `RealVoiceIO.load()` loads only VAD + whisper and TTS loads
    lazily on first `synthesize()`, when the mic is already closed. Do not
    "tidy" Kokoro back into load(). Symptom if you do: the first voice turn
    transcribes as the tail of what was said.

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
2. ✅ **Voice loop** — DONE and live-verified. Mic button/⌘M
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
3. ✅ **Always-on + feel** — DONE (2026-07-22; there is no M3.4, the numbering
   skipped it). ✅ **M3.1 wake word DONE** (2026-07-18):
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
   ✅ **M3.5 chat management DONE** (2026-07-19): the conversation sidebar —
   list/switch/new/rename/delete, `Store.delete_conversation()`, and the
   `conversation.rename`/`conversation.delete` WS messages (both answer by
   **broadcasting the fresh list to every connection**, like wake.status).
   Frontend state is now **keyed by conversation** (`threads` in
   state/conversation.ts): a reply keeps generating in the chat it was asked
   in when you switch away, and only there. `messages`/`streamingText` remain
   mirrors of the active thread, so SphereOrb/MessageList were untouched.
   Delete is a two-step inline confirm, **no undo** (honest undo needs a
   soft-delete column the schema can't gain). 99 backend tests.
   ✅ **Voice path + live Tauri run verified** (2026-07-19, acoustically, in
   the real WKWebView app): wake turn, toggleVoice turn, no_speech slot
   release, barge-in mid-speech, transcript routed to the open conversation.
   No streamKey leak; what looked like one was three real bugs, all fixed
   (gotchas 8-10). Still needs human eyes: sidebar/orb rendering in WKWebView,
   the literal ⌘M keypress, and a long-idle background wake soak.
   ✅ **M3.3 readiness + tiering DONE** (2026-07-22): `system.readiness` (new
   `server/readiness.py`) reports codes-only gate checks — llm, model, voice
   models, wake models, microphone — with `ready` false only on a *fail*, so
   missing voice models warn without blocking text chat. The frontend gate
   (`components/onboarding/Readiness.tsx`) replaces the message list, keeps
   the sidebar reachable, and offers copyable fix commands + a "Check again".
   `models.list` now carries the RAM tier, per-model `params_b` and
   `over_budget`, so the picker reads "llama3.2:3b · 3.2B" / "qwen2.5:7b ·
   7.6B — tight on 8GB" and the empty chat explains the auto-choice. Rename
   no longer bumps `updated_at` (`set_title(..., touch=False)`), so the
   sidebar keeps last-*activity* order. **First-turn clipping fixed** — see
   gotcha 11 and "First voice turn" below. 108 backend tests.
4. **Agency + security** — ⬅ **NEXT, and the largest phase.** Permission engine
   + taint + sandbox, tools ship WITH their security layer, extension loader +
   approval gate. Shipping a half-built permission engine is worse than not
   shipping: cut the tool list before cutting the security layer.
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

## Chat management (M3.5, DONE) — how it works now

Every turn (typed and spoken) is written to the immutable SQLite tree at
`~/Library/Application Support/jarvis/jarvis.sqlite3`, and since M3.5 it is
**navigable**: `conversations.list` on connect populates a sidebar; clicking a
row sends `conversation.history`; "New chat" resets to the unsaved thread.

**The immutability promise, as amended** (architecture.md + conversations.py +
schema.sql all say this now): no turn or message is ever rewritten or
selectively removed — editing still means appending a sibling turn and moving
the active leaf. `Store.delete_conversation()` is the single exception and drops
a whole conversation *container*: user control over their own data, and a
conversation is either wholly present or wholly gone.

**Things that will bite you if you touch this code:**
1. **No CASCADE, ever.** schema.sql declares the FKs without `ON DELETE CASCADE`
   and db.py sets `PRAGMA foreign_keys = ON`, so delete is ordered by hand
   (messages → turns → conversation) in one transaction. Do NOT "fix" this in
   schema.sql: `CREATE TABLE IF NOT EXISTS` means existing databases would never
   pick it up, and there is no migration framework (`SCHEMA_VERSION = "1"`).
2. **Delete races the generation.** `run_exchange` catches `CancelledError` and
   *then* writes its turn, so `conversation.delete` must cancel-and-await the
   generation before deleting — otherwise that append hits the FK constraint
   against a conversation that no longer exists. `Connection.
   generating_conversation_id` tracks the target; because a brand-new
   conversation only reveals its id at `chat.start`, `_generation_send()` wraps
   the sender and sniffs it (this is why `conn` isn't threaded through
   `_generate`/`run_voice_exchange`). Regression test:
   `test_delete_while_generating_into_it` — it fails if you remove the guard.
3. **Frontend state is keyed by conversation.** `threads` in
   state/conversation.ts, keyed by id or `NEW_THREAD` for the unsaved chat;
   `streamKey` names the thread owning the single in-flight generation.
   `messages`/`streamingText` are mirrors of the active thread — keep them in
   step via `patchThread`/`showThread` or the sphere will read stale state.
4. **One generation per connection** (backend answers BUSY). While a reply
   generates in another chat, the composer is disabled with
   `conversation.busyElsewhere` rather than being allowed to bounce off BUSY.
5. **Errors carry no correlation id.** `CONVERSATION_NOT_FOUND` from a rename
   must not tear down an unrelated in-flight stream — hence
   `MANAGEMENT_ERROR_CODES`.

**Settled in M3.3:** rename used to bump `updated_at`, so a renamed chat jumped
to the top. `set_title` now takes `touch: bool = True` and the WS handler passes
`touch=False` — renaming isn't activity, sending a message is. The default
keeps the old store contract for every other caller. Regression test:
`test_rename_keeps_last_activity_order`.

Storage cost is a **non-issue**: text only, ~1KB/turn — tens of MB/year under
heavy use. Delete is a privacy/control feature, not a space-pressure one.

**Still Phase 5:** branch navigation (the sibling/tree UI). `Store.siblings()`
exists and is tested; it is deliberately not surfaced yet.

## Publishing / GitHub (as of 2026-07-22)

The repo is going public so the user can show it as portfolio work — that is an
explicit goal now, and it raises the bar on README/docs quality.

- **Remote exists, nothing pushed yet:** `gh repo create jarvis --public
  --source=. --remote=origin` ran on 2026-07-21 →
  `https://github.com/AayushSharma1003/jarvis`, remote `origin` wired. The
  landing page is still empty; `git push -u origin main` has NOT been run.
- **Pre-push safety scan is DONE and clean** (working tree): no secrets, no
  `*.sqlite`/`.env`/`*.pem`, no file >1MB, no model weights. `.gitignore`
  correctly covers node_modules/, target/, .venv/, `*.onnx`/`*.bin`, `.env`,
  `.claude/`. Only the *working tree* was scanned — historical commits were
  not audited (`git log --all --diff-filter=A --name-only` if paranoid).
- ✅ **README + LICENSE landed** (commit 7f6c754): portfolio README,
  Apache-2.0 LICENSE, third-party NOTICE. Test/feature counts inside the
  README drift as milestones land — re-check them before the push.
- Commit history is fine (conventional prefixes + milestone tags); the
  auto-commit mislabelling only affected the earliest Phase-1 commit. Do NOT
  offer to rewrite history.

## Immediate next action

**Phases 1-3 are complete (3 of 6).** Next is Phase 4, agency + security —
the largest phase in the plan, and the one where a half-built permission
engine is worse than none. Do not start it until the user says so.

**Phase 3 M3.1 + M3.2 shipped and live-verified by the user** (2026-07-19):
text chat, voice loop, "Hey Jarvis" always-on, and the sphere all work in the
real Tauri app on the 8GB M2. User's words: "okay its working."

**M3.5 chat management shipped** (2026-07-19), verified against a *scratch*
database in a browser-hosted build: list/switch/new/rename/delete, background
generation routing, delete-mid-generation (zero orphan rows,
`PRAGMA foreign_key_check` empty), sphere dock/re-centre, narrow-window overlay,
boot-time list load. 91 backend tests, ruff + tsc clean.

**Both M3.5 gaps CLOSED (2026-07-19, acoustically, in the real Tauri app):**
wake turn, toggleVoice turn (⌘M's body), no_speech slot release, barge-in
mid-speech, and transcript-to-open-conversation all verified live in WKWebView.
No streamKey leak existed; the dead-looking wake was three real bugs, fixed
this session (gotchas 8-10): WKWebView background suspension
(`backgroundThrottling: "disabled"`), `connections[-1]` wake routing (now
broadcast), and "[BLANK_AUDIO]" becoming an utterance (transcriber filter).
The confabulation fix also landed: prompts.py now declares "no tools yet" —
llama3.2:3b declines play-music/set-timer/open-app baits instead of claiming
them. 99 backend tests, ruff + tsc clean.

**User should eyeball when convenient** (not automatable from a headless
session): sidebar + orb rendering in WKWebView, a literal ⌘M keypress, and
"Hey Jarvis" after the app has sat hidden for an hour (the suspension fix's
soak test — the real check on gotcha 8; if it fails, the fix didn't take).

**M3.3 landed 2026-07-22** (readiness gate, RAM tiering, rename ordering,
first-turn clipping). Verified in a browser-hosted build against a real
backend on a scratch data dir: the gate rendering with Ollama pointed at a
dead port, the warning rows with copyable commands, "Check again", recovery
to a healthy backend, the tier-annotated picker (`qwen2.5:7b · 7.6B — tight
on 8GB` is real, from this machine), and a full text turn. The first-turn fix
was verified acoustically over the speakers and the real mic.

**Onboarding scope was deliberately cut** to the readiness gate. The original
proposal had a mic-permission walkthrough, model-download progress, a wake
opt-in step and a guided first voice turn. Reasons for cutting, in order:
a download UI needs a cancel/resume path and a progress protocol (that belongs
with the installer, not the chat window); macOS cannot be *asked* whether mic
permission was granted without AVFoundation, so a "walkthrough" would be
theatre (the gate says where the setting lives instead); and the wake toggle
plus ⌘M are already one click each. Reopen it when there's an installer to
hang it off.

**Still open:**
1. Whether the gate should also appear for *warnings* (today: failures only).
2. Sidebar/orb in WKWebView, a literal ⌘M keypress, the hour-long background
   wake soak — all need the user's eyes.

## First voice turn (fixed 2026-07-22) — what it actually was

The *first* voice turn after app start used to clip the opening words. The
obvious half of the cause was ordering: `voice.start` ran `io.load()` before
opening the mic. Fixing only that did **not** fix the bug — it just moved the
loss, because the load itself starves CoreAudio (gotcha 11). Both halves were
needed:

1. **Open the mic first, buffer the load window.** `MicCapture`'s queue is now
   8 s deep and `backlog()` drains it in one go; `run_voice_exchange` feeds the
   backlog to the endpointer before live iteration. If nothing was said, it
   calls `endpointer.reset()` so a silent load doesn't spend the no-speech
   timeout the user hasn't seen yet.
2. **Keep Kokoro out of the load** (gotcha 11), which shrank the pre-listening
   window from ~2.6 s to ~0.45 s as a bonus.

Measured acoustically (speaker → real mic, `say` starting the instant
`voice.start` was sent, counting "one … ten"):

| | before | after |
|---|---|---|
| "listening" reached | 2.6-4.1 s | **0.5 s** cold, 0.11 s warm |
| transcript | `6-7-8-9-10`, `5678910` | `1 2 3 4 5 6 7 8 9 10` (3/3 cold runs) |

Not warming engines at boot was deliberate: it would have cost ~500 MB resident
on the 8 GB target for users who never speak, and it does not fix push-to-talk
one second after launch.

Then Phase 4 (agency + security) — the largest phase, and the one where
shipping a half-built permission engine is worse than not shipping. Cut the
tool list before cutting the security layer.
