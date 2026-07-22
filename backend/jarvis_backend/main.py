"""Sidecar entrypoint.

Binds 127.0.0.1 on an ephemeral port (fixed via JARVIS_PORT if set), then
prints exactly one JSON "ready" line to stdout for the Tauri supervisor:

    {"event": "ready", "port": 54321, "pid": 1234}

The auth token comes from JARVIS_WS_TOKEN (production: injected by the Tauri
shell). If absent (standalone dev), one is generated and included in the ready
line so a developer can connect. If JARVIS_PARENT_PID is set, the process
exits when that pid disappears — a sidecar must never outlive its app.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time

import psutil
import uvicorn

from . import assets
from .config import Config, load, load_wake_enabled, save_wake_enabled
from .llm.ollama import OllamaBackend
from .server.app import AppState, create_app, handle_wake
from .server.auth import make_token
from .server.voice import RealVoiceIO
from .storage import db
from .storage.conversations import Store
from .tools import default_registry
from .wake.service import WakeService

PARENT_POLL_S = 2.0


def run() -> None:
    # Test hook: simulate a slow cold start (used to verify the supervisor's
    # handshake handles a backend that comes up after the webview does).
    if delay := os.environ.get("JARVIS_STARTUP_DELAY"):
        time.sleep(float(delay))

    config = load()
    env_token = os.environ.get("JARVIS_WS_TOKEN")
    token = env_token or make_token()

    store = Store(db.connect(config.data_dir / "jarvis.sqlite3"))
    backend = OllamaBackend(config.ollama_url)
    state = AppState(
        token=token,
        store=store,
        backend=backend,
        config=config,
        voice_io=RealVoiceIO(),
        registry=default_registry(),
    )
    state.wake = _make_wake_service(state, config)
    app = create_app(state)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", int(os.environ.get("JARVIS_PORT", "0"))))
    sock.listen(128)
    port = sock.getsockname()[1]

    ready: dict[str, object] = {"event": "ready", "port": port, "pid": os.getpid()}
    if env_token is None:
        ready["token"] = token  # standalone dev only; production gets it via env
    print(json.dumps(ready), flush=True)

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))

    async def serve() -> None:
        watchdog = None
        if parent := os.environ.get("JARVIS_PARENT_PID"):
            watchdog = asyncio.ensure_future(_watch_parent(int(parent), server))
        try:
            await server.serve(sockets=[sock])
        finally:
            if watchdog:
                watchdog.cancel()
            await backend.close()

    asyncio.run(serve())


def _make_wake_service(state: AppState, config: Config) -> WakeService:
    """Composition root for the real wake service: real models, real mic."""

    def make_pipeline():
        from .stt.vad import SileroVAD
        from .wake.detector import WakeDetector
        from .wake.pipeline import WakePipeline

        vad = SileroVAD(assets.path_for("silero-vad"))  # own instance: own thread
        detector = WakeDetector(
            assets.path_for("wake-melspec"),
            assets.path_for("wake-embedding"),
            assets.path_for("wake-hey-jarvis"),
        )
        return WakePipeline(vad.prob, detector)

    def open_capture():
        from .audio.capture import SyncMicCapture

        cap = SyncMicCapture()
        cap.start()
        return cap

    # The VAD gate makes silero part of the wake path too.
    available = not assets.missing("wake") and assets.is_present("silero-vad")
    return WakeService(
        make_pipeline=make_pipeline,
        open_capture=open_capture,
        on_wake=lambda: handle_wake(state),
        persist=save_wake_enabled,
        enabled=load_wake_enabled(),
        threshold=config.wake_threshold,
        available=available,
    )


async def _watch_parent(parent_pid: int, server: uvicorn.Server) -> None:
    while True:
        await asyncio.sleep(PARENT_POLL_S)
        if not psutil.pid_exists(parent_pid):
            server.should_exit = True
            return


if __name__ == "__main__":
    run()
