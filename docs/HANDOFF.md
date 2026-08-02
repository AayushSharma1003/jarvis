# JARVIS — Session Handoff

> Paste this into a new session to continue. It's the single source of "where we are."
> Deeper detail lives in `docs/architecture.md`, `docs/security-model.md`, and the
> Claude memory files (auto-loaded). This is the orientation layer.

## What JARVIS is

A **local-first, voice-activated AI assistant** that runs on the user's own
machine. Cross-platform desktop (macOS, Windows, Linux). Wake word ("Hey Jarvis"
/ "Hey Friday") → speak → a local LLM with tool access (files, shell, web fetch;
clipboard and screenshots are cut from v1 — architecture.md) responds in a
natural voice while an animated sphere UI reacts to the audio. Fully local by default (zero API keys, works offline after
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
20. **An SSRF guard has three failure modes that each look like it works**
    (`security/ssrf.py`, `tools/web.py`, M4.5). (a) **Check every resolved IP, not
    the first.** A host with one public and one private A record is a trivial
    bypass if you stop at `ips[0]`; the any-IP rule refuses the whole host. (b)
    **Validate IP-literal hosts directly — never resolve them.** `http://169.254.
    169.254/` handed to a resolver can be whitewashed by an attacker's DNS (or, in
    a test, a fake resolver); an IP literal is its own address, so classify it and
    skip resolution. (c) **Re-validate every redirect hop.** The first hop being
    clean is worthless if a 302 to the metadata endpoint is followed blindly —
    follow redirects by hand (cap 5) and run the same check on each `Location`.
    Also: `ipaddress` classification (`is_private/loopback/link_local/multicast/
    unspecified/reserved`) is a superset of a hand-rolled CIDR list and covers
    IPv6, IPv4-mapped (`::ffff:10.0.0.1`), and getaddrinfo-decoded encodings — the
    IPv4-mapped unwrap is native on current CPython, kept only as cross-version
    defense (a test can't distinguish it, so it's commented as such). The residual
    it does **not** close is DNS rebinding — documented (§4), not solved.

21. **`safe` stopped meaning read-only, and the fail-safe default is the
    unintuitive one** (`security/permissions.py`, M5.1). §3 escalates every
    *side-effectful* call once a conversation is tainted, and that was satisfied
    vacuously because nothing side-effectful was classified `safe`. Extensions
    broke it: an extension may declare a tool `safe` and the core cannot verify
    the claim (`set_timer` mutates — and `timers-reminders/manifest.toml`
    declares exactly that, committed, before any of this existed). `safe` skips
    the taint check entirely, so that was a silent hole. The gate now takes
    **`read_only`**, fixed at registration and never assertable per call: core
    reads pass `True`, **every** extension tool gets `False`, and `safe` +
    not-read-only runs freely while clean but confirms once tainted. The default
    is **`False`**, which reads backwards until you see the failure modes:
    forgetting it costs one extra confirmation, while defaulting `True` would
    silently skip the taint check for a tool nobody vouched for. The dataclass
    default and `Registry.register`'s default are **separate tests** — `register`
    always passes the flag explicitly, so a mutation flipping the `Tool` default
    was caught by nothing until `test_a_tool_built_directly_is_not_read_only_either`
    existed.
22. **Mutation testing has a second false-negative mode: stale bytecode.**
    Gotcha 16 covers substring collisions. This one is worse because it is
    *intermittent*. CPython validates a `.pyc` against the source's
    **(mtime_seconds, size)** — so two mutations that remove the **same number of
    characters**, applied within the same wall-clock second, produce sources the
    cache cannot tell apart, and the second run silently executes the **first
    mutation's** bytecode. Hit for real in M5.1: two unrelated mutations each
    removed exactly 47 characters, and a genuinely-caught mutation reported as
    "not caught" on roughly half of runs. A mutation harness must purge
    `__pycache__` between mutations (`PYTHONDONTWRITEBYTECODE=1` alone is not
    enough — a stale `.pyc` from an earlier run is still readable). Symptom:
    re-running the same harness twice gives different answers.
23. **Live testing catches self-conflicts unit tests structurally can't** (M5.2).
    Every M5.2 unit test approved an extension from a *clean* registry, so none
    exercised re-approving one that was already loaded. Doing it by hand in the
    browser did: an approved extension edited on disk, then re-approved through
    the panel, failed with `EXTENSION_TOOL_CONFLICT` — its own already-registered
    tool names collided with the new load (an extension conflicting with itself).
    The fix is one loop: `_approve_extension` unregisters whatever that extension
    previously claimed before re-loading. The lesson is that a test suite that
    always starts from a clean slate will never see a "second time" bug; the live
    walk-through is not ceremony. Regression:
    `test_re_approving_a_changed_extension_loads_the_new_bytes`, which fails
    without the pre-unregister loop.
24. **Lazy initialisation loses the work that has to resume itself** (M5.4,
    `extensions/timers-reminders/extension.py`). The extension was lazy
    everywhere on principle — importing *is* executing (§5), so a module body
    should do as little as it can — and the scheduler thread started inside
    `add()`. That is correct until a restart: the pending timers are read back
    off disk and then **nothing ever looks at them again**, because nobody
    calls `add()`. A reminder set yesterday never fires, and the only way to
    wake it is to set a *new* timer, which no user would think to do. Fixed
    with one `_schedule()` call at the bottom of the module plus an
    `_ensure_thread()` when the loaded schedule is non-empty. **The lesson is
    about the tests, not the code:** every unit test drove `tick(now)` by hand,
    so they proved the firing logic perfectly and never once proved that
    anything *calls* it — the gap between "the function works" and "the function
    runs". Caught by restarting the real backend with a live timer and watching
    the deadline pass with the entry still sitting in `timers.json`. Same shape
    as gotcha 23: the thing a test suite structurally cannot see. Regression:
    `test_a_restored_timer_wakes_the_scheduler_on_load`, which asserts
    `_SCHEDULE is not None` **before** calling `_schedule()` — checking after
    would build it in the test and pass with the fix reverted.
25. **`time.monotonic()` does not advance while macOS sleeps, so
    `threading.Timer` is wrong for anything a laptop might sleep through.**
    Arm a one-hour timer, shut the lid for two hours, and it fires an hour
    after the lid opens. Every deadline in `timers-reminders` is therefore an
    absolute `time.time()` fired by a 1 s poll, never a countdown — which is
    also what makes persistence correct, since an absolute timestamp survives a
    restart and a countdown does not. The poll body is a pure `tick(now)`, so
    tests inject a clock instead of sleeping.
