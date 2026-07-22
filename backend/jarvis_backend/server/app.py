"""FastAPI app: /health for supervision, /ws for everything else.

Connection lifecycle: Origin check → accept → auth handshake (first message,
5s deadline) → ready → dispatch loop. One in-flight generation per connection;
chat.stop cancels it, a second chat.send while busy is refused with BUSY.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .. import __version__
from ..agent.loop import run_exchange
from ..config import Config
from ..llm import capabilities
from ..llm.base import ChatBackend, LLMError
from ..llm.tiering import params_b, pick_model, ram_gb, tier_budget_b
from ..security.confirm import ConfirmBroker
from ..storage.conversations import StorageError, Store
from ..tools.registry import Registry
from ..wake.detector import WakeError
from ..wake.service import WakeService
from . import protocol, readiness
from .auth import origin_allowed, token_valid
from .voice import VoiceIO, run_voice_exchange

AUTH_TIMEOUT_S = 5.0
WS_POLICY_VIOLATION = 1008
# Matches the auto-title length in _generate / run_voice_exchange.
TITLE_MAX_CHARS = 80


@dataclass
class Connection:
    """One authenticated WS client: its serialized sender and its (single)
    in-flight generation task. Registered on AppState so the wake service can
    reach the active client to barge in."""

    send: Any
    generation: asyncio.Task | None = None
    # Which conversation the in-flight generation writes into, so a delete can
    # stop it before pulling the rows out from under it. None until chat.start
    # for a brand-new conversation (the id doesn't exist yet).
    generating_conversation_id: str | None = None
    # The live voice exchange's sentence queue, or None when no spoken turn is
    # running. `voice.say` pushes into it so the frontend can have the backend
    # speak a line it authored (the confirm prompt) without the backend ever
    # writing English — see the i18n rule in CLAUDE.md / run_voice_exchange.
    voice_sentences: asyncio.Queue | None = None

    @property
    def busy(self) -> bool:
        return self.generation is not None and not self.generation.done()

    async def cancel_generation(self) -> None:
        if self.busy:
            self.generation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.generation


@dataclass
class AppState:
    token: str
    store: Store
    backend: ChatBackend
    config: Config
    voice_io: VoiceIO | None = None  # None ⇒ voice.start answers VOICE_UNAVAILABLE
    wake: WakeService | None = None  # None ⇒ wake.set answers WAKE_UNAVAILABLE
    # None ⇒ no tools are offered at all. The registry carries its own security
    # gate (tools/registry.py), so this is a switch for *whether* tools exist,
    # never for whether they are checked.
    registry: Registry | None = None
    # The confirmation broker the registry's gate asks. None ⇒ nothing can
    # answer a confirm, which is why a backend built without one gets
    # SafeOnlyGate and refuses every `ask` tool outright.
    confirm: ConfirmBroker | None = None

    def __post_init__(self) -> None:
        self.connections: list[Connection] = []

    async def registry_for(self, model: str) -> Registry | None:
        """Tools are offered only to models measured able to decline them.

        A model that fires spurious calls manufactures permission dialogs the
        user never asked for — the confirmation-fatigue failure mode
        docs/security-model.md names as an attack surface. `optin` and
        `unsupported` models simply never see the schema, so llama3.2:3b
        cannot answer "what's 17 times 4?" with a shell command.
        See llm/capabilities.py and docs/tool-calling.md.

        The capability probe is cached per model by the adapter, so this is a
        dictionary lookup after the first call.
        """
        if self.registry is None:
            return None
        caps = await self.backend.model_capabilities(model)
        return self.registry if capabilities.classify(model, caps) == capabilities.ON else None


async def handle_wake(state: AppState) -> bool:
    """The wake service heard the wake word. Barge in: cancel every in-flight
    generation (stops playback instantly), then broadcast wake.detected; the
    live UI answers with voice.start. Broadcast — not newest-connection —
    because webview reloads leave stale zombie connections behind and a
    diagnostic client must not steal the wake from the real window; dead pages
    simply never answer. Returns False if nobody is connected to hear it."""
    heard = False
    for conn in list(state.connections):
        await conn.cancel_generation()
        try:
            await conn.send(protocol.wake_detected())
            heard = True
        except Exception:
            continue
    return heard


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="jarvis-backend", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        if not origin_allowed(websocket.headers.get("origin")):
            await websocket.close(code=WS_POLICY_VIOLATION)
            return
        await websocket.accept()

        send_lock = asyncio.Lock()

        async def send(msg: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_json(msg)

        try:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT_S)
        except (TimeoutError, WebSocketDisconnect, ValueError):
            await websocket.close(code=WS_POLICY_VIOLATION)
            return
        if not (
            isinstance(first, dict)
            and first.get("type") == "auth"
            and token_valid(state.token, first.get("token"))
        ):
            await send(protocol.error("AUTH_FAILED"))
            await websocket.close(code=WS_POLICY_VIOLATION)
            return

        await send(protocol.ready(__version__))

        conn = Connection(send=send)
        state.connections.append(conn)
        if state.wake is not None:
            state.wake.ensure_started()
            await send(protocol.wake_status(state.wake.enabled, state.wake.available))
        try:
            while True:
                try:
                    msg = await websocket.receive_json()
                except ValueError:
                    await send(protocol.error("BAD_MESSAGE"))
                    continue
                if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
                    await send(protocol.error("BAD_MESSAGE"))
                    continue
                await _dispatch(state, conn, msg)
        except WebSocketDisconnect:
            pass
        finally:
            state.connections.remove(conn)
            await conn.cancel_generation()

    return app


def _generation_send(conn: Connection):
    """conn.send, but it notes the conversation the generation settled on.

    A generation started with no conversation_id creates one mid-flight and only
    reveals it in chat.start; without this the connection couldn't tell whether
    a delete targets the conversation it is actively writing into. Sniffing the
    outbound message keeps that knowledge in one place for both the text and
    voice paths, instead of threading `conn` through two orchestrators."""

    async def send(msg: dict[str, Any]) -> None:
        if msg.get("type") == "chat.start":
            conn.generating_conversation_id = msg.get("conversation_id")
        await conn.send(msg)

    return send


def _conversations_payload(state: AppState) -> dict[str, Any]:
    return {
        "type": "conversations",
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
            }
            for c in state.store.list_conversations()
        ],
    }


async def _broadcast_conversations(state: AppState) -> None:
    """Push the fresh list to every open UI. Rename/delete change what other
    windows are showing, so they re-sync the same way wake.status does."""
    payload = _conversations_payload(state)
    for c in list(state.connections):
        with contextlib.suppress(Exception):
            await c.send(payload)


async def _dispatch(state: AppState, conn: Connection, msg: dict[str, Any]) -> None:
    """Handle one client message, mutating conn.generation as needed."""
    mtype = msg["type"]
    send = conn.send

    try:
        if mtype == "ping":
            await send({"type": "pong"})

        elif mtype in ("chat.stop", "voice.stop"):
            if conn.busy:
                conn.generation.cancel()

        elif mtype == "voice.start":
            if conn.busy:
                await send(protocol.error("BUSY"))
                return
            conn.generating_conversation_id = msg.get("conversation_id")
            conn.generation = asyncio.create_task(
                run_voice_exchange(state, _generation_send(conn), msg, conn=conn)
            )

        elif mtype == "confirm.respond":
            # Deliberately NOT scoped to `conn`: the confirm was broadcast to
            # every window, so any window may answer it and the first one to do
            # so wins. The correlation id is what binds an answer to a call —
            # see security/confirm.py.
            if state.confirm is not None:
                state.confirm.respond(msg.get("id"), msg.get("answer"))

        elif mtype == "voice.say":
            # The frontend owns all wording (i18n), but TTS lives here, so the
            # client hands us the sentence to speak. Only meaningful during a
            # live spoken turn; otherwise there is no player and nothing to say.
            text = msg.get("text")
            if isinstance(text, str) and text.strip() and conn.voice_sentences is not None:
                conn.voice_sentences.put_nowait(text.strip())

        elif mtype == "chat.send":
            if conn.busy:
                await send(protocol.error("BUSY"))
                return
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                await send(protocol.error("BAD_MESSAGE", "content required"))
                return
            conn.generating_conversation_id = msg.get("conversation_id")
            conn.generation = asyncio.create_task(
                _generate(state, _generation_send(conn), msg, content)
            )

        elif mtype == "wake.set":
            if state.wake is None:
                await send(protocol.error("WAKE_UNAVAILABLE"))
                return
            try:
                state.wake.set_enabled(bool(msg.get("enabled")))
            except WakeError as e:
                await send(protocol.error(e.code, e.detail))
                return
            status = protocol.wake_status(state.wake.enabled, state.wake.available)
            for c in list(state.connections):  # all open UIs stay in sync
                with contextlib.suppress(Exception):
                    await c.send(status)

        elif mtype == "models.list":
            models = await state.backend.list_models()
            default = ""
            with contextlib.suppress(LLMError):
                default = pick_model(models, state.config.default_model)
            # The RAM tier travels with the list so the picker can explain
            # itself: which model was auto-chosen, and which ones this machine
            # would struggle with. Numbers only — the copy lives in i18n.
            budget = tier_budget_b()
            # Tool state per model: "on" (curated + measured), "optin"
            # (capable template, unvetted — off by default) or "unsupported".
            # See llm/capabilities.py for why unvetted defaults to off.
            tools = await capabilities.resolve(state.backend, models)
            await send(
                {
                    "type": "models",
                    "default": default,
                    "source": "configured" if state.config.default_model else "auto",
                    "tier": {"ram_gb": round(ram_gb(), 1), "budget_b": budget},
                    "models": [
                        {
                            "id": m.id,
                            "parameter_size": m.parameter_size,
                            "size_bytes": m.size_bytes,
                            "params_b": (p := params_b(m)),
                            "over_budget": p is not None and p > budget,
                            "tools": tools[m.id],
                        }
                        for m in models
                    ],
                }
            )

        elif mtype == "system.readiness":
            await send(await readiness.payload(state))

        elif mtype == "conversations.list":
            await send(_conversations_payload(state))

        elif mtype == "conversation.rename":
            cid = msg.get("conversation_id", "")
            title = msg.get("title")
            if not isinstance(title, str) or not title.strip():
                await send(protocol.error("BAD_MESSAGE", "title required"))
                return
            # touch=False: the sidebar sorts by last activity, and a rename is
            # not activity — see Store.set_title.
            state.store.set_title(cid, title.strip()[:TITLE_MAX_CHARS], touch=False)
            await _broadcast_conversations(state)

        elif mtype == "conversation.delete":
            cid = msg.get("conversation_id", "")
            if not isinstance(cid, str) or not cid:
                await send(protocol.error("BAD_MESSAGE", "conversation_id required"))
                return
            # Deleting what we're generating into would race: run_exchange
            # persists its turn even when cancelled, so stop it and let that
            # write land BEFORE the rows go away — otherwise the append hits
            # the FK constraint against a conversation that no longer exists.
            if conn.generating_conversation_id == cid:
                await conn.cancel_generation()
            state.store.delete_conversation(cid)
            await _broadcast_conversations(state)

        elif mtype == "conversation.history":
            cid = msg.get("conversation_id", "")
            turns = state.store.path(cid)
            await send(
                {
                    "type": "history",
                    "conversation_id": cid,
                    "turns": [
                        {
                            "id": t.id,
                            "parent_turn_id": t.parent_turn_id,
                            "messages": [
                                {"id": m.id, "role": m.role, "content": m.content}
                                for m in t.messages
                            ],
                        }
                        for t in turns
                    ],
                }
            )

        else:
            await send(protocol.error("UNKNOWN_TYPE", mtype))

    except LLMError as e:
        await send(protocol.error(e.code, e.detail))
    except StorageError as e:
        await send(protocol.error(e.code, e.detail))


async def _generate(state: AppState, send, msg: dict[str, Any], content: str) -> None:
    try:
        conversation_id = msg.get("conversation_id") or state.store.create_conversation(
            title=content[:80]
        )
        model = msg.get("model") or pick_model(
            await state.backend.list_models(), state.config.default_model
        )
        await send(protocol.chat_start(conversation_id, model))
        result = await run_exchange(
            store=state.store,
            backend=state.backend,
            model=model,
            conversation_id=conversation_id,
            user_text=content,
            on_delta=lambda text: send(protocol.chat_delta(text)),
            parent_turn_id=msg.get("parent_turn_id"),
            registry=await state.registry_for(model),
            on_span=lambda span: send(protocol.tool_span(span)),
        )
        if result.error_code:
            await send(protocol.error(result.error_code, result.error_detail))
        if result.turn_id is not None:
            await send(protocol.chat_done(conversation_id, result.turn_id, result.interrupted))
    except asyncio.CancelledError:
        raise  # chat.stop / delete / disconnect — cancellation must propagate
    except (LLMError, StorageError) as e:
        await send(protocol.error(e.code, e.detail))
    except Exception:  # noqa: BLE001
        # This task IS the generation slot. Dying silently leaves the frontend
        # holding `streamKey` with no chat.done ever coming, which disables the
        # composer until the app restarts — so an unexpected failure has to come
        # back as an error the UI can clear itself on.
        await send(protocol.error("GENERATION_FAILED"))
