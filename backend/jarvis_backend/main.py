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
import logging
import os
import socket
import time

import psutil
import uvicorn

from . import assets
from .config import (
    Config,
    approvals_path,
    config_dir,
    data_dir,
    extensions_dir,
    load,
    load_wake_enabled,
    save_wake_enabled,
)
from .extensions import host
from .extensions.approvals import ApprovalStore
from .extensions.loader import discover, load_approved
from .llm.ollama import OllamaBackend
from .security.confirm import ConfirmBroker
from .security.permissions import PermissionGate
from .security.sandbox import Sandbox
from .security.taint import TaintTracker
from .server.app import AppState, create_app, handle_wake
from .server.auth import make_token
from .server.voice import RealVoiceIO
from .storage import db
from .storage.conversations import Store
from .tools import default_registry
from .tools.registry import Registry
from .wake.service import WakeService

PARENT_POLL_S = 2.0

log = logging.getLogger(__name__)


def load_extensions(registry: Registry) -> dict[str, tuple[str, ...]]:
    """Add the tools of every approved extension. Never raises (§5).

    Called after `default_registry`, so the core tool names are already taken
    and an extension trying to claim one is refused rather than shadowing it.

    With nothing approved this registers nothing, which is the out-of-the-box
    state: dropping a folder into the extensions directory makes it *visible*
    (`jarvis extensions list`), never active. Reporting goes through `logging`,
    which writes to stderr — stdout carries the one JSON ready line the Tauri
    supervisor parses and must not gain a second.

    Returns **extension name → the tool names it actually claimed**, which is
    what M5.2's revoke unregisters. That set comes from the registration, never
    from the manifest: an extension that declared `read_file` and lost the
    conflict never claimed it, and removing it on revoke would take the
    sandboxed core tool with it.
    """
    loaded: dict[str, tuple[str, ...]] = {}
    try:
        store = ApprovalStore(approvals_path())
        found = discover(extensions_dir(), store)
        for entry in found:
            if entry.status != "approved":
                log.warning(
                    "extension %s not loaded: %s%s",
                    entry.name,
                    entry.status,
                    f" ({entry.code})" if entry.code else "",
                )
        for result in load_approved(registry, found):
            if result.ok:
                loaded[result.name] = result.tools
                log.info("extension %s loaded: %s", result.name, ", ".join(result.tools) or "-")
                if result.detail:
                    log.warning("extension %s: %s", result.name, result.detail)
            else:
                log.warning("extension %s failed: %s %s", result.name, result.code, result.detail)
    except Exception:  # noqa: BLE001 - extensions must never stop the sidecar booting
        log.exception("extension loading failed")
    return loaded


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
    # The broker needs the connection list, which belongs to the AppState that
    # needs the registry that needs the gate that needs the broker. Built first
    # and bound after, the same way the wake service is wired below.
    confirm = ConfirmBroker()
    taint = TaintTracker()
    gate = PermissionGate(
        confirm, taint=taint, allow_dangerous=lambda: config.allow_dangerous_tools
    )
    # §2: our own config and data directories are permanently outside every
    # root, so a file tool can never widen its own sandbox by rewriting
    # config.toml or dropping something in the extensions directory. They are
    # excluded here rather than filtered out of the roots, because a user is
    # free to configure a root that contains them (on Linux both sit under the
    # home directory) and the exclusion has to survive that.
    sandbox = Sandbox(
        roots=list(config.filesystem_roots), excluded=[config_dir(), data_dir()]
    )
    registry = default_registry(gate, sandbox)
    # After the core tools, never before: a name already in the registry is
    # refused, so this ordering is what stops an extension shadowing read_file.
    # The returned map (name → claimed tool names) is what revoke unregisters.
    extensions_loaded = load_extensions(registry)
    state = AppState(
        token=token,
        store=store,
        backend=backend,
        config=config,
        voice_io=RealVoiceIO(),
        registry=registry,
        confirm=confirm,
        taint=taint,
        extensions_loaded=extensions_loaded,
    )
    confirm.bind(lambda: state.connections)
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
        # The extension host needs the *running* loop, so it binds here rather
        # than beside confirm.bind() above — an extension's scheduler thread
        # hands work back with call_soon_threadsafe and there is no loop to
        # hand it to until now. Everything loaded before this point (which is
        # every extension: load_extensions runs at composition time) simply
        # notifies into the void, which is correct — there are no UIs yet.
        host.bind(asyncio.get_running_loop(), lambda: state.connections)
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
