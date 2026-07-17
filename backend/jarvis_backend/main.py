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

import psutil
import uvicorn

from .config import load
from .llm.ollama import OllamaBackend
from .server.app import AppState, create_app
from .server.auth import make_token
from .storage import db
from .storage.conversations import Store

PARENT_POLL_S = 2.0


def run() -> None:
    config = load()
    env_token = os.environ.get("JARVIS_WS_TOKEN")
    token = env_token or make_token()

    store = Store(db.connect(config.data_dir / "jarvis.sqlite3"))
    backend = OllamaBackend(config.ollama_url)
    app = create_app(AppState(token=token, store=store, backend=backend, config=config))

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


async def _watch_parent(parent_pid: int, server: uvicorn.Server) -> None:
    while True:
        await asyncio.sleep(PARENT_POLL_S)
        if not psutil.pid_exists(parent_pid):
            server.should_exit = True
            return


if __name__ == "__main__":
    run()
