# Contributing

> Status: pre-alpha; contribution workflow will firm up once phase 1 lands.

## Where to contribute

- **Extensions** — the intended entry point. See [docs/extensions.md](docs/extensions.md). New tools usually belong in an extension, not core.
- **TTS voices / LLM backends** — adapter interfaces in `backend/jarvis_backend/tts/base.py` and `llm/base.py`.
- **Translations** — v1.1 goal; all strings already live in `app/src/i18n/`.
- **Core** — open an issue first for anything touching `backend/jarvis_backend/security/` (read [docs/security-model.md](docs/security-model.md) — it's normative).

## Ground rules

- No dependency that drags in PyTorch or another ML runtime. onnxruntime + whisper.cpp is the whole story.
- User-facing strings go through i18n; the backend emits error codes, not sentences.
- Small, reviewable PRs. Tests where they earn their keep — pipeline plumbing yes, voice UX no (that's manual QA).
- Latency regressions on the golden-fixture benchmark are release blockers.

## Dev setup

Prereqs: [uv](https://docs.astral.sh/uv/), Node 22+, Rust stable, [Ollama](https://ollama.com) running with at least one model pulled (`ollama pull llama3.2:3b`).

```sh
cd backend && uv sync          # backend deps
uv run pytest                  # tests
uv run jarvis doctor           # diagnose your setup
cd ../app && npm install       # frontend deps
npm run tauri dev              # full app (debug builds run the backend via uv)
```

Frontend-only iteration without the Tauri shell: start the backend yourself
(`JARVIS_WS_TOKEN=<any> JARVIS_PORT=8765 uv run jarvis-backend`), then
`VITE_JARVIS_PORT=8765 VITE_JARVIS_TOKEN=<same> npm run dev` and open
http://localhost:1420 in a browser.
