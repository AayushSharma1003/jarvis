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
12. **Reasoning models are a latency trap, and `think: false` does not save
    you.** qwen3:4b has the best tool discipline of anything measured (33/33)
    and is still unusable as a default: its thinking pass runs entirely before
    the first *content* token — **20s** on the 8GB M2 against a ~0.65s LLM-leg
    budget. Setting `"think": false` does not disable the reasoning, it stops
    Ollama **separating** it: the monologue then arrives in `message.content`
    with raw `<think>` tags, so it renders in the transcript and gets **spoken
    aloud** (`tts/chunker.py`'s markdown stripper doesn't touch `<think>`).
    Consequence: merely *installing* qwen3:4b used to make it the 8GB default
    (4.0B beats 3.2B inside the budget). `pick_model` now skips catalog-tagged
    `reasoning` models when choosing FOR the user; a configured model still
    wins. Measurements: docs/tool-calling.md.
    **Corollary that saved the transcript:** the Ollama adapter reads only
    `message.content` and ignores `message.thinking`, so with thinking
    *separated* the monologue never becomes a TextDelta and cannot be spoken.
    Do not "helpfully" start streaming the thinking field without deciding
    what voice mode does with it.
13. **Small models PRINT tool calls instead of emitting them.** llama3.2:3b
    leaked 4 in 33 calls as ordinary assistant prose —
    `{"name":"run_command","parameters\":{\"command":"git status"}}` — which
    renders in the transcript AND gets handed to Kokoro. Worse, it is a silent
    failure: the tool never ran, so the user's request just didn't happen.
    `agent/toolfilter.py` withholds a delta stream that starts to look like a
    printed call and drops it, surfacing a failed span instead. It only fires
    when the JSON names a **registered** tool, so a user asking for JSON still
    gets their answer — without that guard any JSON reply would be at risk.
14. **Dismiss the confirm dialog with an AWAITED send, not a background task.**
    The instinct on `except asyncio.CancelledError` is to fire the dismissal as
    an independent task, on the theory that awaiting inside a cancellation
    handler will re-raise. It does not here — the cancellation has already been
    delivered — and firing it loses the race: `chat.done` for the cancelled turn
    goes out first and the dialog flickers on screen *after* the turn it belonged
    to is gone. `run_voice_exchange` already awaits its final `voice.state` in
    exactly this position, so the pattern was proven before this. Regression
    test: `test_chat_stop_while_a_confirm_is_pending`.
    **Related trap in the tests:** a delete-during-confirm test that asserts only
    the *end state* passes with the cancel guard removed — the rows are gone
    either way and the FK violation surfaces later, in a task nobody awaits. The
    assertion that actually bites is **ordering**: `confirm.close` and
    `chat.done` must appear before the `conversations` broadcast.
15. **Two truncation layers, and the inner one's message gets eaten.** The
    directory-listing cap was 500 entries, which at ~20 chars each overflows the
    registry's `MAX_RESULT_CHARS` (8000) — so the registry truncated the listing
    *including* the "… and N more" line the cap had just added, and the model was
    silently shown a partial directory with no indication of it. Any inner cap
    has to bind before the outer one for its own message to survive; `MAX_ENTRIES`
    is now 200 with a comment saying why. The registry's truncation stays as the
    backstop for pathologically long filenames.
16. **Mutation-testing your own tests: watch for substring collisions.** A
    mutation that replaces `"        raise SandboxError(...)"` matches the
    16-space indented copy inside a loop as a *substring* too, so the "mutation"
    was a syntax error and the test "caught" nothing. If a mutation reports a
    collection error rather than a failure, it did not prove anything — assert
    the pattern occurs exactly once, and use a multi-line anchor.

17. **`resolve()` settles symlinks, not *spelling* — and two spellings of one
    file broke the sandbox exclusion.** macOS and Windows filesystems are
    case-insensitive by default, and APFS is *also* normalisation-insensitive
    (a name written NFC opens as NFD). `Path.parts` compares both as different,
    so `<root>/Jarvis-Config/config.toml` missed the excluded-directory check,
    matched the root, and wrote to the real config — the self-escalation the
    exclusion exists to stop. Verified live: with the pre-fix code the model
    wrote `PWNED` into a canary inside the excluded dir after the user clicked
    Allow. Fix is an **asymmetry**, and it is the point: comparisons where a
    match means **deny** are casefolded + NFC-normalised (`Sandbox._fold`),
    comparisons where a match means **allow** stay exact. Folding the roots test
    too would *widen* the sandbox on Linux, where `~/documents` and
    `~/Documents` really are two directories. Tripwire:
    `test_a_root_is_still_matched_case_sensitively`.
