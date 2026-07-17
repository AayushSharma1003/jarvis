# app/ — Tauri 2 shell + React frontend

The native shell (Rust) and the UI (React + TypeScript + Tailwind + Three.js).

## src-tauri/ (Rust)

| File | Responsibility |
|---|---|
| `sidecar.rs` | Spawn/supervise the Python backend, kill it reliably on exit (onedir sidecars orphan easily — this file is why we don't use onefile) |
| `tray.rs` | System tray (Win/Linux) / menu bar (macOS) |
| `shortcuts.rs` | Global hotkeys: push-to-talk, interrupt |

## src/ (React)

| Dir | Responsibility |
|---|---|
| `components/sphere/` | Three.js audio-reactive sphere (idle/listening/thinking/speaking) + **`SphereFallback2D.tsx`** — WebKitGTK on Linux has flaky WebGL; the 2D canvas fallback is mandatory, not optional |
| `components/chat/` | Chat view, message tree rendering, `BranchSwitcher.tsx` (edit/regenerate navigation) |
| `components/onboarding/` | First launch: mic permission → model download (runs *concurrently* with wake-word test) → tool permissions |
| `components/settings/` | Backend picker, voice picker, permissions panel, curated model catalog |
| `lib/` | WebSocket client (token + reconnect), Tauri IPC wrappers, shared types |
| `state/` | Stores. Conversation state mirrors the backend's message tree — active path only |
| `i18n/` | **All user-facing strings live here**, English only for v1. The backend emits error *codes*; the frontend translates. Hardcoded UI strings don't pass review |