26. **State that cannot be remembered across two processes has to be
    *derived*** (M5.3, `extensions/install.py`'s `provenance()`). `jarvis
    install` records `source` and `commit` when it approves in one breath, and
    that path worked first time. The flows this milestone *added* did not:
    install-then-decline-then-`extensions approve`, and `--force`-then-
    re-approve, are two separate CLI invocations with nothing persisted in
    between, and `extensions approve` called `store.approve(manifest, digest)`
    with no provenance at all — so approving an extension that plainly came
    from a URL **blanked** its source and commit. Carrying the previous
    record's values forward would have been worse than blanking after a
    `--force`: it would claim these bytes are a commit they are not. The fix is
    to read both back off the `.git` the install left behind — which is only
    possible because `.git` is excluded from the digest, so keeping it costs
    the identity nothing. Informational, never authoritative: that checkout is
    as editable as the rest of the folder, and nothing consults it when
    deciding what may run. **Only visible live**, because every unit test
    installed and approved in one call.
27. **A `git clone` cannot be verified against a static file server.** Dumb HTTP
    refuses shallow clones outright — *"dumb http transport does not support
    shallow capabilities"* — and `jarvis install` uses `--depth 1`, so
    `python3 -m http.server` over a `git update-server-info` repo proves
    nothing. Python 3.13 also removed `CGIHTTPRequestHandler`, so
    `git-http-backend` is not a one-liner either. The smart protocol is two
    endpoints (`GET /info/refs?service=git-upload-pack` and
    `POST /git-upload-pack`, both proxying `git upload-pack --stateless-rpc`),
    which is ~50 lines of scratch tooling and exercises the exact path GitHub
    speaks. Worth rebuilding rather than reaching for a real network repo: it
    keeps the check hermetic and lets the test move HEAD to a new commit on
    demand.
28. **An overloaded `None` can make a whole feature inexpressible** (M5.5,
    `storage/conversations.py`). `append_turn(parent_turn_id=None)` meant
    "append to the active leaf", which left **no way to say "a turn with no
    parent"** — so a root sibling, which is precisely what editing the first
    message of a conversation produces, could not be represented at all.
    `test_root_branching` had recorded the limitation in a comment for four
    phases rather than anyone reading it as a bug. Fixed by giving the two
    meanings separate values: `None` is now no-parent, and `ACTIVE_LEAF` — a
    sentinel that is deliberately **not a `str`**, so a turn id off the wire can
    never impersonate it — is "carry on", and the default.
    **The wire has to keep the same distinction, and `.get()` cannot.**
    `msg.get("parent_turn_id")` returns `None` both when the key is absent and
    when it is explicitly null, so the first version of this quietly turned every
    ordinary second message into a root sibling. JSON *can* tell them apart —
    `"parent_turn_id" in msg` — and `protocol.parent_turn_from` is the one place
    that does. Caught by an existing test
    (`test_chat_roundtrip_streams_and_persists`) within a minute of the change,
    which is the argument for a suite that covers the boring paths.
29. **Turn metadata that only arrives with `history` leaves fresh turns inert**
    (M5.5). The branch and edit controls hang off the *turn*, and a turn's id is
    only known once `chat.done` reports it — so a message the user had just sent
    had no `turnId`, no entry in the path metadata, and therefore no controls.
    Every unit test loaded a conversation from history, where the metadata is
    always present, so none of them could see it; in the browser it read as "the
    edit pencil is missing on the messages I care about most". `chat.done` now
    stamps the new turn id onto the messages the exchange put on screen and
    appends the turn to the path, rather than paying a history round trip.

30. **A green CI can ship a mute app, because PyInstaller cannot see a path built
    at runtime** (M6.0, `scripts/sidecar.spec`). Three packages resolve their own
    data with `Path(__file__).parent / ...` *while running*, which static analysis
    never follows: `kokoro_onnx/config.json` and `language_tags/data/json/*.json`
    are read at **module import**, and `espeakng_loader` resolves both its
    `libespeak-ng.dylib` and its `espeak-ng-data` directory that way. None were
    collected, so `from kokoro_onnx import Kokoro` raised `FileNotFoundError` and
    the entire TTS stack was dead in every packaged build ever produced.
    **The reason nobody noticed is the smoke test's shape**: `build_sidecar.py`
    started the exe and read the ready line, and Kokoro loads *lazily* (gotcha 11,
    which is load-bearing and must not be "fixed"), so the sidecar boots
    perfectly and only dies the first time the user asks it to speak — into a
    swallowed log line, not a crash. A build gate that proves startup proves
    nothing about a lazily-loaded subsystem. The check added is **derived, not a
    hardcoded file list**: for each named package it compares the non-`.py` files
    in the build venv against the frozen tree, so a dependency that renames or
    adds a data file is followed automatically. `collect_data_files` — *not*
    `collect_dynamic_libs` — is right for the espeak dylib too, because it keeps
    the file package-relative, which is exactly where that runtime lookup goes.
    onnxruntime and whisper.cpp need nothing: their libraries hang off load
    commands on an extension module, which PyInstaller *does* follow (verified in
    the built tree rather than assumed).
31. **espeak-ng `exit()`s the process when its data path exceeds 151 characters,
    and a .app is 83 characters deep** (M6.0, `tts/espeak.py`). Measured, not
    inferred: 151 works, 152 kills the sidecar with rc=1 — the first time Jarvis
    speaks, which the user sees only as "Backend didn't start in time". It cannot
    be caught, because there is no exception; by the time anything could handle it
    the process is gone. The one diagnostic it emits is actively misleading —
    espeak falls back to a path baked in when **espeak itself** was compiled
    (`Error processing file '/Users/runner/work/espeakng-loader/…'`), so the error
    names a GitHub Actions directory that has nothing to do with this machine.
    In-bundle the path is `Contents/Resources/sidecar/jarvis-backend/_internal/
    espeakng_loader/espeak-ng-data`, **83 characters after the .app**, so
    `/Applications` is fine at 107 and
    `~/Downloads/jarvis-v0.1.0-macos-arm64-unsigned/Jarvis.app` is fatal at 157 —
    and running straight out of `target/release/bundle/macos/` is 179, which is
    how it was found. Fixed by copying the data directory to `<data dir>/
    espeak-ng-data` (69 chars here) **only when the bundled path is over the
    limit**, so the /Applications majority pays nothing. **A symlink does not
    work and looks like it does**: phonemizer resolves the link before espeak
    sees it, so a short symlink to a long target still fails — and, worse, a test
    written with symlinks passes at *any* length and hides the bug entirely.
    Test with real directories.
32. **`collect_data_files` silently drops `.so` but keeps `.dylib` and `.dll`,
    so a packaging fix can be correct on two platforms and broken on the third**
    (M6.1, `scripts/sidecar.spec`). Gotcha 30's fix — `collect_data_files(
    'espeakng_loader')` for the espeak library — worked on macOS, worked on
    Windows, and **has never once built on Linux**. The reason is a one-line
    asymmetry inside PyInstaller: `collect_data_files` excludes everything
    ending in `PyInstaller.compat.ALL_SUFFIXES`, and that is Python's
    **extension-module** suffix list, not a library list —
    `['.py', '.pyc', '.cpython-313-darwin.so', '.abi3.so', '.so']`. `.dylib`
    and `.dll` are not in it and sail through as data; a Linux shared library
    *is* `.so`, matches `**/*.so`, and is dropped **without a warning**. The
    docstring says so in seven words ("based on extension check") and nothing
    else does. Fix: collect libraries **explicitly** from the installed package
    directory (`collect_package_libraries`, derived by walking it, so a renamed
    or added library is followed automatically) and strip them back out of the
    `collect_data_files` result (`drop_libraries`) so macOS and Windows do not
    declare them twice. `collect_dynamic_libs` is still NOT the answer: it
    flattens to the bundle root, and `espeakng_loader.get_library_path()` looks
    package-relative. **The generalisable lesson: gotcha 30's derived gate is
    the only reason this was ever seen.** It fired on the first release tag
    anyone pushed, naming the exact missing file — a hardcoded expected-files
    list written on a Mac would have listed `libespeak-ng.dylib` and passed
    Linux happily. Also: **the first diagnosis was wrong.** "PyInstaller
    reclassified the .so out of datas" is plausible, fits the log's own
    "binary vs. data reclassification" line, and would have produced a fix that
    changed nothing, because the file never reached the reclassifier. Reading
    PyInstaller's source cost five minutes and a CI cycle costs eleven.

33. **The assistant said its own wake word and interrupted itself — and five
    phases of acoustic testing could not see it** (M6.2, `wake/service.py`,
    `server/voice.py`). Ask the packaged app to *"introduce yourself"* and it
    speaks two words — *"I'm JARVIS"* — then stops dead and starts listening.
    Every link was working as designed: the wake detector runs during the
    speaking phase **on purpose** (that is wake-word barge-in, an approved v1
    tier), there is no AEC so the microphone hears the speakers (gotcha 7 says
    so), and `handle_wake` cancels the in-flight generation, which its own
    comment describes as stopping playback instantly. Nobody had put those
    three facts next to a reply that contains the word "Jarvis".
    **Measured on the real chain, through real speakers, into the real mic:**
    *"I'm JARVIS, your computer's personal assistant."* scores **0.990** against
    a 0.5 threshold and fires **three times** in one sentence; *"The capital of
    France is Paris."* scores **0.000**. One variable.
    **Why the suite structurally could not catch it**: the bug needs the
    assistant to say its own name, and every acoustic verification in the
    project's history used a reply that happens not to contain it — the M6.1
    hour-long soak ended on *"The capital of France is Paris."* (literally the
    control line), A3 on *"Summary written to …"*, M5.4 on *"Hello there
    friend."* Same family as 23, 24 and 27, but sharper: this one is reachable
    by the single most obvious sentence a new user will ever type, and it
    survived to a release candidate.
    **The fix is deliberately not "suppress wake while speaking."** That is
    three lines and it silently deletes barge-in. Instead the exchange takes a
    *second*, independent suppression for any sentence whose text contains a
    wake word (`contains_wake_word`), held to the end of that turn's speech —
    so barge-in stays live for every reply that cannot possibly trigger the
    detector. It generalises past the reported bug for free: reading aloud a
    file or web page containing "Jarvis" was the same hazard.
    `speak_line` needed it too, and is worse — a notification is spoken with
    wake **fully** live, because no turn is in flight to have suppressed it.
    **`_WAKE_WORDS` is `("jarvis",)` and the omission is the design.** Adding
    `"friday"` before the "Hey Friday" model ships would blind barge-in on *"your
    meeting is on Friday"* to protect a wake word that cannot fire.
    **A verification trap came with it, and it nearly published a false pass:**
    `[wake] enabled` in `config.toml` is **not** the toggle — the wake state
    lives in `state.toml` in the *data* dir (`config.save_wake_enabled`). The
    first acoustic re-run therefore reported a clean PASS with the wake service
    **switched off**, which proves exactly nothing. Always assert the control
    first: enable via the `wake.set` message, play the wake word into an *idle*
    app, and see it fire — only then is a negative result evidence.

34. **"Unsigned" and "broken-signed" are different products, and every build
    this project ever shipped was the second one** (M6.2,
    `app/src-tauri/tauri.conf.json`). Tauri was never told to sign the macOS
    bundle — there was no `bundle.macOS.signingIdentity` key at all — so
    `Contents/_CodeSignature/CodeResources` was **absent** while the main
    executable's code directory declared that resources must be sealed.
    `codesign --verify --deep --strict` failed on every artifact ever produced
    with *"code has no resources but signature indicates they must be
    present"*. The consequence is not cosmetic:

        consistent ad-hoc seal  → "cannot be verified"  → Open Anyway WORKS
        resource seal missing   → "is damaged"          → Open Anyway CANNOT fix it

    A user who downloads the dmg gets the second dialog and is told to move the
    app to the Trash. **Observed by the owner on rc4**, minutes after installing
    it.
    **Why it survived four release candidates and an entire verification
    milestone:** the two states are indistinguishable without a quarantine
    flag. `gh run download` does not set `com.apple.quarantine`, so Gatekeeper
    is never consulted, and a broken bundle launches perfectly — which is
    exactly what M6.1's B1 observed and reasonably read as success. `codesign
    -dv` also *looks* fine: it reports `Signature=adhoc` / `TeamIdentifier=not
    set`, matching the docs word for word, because it is reading the **main
    executable's** embedded signature and not the bundle seal. Two independent
    checks both said "unsigned, as intended". Only `--verify --strict` and a
    real quarantined double-click disagreed.
    **The diagnosis that isolated it:** two copies on disk, one launching and
    one refusing, with *identical* broken signatures — the only difference
    being that one carried quarantine. The working copy was never healthy;
    macOS simply never checked it. Do not read "it launches here" as evidence
    about a signature.
    **Fix:** `"macOS": {"signingIdentity": "-"}`. Tauri then runs a proper
    bundle sign (nested sidecar included — verified `Signature=adhoc` on the
    frozen binary too), `--verify --deep --strict` passes, the bundle
    identifier becomes the real `app.jarvis-assistant.desktop` instead of a
    synthesised `jarvis-0d3473aa…`, and a quarantined copy now returns a plain
    `spctl: rejected` — the recoverable, unnotarized path. **Signing does not
    break PyInstaller**: the frozen sidecar in the signed bundle boots and
    synthesises speech (checked, because ad-hoc signing a bootloader is exactly
    the sort of thing that could have).
    **`release.yml` now gates on it**, same argument as gotcha 30's derived
    data gate: a property nobody can see by looking has to be asserted by the
    build, or the next person to touch bundling reintroduces it. The gate also
    asserts `Signature=adhoc`, so it cannot be "fixed" by requiring a paid
    identity this project deliberately does not buy.

35. **Fixing the signature silently revoked the microphone, and `cp` into
    /Applications silently translocated the app** (M6.2, discovered minutes
    after gotcha 34's fix). Voice stopped working entirely — neither "Hey
    Jarvis" nor ⌘M — with a **green** baseline and a fix that had just been
    verified acoustically. Two independent environmental causes, no code
    regression:
    *(a)* **macOS keys TCC permissions on code identity.** Gotcha 34's fix
    changed the bundle identifier from a synthesised `jarvis-0d3473aa5636e31f`
    to the real `app.jarvis-assistant.desktop`, so the OS sees a **different
    app** and the previously granted microphone permission no longer applies.
    Both the wake word and push-to-talk need the mic, so both die at once, and
    nothing says why — the mic simply returns nothing.
    **The release consequence, which is the important half:** anyone upgrading
    across this change loses their mic grant and must re-approve. It should be
    in the rc5 release notes, and the general rule is that **changing signing
    identity is a breaking change for every OS permission the app holds** —
    never do it casually after a release.
    *(b)* **`cp -R` into /Applications is not a Finder drag.** App Translocation
    applies to a quarantined bundle that the *user* has not moved, so copying
    one in from a shell still gets it run from a random read-only
    `/private/var/folders/…/AppTranslocation/<UUID>/d/Jarvis.app` — 145
    characters, which also puts the espeak data path near 228 and straight over
    gotcha 31's cliff.
    **CORRECTED in M6.3 — "Open Anyway" does NOT end translocation.** This
    entry originally said the click clears quarantine and therefore fixes (b).
    It does not, at least on macOS 15: approving sets the `USER_APPROVED` bit
    and **leaves the attribute in place** (observed as
    `com.apple.quarantine: 00c1` = `DOWNLOAD | HARD | USER_APPROVED` on an app
    the owner had already approved), and translocation keys on the attribute
    being *present*, not on approval. So the app launches with no dialog and
    still runs from `/private/var/folders/…`. What actually ends it is removing
    the attribute (`xattr -dr com.apple.quarantine`) or a real Finder drag,
    which marks the bundle user-moved. Note the consequence for verification:
    once approved, a copy can no longer reproduce the first-run dialog, so
    Gatekeeper screenshots must come from a **fresh download**, not from a
    machine that has already said yes.
    **The diagnostic lesson:** TCC failures are invisible from inside the
    process and `TCC.db` is unreadable without Full Disk Access, so a mic that
    returns silence looks identical to a mic that was never granted. When voice
    dies right after *any* change to signing, bundling, or install location,
    suspect identity before suspecting code — and check
    `codesign -dv | grep Identifier` first, because it takes one second and the
    alternative is auditing the audio path.

36. **Signing the bundle turned ON the hardened runtime, which made the missing
    microphone entitlement fatal — the app was deaf with a green baseline and
    no way to grant anything** (M6.3, `app/src-tauri/Entitlements.plist`).
    Gotcha 35 blamed the identity change for the dead microphone and was only
    half right. The identity change was real, but re-granting could never have
    worked, because there was nothing to grant:

        signingIdentity: "-"  → Tauri signs the bundle
                              → Tauri also sets flags=0x10002(adhoc,RUNTIME)
                              → hardened runtime ENFORCES entitlements
                              → bundle declared NO entitlements at all
                              → microphone denied, silently, forever

    Before M6.2 the bundle was never signed, so there was no hardened runtime,
    so entitlements were never enforced and the mic worked with none declared.
    **The fix for gotcha 34 is what broke it**, and it broke it in a second,
    independent way on top of 35a — one commit, two voice regressions, only one
    of which was diagnosed at the time.
    **The fingerprint is exact silence, not an error.** The stream opens, every
    callback fires on schedule, and every sample is `0.0`. Measured through the
    packaged app: **79/79 chunks at 0.000**, while a plain shell process in the
    same room, at the same volume, seconds later read **93/93 non-zero (max
    0.393)**. That differential is the whole diagnosis — same hardware, same
    room, different code identity.
    **No permission prompt is ever shown, and that is the tell.** The request is
    refused before TCC is consulted, so `tccutil reset Microphone
    app.jarvis-assistant.desktop` succeeds, changes nothing, and re-launching
    still produces no dialog. If an app has no TCC entry and *still* does not
    prompt, stop investigating permissions — the answer is upstream of them.
    Check `codesign -dvvv | grep flags=` for `runtime`, then
    `codesign -d --entitlements -` for what the bundle actually declares.
    **The entitlements plist must not contain XML comments.** The first version
    documented all of the above inline and `codesign` rejected it with
    `Failed to parse entitlements: AMFIUnserializeXML: syntax error near line
    5`. AMFI's parser is not a general plist parser. Keep the file minimal and
    put the prose here — which is also why `disable-library-validation` is
    deliberately absent: the frozen sidecar is a separate, non-hardened process,
    so the dylibs it loads are not the app's concern, and every entitlement is a
    hole in the runtime the security model just bought.
    **`release.yml` gates both halves** (runtime flag AND the entitlement), same
    argument as gotchas 30 and 34: run the gate against the *current* artifact
    first and watch it fail, or it is decoration. It failed on the build that
    was installed at the time, naming the missing key.
    **Verified after the fix, on the packaged app**: the mic differential
    inverted (58/59 non-zero, max 0.506), a full turn ran
    `loading→listening→transcribing→thinking→speaking→idle(done)`, the wake
    detector fired on an idle app (control asserted first, per gotcha 33),
    *"Introduce yourself"* spoke its whole reply with the reply **confirmed to
    contain "JARVIS"**, and barge-in still cut an ordinary reply off with
    `reason="stopped"`.
    **CONFIRMED through a real build (M6.3):** Tauri does apply
    `bundle.macOS.entitlements`, and it survives the `--config
    tauri.release.conf.json` layer, which only overrides `bundle.resources`.
    Verified inside the shipped dmg, not on an intermediate.
    **Two bugs in the gate itself, both found only by running it** — which is
    the entire argument for running gates against real artifacts:
    *(i)* `--bundles dmg` **deletes** `target/release/bundle/macos/Jarvis.app`
    after building the dmg ("Cleaning …Jarvis.app" in the log), so a gate
    pointed at that path fails with "No such file" on every real tag. The gate
    now mounts the dmg and checks the bundle that actually ships, which is also
    the more honest target. *(ii)* `codesign … | grep -q` exits 141: `grep -q`
    stops at the first match, `codesign` takes SIGPIPE, and `set -o pipefail`
    turns a perfectly good bundle into a failed build. Read once into a
    variable and match with `case` instead.
    **Mutation-proved:** removing `bundle.macOS.entitlements` and rebuilding
    makes the gate exit 1 with the intended error — while
    `codesign --verify --deep --strict` still reports "valid on disk, satisfies
    its Designated Requirement". That is gotcha 36 in one line: the signature
    gate passes on a bundle that ships deaf, which is exactly why the second
    gate has to exist.

37. **Nothing could tell "nobody spoke" from "the microphone is dead", and the
    test suite had that blindness written into its fixtures** (M6.3,
    `audio/silence.py`). Every symptom of gotcha 36 arrived as `no_speech`, and
    the UI said *"Didn't catch anything — press the mic and try again"*, which
    is advice that can never work. Worse on the wake path: with the toggle on
    and the mic revoked the user gets **no feedback at all** — no state change,
    no error, nothing — which is precisely what "Hey Jarvis isn't working" felt
    like from the outside.
    **The discriminator is exactness, not loudness.** A revoked or hardware-
    muted microphone returns samples of *exactly* `0.0` forever; a live mic in a
    soundproof room still returns room tone, self-noise and ADC dither. So the
    test is `== 0.0` on every sample, never "RMS below epsilon" — an epsilon
    would need tuning against real rooms and would start guessing.
    **The fixture was the bug's best hiding place:** `test_voice_ws.py` defined
    `SILENCE = np.zeros(...)` and used it everywhere to mean "a quiet room", so
    a dead microphone and a quiet room were **byte for byte the same input**.
    No test could have distinguished them. Quiet is now seeded low-amplitude
    noise (`ROOM_TONE`) and `np.zeros` means the one thing it means on real
    hardware (`DEAD_MIC`). Watch for this shape elsewhere: a fixture that
    models an edge case as a perfect zero usually deletes the edge case.
    Backend emits codes only: voice turns end `reason="mic_silent"` instead of
    `"no_speech"`, and the wake service reports `MIC_SILENT` once per deaf
    episode (latched, re-arming only after real audio returns — a warning every
    three seconds forever is not help).
    **One mutation came back NOT CAUGHT and that was the useful run.** The
    "reported once, not per chunk" assertion was vacuous: returning `"silent"`
    ends that listen session, the service opens a *fresh* capture, and the test
    had queued its remaining chunks on the abandoned one. It passed against a
    deliberately broken implementation. Feed the *current* capture.

38. **Opening the microphone ran on the event loop, so a pending permission
    prompt froze the entire backend** (M6.3, `server/voice.py`). Found by
    accident while verifying gotcha 36's fix on a freshly installed build:
    every `voice.start` wedged the sidecar. Not just voice — `/health` stopped
    answering, the WebSocket stopped accepting connections, and existing
    clients died with `keepalive ping timeout`. The process sat at 0% CPU
    looking perfectly healthy in `ps`.
    **`sample <pid>` named it in one shot**, which is the reusable lesson:

        task_step_impl  (in _asyncio…)      <- inside a coroutine
          Pa_OpenStream  (in libportaudio)
            OpenAndSetupOneAudioUnit

    `RealVoiceIO.open_capture()` did `MicCapture(); cap.start()`, and `start()`
    is `Pa_OpenStream`. On macOS that call **blocks while TCC decides whether
    the app may record** — and if the permission prompt has not been answered,
    it blocks indefinitely. Reinstalling changes the code identity, macOS
    re-asks, and the first ⌘M hangs the whole sidecar.
    **Why it never showed before:** the block only lasts as long as the
    decision. With a grant already in place `Pa_OpenStream` returns in
    milliseconds, so a year of testing on an already-approved machine sees
    nothing. It needs a *first run on a new identity*, which is exactly the
    upgrade path no test covers.
    Fixed by splitting construction from start: `MicCapture()` still needs the
    running loop, so it is built on the loop and `await
    asyncio.to_thread(capture.start)` opens it. The `Capture` protocol already
    declared `start()`, so nothing else moved.
    **The obvious test does not work, and passing it means nothing.** "Send
    voice.start, then ping, assert the pong is fast" passes against the broken
    code — the `loading` frame that tells the client the open has begun is
    itself only flushed once the loop is free, so the measurement starts after
    the block has ended (measured 0.000s against deliberately blocking code).
    The test asserts **thread identity** instead: whichever thread calls
    `start()` must not be the one that called `open_capture()`. That cannot be
    faked by scheduling luck.

39. **The readiness gate answered "is there a microphone?" and printed it as
    "is the microphone working?"** (M6.3, `server/readiness.py`). Through the
    entire gotcha-36 outage the first-run gate reported
    `ready: true, microphone: ok, count: 2` on a machine that was completely
    deaf. It was not lying by accident — `_mic_check` enumerated devices, and a
    *denied* device enumerates perfectly. The row simply answered a different
    question from the one the user reads it as.
    **The fix is not to probe.** Opening a stream inside readiness looks
    obvious and is wrong three ways: `Pa_OpenStream` blocks on an unanswered
    permission prompt, so readiness would hang exactly when it matters
    (gotcha 38); it contends with the always-on wake service for the device;
    and it drags the OS permission prompt in front of a user who may only ever
    type, out of the context that would explain it.
    **So nothing probes — the components that already hold the microphone
    report what they saw.** `MicHealth` (audio/silence.py) is written by the
    voice exchange (from its `SilenceWatch`), by `handle_wake` (a trigger is
    proof of real audio — nothing scores against zeros) and by
    `handle_mic_silence`. Readiness reads the verdict. Cost: nothing.
    **`UNKNOWN` is a first-class answer and the row now says so.** A text-only
    user who never triggers voice has told us nothing about their microphone,
    so the row reports `verified: false` — "there is a device, nobody has
    tried". Collapsing that into `ok` is precisely the original bug, and the
    mutation run proved the point: making `verified` return `True`
    unconditionally was **NOT CAUGHT** until a test was added for the untested
    case. A flag that is only ever asserted true is not a flag.
    Never fatal, unchanged: a broken mic costs voice, not conversation — the
    same reasoning that keeps missing voice models a warning.
    **It broke CI on the first push, and the reason generalises.** `_mic_check`
    asks the OS for input devices *before* it consults the verdict — correct
    ordering, because "no audio runtime at all" is a more fundamental answer
    than "the mic delivered silence" — but it means a runner with no PortAudio
    answers `AUDIO_RUNTIME_MISSING` and never reaches the branch under test.
    The tests asserted against real hardware and passed only because this Mac
    has a microphone. Fixed by pinning the device layer with a fake
    `sounddevice` module, so they assert the same thing everywhere.
    **The reusable bit: to test a layered check, pin the layers underneath it.**
    And `tests/test_readiness.py` already said so in a comment — *"present on a
    dev Mac, absent in CI"* — which is why the pre-existing mic test asserts
    `status in ("ok", "warn")`. The warning was there and got walked past.
    A CI runner can be simulated locally in about five lines (force
    `import sounddevice` to raise, run the suite); doing that turns a
    push-and-wait cycle into seconds and is worth it for anything hardware-
    adjacent.

## Repo map

```
app/            Tauri 2 shell (src-tauri/) + React frontend (src/)
                capabilities/default.json ← the handshake fix, don't delete
backend/        Python sidecar (jarvis_backend/: server audio wake stt tts llm
                agent tools security extensions storage doctor)
extensions/     timers-reminders (x-platform ref). calendar-macos is a manifest
                with no code — CUT from v1, excluded from BUNDLED
docs/           architecture.md, security-model.md, extensions.md, latency.md,
                unsigned-install.md, HANDOFF.md (this), design/sphere.md
docs/design/sphere-refs/  the sphere UI reference images (gif + avif)
scripts/        build_sidecar.py + sidecar.spec, fetch_models.py
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
4. ✅ **Agency + security** — **DONE 2026-07-24, and the largest phase.** Permission
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
   ✅ **M4.5 web_fetch + SSRF DONE** (2026-07-24) — **Phase 4 tool list complete
   (files, shell, web_fetch).** `tools/web.py` (the fetch) + `security/ssrf.py`
   (the guard, previously an empty stub). `web_fetch` is **`ask`** — every fetch
   confirms showing the URL, the exfiltration defense the SSRF guard can't provide
   (`safe` breaks §3's "safe = read-only" invariant; web egress is a side effect).
   It's the canonical **taint** source (`taint_source=url`). SSRF guard: scheme
   allowlist (http/https), resolve-and-check-**every**-IP via `ipaddress`
   classification (superset of §4's CIDR list — covers IPv6, IPv4-mapped, decimal
   encodings), **IP-literal hosts validated directly not resolved**, and **every
   redirect hop re-validated** (302→metadata is the classic escalation). Bounded
   like shell: 512KB incremental read cap + 15s timeout (`JARVIS_FETCH_TIMEOUT_S`).
   HTML→text via stdlib; non-200 shown as `[HTTP N]`. httpx (existing dep) +
   stdlib only — no new dependency. No frontend changes; 6 new i18n codes
   (`URL_*`/`FETCH_*`). **DNS-rebinding residual documented, not closed** (owner-
   delegated), same posture as §2's file TOCTOU. **361 backend tests** (47 new,
   each mutation-proven — classifier, any-IP rule, scheme block, IP-literal,
   redirect re-validation, byte cap, taint). See gotcha 20.
5. ⬅ **Extended scope — IN PROGRESS.** The extension work splits four ways:
   **M5.1 loader + approvals (DONE)**, **M5.2 approval UI (DONE)**, M5.3 `jarvis
   install`, M5.4 the default extensions. Also: branching UI, model catalog UI,
   wake-word training + "Hey Friday", opt-in VAD barge-in.
   ✅ **M5.1 extension manifest + approval + loader DONE** (2026-07-24) — §5, the
   last security section, and the honest version of it. **The headline is what it
   does NOT do:** an approved extension is `extension.py` imported into the
   sidecar, running with everything that process can do, so `network = false`
   cannot stop `import socket`. The `[permissions]` block is a **declaration of
   intent, not a capability boundary** — enforcing it needs a subprocess per
   extension behind RPC, a different architecture. §5 now leads with that rather
   than burying it, and the approval prompt says it in those words.
   **What IS enforced, each mutation-proven:** approval is keyed on a SHA-256 of
   **every file** (manifest included, so a risk level cannot be lowered after the
   fact) — one edited byte ⇒ `changed` ⇒ not loaded; **approval precedes
   execution** (discovery reads TOML + hashes bytes and imports NOTHING, since
   importing *is* executing — tripwire: an extension whose module body writes a
   sentinel file, asserted absent); the record lives in the sandbox-excluded data
   dir so **nothing can approve itself**; risk levels are **floors** the core
   raises and never lowers (`network = true` ⇒ floor `ask`, the one enforceable
   consequence that declaration can carry); the manifest is an **allowlist** (an
   undeclared function is never registered); a name already registered is
   **refused**, so `read_file` cannot be hijacked; a broken extension is a
   `LoadResult`, never a startup crash.
   New: `extensions/{manifest,approvals,loader}.py` (the three stubs were 0
   bytes), `config.extensions_dir()`/`approvals_path()`, `jarvis extensions
   list|approve|revoke` (the CLI's prompt IS the approval dialog until M5.2 —
   `--yes` skips the question, not the printing), and 26 `extension.*` i18n keys
   (unreferenced until M5.2's UI, added so nothing has to be backfilled).
   **Core change: the `read_only` flag** — see gotcha 21. **481 backend tests**
   (120 new), 51 mutations proven. Behaviour out of the box is unchanged: nothing
   is approved, so nothing loads.
   ✅ **M5.2 extension approval UI DONE** (2026-07-25) — the panel, so approving no
   longer means a terminal. `extensions.list|approve|revoke` WS messages (payload
   is codes + data only, tool risks are the **effective** level not the declared
   one), a standalone `ExtensionsPanel.tsx` modal opened from a header puzzle icon
   with a pending badge, and `Registry.unregister`. **Two properties carry the
   security:** (1) **approval is two steps** — a list row, then a detail card
   showing declarations + effective risks + digest + the "runs as you" warning,
   and only there an Approve button; (2) **the digest is a correlation id** —
   echoed back on approve and re-hashed server-side, refusing `EXTENSION_CHANGED`
   if the folder moved under the panel (the same backend-mints/client-echoes shape
   as a confirm id). **Owner decision: approval applies live** (approve → imports
   off the event loop → tools usable with no restart; revoke → unregisters exactly
   the names that extension *claimed*, never the core tool it lost a conflict to).
   Revoke is a two-step inline confirm carrying the honest caveat: *tools gone now,
   code it already ran stays until restart.* **Live-verified in the browser-hosted
   build** against a scratch backend, all six steps (pending → detail card →
   approve-loads-live → edit-a-byte-shows-changed → revoke → back to pending), zero
   console errors. **The live run caught a real bug the unit tests missed:**
   re-approving a *changed* extension self-conflicted (its old version still held
   the tool names) — fixed by unregistering the prior version first, with a test
   that fails without the fix (gotcha 23). **507 backend tests** (26 new), 16 new
   mutations proven, tsc clean. Still out of the box: no approvals ⇒ one new header
   button and nothing else changes.
   ✅ **M5.4 default extension `timers-reminders` DONE** (2026-07-25) — the first
   extension that *does* something, and the milestone that found the extension API
   was request/response only. **The headline finding:** a tool takes a call and
   returns a string, and a timer's whole job happens *later*, with no model call in
   flight. There was no notification path anywhere in the stack (backend, frontend
   or Rust), and `voice.say` only worked mid-turn. So M5.4 adds the smallest surface
   that closes it: `extensions/host.py` (`notify` + `state_dir`, the only sanctioned
   thing an extension imports), a `notification` WS broadcast, a `NotificationToast`,
   and one new branch in `voice.say` routing to a new `speak_line()` when no turn is
   live. **The words still never originate in the backend** — the UI renders the
   sentence from `code` + `data` and sends it back as `voice.say`, the M4.2 confirm
   pattern — and the notification's id makes that **single-use**, so three open
   windows do not say the same line three times.
   **Risk levels: all four stay `safe`, decided not inherited** (reasoning recorded
   in `manifest.toml`). It only holds because of gotcha 21: an extension's `safe` is
   not read-only, so these already confirm once the conversation is tainted. A
   dialog in front of "set a timer for ten minutes" in a *clean* chat is the
   confirmation fatigue §1 names as an attack surface, and the real risk — a model
   looping `set_timer` — is a volume problem, which this codebase answers with a cap
   (`MAX_PENDING = 32`, plus a global 10/min notification rate limit).
   **Live-verified** (browser build, scratch dirs, qwen3:4b), and it earned it:
   *(a)* **open item 7 closed** — a model drove an approved extension's tool
   end-to-end for the first time, timer → toast a minute later. *(b)* **First live
   proof of gotcha 21**: `web_fetch` example.com, then ask for a timer in the same
   chat → the ROUTINE tool showed a permission dialog with the amber provenance
   block and **no "allow this session" button**. *(c)* Two windows open, one
   notification: **both** received it (fan-out, not `connections[-1]`) and **exactly
   one** spoke it. *(d)* Editing a byte flipped it to `changed`, the overdue timer
   stayed frozen until re-approval, then fired on load.
   **The live run caught a real bug** — see gotcha 24: a timer survived a restart and
   then never fired, because the scheduler thread only started inside `add()` and
   nothing calls `add()` after a restart. Every unit test drove `tick()` by hand, so
   they proved the firing logic and never that anything calls it. **593 backend
   tests** (86 new), 43 mutations proven, ruff + tsc clean.
   **Known limits, recorded rather than implied away:** a fired timer reaches a
   hidden window only *audibly* (no OS-level toast without `tauri-plugin-notification`,
   deliberately out of scope) and only while a UI is connected to answer `voice.say` —
   the same dependency shape as `wake.detected`; and **revoke removes tools but
   cannot unload code**, so a revoked extension's already-running scheduler still
   fires its pending timers (observed, not theorised — suppressing the notification
   was rejected as making revoke *look* complete while the extension still runs).
   Not done: `calendar-macos` (pyobjc — its own conversation), and nothing yet copies
   the bundled defaults into the data dir (documented `cp`, belongs with packaging).
   ✅ **M5.3 `jarvis install <url>` DONE** (2026-07-25) — the last promised piece of the
   extension machinery, and deliberately the least interesting one: install **delivers
   bytes; it does not bless them**. What it clones lands as `pending` and goes through
   the same declaration prompt as a hand-dropped folder — `_print_declaration` has one
   copy and two callers so a second entry point cannot quietly start showing less
   (mutation-proven from both directions). `extensions/install.py`, `jarvis install
   <url> [--ref|--yes|--force]`.
   **The load-bearing check is the URL, before `git` is ever invoked**: only
   `http`/`https`, because `git clone 'ext::sh -c "…"'` **executes that command** —
   `ext::` is a remote-helper transport, so an unvalidated pasted URL is RCE, not a bad
   fetch. An allowlist, since the set of transports git supports is not ours to track.
   The rest reuses checks that already existed: the installed name comes from the
   **manifest** (already `NAME_RE`-validated, so traversal is closed by an old check),
   and the digest is computed **in staging**, so a symlinked repo is refused before
   anything moves. Staging lives outside `extensions/` because `discover()` lists every
   subdirectory there. `--force` replaces the folder and **leaves the old approval
   record alone**, so new bytes read as `changed` — updates need no special case,
   because the content-keyed approval already is the mechanism.
   Small shared refactor: `tools/shell.py`'s `_child_env` → `child_env`, so there is one
   definition of what a Jarvis subprocess may see (the `JARVIS_WS_TOKEN` must not reach
   `git` either). Plus `GIT_TERMINAL_PROMPT=0`, so a private repo fails cleanly instead
   of hanging on a credential prompt nobody can see.
   **Verified against a real git server, not a mock.** Dumb HTTP cannot do shallow
   clones, so the live check runs a ~50-line smart-HTTP server (scratch tooling) and
   exercises the actual `--depth 1` path GitHub speaks: install → declaration → approve
   → `source` + `commit` in `extensions.toml` → the backend loads all four tools; then
   refuse-without-`--force`, `--force` → `changed` with the *old* record intact,
   `GIT_NOT_FOUND` with git off PATH, and every refused URL form through the real CLI
   with `ext::` executing nothing.
   **Two things the live run caught** — see gotcha 26 for the first: `jarvis extensions
   approve` **blanked the provenance** of an extension that was demonstrably installed
   from a URL, which is exactly the flow this milestone introduces (install → decline →
   approve later; `--force` → re-approve). Fixed with `provenance()`, which reads source
   and commit back off the checkout rather than trying to remember them across two
   processes. Second: the panel's "Approved, but it didn't load" reads as a *failure*,
   but a CLI install while the app is running produces exactly that state benignly —
   reworded to name both cases and say what to do.
   **648 backend tests** (55 new), 30 mutations proven, ruff + tsc clean.
   **Known limit, stated not implied:** the CLI is a different process from the sidecar,
   so an extension installed while the app is open is approved-but-not-running until a
   restart (or until Approve is pressed in the panel, which loads live). The CLI says so
   on the way out. A panel "install from URL" field would close it and was deliberately
   left out — it puts a network fetch and a `git` subprocess behind a webview message,
   and it is a second approval UI to keep in step.
   ✅ **M5.5 branching UI DONE** (2026-07-25) — the tree the app has carried since
   Phase 1, finally reachable. Edit a question or regenerate an answer to fork an
   alternative, and `‹ 2/3 ›` moves between them.
   **Most of it already existed and nobody could get at it.** `chat.send` already
   took `parent_turn_id`, `run_exchange` already used it for both the history
   context and the append, and `types.ts` already declared the field — so *forking*
   worked. **Coming back did not**: `set_active_leaf` was called by nothing but
   tests, and `history` carried no sibling data, so the UI could not know an
   alternative existed. New: `Store.tip()`, `Store.active_leaf()`, a
   `conversation.branch` message, `protocol.history()` carrying `siblings` per turn,
   and the 0-byte `BranchSwitcher.tsx`.
   **Three things that were not in the plan and had to be:**
   *(a)* **`None` meant two things.** `append_turn(parent_turn_id=None)` meant
   "append to the active leaf", so *a root turn had no representation* — editing the
   first message of a conversation was literally inexpressible, and
   `test_root_branching` documented the limitation in a comment. `None` now means no
   parent; `ACTIVE_LEAF` (a non-str sentinel) means "carry on". See gotcha 28.
   *(b)* **`Store.tip()`.** Switching to a sibling must land on that branch's *end*,
   not the turn clicked — otherwise a branch you had continued comes back looking
   truncated, which is exactly what an immutable tree exists to prevent.
   *(c)* **The race the plan predicted.** `parent_turn_id` stayed unresolved from the
   top of `run_exchange` to `append_turn`, so the live leaf was read **twice** —
   history from one branch, parent from another. Resolved once at the top now.
   **Verified live** (browser build, scratch dirs, llama3.2:3b), all eight steps:
   edit the first question → root sibling with `‹ 2/2 ›`; switch back → the original
   question, reply **and everything after it** return verbatim; regenerate → a second
   counter on the same turn; **switch branches mid-stream** (caught at 1477 streamed
   characters) → the stream survives and the reply lands on the branch it was asked
   in; reload → the branch persists with sibling counts `[2,2,1,1]`; and the sidebar
   order is unmoved (checked in the database, not just the UI). Final tree: two root
   turns, two children of the first, nothing destroyed.
   **The live run caught one bug** the tests could not: a freshly-sent turn carried
   no turn metadata (that only arrived with `history`), so **the edit and branch
   controls never appeared until something re-fetched** — you could not edit a
   message you had just sent. `chat.done` now stamps the turn id onto the messages
   the exchange put on screen. Fourth milestone running that the browser walk-through
   found something the suite structurally could not.
   **674 backend tests** (26 new), 18 mutations proven, ruff + tsc clean.
6. ⬅ **Ship — IN PROGRESS.** Installers, onboarding polish, docs, tagged unsigned
   release.
   ✅ **M6.0 the packaged build actually works DONE** (2026-07-25) — the first
   time anyone ran the artifact CI has been building on every tag. It did not
   work, and could not have: **every packaged build ever produced shipped mute**
   (gotcha 30) and **died outright on first speech if installed anywhere but
   /Applications** (gotcha 31). Both were invisible to a green suite and a green
   CI, which is the whole argument for this milestone existing.
   **Fixed:** `scripts/sidecar.spec` collects the three packages whose data is
   resolved at runtime (`kokoro_onnx`, `language_tags`, `espeakng_loader`);
   `scripts/build_sidecar.py` gained a **derived** bundled-data gate so the
   release build breaks instead of the release, plus a scratch `JARVIS_DATA_DIR`
   so a build script stops writing into the developer's real data dir;
   `tts/espeak.py` keeps the espeak data path under the 151-char cliff.
   **Also landed: `extensions/bundled.py`** — nothing had ever copied the bundled
   extensions into the data dir, so `timers-reminders`, built and live-verified in
   M5.4, **did not exist for any real user**. Seeding delivers bytes and does not
   bless them: seeded extensions land `pending`, nothing is ever overwritten (§5
   forbids extension auto-update, and that applies to us too), and the list is
   **explicit** so `calendar-macos` — a manifest with a 0-byte `extension.py` —
   cannot ship a stub that fails the moment it is approved.
   **Verified live in the real WKWebView, packaged, from /Applications**, against
   a scratch data dir: the sidecar spawning from `resource_dir()/sidecar/…`
   (`sidecar.rs:200`'s release branch, never executed before); first-run seeding
   on an empty data dir with `calendar-macos` correctly skipped; the extensions
   panel end-to-end (pending badge → detail card showing declarations, *effective*
   ROUTINE risks, digest and the "runs with the same access … isn't a sandbox"
   warning → Approve → Approved/Revoke), with the digest in `extensions.toml`
   matching the one the card displayed; the sphere, the RAM-tiered picker
   (`qwen2.5:7b · 7.6B — tight on 8GB`), and M5.5's edit/branch controls on a
   freshly-sent turn.
   **Two things the live run proved that no test could.** *(a)* **Zero telemetry,
   re-proven on the packaged artifact**: the sidecar holds exactly two sockets,
   its own loopback listener and one WebSocket — no outbound connections at all.
   *(b)* **§1's "absence of an answer is a deny", by accident and therefore
   honestly**: the machine screen-locked mid-turn, nobody could answer the
   `write_file` dialog, and the span persisted as `TOOL_CONFIRM_TIMEOUT` /
   `ok: false` with **the file never written**.
   **Measurement worth keeping:** a qwen3:4b tool-calling turn took **96 s**
   end-to-end on the 8GB M2 under memory pressure (three builds and Ollama
   resident), against gotcha 12's ~20 s on a quiet machine. It looks exactly like
   a hang — the backend is idle, there is no outbound connection to Ollama between
   requests, and the UI just sits on "Stop". Do not diagnose a stuck turn before
   waiting two minutes.
   **Not fixed, environmental:** `bundle_dmg.sh` fails here — create-dmg runs an
   AppleScript to style the Finder window and it times out (`-1712`) in a
   non-interactive session. The `.app` builds cleanly with `--bundles app`; the
   dmg step is only ever exercised on CI runners, and has never been verified by
   a human either. **687 backend tests** (13 new), 13 mutations proven, one
   deliberate "NOT CAUGHT" that deleted a dead branch rather than tuning a test.
   **The wake soak was attempted and is inconclusive** — 18 of 60 minutes, ended
   by the app being quit, with a suggestive but unproven RSS decline. See
   "Immediate next action" A2; it has to be redone before gotcha 8 can be called
   closed.
   ✅ **M6.1 verification debt + the Linux release DONE** (2026-07-26) — the
   milestone where the packaged app stopped being something only the owner could
   check. A `Jarvis.app` in `/Applications` has a bundle id and **can be driven
   with computer-use**, so A1/A3/A4 were run by the session rather than narrated
   to it. Three of the four items had never executed once.
   **A1 — `show_window`, first execution ever.** Sent a `write_file` turn, closed
   the window (hides to tray, `lib.rs:41`), and the confirm **revealed and focused
   it**, in the real WKWebView, from `/Applications`, against a scratch data dir.
   The dialog was correct in every particular M4.2 specified: amber PERMISSION
   badge, Deny default-focused, path + content shown, and — the conversation being
   clean — an "Allow this session" button. **A real finding came with it:** once
   hidden, the tray icon is the app's ONLY way back. There is no Dock icon,
   activating the app does not restore the window, and the Window menu carries no
   entry for it (all three checked). That makes `show_window` load-bearing while
   `lib.rs:13` discarded all three of its errors — they are logged now. The tray
   icon itself was proved present by diffing the menu bar across a quit, and
   `RunEvent::Exit` → `sidecar::kill` was proved clean (no orphaned sidecar) via
   ⌘Q, which is the same handler the tray's Quit drives. **What is still
   owner-gated:** the literal tray menu *clicks*. macOS hosts menu-bar extras
   inside the Control Centre process, which is not grantable to computer-use, and
   `osascript` is refused assistive access — so "Open Jarvis" and "Quit Jarvis"
   remain two unclicked buttons. Two clicks, thirty seconds, owner's.
   **A4 — `run_command` and `web_fetch` live, and the taint A/B.** `run_command`
   showed the **RISKY** badge with **only two buttons** — no session grant, which
   is `dangerous` enforced server-side — and returned `/Users/aayushsharma`,
   live-confirming M4.4's documented "cwd = home" decision for the first time.
   `web_fetch` showed the URL and fetched example.com for real. Then the same
   `write_file` tool as A1, in the now-tainted conversation, rendered a **different
   dialog**: the amber *"follows content Jarvis read from http://example.com"*
   block, and the session button **gone**. Same tool, same risk level, two
   dialogs — §3's escalation, isolated to one variable.
   **A3 — a spoken file turn, heard end to end.** "Hey Jarvis" synthesised through
   the speakers, heard by the real mic; whisper turned *"slash tmp slash j w slash
   notes dot txt"* into `/tmp/jw/notes.txt`; `read_file` ran with **no dialog**
   (read-only, correct); the `write_file` confirm carried **"from a spoken
   request"** plus the taint block; the file landed with a genuine summary; and the
   room recording caught Jarvis saying **"Summary written to /tmp/jw/summary.txt"**.
   **A3 also found the one real UX gap of the milestone** — see "the sandbox is
   undiscoverable" below.
   **A2 — the wake soak, run for the full hour and CLOSED.** 60 minutes with the
   window hidden to the tray, 30 samples, the app alive throughout — and then,
   after **62 minutes occluded**, a synthesised "Hey Jarvis" through the speakers
   woke it and the room recording caught **"The capital of France is Paris."**
   spoken back. That reply is the proof, and nothing weaker is: it can only
   happen if the WebContent process was alive enough to answer `wake.detected`
   with `voice.start`. Gotcha 8's `backgroundThrottling: "disabled"` **holds after
   an hour**. Worth noting the whole turn happened with **no window on screen at
   all**, which is the real product experience rather than a test rig.
   **The RSS story, settled.** The failed attempt's 38 MB → 5.2 MB monotonic
   decline was **memory-pressure compaction, not suspension** — it ran at 17% free
   memory. This run sat at 34-69% free and RSS *fluctuated* (3.4 → 18.4 at 16 min
   → 4.4), which a suspended process does not do; it never approached the ~600 KB
   signature the gotcha cites. **Do not read RSS as a suspension oracle** — it is
   a memory-pressure reading with a suspension signal buried in it.
   **One number that wants a cleaner re-measure**: sidecar CPU averaged **4.3%**
   across the soak (2.8-6.9% per 10-minute window), against M3.1's 2.4% budget.
   NOT a regression claim: this process had whisper, VAD and Kokoro resident from
   the A3 voice turns, where M3.1 measured a fresh wake-only process, and gotcha 6
   warns that idle CPU% on Apple Silicon reads high on E-cores. Re-measure on a
   freshly started sidecar that has never spoken before treating it as real.
   **B — the release workflow, and the Linux build that had never worked.** The
   first `v*` tag anyone ever pushed **failed**, and `publish` never ran, which is
   why no draft release or SHA256SUMS has ever existed. macOS built its dmg in
   4m32s and Windows its msi/nsis; **Linux died in the sidecar step** on
   `libespeak-ng.so: 1 data file(s) missing from the bundle` — gotcha 32, and
   caught only because M6.0's gate is derived rather than a hardcoded file list.
   Fixed; the second tag got Linux through the sidecar, through Rust, and through
   `Jarvis_0.1.0_amd64.deb`, failing at `linuxdeploy` on the AppImage.
   **`APPIMAGE_EXTRACT_AND_RUN=1` was tried on rc3 and did NOT fix it** — same
   error, same place, so it is not the missing-FUSE case it looked like. Tauri
   swallows linuxdeploy's stderr, so the log says only `failed to run
   linuxdeploy` and nothing else; the next diagnosis has to come from running
   linuxdeploy by hand in the workflow, not from the Tauri output. See §B2 for
   the recommendation, which is to stop paying for this and ship the `.deb`.
   **B1 — a dmg was downloaded, mounted and installed from, for the first time.**
   `Jarvis_0.1.0_aarch64.dmg`, 72.7 MB, pulled from the rc2 run's artifact. It
   mounts with the expected drag-to-install layout (`Jarvis.app` + an
   `Applications` symlink), so create-dmg's AppleScript styling works on a CI
   runner even though it times out locally (`-1712`). Installed to
   `/Applications`, launched, sidecar spawned from the bundle, WS up, and a
   wake-word voice turn completed end to end (*"Hello there friend."*).
   **The half of B1 that mattered most: the >151-character install.** Copied to
   `~/Downloads/jarvis-v0.1.0-macos-arm64-unsigned/Jarvis.app`, which puts the
   espeak data path at **158 characters**, over gotcha 31's cliff. The frozen
   sidecar started there, loaded Kokoro and synthesised with **no `exit(1)` and no
   espeak error** — and the fallback is confirmed to have *fired*, not been
   skipped: 121 files were copied to a 97-character path under the data dir.
   **That branch had never executed in a real bundle before** — it is unit-tested,
   and `/Applications` at 107 characters never reaches it.
   **B3 — one honesty check passed, one is untestable this way.**
   `codesign -dv` reports `Signature=adhoc`, `TeamIdentifier=not set`, exactly as
   `unsigned-install.md` says. **But the Gatekeeper flow itself was NOT
   reproduced**: `gh run download` does not set `com.apple.quarantine`, so the app
   never went through the "Apple could not verify…" path the doc walks users
   through. Only a browser download from a published release exercises that, and
   there has never been one. The doc also still carries its
   `<!-- SCREENSHOT PLACEHOLDERS -->`.
   **C — the decisions, made.** `calendar-macos` and "Hey Friday" are **cut from
   v1** and recorded in `architecture.md` rather than left implied. **Seventeen**
   zero-byte files were deleted — HANDOFF's inventory had said fourteen and missed
   `lib/api.ts`, `state/session.ts`, `state/settings.ts`. A 0-byte file the docs
   describe as if it exists is worse than its absence on a public portfolio repo.
   **705 backend tests** (18 new), 6 mutations proven, ruff + tsc + cargo clean.
   **Known gap, stated not implied — the sandbox is undiscoverable.** Nothing tells
   the model which directories it may touch: `prompts.py` names no paths (by
   design — prompt length is TTFT, and hardening it made llama3.2:3b's discipline
   *worse*), and `read_file`'s description is "Read a text file from the user's
   computer." A3's first attempt asked for "notes.txt in my workspace" and qwen3:4b
   guessed `/home/user/workspace/notes.txt` — refused, correctly, as
   `PATH_OUTSIDE_SANDBOX`. It bites hardest exactly where it matters most: a user
   **cannot speak an absolute path**, so voice + files needs the sandbox to be
   discoverable. Mitigated by luck on the default roots (a guess at
   `~/Documents/notes.txt` often lands) and fatal with any custom root. NOT fixed
   here, because both candidate fixes — naming the roots in the system prompt, or
   in the file tools' descriptions — spend the exact budget `prompts.py` documents
   as measured and dangerous. Owner's call, with `docs/tool-calling.md` numbers in
   hand.
   ✅ **CLOSED in M6.2** (2026-07-29) — measured, and the fear was the wrong one.
   `tools/filesystem.py`'s `roots_hint()` names the live `Sandbox` roots at the end
   of `read_file`/`list_dir`/`write_file`; `delete_file` and `run_command` are
   excluded on purpose (a destructive turn should not complete on a guessed path,
   and the shell **is not sandboxed at all**, so a sentence about roots on it would
   be the one placement that lies). ~125 tokens on a ~780-token tool block.
   **A/B'd with the probe, same machine, same session** (see the new section in
   docs/tool-calling.md): on **qwen3:4b — the only `tool-calling`-tagged model, so
   the only one this reaches by default — restraint did not move (18/18 → 18/18),
   routing did not move, malformed went 1 → 0, and discovery went 0/9 → 9/9.**
   The prompt-budget worry did not materialise, and the thing it was protecting
   was already broken: discovery measured **0/9 on both models** beforehand.
   The pre-fix guesses are the argument, and they are worse than "sometimes wrong":
   qwen3:4b answered "what's in my Downloads folder" with the relative string
   `Downloads`, wrote to `/Desktop/haiku.txt` (the filesystem root), and on "read
   notes.txt in my documents" **called no tool at all, 3/3**; llama3.2:3b answered
   the same question with `C:\Users\[User]\Downloads` — Windows paths on a Mac,
   with the placeholder left in. That is a model sampling its training
   distribution, not reasoning about this machine.
   ✅ **M6.2 the release exists, and the sandbox is discoverable DONE**
   (2026-07-29) — the milestone where `publish` finally ran. **713 backend tests**
   (8 new), 6 mutations proven, ruff + tsc + cargo clean.
   **The AppImage is CUT, not fixed.** `bundles: deb,appimage` → `deb`, and the
   `APPIMAGE_EXTRACT_AND_RUN` env came back out along with the comment asserting a
   missing-FUSE diagnosis that rc3 had already disproved — leaving it would have
   told the next reader something known to be false. Still ubuntu-22.04: the
   runner sets the `.deb`'s glibc floor.
   **B2/B1 CLOSED — the first successful Release run in the project's life.**
   `v0.1.0-rc4`: all three build jobs green, **`publish` green**, and a draft
   release carrying `Jarvis_0.1.0_aarch64.dmg` (72.7 MB), `_amd64.deb` (83.2 MB),
   `_x64-setup.exe` (51.7 MB), `_x64_en-US.msi` (70.1 MB) and — for the first time
   ever — **`SHA256SUMS.txt`**. The dmg was downloaded and verified against it with
   the exact command `unsigned-install.md` tells users to run: match.
   **C4 CLOSED.** The seven 0-byte backend modules are deleted, and
   `tests/test_hygiene.py` now fails on any 0-byte module under `jarvis_backend/`
   (mutation-proven) so there is no third sweep. `architecture.md` distinguishes
   two states that had been blurred: **specified-deferred** (the OpenAI-compatible
   and Anthropic adapters — the pattern is real, the implementations are not) and
   **cut from v1** (`take_screenshot`, and **clipboard**, which had been dropped by
   silence since phase 4 and is now an actual decision).
   **The sandbox is discoverable, and the fear was the wrong one.** See the M6.1
   gap entry above, now marked closed. Gated on the probe because `prompts.py`
   documents prompt text as a measured *discipline* risk; the A/B says the budget
   was not the problem. On **qwen3:4b** — the only `tool-calling`-tagged model, so
   the only one this reaches by default — restraint held at **18/18**, routing at
   15/15, malformed went **1 → 0**, and discovery went **0/9 → 9/9**. What the
   probe could not see before is that discovery was **0/9 on both models**: the
   thing the prompt budget was protecting was already broken.
   **Live-verified in the packaged frozen sidecar against a CUSTOM root** — the
   case HANDOFF called "fatal" — first typed, then **acoustically**: Kokoro through
   the speakers, heard by the real mic, whisper, and qwen3:4b emitting the
   **108-character** scratch path from the spoken words *"notes.txt in my
   workspace"*, `read_file` with no dialog (read-only, correct), and the summary
   spoken back. That is the M6.1 A3 failure, reproduced and fixed.
   **The probe gained a `discovery` group and a `--roots` switch**, because it
   hardcodes its own schemas and would otherwise have measured a change it could
   not see — two identical numbers. It now imports `roots_hint()` itself, so the
   A/B tests the sentence that actually ships. Also `LEAK xN` per case: the
   aggregate malformed count could not distinguish a new case finding a new leak
   from an old case regressing, and that ambiguity showed up immediately.
   **B3 — a real finding, and one honest gap.** `codesign -dv` says `adhoc` /
   `TeamIdentifier=not set`, as M6.1 recorded — but that reads the **main
   executable's** signature. The **bundle has no `Contents/_CodeSignature/
   CodeResources` at all**, so `codesign --verify --strict` and `spctl --assess`
   both fail with *"code has no resources but signature indicates they must be
   present"*. True of every build ever made here (local, the rc2 copy in
   `/Applications`, and rc4), so it is a property of bundling without an identity,
   not a regression. It matters because it is the difference between macOS saying
   *"cannot be verified"* (which "Open Anyway" fixes) and *"is damaged"* (which it
   does not). **Also found: App Translocation.** A quarantined app launched from
   `Downloads` or from inside the dmg runs from a randomised
   `/private/var/folders/…/AppTranslocation/…` path, which puts the espeak data
   path at **206 characters** — past gotcha 31's 151 cliff. `unsigned-install.md`
   now says to drag to `/Applications` *before* launching, and carries a
   verified/not-verified table instead of an unqualified walkthrough.
   **The owner found a real bug in the rc4 build within minutes of installing
   it: Jarvis wakes itself.** "Introduce yourself" → two words spoken, then
   silence and listening. Full write-up in gotcha 33; the short version is that
   the reply contains the wake word, the detector runs during speaking by
   design, and there is no AEC. Fixed with a second, wake-word-conditional
   suppression rather than by muting wake for the whole speaking phase, so
   barge-in survives. **722 backend tests** (9 more), 7 mutations proven, and
   both halves verified acoustically: the reported turn now speaks its whole
   reply (`reason="done"`), while "Hey Jarvis" over an ordinary reply still
   cuts it off mid-word (`reason="stopped"`). **rc4 ships this bug** — any
   build cut before this fix has it.
   **Still not seen by a human: the Gatekeeper dialog itself.** A hand-set
   `com.apple.quarantine` reproduced translocation and left the process alive at
   ~32 KB RSS with no sidecar for six minutes — consistent with it being blocked on
   the modal, which is what the doc describes, but screen access was declined so it
   was not confirmed. **Do not read that as "the app is broken"; it is unresolved.**
   It needs one person to double-click it once.
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

**The baseline, run before touching anything and after every batch.** All four
must be green; if the first one isn't, stop and say so rather than folding a fix
into new work:

```sh
cd backend && uv run pytest              # 705 passed
cd backend && uv run ruff check .        # clean
cd app && npm run build                  # tsc + vite, clean
cd app/src-tauri && cargo test --lib     # 2 passed
```

**Mutation proving is scratch tooling, rebuilt per milestone — deliberately.**
Every milestone since M4 has driven it from a throwaway script in the session's
scratchpad: a list of `(file, label, find, replace, test-selector)`, applied one
at a time, each expected to make its named test *fail*. Two rules are what make
it honest, and both are gotchas because both have lied here before: the `find`
string must occur **exactly once** (16), and `__pycache__` must be purged around
every run (22). A run that reports "NOT CAUGHT" is the useful outcome — in M5.3
three of them exposed checks that could never be the deciding branch, and the
right fix was deleting the code, not tuning the test. Nothing is committed: the
harness is disposable, the *tests* it validates are the artefact.

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

**Branch navigation landed in M5.5.** `Store.siblings()` had existed and been
tested since Phase 1 without ever being reachable; it now backs `‹ 2/3 ›`, with
`Store.tip()` added so switching lands on a branch's *end* rather than the turn
that was clicked.

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

## OPEN AT END OF M6.3 — start here next session

The baseline is **green** (726 backend, ruff, tsc+vite, 2 Rust) and voice is
**fully working on the installed build**, re-verified after the microphone was
granted: control fired on an idle app, mic **54/55** non-zero (max 0.473), a
complete turn ran `loading→listening→transcribing→thinking→speaking→idle(done)`,
*"Introduce yourself"* spoke its whole reply with the reply confirmed to contain
"JARVIS", and barge-in still ended `reason="stopped"`. Committed as `ea569d2`.

**The live probes now live in the repo**: `backend/tests/manual/probe_voice_live.py`
(`control` / `mic` / `acoustic`), auto-discovering the running sidecar's port and
token. Run `control` FIRST, always — it is what stops a "pass" from a
switched-off detector (gotcha 33). They found gotchas 36 and 38; the scratch
copies were lost mid-session, which is why they are checked in now.

1. ✅ **Voice is FIXED, and gotcha 35 was only half the story.** The mic was not
   recoverable by granting anything: signing turned on the hardened runtime and
   the bundle declared no `com.apple.security.device.audio-input`, so the stream
   returned exact zeros and macOS never prompted. See **gotcha 36**. Fixed with
   `app/src-tauri/Entitlements.plist` + `bundle.macOS.entitlements`, plus a
   `release.yml` gate on the runtime flag and the entitlement. Verified on the
   packaged app: mic 58/59 non-zero, wake control fired on an idle app,
   *"Introduce yourself"* spoke its whole reply (reply confirmed to contain
   "JARVIS"), barge-in still `reason="stopped"`.
   Translocation was also real and is fixed — but by removing the quarantine
   attribute, **not** by Open Anyway (see the correction in gotcha 35b).
2. ✅ **The entitlement fix IS confirmed through a real build.** Tauri honours
   `bundle.macOS.entitlements` and it survives the `tauri.release.conf.json`
   layer; verified inside the shipped dmg, and mutation-proved by removing the
   key and watching the gate fail. Two bugs in the gate itself were found by
   running it (a deleted `.app` and a SIGPIPE) — both fixed. See gotcha 36.
   `/Applications/Jarvis.app` is now a real build, not the hand-signed one.
3. ✅ **`v0.1.0-rc5` is CUT and the Release run was GREEN on all four jobs**
   (run `30747979798`, on `6355145`). macOS dmg, Windows msi+nsis, Linux deb,
   then `publish` — a **draft** release with four bundles and `SHA256SUMS.txt`.
   **The new macOS gate ran on the runner and passed**, which was the one
   genuinely unexercised thing: `hdiutil attach` works there, and the log says
   `shipped bundle ok: ad-hoc seal + hardened runtime + audio-input`. Confirmed
   it *executed* rather than skipping — a skipped gate would be worse than a
   failing one.
   **The CI artifact was then verified independently**, because a local build
   proves nothing about CI's: all four checksums verify, and the workflow's own
   gate text — extracted from the YAML and run verbatim against the
   **downloaded** dmg — passes, with `Identifier=app.jarvis-assistant.desktop`,
   `flags=0x10002(adhoc,runtime)` and `audio-input` present.
   The release body now carries the microphone re-grant warning and a note that
   rc1–rc4 are not installable; it is baked in at publish time, so it had to
   land *before* the tag.
   **rc4's draft release is deleted** (its artifacts fail Gatekeeper with "is
   damaged"); the `v0.1.0-rc4` git tag is kept as history.
   ✅ **The readiness microphone gap is CLOSED** — see gotcha 39.
4. **Finish the Gatekeeper walkthrough — needs a FRESH BROWSER download.**
   `spctl -a -t exec` on the fixed build returns a plain `rejected`, the
   recoverable "cannot be verified" path, so gotcha 34's fix is confirmed. Two
   things still block the screenshots and both are structural, not laziness:
   *(a)* a `gh release download` carries **no `com.apple.quarantine`** —
   re-confirmed on the rc5 artifact — so Gatekeeper is never consulted, which
   is exactly how gotcha 34 hid for four candidates; the download must come
   from a browser. *(b)* This machine has already approved the app and
   approval is **sticky** (gotcha 35b), so it cannot produce a first-run dialog
   at all. Needs a browser download, ideally on another Mac or a fresh user
   account.
5. **The two tray menu items** have still never been clicked (computer-use cannot
   reach Control Centre). Thirty seconds.
6. **A5** — Windows/Linux file tools by hand + the `qwen3:8b` probe on the A6000.
   Not v1-blocking; an untagged model already means tools off.
7. **Deliberately deferred, recorded not forgotten:** teachable refusals
   (`agent/loop.py` sends the model only `result.code`, so `PATH_OUTSIDE_SANDBOX`
   teaches it nothing — a zero-token complement to M6.2's discoverability fix,
   but it changes what every tool reports on failure); AEC (the real fix for
   gotcha 33's whole class); and the App Translocation espeak path, which the
   gotcha-31 fallback should cover but nobody has exercised.

## Immediate next action

**Phases 1-5 complete. Phase 6: M6.0 and M6.1 are DONE** — **705 backend tests,
2 Rust, ruff + tsc clean**. The macOS `.app` builds, installs, boots, seeds its
bundled extension, speaks, and every dialog and tool path in it has now been
driven live. See the Phase 6 entry above and gotchas 30-32.

**What is actually left before a v1 tag is now short**, and most of it is one
CI cycle plus two clicks:

1. ✅ **Linux AppImage CUT in M6.2**, not fixed. `bundles: deb`, env removed.
2. ✅ **`publish` RAN, on `v0.1.0-rc4`** — the first successful Release run ever.
   Draft release with four bundles and `SHA256SUMS.txt`; the dmg was downloaded
   and checksum-verified with the doc's own command.
3. ✅ **The Gatekeeper question is ANSWERED, and the answer was the bad one —
   now fixed.** The owner double-clicked a quarantined rc4 and got **"Jarvis is
   damaged"**, which "Open Anyway" cannot rescue. Root cause: Tauri was never
   told to sign the bundle, so the resource seal was missing on every artifact
   the project ever built. Fixed with `bundle.macOS.signingIdentity: "-"` plus a
   `codesign --verify --deep --strict` gate in `release.yml`. See gotcha 34.
   **Still open, and now cheap:** the "cannot be verified" → Open Anyway
   walkthrough on a **fixed** (rc5+) build, plus the two screenshots. One
   double-click.
4. ✅ **The seven 0-byte backend files are deleted (M6.2)**, with the deferrals
   recorded in `architecture.md` and `tests/test_hygiene.py` guarding against a
   third sweep.
5. **The tray menu's two items have never been clicked** (A1 could not: macOS
   hosts menu-bar extras in Control Centre, which computer-use cannot be
   granted, and osascript is refused assistive access). Owner, 30 seconds.
6. ✅ **The sandbox is undiscoverable to the model — CLOSED in M6.2.** Measured
   before and after with the probe; `qwen3:4b` held restraint at 18/18 and went
   0/9 → 9/9 on the new **discovery** cases. See the M6.2 entry and the new
   section in `docs/tool-calling.md`.
7. **A5 (Windows/Linux by hand + the qwen3:8b probe)** — owner-gated, and
   **not v1-blocking**: an untagged model already means tools off, so not
   measuring qwen3:8b yields the correct behaviour by default.

### A. Verification debt — A1/A2/A3/A4 CLOSED in M6.1, A5 open

The July limitation is **gone**: a packaged `Jarvis.app` in `/Applications` has a
bundle id (`app.jarvis-assistant.desktop`) and **can be driven directly with
computer-use** — screenshots and clicks included. `target/debug/jarvis` still
cannot, so build and install first, then verify. Everything below marked ✅ was
driven by a session, not narrated to one. Details in the M6.1 entry above.

- ✅ **A1. `show_window` — executed, works.** Window hidden to the tray, confirm
  arrived, window revealed and focused, dialog correct, tool ran. Errors are now
  logged instead of discarded, because the finding that came with it is that a
  hidden window has **no other way back**: no Dock icon, activating does not
  restore, no Window-menu entry. **Still open: the two tray menu items have
  never been clicked** — macOS hosts menu-bar extras inside Control Centre,
  which computer-use cannot be granted, and osascript is refused assistive
  access. Owner's, thirty seconds. (The *handlers* are covered indirectly:
  "show" is the same two calls `show_window` makes, and "quit" is `app.exit(0)`
  → `RunEvent::Exit` → `sidecar::kill`, proved clean via ⌘Q with no orphan.)
- ✅ **A2. The wake soak — CLOSED. Full hour, then a spoken reply.** See the
  M6.1 soak entry. The 2026-07-25 attempt ran 18 of 60 minutes,
  ended because the app was quit, and never ran the deciding test. The redo
  ends by **speaking "Hey Jarvis" and listening for a spoken reply**, which is
  the only thing that separates a live webview from a frozen one — **`ws=1`
  proves nothing on its own**, because WebKit's networking process keeps the TCP
  established while the JS is frozen. That is gotcha 8's whole trap.
- ✅ **A3. A spoken file turn — heard, end to end.** Wake, whisper, `read_file`
  with no dialog, a tainted `write_file` confirm marked "from a spoken request",
  the file on disk, and the spoken reply recorded off the room.
- ✅ **A4. `run_command` and `web_fetch` driven live**, with the taint A/B: the
  same `write_file` tool showing a session-grant button in a clean conversation
  and the amber provenance block with no session button in a tainted one.
  **Budget real time**: qwen3:4b tool turns ran **3-5 minutes** here under
  memory pressure, longer than the 96 s M6.0 measured. **How to tell a slow turn
  from a stuck one** (better than the old "no outbound connection" heuristic,
  which is only true *between* rounds): `lsof -nP -a -p <sidecar> -iTCP` — an
  ESTABLISHED connection to `:11434` means a request is genuinely in flight, and
  `ollama ps` showing an UNTIL that is not counting down means the model is
  pinned by an active stream.
- **A5. Windows/Linux file tools by hand** and the **`qwen3:8b` tool-calling
  probe** (`backend/tests/manual/probe_tool_calling.py`, ~20 min) on the A6000
  box. One AnyDesk session, independent of everything else. Expect qwen3:8b to
  hit the same hybrid-reasoning latency trap as qwen3:4b (gotcha 12). **Not
  v1-blocking** — an untagged model already means tools off, so the fail-safe
  gives the right answer without the measurement.

### B. Release mechanics — still the largest remaining risk, but now measured

The workflow **has now been run**, twice, and it had never once succeeded.

- ✅ **B1. The dmg has now been produced, downloaded, mounted and installed
  from** — including to a 158-character path, which fired gotcha 31's fallback
  for the first time in a real bundle. See the M6.1 entry. **Still true: no
  draft release and no SHA256SUMS have ever existed**, because `publish` needs
  all three platforms and Linux has never passed.
- **B2. Linux — sidecar ✅ (gotcha 32), Rust ✅, `.deb` ✅, AppImage ❌.**
  Three tags spent: rc1 died in the sidecar step, rc2 got to `linuxdeploy`, rc3
  added `APPIMAGE_EXTRACT_AND_RUN=1` and **failed identically** — so it is not
  the missing-FUSE case, and the FUSE env var should probably come back out.
  Tauri swallows linuxdeploy's stderr; the log carries only `failed to run
  linuxdeploy`, which is why three cycles bought so little.
  **RECOMMENDATION: drop `appimage` from the matrix and ship the `.deb`.**
  `bundles: deb` is a one-word change, it unblocks `publish` — and therefore the
  draft release, SHA256SUMS, and B1/B3 — and `.deb` is the primary desktop
  format anyway. **Do NOT "fix" this by moving to ubuntu-24.04**: the runner
  version sets the glibc floor for the `.deb` too, and jammy's 2.35 is why the
  deb runs on older distros. Trading that for an AppImage is a downgrade.
  If AppImage is ever wanted back, the next diagnosis is to invoke linuxdeploy
  by hand in a workflow step so its stderr is visible, rather than guessing
  through Tauri.
- **B3. `docs/unsigned-install.md` — half-verified.** The ad-hoc-signing claim is
  true on a real artifact (`Signature=adhoc`, `TeamIdentifier=not set`). **The
  Gatekeeper walkthrough is still unverified and cannot be verified this way**:
  a `gh run download` sets no `com.apple.quarantine`, so the "Apple could not
  verify…" path never triggers. It needs a **browser download from a published
  release** — which is blocked on `publish` running, which is blocked on Linux.
  Still carries `<!-- SCREENSHOT PLACEHOLDERS -->`.

### C. Decisions — ALL MADE 2026-07-25/26

- ✅ **C1. `calendar-macos`: CUT from v1.** pyobjc on a dependency-strict
  project, macOS-only (against cross-platform consistency), and it wants a TCC
  usage declaration. Already excluded from `BUNDLED` in `extensions/bundled.py`,
  so nothing ships broken. Add it to `BUNDLED` when it has code.
- ✅ **C2. Settings/onboarding UI: CUT from v1, and 17 empty files are
  deleted.** The inventory here used to say fourteen and was itself wrong — it
  missed `app/src/lib/api.ts`, `app/src/state/session.ts` and
  `app/src/state/settings.ts`. M3.3's readiness gate already does onboarding's
  load-bearing work, and on a public portfolio repo a 0-byte file that the repo
  map describes as if it exists reads as abandoned scaffolding. Deleting is
  honest; a stub is not — the same argument that keeps `calendar-macos` out of
  `BUNDLED`.
- ⬅ **C4. SEVEN MORE 0-byte files, in the BACKEND — found by a later audit,
  not yet actioned.** The C2 sweep was frontend-and-scripts only, so it missed
  these entirely. All seven are confirmed **unreferenced** (nothing imports
  them; `__init__.py` package markers are excluded and are fine):
  - **Dead duplicates — delete, they actively mislead.** A maintainer opening
    any of these expecting the implementation finds an empty file:
    `agent/capabilities.py` (real one is `llm/capabilities.py`),
    `extensions/installer.py` (real one is `extensions/install.py`, M5.3),
    `server/websocket.py` (real one is `server/app.py`).
  - **Unbuilt, and each is a decision rather than a deletion:**
    `llm/anthropic.py` + `llm/openai_compat.py` — the cloud adapters named in
    the approved stack, deferred and never written; `tools/clipboard.py` —
    clipboard was cut from Phase 4 "to phase 5" and phase 5 shipped without it,
    so it was dropped silently rather than decided; `tools/desktop.py` —
    `take_screenshot`, explicitly cut from v1 (every model in the 8GB budget is
    text-only), so this one is settled and only the file lingers.
  Same call as C2 is the consistent one: delete all seven and record the
  deferrals in `architecture.md`. Deleting `llm/anthropic.py` does **not** cut
  the adapter feature — a 0-byte file was never the feature.
- ✅ **C3. "Hey Friday": CUT from v1.** `scripts/train_wake_word.py` was 0 bytes,
  so an approved feature was silently unbuildable. Training a wake word needs
  PyTorch and a data pipeline, against the project's no-torch ML story. Recorded
  in `architecture.md` and the README rather than left implied.
- **The one that replaced them: make the sandbox discoverable.** See the M6.1
  entry. Naming `[filesystem] roots` somewhere the model can see costs prompt
  budget that `prompts.py` documents as measured and dangerous, so it is an
  owner call with `docs/tool-calling.md` in hand — but voice + files does not
  really work until it is answered.

### D. Nice-to-haves, explicitly optional for v1

Model catalog UI; opt-in VAD barge-in (approved as a v1 opt-in tier, with a
headphones/beamforming-mic warning); a read-only "what can Jarvis reach" view
(today `[filesystem] roots` and `[tools] allow_dangerous` have no UI at all, so
a user cannot see their own sandbox — the one real casualty of C2).

### E. Post-v1, already agreed

AEC milestone (macOS Voice Processing AU, then WebRTC AEC3); voice-cloning TTS
eval (Chatterbox-Turbo tier); auto-update (blocked on code signing, which is
blocked on budget).

**Small things noticed and deliberately not fixed** (none block anything):
`extension.loadedNote` ("Active") is defined in en.json but never rendered — the
panel only shows the *not*-loaded note, so an approved-and-working extension reads
as plain "Approved"; `security/permissions.py`'s module docstring still cites
`send_notification` as its example of a `safe` core tool, which has never existed
(M5.4's notification path is an extension-facing host call, not a tool); a
panel "install from URL" field would close M5.3's restart caveat but adds a second
approval UI plus a `git` subprocess behind a webview message; and **macOS's own
Window menu binds ⌘M to Minimize**, which collides with the app's voice toggle —
the app's binding was verified working in M3.x and again here, so the frontend
handler is winning, but the collision is real and a future Tauri or WebKit change
could flip it. Worth an explicit `preventDefault` audit if ⌘M ever "stops
working" and starts minimising instead.

**Known M5.1 limits, deliberate:** single-file `extension.py` only (`sys.path` is
untouched, because an extension shipping `json.py` would shadow the stdlib
process-wide); symlinks anywhere in an extension tree refuse it outright
(otherwise a symlinked `extension.py` is a digest bypass); `__pycache__` is
excluded from the digest, with the planted-`.pyc` residual documented in §5.

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
  **Observed live for the first time in M6.0**, quitting the packaged app while a
  turn was in flight: `RuntimeError: Cannot call "send" once a close message has
  been sent`, through `ws → cancel_generation → _generate → send`
  (`server/app.py` 232 → 103 → 636 → 249 → 194). Still benign — it happens during
  teardown, after the connection is already gone — but it is now a real traceback
  in a user-visible log on ordinary quit, which is worth one `except
  RuntimeError`/connection-state check if anyone touches that path. Do not fix it
  blind: the regression test has to prove the *ordering* (that nothing else was
  skipped), the same trap gotcha 14 records.

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

**Phase 4 is complete. Next is Phase 5 (extended scope).** The obvious security
piece carried into it is the **extension loader + approval gate** (§5): manifest-
declared permissions shown at load, `jarvis install <url>` pinning the commit SHA,
extension risk levels as floors the core engine can raise. It reuses the M4.x
machinery wholesale — the registry takes a gate, the confirm broker is generic,
the risk levels exist. The rest of Phase 5 is UX/features: branching UI (the
`Store.siblings()` tree is built and tested, just unsurfaced), model catalog UI,
"Hey Friday" training, opt-in VAD barge-in.

**M4.5 (web_fetch + SSRF) — the two decisions:** risk = **`ask`** (every fetch
confirms showing the URL — the exfiltration defense; `safe` breaks §3's "safe =
read-only" since egress is a side effect). DNS rebinding = **validate + document
the residual** (owner-delegated) — scheme allowlist + resolve-and-check-every-IP +
per-redirect re-validation + IP-literal-direct-validation close the direct
vectors; the resolve-then-connect TOCTOU is documented like §2's file TOCTOU, not
closed (pinning needs fragile custom-transport plumbing). See §4 and gotcha 20.

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
5. A spoken *file* turn has not been heard. M4.2 verified voice+tools
   acoustically with the dev `echo` tool, and M4.3's tools share `run_exchange`
   and the same gate — but "read my notes and write a summary", spoken, has
   never actually happened.
6. **New (M4.4/M4.5): no full-stack live run of shell or web_fetch through the
   model.** Both are covered by mutation-proven unit suites, and both reuse the
   exact gate → broker → dialog → span → taint path that M4.3 *did* verify live
   with a `dangerous` tool (`delete_file`: Risky badge, Deny focused, no session
   button, real deletion) — so the confirmation path is proven, just not with
   these two tools driving it. What *was* proven live for M4.5: the **real**
   `web_fetch` (default httpx client + real getaddrinfo, not MockTransport)
   refused `http://localhost:11434/` as `URL_BLOCKED` (resolved `::1`, refused
   before touching the running Ollama) and `http://169.254.169.254/` likewise,
   and fetched `http://example.com/` successfully with HTML stripped to text and
   `taint_source` set. Outstanding: a spoken/typed "run git status" and "fetch
   <url>" end-to-end through qwen3:4b in the real app, watching the dialog.
7. **New (M5.2): the extensions panel has only been seen in a browser-hosted
   build, not the real WKWebView** — same caveat as item 4's confirm dialog. The
   full flow was driven with the preview browser against a real scratch backend
   (pending → detail card → approve-loads-live → changed → revoke), zero console
   errors, but nobody has clicked it in the packaged app.
   ✅ **The second half of this item is CLOSED (M5.4, 2026-07-25):** qwen3:4b
   drove `set_timer` end-to-end in a browser-hosted build against a scratch
   backend — tool span, the timer firing a minute later, the toast. See the M5.4
   entry below. Still open: nobody has clicked the panel in the real WKWebView.

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

Phase 4 (agency + security) is **complete** — the largest phase, and the one
where shipping a half-built permission engine would have been worse than not
shipping. The rule that got it there, kept for Phase 5's extension loader: cut
the tool list before cutting the security layer.
