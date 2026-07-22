"""The agent loop: one user turn, streamed, with tool rounds, persisted.

A turn is the atomic branching unit (docs/architecture.md), so everything the
assistant did in response to one user message — prose, tool calls, tool
results, the final answer — lands in ONE turn, in the order it happened.

**Tool history is not replayed across turns.** The messages sent to the model
are rebuilt from `store.path()`, and stored `role='tool'` rows are skipped:
only user and assistant prose is replayed. Within a single exchange the model
of course sees its own tool results — that is what makes the round trip work.
Across turns it sees the answer it already wrote, which contains the
information. The reason is cost: replaying every historical tool result grows
the prompt without bound, and prompt length is TTFT (docs/latency.md) on a
machine where the whole LLM leg has ~650ms. Small models also degrade sharply
with long tool transcripts.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..llm.base import ChatBackend, ChatMessage, LLMError, TextDelta, ToolCall
from ..security.permissions import ToolContext
from ..storage.conversations import Message, Store
from ..tools.registry import Registry, ToolResult
from .prompts import system_prompt
from .toolfilter import MalformedToolCallFilter

# How many times the model may ask for tools before we stop and let it answer.
# A cap, not a target: without one a confused model can ping-pong forever, and
# every round is a full round trip the user waits through.
MAX_TOOL_ROUNDS = 4


@dataclass(frozen=True)
class ToolSpan:
    """One tool call and what came back — the unit the transcript renders."""

    call_id: str
    name: str
    arguments: dict
    content: str
    ok: bool
    code: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "call_id": self.call_id,
                "name": self.name,
                "arguments": self.arguments,
                "content": self.content,
                "ok": self.ok,
                "code": self.code,
            }
        )


@dataclass(frozen=True)
class ExchangeResult:
    conversation_id: str
    text: str
    turn_id: str | None  # None only when nothing was worth persisting
    interrupted: bool = False
    error_code: str | None = None
    error_detail: str = ""
    spans: tuple[ToolSpan, ...] = field(default_factory=tuple)


def _history_messages(store: Store, conversation_id: str, parent_turn_id: str | None):
    """Replay prior turns as chat messages — prose only, see module docstring."""
    messages = []
    for turn in store.path(conversation_id, parent_turn_id):
        for m in turn.messages:
            if m.role == "tool":
                continue
            if m.content:
                messages.append(ChatMessage(m.role, m.content))
    return messages


async def run_exchange(
    store: Store,
    backend: ChatBackend,
    model: str,
    conversation_id: str,
    user_text: str,
    on_delta: Callable[[str], Awaitable[None]],
    parent_turn_id: str | None = None,
    voice_mode: bool = False,
    registry: Registry | None = None,
    on_span: Callable[[ToolSpan], Awaitable[None]] | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> ExchangeResult:
    """Stream one user→assistant exchange and persist it as ONE atomic turn.

    Persistence policy: a completed or partially-streamed exchange is written
    (partial ⇒ interrupted=True); an exchange that failed before producing
    anything is not, so history never accumulates empty turns. The immutable
    store never sees a half-open turn because the write happens after streaming
    ends.
    """
    tools = registry.schemas() if registry is not None and len(registry) else None
    tool_names = {t["function"]["name"] for t in tools} if tools else set()

    # One context for the whole exchange, so its deny-memo spans every round: a
    # model refused a tool in round 1 must not get a second dialog for the same
    # call in round 2. It dies with the exchange, which is exactly the lifetime
    # "for this request" should mean.
    context = ToolContext(conversation_id=conversation_id, voice=voice_mode)

    messages = [
        ChatMessage(
            "system",
            system_prompt(
                store.get_system_prompt(conversation_id),
                voice=voice_mode,
                # Telling a model it has no tools while handing it a tool schema
                # both lies and suppresses the tools — see prompts.py.
                has_tools=tools is not None,
            ),
        ),
        *_history_messages(store, conversation_id, parent_turn_id),
        ChatMessage("user", user_text),
    ]

    # Persisted in the order things happened, so a reloaded transcript reads
    # the same as the live one.
    turn_messages: list[Message] = [Message("user", user_text)]
    all_text: list[str] = []
    spans: list[ToolSpan] = []
    interrupted = False
    error_code: str | None = None
    error_detail = ""

    try:
        for _round in range(max_rounds + 1):
            # The final pass is offered NO tools, which forces the model to
            # answer in words instead of asking again. Cleaner than letting it
            # request a tool we then refuse: it never makes the request.
            final_pass = _round == max_rounds
            round_tools = None if final_pass else tools
            round_text: list[str] = []
            calls: list[ToolCall] = []
            # A fresh filter per round: the leak is per-generation, and a
            # settled filter from a previous round would pass the next one's
            # opening blob straight through.
            text_filter = MalformedToolCallFilter(tool_names)

            async for event in backend.stream_chat(model, messages, tools=round_tools):
                if isinstance(event, TextDelta):
                    if safe := text_filter.feed(event.text):
                        round_text.append(safe)
                        await on_delta(safe)
                else:
                    calls.append(event)
            if tail := text_filter.flush():
                round_text.append(tail)
                await on_delta(tail)

            text = "".join(round_text)
            if text:
                all_text.append(text)
                turn_messages.append(Message("assistant", text))

            # A tool call the model printed instead of emitting: the tool never
            # ran, so surface it as a failed span rather than letting the user
            # believe something happened.
            for _ in text_filter.dropped:
                span = ToolSpan("", "", {}, "", False, "TOOL_CALL_MALFORMED")
                spans.append(span)
                turn_messages.append(Message("tool", span.to_json()))
                if on_span is not None:
                    await on_span(span)

            if not calls or registry is None or final_pass:
                break

            messages.append(ChatMessage("assistant", text, tool_calls=tuple(calls)))
            for call in calls:
                result: ToolResult = await registry.invoke(
                    call.id, call.name, call.arguments, context
                )
                span = ToolSpan(
                    call_id=call.id,
                    name=call.name,
                    arguments=call.arguments,
                    content=result.content,
                    ok=result.ok,
                    code=result.code,
                )
                spans.append(span)
                turn_messages.append(Message("tool", span.to_json()))
                if on_span is not None:
                    await on_span(span)
                # The model needs the failure code too — it is what lets it
                # apologise accurately instead of inventing a result.
                messages.append(
                    ChatMessage(
                        "tool",
                        result.content if result.ok else result.code,
                        tool_name=call.name,
                    )
                )
    except asyncio.CancelledError:
        interrupted = True
    except LLMError as e:
        error_code, error_detail = e.code, e.detail
        interrupted = bool(all_text)

    text = "".join(all_text)
    turn_id = None
    if len(turn_messages) > 1 or error_code is None:
        if len(turn_messages) == 1:
            # Nothing came back but nothing failed either. Keep the old
            # [user, assistant] shape so history and the transcript don't have
            # to special-case a turn with no reply in it.
            turn_messages.append(Message("assistant", ""))
        turn_id = store.append_turn(
            conversation_id, turn_messages, parent_turn_id=parent_turn_id
        )
    return ExchangeResult(
        conversation_id=conversation_id,
        text=text,
        turn_id=turn_id,
        interrupted=interrupted,
        error_code=error_code,
        error_detail=error_detail,
        spans=tuple(spans),
    )
