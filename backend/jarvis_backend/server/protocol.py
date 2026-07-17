"""WebSocket message protocol. JSON objects with a `type` discriminator.

Client → server: auth, chat.send, chat.stop, models.list, conversations.list,
                 conversation.history, ping
Server → client: ready, chat.start, chat.delta, chat.done, models,
                 conversations, history, error, pong

Errors carry machine-readable codes only; the frontend owns the wording (i18n).
"""

from __future__ import annotations

from typing import Any


def error(code: str, detail: str = "") -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "error", "code": code}
    if detail:
        msg["detail"] = detail
    return msg


def ready(version: str) -> dict[str, Any]:
    return {"type": "ready", "version": version}


def chat_start(conversation_id: str, model: str) -> dict[str, Any]:
    return {"type": "chat.start", "conversation_id": conversation_id, "model": model}


def chat_delta(text: str) -> dict[str, Any]:
    return {"type": "chat.delta", "text": text}


def chat_done(conversation_id: str, turn_id: str, interrupted: bool = False) -> dict[str, Any]:
    return {
        "type": "chat.done",
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "interrupted": interrupted,
    }
