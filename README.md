# JARVIS

**A local-first, voice-activated AI assistant that runs entirely on your own machine.**
Say *"Hey Jarvis"*, talk, get a spoken answer — no API keys, no account, no telemetry,
and no network traffic you didn't ask for.

[![CI](https://github.com/AayushSharma1003/jarvis/actions/workflows/ci.yml/badge.svg)](https://github.com/AayushSharma1003/jarvis/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)

<!-- DEMO GIF: record "Hey Jarvis" → question → spoken answer, sphere reacting. -->

> **Status: pre-alpha, 4 of 6 phases complete, phase 5 underway.** The voice loop is real
> and works end to end on an 8 GB M2: wake word, endpointing, transcription, local LLM,
> streaming speech, barge-in, and an audio-reactive sphere. The permission engine, the
> filesystem sandbox, taint tracking and the SSRF guard are **built and enforcing**, and
> the tool set ships on top of them: files, shell (`run_command`, always confirms) and
> `web_fetch`. Extensions load only what you approved, keyed on a hash of their exact
> files — though an approved one runs unsandboxed, which the docs say plainly.
> [What works today](#what-works-today) is honest about the line.

---

## The interesting part

Everything below is measured on the primary target: an **8 GB M2 Pro MacBook**, the
machine this project is tuned for on the theory that if it's smooth there, it's smooth
anywhere.

| Metric | Measured | Why it's not free |
|---|---|---|
| Always-on wake word, idle CPU | **2.4%** of one core | An always-listening loop that costs 15% of a core is a laptop-battery bug wearing a feature costume. Budget was <3%. |
| End of speech → first audible word | **1.17–1.41 s** | Started at 3.92 s. The fix wasn't a faster model — see [latency.md](docs/latency.md). |
| Warm text time-to-first-token | **407 ms** | llama3.2:3b via Ollama. |
| Whisper transcription at endpoint | **~140 ms** | Whole utterance on Metal at the endpoint — measured fast enough that streaming STT was unnecessary complexity. |
| Sphere render cost | **1.8 ms CPU/frame** | ~6k shader-displaced points + bloom, with a behaviour-identical Canvas-2D fallback. |
| Backend test suite | **481 tests** | Voice orchestration included: a `VoiceIO` boundary lets the whole spoken turn be driven over the WebSocket with zero hardware and zero model files. Security regressions are mutation-proven — the test is broken on purpose to watch it fail before it's trusted. |

Four decisions this project is actually about:

- **One ML runtime story.** onnxruntime (wake word, VAD, TTS) + whisper.cpp (STT).
  No PyTorch, no ctranslate2 — a dependency that drags a second 2 GB runtime into a
  desktop bundle is a regression, not a shortcut. The openWakeWord inference chain is
  [vendored](backend/jarvis_backend/wake/detector.py) (three ONNX sessions and a ring
  buffer, bit-exact against the reference implementation) specifically to keep scipy and
  scikit-learn out of the shipped sidecar.
- **Local-first is a constraint, not a marketing line.** Ollama is detected or installed,
  never bundled. The model catalog is bundled data with a *manual* refresh. There is no
  server, no auto-update, no crash reporter, no analytics. The app makes exactly the
  network calls you ask it to.
- **The security model was designed before the tools existed** ([security-model.md](docs/security-model.md)),
  because retrofitting a permission engine onto a shipped tool list is how assistants get
  their users owned by a web page.
- **Messages are an immutable tree from day one.** Turn-grouped, `parent_id`-linked,
  branching-ready — the branching *UI* is phase 5, but retrofitting immutability is the
  expensive half, so it was done first.

---

## What works today

| | Status | |
|---|---|---|
| **Text chat** | ✅ working | Streaming, stop/interrupt, conversation sidebar (list / switch / rename / delete), RAM-tier-aware model picker, a setup readiness gate, reconnect with backoff, full i18n. |
| **Voice loop** | ✅ working | Hotkey or wake word → Silero VAD endpointing → whisper.cpp (Metal) → local LLM → clause-chunked Kokoro TTS → playback with barge-in. |
| **"Hey Jarvis" always-on** | ✅ working | Vendored openWakeWord chain behind an adaptive energy gate + VAD, so the expensive embedding model sleeps in silence. Wake word also interrupts playback. |
| **The sphere** | ✅ working | Audio-reactive orb, four states, docks into the header while you chat and glides to centre stage when you speak. WebGL with a 2D fallback. |
| **Storage** | ✅ working | SQLite message tree, branching-ready. Delete is the one exception to immutability, by design. |
| **Permission engine** | ✅ working | `safe`/`ask`/`dangerous`, an in-app confirmation the *backend* requests (never a claim a client can make), "allow for this session" keyed on tool + exact arguments and never for `dangerous`, and every way of not getting an answer resolving to deny. |
| **Filesystem sandbox + taint** | ✅ working | Paths enforced after `resolve()`, Jarvis's own config/data excluded ahead of the root test; reading a file marks the conversation, and from there side-effectful calls confirm with provenance and can't be covered by a session grant. |
| **File tools** | ✅ working | `read_file` / `list_dir` (safe), `write_file` (ask), `delete_file` (dangerous, refuses directories). Tool use is gated on the model — unvetted models never see a schema. |
| **Shell + `web_fetch`** | ✅ working | `run_command` always confirms with the full command shown — no classifier, no denylist — and takes no sandbox, because a subprocess escapes one by design. `web_fetch` is `ask` (a URL can carry data out) and runs behind an SSRF guard: scheme allowlist, every resolved IP checked, IP literals not resolved, every redirect hop re-validated. Both bounded by timeouts and incremental output caps. |
| **Extensions** | ⚠️ partly | Manifest, content-keyed approval and loader are built (`jarvis extensions list/approve/revoke`); the in-app approval panel and `jarvis install <url>` are not. An approved extension runs **unsandboxed**, with the sidecar's full privileges — its declared permissions are intent, not a boundary. |
| **Installers** | 🚧 phase 6 | The release workflow already builds unsigned bundles for all three OSes on a tag. |

**Verified on macOS (Apple Silicon), hands-on.** Windows and Linux are built by CI every
tag and are *not* yet hands-on tested — the cross-platform code paths exist, the hardware
validation does not. Said plainly because a README that claims three platforms and has
tested one is a bug report waiting to be filed.

---

## How it works

```mermaid
flowchart LR
    subgraph app["Tauri 2 app"]
        UI["React 19 + three.js<br/>sphere · chat · settings"]
        RS["Rust shell<br/>tray · hotkeys · sidecar supervisor"]
    end
    subgraph py["Python sidecar"]
        WS["WebSocket server<br/>127.0.0.1 · token + Origin auth"]
        WAKE["Wake service<br/>energy gate → Silero → openWakeWord"]
        VOICE["Voice exchange<br/>VAD endpoint → whisper.cpp → Kokoro"]
        AG["Agent loop"]
        SEC["Security layer<br/>permissions · taint · sandbox · SSRF"]
        TOOLS["File tools<br/>read · list · write · delete"]
        DB[("SQLite<br/>immutable message tree")]
    end
    OLLAMA["Ollama<br/>detected, never bundled"]

    UI <-->|"ws://127.0.0.1"| WS
    RS -->|spawns + supervises| WS
    WAKE -->|wake.detected| WS
    WS --> VOICE --> AG
    AG --> SEC --> TOOLS
    AG <--> OLLAMA
    AG --> DB
```

The Rust shell spawns the Python sidecar with a token in its environment and waits for a
JSON ready-line on stdout; the webview then connects over a loopback WebSocket that
checks both the token and the `Origin` header. The sidecar watches its parent PID and
exits with it, so there are no orphaned Python processes.

**Where the 1.4 seconds go** (8 GB M2, llama3.2:3b — full breakdown in [latency.md](docs/latency.md)):

| Stage | Time |
|---|---|
| VAD hangover before the endpoint fires | 700 ms *(a perception tunable, reported separately)* |
| whisper.cpp on the whole utterance (Metal) | ~140 ms |
| LLM time to first sentence | ~500–650 ms |
| Kokoro first chunk | ~550–850 ms |
| Audio out | ~40 ms |

The chunking is the trick: waiting for a complete first sentence cost 3.0 s of synthesis
on a long opener, so TTS fires on the first clause or 10 words, whichever closes first,
and the voice-mode system prompt asks the model for a short opening sentence. Latency is
a prompt-engineering problem as much as an inference one.

---

## Security model, short version

Full write-up: **[docs/security-model.md](docs/security-model.md)** — normative, and
written before the code.

Built and enforcing today:

- Every tool carries a risk level: `safe` / `ask` / `dangerous`. Risky calls confirm with
  the exact action shown, the dialog defaults focus to **Deny**, and `dangerous` can be
  switched off wholesale (`[tools] allow_dangerous`) — off means refused without asking.
- Filesystem tools are sandboxed to user-chosen roots, enforced on `resolve()`-ed paths so
  a `..` or a symlink inside a root pointing out is refused. Jarvis's own config and data
  directories are excluded *before* the root test, so no tool can widen its own sandbox.
- **Taint tracking:** once untrusted content (today: a file Jarvis read) enters the
  conversation, every side-effectful call escalates to confirmation with provenance, and
  cannot be covered by — or create — an "allow for this session" grant. Prompt injection
  is assumed, not defended against by hope.
- **Shell always confirms**, full command text shown, no classifier and no denylist (both
  are bypass generators). `run_command` deliberately takes *no* sandbox — a subprocess
  escapes it by design (`cat ~/.ssh/id_rsa` ignores every root), so its guardrail is the
  unconditional confirmation, and the docs say so rather than letting the sandbox imply a
  protection it doesn't provide.
- **`web_fetch` + SSRF guard:** http/https only, every resolved IP checked against
  private/loopback/link-local/metadata ranges (a host with one public and one private
  record is refused outright), IP literals validated without resolving, and **every
  redirect hop re-validated** — a 302 to the cloud metadata endpoint is the classic
  escalation. Fetching is `ask`, because a URL can carry data *out*.
- Both are bounded so one call can't hold the app hostage: incremental output caps read as
  the bytes arrive (never buffer-then-truncate) and real timeouts, with the whole process
  group killed on a shell timeout or barge-in.
- The backend binds 127.0.0.1 with a per-session token and a strict `Origin` check.
- Tool use is gated on the *model*: one that can't reliably decline a tool manufactures
  permission dialogs, and confirmation fatigue is how permission engines fail.

- **Extensions are approved by content, not by name** (§5). An extension runs only if a
  SHA-256 of every file in its folder — `manifest.toml` included, so a declared risk level
  can't be lowered afterwards — matches a recorded approval. Discovery reads TOML and
  hashes bytes; it *imports nothing*, because importing is executing and permission asked
  after the code runs is not permission. Declared risk levels are floors the core raises
  and never lowers, the manifest is an allowlist of what gets exposed, and a tool name
  already taken is refused so `read_file` can't be hijacked.

  And the part that matters most, said plainly: **an approved extension is not sandboxed.**
  It runs in the sidecar process with everything that process can do, so `network = false`
  in a manifest is a *declaration of intent*, not a cage. Approving one is informed consent
  to run someone's code as yourself, and the prompt says so in those words. Enforcing the
  permissions block for real needs a subprocess per extension behind an RPC boundary — a
  different architecture, and not one v1 claims to have.

Specified, not yet built: the in-app approval panel and `jarvis install <url>` (approval
today is `jarvis extensions approve`).

Known residuals, stated rather than hidden: a DNS-rebinding window between the SSRF check
and the connect, a TOCTOU window between path resolution and file open, and an approved
extension's full process privileges. All are documented in
[security-model.md](docs/security-model.md); none is quietly ignored.

---

## Run it from source

Prereqs: [uv](https://docs.astral.sh/uv/), Node 22+, Rust stable, and
[Ollama](https://ollama.com) running with a model pulled (`ollama pull llama3.2:3b`).

```sh
# backend: deps, tests, and a setup diagnosis
cd backend && uv sync && uv run pytest && uv run jarvis doctor

# voice models (~500 MB, pinned URLs + SHA-256, resumable) — user-invoked, never automatic
uv run python ../scripts/fetch_models.py

# the app
cd ../app && npm install && npm run tauri dev
```

`uv run jarvis doctor --latency` runs the real voice pipeline against a synthetic
utterance — Kokoro speaks the test question, so no microphone is needed — and prints the
per-stage breakdown above for your machine.

There are no installers yet. When there are, they will be **unsigned**: this is a
zero-budget project and code-signing certificates are not free. The OS warnings you'd see
and why are documented in [unsigned-install.md](docs/unsigned-install.md) rather than
hand-waved.

---

## Engineering notes

The things that cost real time, kept so nobody rediscovers them:

- **CPU% lies on Apple Silicon.** A mostly-idle background thread gets scheduled onto
  efficiency cores at roughly a third of the clock, so the same work reads ~3× the CPU%
  you measured in a hot benchmark loop. Always-on budgets have to come from measured idle
  deltas, not from hot-loop arithmetic.
- **int8 Kokoro is 2.4× *slower* than fp32** on Apple Silicon (RTF 0.66 vs 0.28), and the
  CoreML execution provider fragments the graph into 155 partitions. Both "optimisations"
  were tried and reverted.
- **WKWebView suspends the WebContent process** when the window is occluded — frozen JS
  can't answer a wake event, while WebKit's separate networking process keeps the
  WebSocket `ESTABLISHED` so everything *looks* healthy. Always-on means the webview has
  to be told not to throttle.
- **Whisper transcribes silence as `[BLANK_AUDIO]`**, which is a non-empty string, which
  became a real LLM turn. Ambient room noise was starting conversations.
- **UnrealBloom writes alpha = 1**, turning a transparent canvas into an opaque square;
  the sphere's edges dissolve via an in-scene vignette that fades to the exact page
  background instead. And the render watchdog measures *render-call duration*, never
  frame cadence — rAF throttling makes cadence lie and will happily demote a perfectly
  capable GPU to the 2D fallback forever.
- **Tauri 2 needs an explicit capabilities file** or the webview gets zero IPC permissions
  and `event.listen` fails silently — which presents as "the backend didn't start".

- **A model that can't decline a tool is a security problem, not a quality one.**
  llama3.2:3b answers "what's 17 times 4?" by running `echo 17*4` in a shell, 3 times
  out of 3. Every spurious call is a permission dialog the user didn't provoke, and
  confirmation fatigue is the documented way permission engines fail. Tool use is
  therefore gated on the model, and unvetted models default to off —
  [with measurements](docs/tool-calling.md).

More: [architecture.md](docs/architecture.md) · [latency.md](docs/latency.md) ·
[tool-calling.md](docs/tool-calling.md) · [docs/design/sphere.md](docs/design/sphere.md)

---

## Repo layout

| Path | What lives there |
|---|---|
| [app/](app/) | Tauri 2 shell (`src-tauri/`) + React/TypeScript frontend — sphere, chat, settings |
| [backend/](backend/) | Python sidecar: `wake` `stt` `tts` `llm` `agent` `tools` `security` `storage` `server` |
| [extensions/](extensions/) | Manifests for the default extension set — the loader is built, the bodies are M5.4 |
| [scripts/](scripts/) | Installers, PyInstaller sidecar build, model fetch, offline wake-word training |
| [catalog/](catalog/) | Curated model catalog — bundled data, manual refresh, not a service |
| [docs/](docs/) | Architecture, security model, latency budgets, extension authoring |

Backend emits machine-readable error **codes**; every user-facing string lives in
`app/src/i18n/`. That rule is enforced in review — it's what makes translation a data
problem later instead of a refactor.

---

## Roadmap

1. ✅ **Walking skeleton** — Tauri shell + sidecar + streaming text chat + SQLite tree.
2. ✅ **Voice loop** — VAD, whisper.cpp, Kokoro, barge-in, inside the latency budget.
3. ✅ **Always-on + feel** — wake word, sphere, chat management, readiness gate, RAM tiering.
4. ✅ **Agency + security** — the largest phase; tools ship *with* their security layer, never before it. The [model capability gate](docs/tool-calling.md) (tool use is gated on the model, because *"can this model decline a tool?"* turns out to be a security property), the tool plumbing, the permission engine + confirmation, the filesystem sandbox + file tools + taint, then shell and `web_fetch` + SSRF.
5. 🚧 **Extended scope** — the extension loader + content-keyed approval gate is done (`jarvis extensions`); still to come: the in-app approval panel, `jarvis install <url>`, branch navigation UI, model catalog UI, custom wake words.
6. **Ship** — installers, docs, a tagged unsigned release with checksums.

Post-v1: acoustic echo cancellation (macOS Voice Processing AU, then WebRTC AEC3), voice
cloning evaluation, and auto-update if signing ever becomes affordable.

---

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md). Extensions are the intended entry point; anything
touching `backend/jarvis_backend/security/` wants an issue first.

## License

[Apache-2.0](LICENSE). Third-party models and vendored code are credited in
[NOTICE](NOTICE) — openWakeWord, Silero VAD, whisper.cpp and Kokoro, without which none
of this would run on a laptop.

No model weights live in this repository; `scripts/fetch_models.py` downloads them from
upstream on request. One caveat worth stating up front: openWakeWord's **pre-trained wake
models are CC BY-NC-SA 4.0** (non-commercial), a constraint that belongs to those
downloaded weights rather than to JARVIS. `scripts/train_wake_word.py` trains a
replacement offline for anyone who needs to be clear of it.
