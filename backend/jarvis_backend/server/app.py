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
from ..llm.base import ChatBackend, LLMError
from ..llm.tiering import pick_model
from ..storage.conversations import StorageError, Store
from . import protocol
from .auth import origin_allowed, token_valid

AUTH_TIMEOUT_S = 5.0
WS_POLICY_VIOLATION = 1008


@dataclass
class AppState:
    token: str
    store: Store
    backend: ChatBackend
    config: Config


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

        generation: asyncio.Task | None = None
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
                generation = await _dispatch(state, send, msg, generation)
        except WebSocketDisconnect:
            pass
        finally:
            if generation is not None and not generation.done():
                generation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await generation

    return app


async def _dispatch(
    state: AppState,
    send,
    msg: dict[str, Any],
    generation: asyncio.Task | None,
) -> asyncio.Task | None:
    """Handle one client message; returns the (possibly new) generation task."""
    mtype = msg["type"]
    busy = generation is not None and not generation.done()

    try:
        if mtype == "ping":
            await send({"type": "pong"})

        elif mtype == "chat.stop":
            if busy:
                generation.cancel()

        elif mtype == "chat.send":
            if busy:
                await send(protocol.error("BUSY"))
                return generation
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                await send(protocol.error("BAD_MESSAGE", "content required"))
                return generation
            return asyncio.create_task(_generate(state, send, msg, content))

        elif mtype == "models.list":
            models = await state.backend.list_models()
            default = ""
            with contextlib.suppress(LLMError):
                default = pick_model(models, state.config.default_model)
            await send(
                {
                    "type": "models",
                    "default": default,
                    "models": [
                        {"id": m.id, "parameter_size": m.parameter_size, "size_bytes": m.size_bytes}
                        for m in models
                    ],
                }
            )

        elif mtype == "conversations.list":
            await send(
                {
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
            )

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

    return generation


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
        )
        if result.error_code:
            await send(protocol.error(result.error_code, result.error_detail))
        if result.turn_id is not None:
            await send(protocol.chat_done(conversation_id, result.turn_id, result.interrupted))
    except (LLMError, StorageError) as e:
        await send(protocol.error(e.code, e.detail))
