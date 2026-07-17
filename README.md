# JARVIS

> A local-first, voice-activated AI assistant that runs on **your** machine. Wake word, streaming voice, real tool use — zero API keys, zero telemetry, cloud optional.

**Status: pre-alpha, phase 1 of 6.** Text chat against local Ollama works end-to-end (streaming, history, model auto-selection by RAM tier); voice arrives in phase 2. See [docs/architecture.md](docs/architecture.md) for the plan.

<!-- DEMO GIF PLACEHOLDER: sphere responding to "Hey Jarvis" -->

## What it will do

- **"Hey Jarvis"** (or "Hey Friday") → speak → local LLM answers out loud, sphere UI reacting to the audio
- **Fully local by default**: Ollama + whisper.cpp + Kokoro + openWakeWord. No internet needed after install.
- **Real agency**: web search, filesystem, shell, clipboard, screenshots, notifications — behind a permission model designed for a world where prompt injection exists ([security model](docs/security-model.md))
- **Extensible**: drop a Python file in a folder, or `jarvis install <github-url>` — see [docs/extensions.md](docs/extensions.md)
- **Cross-platform**: macOS, Windows, Linux. One codebase.

## Install

Not yet. When it ships:

```sh
# macOS / Linux
curl -fsSL https://jarvis.example/install.sh | sh   # (you'll be able to read it first)
# Windows
irm https://jarvis.example/install.ps1 | iex
```

v1 ships **unsigned** (open-source project, zero budget for certs). See [docs/unsigned-install.md](docs/unsigned-install.md) for the OS security prompts you'll see and why.

## Repo layout

| Path | What lives there |
|---|---|
| [app/](app/) | Tauri 2 shell + React/TypeScript frontend (sphere, chat, onboarding, settings) |
| [backend/](backend/) | Python sidecar: voice pipeline, agent loop, tools, security layer |
| [extensions/](extensions/) | Default extension set shipped with the app |
| [models/](models/) | Small bundled models (wake word, VAD) — fetched by script, not committed |
| [scripts/](scripts/) | Installers, sidecar build, model fetch, offline wake-word training |
| [catalog/](catalog/) | Curated model catalog (data, not a service) |
| [docs/](docs/) | Architecture, security model, extension authoring, latency budgets |

## Security model (the short version)

Every tool has a risk level; risky ones confirm with the exact action shown. Filesystem tools are sandboxed to user-chosen roots (symlink-resolved). Shell commands **always** confirm — no classifier, no denylist, no exceptions. Once untrusted content (web pages, unknown files) enters the conversation, the session is *tainted* and every side-effectful call escalates to confirmation. Full write-up: [docs/security-model.md](docs/security-model.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The extension API is the intended entry point for most contributions.
