"""The agent loop. Phase 1: a single LLM exchange, streamed and persisted.

Phase 4 extends this with tool dispatch — which is why exchange orchestration
lives here and not in the WebSocket handler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..llm.base import ChatBackend, ChatMessage, LLMError
from ..storage.conversations import Message, Store
from .prompts import system_prompt


@dataclass(frozen=True)
class ExchangeResult:
    conversation_id: str
    text: str
    turn_id: str | None  # None only when nothing was worth persisting
    interrupted: bool = False
    error_code: str | None = None
    error_detail: str = ""


async def run_exchange(
    store: Store,
    backend: ChatBackend,
    model: str,
    conversation_id: str,
    user_text: str,
    on_delta: Callable[[str], Awaitable[None]],
    parent_turn_id: str | None = None,
    voice_mode: bool = False,
) -> ExchangeResult:
    """Stream one user→assistant exchange and persist it as ONE atomic turn.

    Persistence policy: a completed or partially-streamed exchange is written
    (partial ⇒ interrupted=True); an exchange that failed before producing any
    text is not, so history never accumulates empty turns. The immutable store
    never sees a half-open turn because the write happens after streaming ends.
    """
    history = store.path(conversation_id, parent_turn_id)
    messages = [
        ChatMessage(
            "system", system_prompt(store.get_system_prompt(conversation_id), voice=voice_mode)
        )
    ]
    for turn in history:
        for m in turn.messages:
            messages.append(ChatMessage(m.role, m.content))
    messages.append(ChatMessage("user", user_text))

    chunks: list[str] = []
    interrupted = False
    error_code: str | None = None
    error_detail = ""
    try:
        async for delta in backend.stream_chat(model, messages):
            chunks.append(delta)
            await on_delta(delta)
    except asyncio.CancelledError:
        interrupted = True
    except LLMError as e:
        error_code, error_detail = e.code, e.detail
        interrupted = bool(chunks)

    text = "".join(chunks)
    turn_id = None
    if chunks or error_code is None:
        turn_id = store.append_turn(
            conversation_id,
            [Message("user", user_text), Message("assistant", text)],
            parent_turn_id=parent_turn_id,
        )
    return ExchangeResult(
        conversation_id=conversation_id,
        text=text,
        turn_id=turn_id,
        interrupted=interrupted,
        error_code=error_code,
        error_detail=error_detail,
    )