18. **`run_exchange` swallows `CancelledError`, so callers cannot detect a
    barge-in by catching one.** It absorbs the cancellation on purpose — that is
    what lets it persist the partial turn, which the delete-races-the-generation
    guard depends on — and then *returns normally*. `run_voice_exchange` was
    therefore carrying straight on after a `voice.stop` raised while the model
    was still streaming: it awaited the synth worker and `player.drain()`,
    speaking the whole queued reply to a user who had just interrupted it, and
    reporting `reason="done"`. Worse, `handle_wake` does `await
    cancel_generation()` before broadcasting, so the wake word stayed dead for
    the length of the reply it had failed to interrupt. It hid because the
    barge-in that was verified acoustically happens *after* streaming ends,
    where the task is parked in `await synth_task` and asyncio cancels that
    inner task for free. Ask `asyncio.current_task().cancelling()` — it survives
    the absorbed cancel — and re-raise, **after** `chat.done` has gone out or
    the frontend keeps `streamKey` and the composer never re-enables. Related:
    a `to_thread`-parked worker task is not reached by its parent's
    cancellation, and `Player.stop()` only *clears* the buffer (the stream stays
    open), so a late `enqueue()` un-silences the barge-in.
19. **A shell subprocess needs three things a naïve `run` gets wrong, and each
    is a real DoS or leak** (`tools/shell.py`, M4.4). (a) **Never `communicate()`
    for untrusted output.** It buffers the child's *entire* output before
    returning, so `yes` / `cat /dev/urandom` balloon RAM to gigabytes on the 8GB
    target long before any timeout fires — the wait_for wraps the reader, not the
    memory. Read in chunks against a byte budget and kill the producer the moment
    it's hit. (b) **`start_new_session=True` + `os.killpg`, not `proc.kill()`.**
    A shell backgrounds children (`(sleep 2; …) & …`); non-interactive `sh -c`
    has no job control, so they share the shell's process group — but SIGKILL to
    the shell alone reparents them to init and they live on. Kill the whole group.
    The tripwire is a backgrounded child that writes a sentinel *after* the kill
    window; if it appears, only the parent died
    (`test_timeout_kills_the_whole_process_group`). (c) **Do all termination in
    one `finally`.** The explicit `except CancelledError` you reach for is
    redundant with it and invites drift — the finally already kills on the cap
    break, the timeout, and a barge-in's CancelledError propagating through. The
    one branch that earns its place is `except TimeoutError`, and only to
    *translate* the code to `COMMAND_TIMEOUT`; the kill is still the finally's.

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
4. **Agency + security** — ⬅ **IN PROGRESS, and the largest phase.** Permission
   engine + taint + sandbox, tools ship WITH their security layer. Shipping a
   half-built permission engine is worse than not shipping: cut the tool list
   before cutting the security layer. **Scope agreed 2026-07-22:** M4.0 model
   capability gate → M4.1 tool plumbing (zero side effects) → M4.2 permission
   engine + confirmation → M4.3 filesystem sandbox + file tools + taint →
   M4.4 shell → M4.5 web_fetch + SSRF. Ships **files, shell, web_fetch**.
   **Cut:** extension loader + approval gate → phase 5; clipboard → phase 5;
   `web_search` → phase 5 or never (no search API on a zero budget, and
   scraping is an unrequested network dependency); `take_screenshot` → cut
   from v1 (every model in the 8GB budget is text-only).
   ✅ **M4.0 model capability gate DONE** (2026-07-22): tool use is gated on
   the model, because *"can this model decline a tool?"* turned out to be a
   security property. Measured with `backend/tests/manual/probe_tool_calling.py`
   (11 cases, routing vs restraint, malformed-leak and warm-TTFT gates):
   llama3.2:3b restraint **22%** — it answers "what's 17 times 4?" by running
   `echo 17*4` in a shell, and "what does idempotent mean?" with a web fetch,
   which under the taint rules would escalate every later call. Prompt
   hardening made it **worse** (76%→67%). qwen2.5:3b is better (77%) and still
   fails. qwen3:4b is perfect (33/33) and disqualified on latency (gotcha 12).
   **No model in the 8GB ≤4.5B budget clears both gates, so the 8GB tier ships
   tools opt-in.** New: `llm/capabilities.py` (three states — `on` curated +
   measured, `optin` capable-but-unvetted → OFF by default, `unsupported` →
   hard no) and `llm/catalog.py`, the first ever reader of
   `catalog/models.toml`. Fail-safe throughout: a missing catalog disables
   tools rather than enabling them. `models.list` carries `tools` per model;
   `jarvis doctor` has a `tool use` line. 130 backend tests.
   ✅ **M4.1 tool plumbing DONE** (2026-07-22): the wire, with nothing
   dangerous on it. `stream_chat` now yields `TextDelta | ToolCall` and takes
   a `tools` schema; `run_exchange` is a multi-round loop (cap
   `MAX_TOOL_ROUNDS = 4`, and the final pass is offered NO tools so the model
   must answer in words). Tool spans persist as `role='tool'` rows —
   `schema.sql` already allowed it, so no migration. `tools/registry.py` does
   signature→JSON-schema introspection (extensions reuse it in phase 5) and
   **takes its security gate as a constructor argument**, so calling a tool
   without consulting the security layer is not an expressible operation.
   M4.1 ships `security/permissions.py`'s **SafeOnlyGate**, which refuses
   every `ask`/`dangerous` tool: the confirmation machinery is M4.2 and until
   it exists there is no honest way to run one. The only tool is
   `get_datetime`. `agent/toolfilter.py` suppresses tool calls the model
   *prints* as prose (gotcha 13). New `tool.span` WS message + a collapsed
   `ToolSpan` component. 172 backend tests.
   ✅ **M4.2 permission engine + confirmation DONE** (2026-07-22): tools can
   now ask. `security/confirm.py` is an async broker — backend-minted
   correlation ids, broadcast to every window, first answer wins, single-use
   ids, and **every** way of not getting an answer (no UI, send failed,
   timeout, broker raised) ends in a deny. `PermissionGate` replaces
   SafeOnlyGate (which stays as the no-broker fallback). "Allow for this
   session" is keyed on **tool + exact arguments**, memory-only, and never
   honoured for `dangerous` — enforced server-side, not by hiding the button.
   A refusal is remembered for the rest of the exchange so a nagging model
   can't manufacture a second dialog. **The dialog is an in-app React modal,
   not a native OS one** — security-model.md §1 was amended with the reasoning
   (short version: the webview is in the answer path either way, so native
   bought nothing and cost the third button, Deny-default focus, Linux
   portability and headless verification). Default focus is **Deny**, Escape
   denies, focus is trapped. New WS messages: `confirm.request`,
   `confirm.close`, `confirm.respond`, and **`voice.say`** — the frontend
   sends the sentence to speak so the backend can voice "I need your OK" while
   authoring no English (the i18n rule vs. backend-side TTS). Also landed: the
   deferred M4.0 readiness `tools` row + picker copy, `[tools]
   allow_dangerous` config, and a catch-all in `_generate`/`run_voice_exchange`
   so an unexpected exception no longer strands the UI holding `streamKey`
   forever. 217 backend tests.
   ✅ **M4.3 sandbox + file tools + taint DONE** (2026-07-23): the first tools
   with real side effects, shipped with the two layers that make them safe.
   `security/sandbox.py` resolves before it checks — expanduser → require
   absolute → `.resolve()` (collapsing `..`, following symlinks) → must be
   under a root and **not** under an excluded dir (config/data checked FIRST,
   so "inside a root" can never override them). Defaults are Documents /
   Downloads / Desktop via platformdirs; an absent `[filesystem] roots` key
   means those defaults, an explicit `roots = []` means **no file access at
   all** — the two are deliberately distinguishable. `security/taint.py`
   marks a conversation the moment `read_file` returns: sticky for the
   process's life, in memory, never persisted, and it **invalidates session
   grants in both directions** (a grant given before the taint doesn't cover a
   call after it; approving a tainted call grants nothing). The dialog shows
   the source path and drops its "allow for this session" button. Tools:
   `read_file`/`list_dir` safe, `write_file` ask, `delete_file` dangerous
   (directories refused — one confirmation can't stand for an unbounded set of
   files). `ToolOutput` lets a tool declare its own untrusted content; the
   registry relays it, the loop applies it. 279 backend tests.
   ✅ **M4.4 shell DONE** (2026-07-24): `run_command`, the sharpest tool in the
   project (`tools/shell.py`). It runs the command **verbatim** through a shell
   — no classifier, no denylist, both bypass generators — and its only guardrail
   is the unconditional confirmation. It takes **no sandbox**: a subprocess
   escapes §2 by design (`cat ~/.ssh/id_rsa` ignores every root), so it registers
   unconditionally, is `dangerous` (never session-grantable, off entirely under
   `allow_dangerous = false`), and rides taint like anything else. Owner decisions
   (delegated, security-first): **cwd = home** (a shell `cd`s anywhere, so pinning
   to a root implies a wall that isn't there); **env = inherited minus `JARVIS_*`**
   (real PATH so tools work, but the WS auth token never reaches a child);
   **30s timeout + 64KB incremental output cap** (one generation slot, no output
   streaming — a quick-command tool, not a build runner). The subprocess lifecycle
   is the meat: bounded incremental read (never `communicate()`), and **whole
   process-group kill** on timeout / cancellation / cap so a backgrounded child is
   never orphaned. No frontend changes — the confirm dialog and tool span were
   already generic; only three i18n codes (`COMMAND_REQUIRED/TIMEOUT/FAILED`).
   **314 backend tests** (14 new, each mutation-proven — incl. the process-group
   kill via a surviving-sentinel tripwire). See gotcha 19.
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

- ✅ **Pushed 2026-07-22.** `gh repo create jarvis --public --source=. --remote=origin`
  ran 2026-07-21 → https://github.com/AayushSharma1003/jarvis; `git push -u origin
  main` landed on 2026-07-22 through `302c714` (M4.1). Sanity check any time:
  `git status -sb` must show `## main...origin/main` with no `[ahead N]`.
  **Gotcha for future commit blocks:** the environment auto-commits the working
  tree before the block runs, so `git commit` finds nothing, exits non-zero, and
  an `&&`-chained `git push` never fires. Chain push blocks with `;` not `&&`.
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

**Phases 1-3 complete; Phase 4 in progress.** M4.0-M4.4 are done and green
(**314 backend tests, 2 Rust**). Only M4.5 (web_fetch + SSRF) remains in Phase 4.

### Pre-public security + bug audit (2026-07-23 → 2026-07-24)

A stop-and-verify pass, no new features. Every fix below has a regression test,
and every regression test was mutation-proven (break the code, watch it fail,
revert). Nothing in the voice or text path regressed.

**🔴 Exploitable — fixed**
- **Filesystem sandbox escape** (`security/sandbox.py`). The excluded-directory
  check compared path *spellings* exactly (`Path.is_relative_to`), but
  `resolve()` settles symlinks, not case or Unicode form. On case-insensitive
  macOS/Windows and normalisation-insensitive APFS, `<root>/Jarvis-Config/config.toml`
  missed the exclusion, matched the root, and let a tool overwrite Jarvis's own
  config — the self-escalation the exclusion exists to stop. **Fix:** deny-side
  comparisons casefold + NFC-normalise (`Sandbox._fold`); allow-side (roots)
  stay exact, because folding those would *widen* the sandbox on Linux where
  `~/documents` ≠ `~/Documents`. **Proven live in the real app**: with the fix
  reverted, qwen3:4b wrote `PWNED` into a canary inside the excluded dir after
  the user clicked Allow; with the fix, all spellings return
  `PATH_OUTSIDE_SANDBOX`. Reachable only when a configured root *contains* the
  config/data dir — not the Documents/Downloads/Desktop default, but exactly the
  Linux layout and any `roots = ["~"]`. Gotcha 17; `test_sandbox.py`.

**🟠 Real bug, bounded impact — fixed**
- **Barge-in was dead while the model was still streaming** (`server/voice.py`).
  `run_exchange` deliberately absorbs `CancelledError` (to persist the partial
  turn — the delete-races-generation guard needs that), so a `voice.stop`,
  `chat.stop`, or wake word raised *during* generation returned as an ordinary
  result and the turn went on to speak its entire queued reply, reporting
  `done`. Worse, `handle_wake` `await`s `cancel_generation()` before
  broadcasting, so the wake word was dead for the length of the reply it failed
  to interrupt. It hid because the acoustically-verified barge-in happens
  *after* streaming, where the task is parked in `await synth_task` and asyncio
  cancels the inner task for free. **Fix:** `asyncio.current_task().cancelling()`
  (survives the absorbed cancel), re-raise after `chat.done` goes out.
  Gotcha 18; `test_voice_ws.py`.
- **Barge-in could speak one sentence after being silenced** (same file). The
  synth worker is a separate task; parked in `to_thread(synthesize)` it finishes
  and `enqueue()`s *after* `player.stop()`, and `Player.stop()` only clears the
  buffer (the stream stays open), so the late chunk un-silences. **Fix:** cancel
  the synth worker in the barge-in handler and again in `finally`.
- **Corrupt database crashed sidecar startup** (`storage/db.py`). A junk or
  foreign `jarvis.sqlite3` raised `sqlite3.DatabaseError` out of `main.py`,
  which the user saw only as "backend didn't start in time" with no recovery.
  **Fix:** on `DatabaseError` at open, rename the bad file to
  `jarvis.sqlite3.corrupt-{unix_ts}` (kept, never deleted, so data can be
  recovered), log a WARNING with both paths + the error, and open a fresh db.
  Narrow catch — a non-`DatabaseError` still propagates. **Proven live**: the
  real backend booted on a junk db, logged the warning, reached `ready`.
  `test_db.py` (4 tests).

**🟡 Correctness — fixed**
- **`token_valid` crashed on a non-string token** (`server/auth.py`). `{"token":
  123}` hit `.encode()` and raised `AttributeError` out of the pre-auth path,
  where nothing catches it — any local process could crash the handler. Now an
  `isinstance` refusal. Fail-safe already, now clean. `test_auth.py`.
- **`confirm.py` module docstring contradicted its own code** — described firing
  the dialog dismissal as an independent task (`_close_soon`, which doesn't
  exist), the exact opposite of gotcha 14's awaited-send fix. Rewritten; a
  maintainer trusting it would have reintroduced the bug.

**🟡 Reported, not fixed** (bounded, deliberately left)
- `server/app.py` `conversation.rename`/`.history` don't type-check
  `conversation_id` the way `.delete` does — an unhandled type error can tear
  down the connection. No security impact (authenticated).
- `_generate`'s catch-all `await send(...)` can itself raise on a closed socket;
  noisy in logs, not a leak (`connections.remove` already ran).

**Supply chain & hygiene**
- **pip-audit / npm audit / cargo audit: 0 vulnerabilities** (17 cargo warnings,
  all unmaintained GTK3 transitives from Tauri on the Linux path — no fix
  available, not actionable).
- **Zero-telemetry claim re-proven** by inspection: every outbound call is the
  configured Ollama URL; the only socket is the loopback bind; no `fetch` in the
  frontend, no HTTP in Rust, `fetch_models.py` is user-invoked.
- **README corrected** where it both under-claimed (permission engine listed as
  unbuilt) and over-claimed (`web_fetch` SSRF + extension approval as present
  tense — neither exists). Now split into built-vs-specified.
- **`.gitignore` gaps closed**: `*.sqlite3` (the store is `jarvis.sqlite3`, only
  `*.sqlite` was ignored) and `build/` (PyInstaller workpath, multi-MB binary).
- **NOTICE**: added missing `tomli-w` (MIT) and `TypeScript` (Apache-2.0).
- ✅ **Git history scanned for secrets — clean** (2026-07-24, user-run). The
  last open item from the audit; done. History audit is no longer outstanding.

**Scope not covered** (cheap, non-critical, for a later pass): clean-clone build
trace as a stranger, CI-tests-what-it-claims, `unsigned-install.md` honesty
re-read, and Windows/Linux file-tool behaviour by hand (only macOS exercised;
the deny-side folding closes the case-insensitivity class generically).

**Next is M4.5: `web_fetch` + SSRF guards** (§4). That closes Phase 4's tool
list (files, shell, web_fetch). `security/ssrf.py` is still an empty stub; §4
already specifies the resolved-IP private/link-local block, and the network-write
class is `dangerous`. Taint (§3) is the reason web_fetch matters most: its result
is the canonical untrusted content, and reading it must mark the conversation.

**M4.4 (shell) is done — how its three casually-dangerous decisions actually
went** (owner delegated all three; standing-authorization = security first):
1. **Working directory + environment.** cwd = **home**, not a sandbox root: a
   shell `cd`s anywhere, so pinning would imply containment it can't provide (its
   real protection is the confirmation). env = **inherited minus `JARVIS_*`** —
   the user's PATH/tools stay (a shell that can't find `git` won't get used), but
   the WS auth token never reaches a child.
