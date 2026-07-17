# backend/ — Python sidecar

The brain and the voice pipeline. Ships as a PyInstaller **onedir** bundle spawned by Tauri as a sidecar; in development, run it directly with `uv run`.

**Dependency policy:** onnxruntime (Silero VAD, openWakeWord, kokoro-onnx) + pywhispercpp (whisper.cpp: Metal/CUDA/CPU). **No PyTorch, no ctranslate2** — that's what keeps the installer under control. Adding a dependency that drags in torch is a design regression, not a convenience.

## Layout (`jarvis_backend/`)

| Module | Responsibility |
|---|---|
| `main.py` | Entrypoint: FastAPI app, sidecar lifecycle, clean shutdown |
| `cli.py` | `jarvis doctor`, `jarvis install <url>` |
| `server/` | WebSocket protocol, token auth, Origin checks |
| `audio/` | Device enumeration, capture, playback (`sounddevice`) |
| `wake/` | openWakeWord detector (always-on, <3% CPU budget) |
| `stt/` | Silero VAD → endpointing → whisper.cpp transcription |
| `tts/` | TTS adapter (`base.py`) + Kokoro backend + sentence chunker. New voices = new adapter, see docs/extensions.md |
| `llm/` | LLM adapter + Ollama / OpenAI-compatible / Anthropic backends, RAM-tier model selection |
| `agent/` | The loop: prompt building, capability flags (vision available? taint state?), tool dispatch |
| `tools/` | Built-in tools. Every tool registers through `registry.py` — which routes through `security/` unconditionally |
| `security/` | Permission engine, taint tracking, path sandbox, confirmation broker, SSRF guard. **Read docs/security-model.md before touching this** |
| `extensions/` | Manifest parsing, loader with approval gate, `jarvis install` implementation |
| `storage/` | SQLite. Messages are immutable rows with `parent_id` (branching-ready from day 1); `schema.sql` is the source of truth |
| `doctor/` | Diagnostics: audio devices, Ollama connectivity, model status, permission audit, latency instrumentation |

## Tests

`tests/` — unit tests for plumbing; golden-audio fixture tests feed known WAVs through VAD→STT→endpointing and assert transcripts + timing. Voice *UX* is manual QA by design; don't try to CI it.