2. **Timeout + output caps.** 30s timeout (env-overridable) + a 64KB **incremental**
   output cap — read as bytes arrive, because `communicate()` would let `yes`
   balloon RAM before the timeout fired. A quick-command tool, not a build runner.
3. **A shell escapes every other layer** — now stated at length in §1's
   `run_command` subsection, and the §"Known limitations" bullet flipped from
   prediction to landed.

Still deferred from earlier milestones: nothing.

**Two open items from M4.0:**
1. `qwen3:8b` sits in the catalog as the 16GB default with **no**
   `tool-calling` tag, because nobody has measured it — it needs a probe run
   on the RTX A6000 box. Expect the same hybrid-reasoning latency trap as
   qwen3:4b (gotcha 12).
2. The 8GB tier ships tools **opt-in**. If a user opts in, qwen2.5:3b is the
   model to point them at (77% restraint, 0 malformed, 0.22s TTFT) — never
   llama3.2:3b (22%, 4 malformed).

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

**Live-verified by the user 2026-07-22** in the real Tauri app: sidebar, orb,
the green ready dot, and a literal ⌘M keypress all work. **Still needs eyes:**
"Hey Jarvis" after the app has sat hidden for an hour (the real check on gotcha
8 — if it fails, the suspension fix didn't take), and the M4.1 tool span
rendering in WKWebView (verified in a browser-hosted build, not the real webview).

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
2. The hour-long background wake soak — still the only real test of gotcha 8.
3. ✅ **CLOSED 2026-07-22.** The voice path with tools was heard acoustically
   during M4.2: speaker → real mic → whisper → `echo` tool call → the dialog
   (marked "from a spoken request") → **"I need your OK — check the window"
   spoken aloud** → answered → tool ran → spoken reply → idle. The tool span
   also rendered correctly in that run.
4. The dialog has only been seen in a browser-hosted build, not the real
   WKWebView, and `show_window` (the Rust command that reveals the window when
   a confirm arrives while the app is hidden in the tray) has never been
   exercised — there is no way to hide to the tray outside the real app. Now
   covers M4.3's dialog too (the taint provenance block and the missing
   "allow this session" button).
5. **New:** a spoken *file* turn has not been heard. M4.2 verified voice+tools
   acoustically with the dev `echo` tool, and M4.3's tools share `run_exchange`
   and the same gate — but "read my notes and write a summary", spoken, has
   never actually happened.

**Live-verified in M4.3** (browser-hosted build, scratch sandbox, qwen3:4b,
2026-07-23): a read with **no dialog**, a write in the same conversation
confirming with the amber "follows content Jarvis read from …/notes.txt" block
and **no "allow this session" button**, the file appearing on disk, `/etc/passwd`
refused as `PATH_OUTSIDE_SANDBOX`, **a symlink inside the sandbox pointing out
refused** with the key material never leaking, `delete_file` showing the *Risky*
badge with Deny focused and the file actually disappearing, and — after
`allow_dangerous = false` + restart — a delete refused with **no dialog at all**
and the file surviving.

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
