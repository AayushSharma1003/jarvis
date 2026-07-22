"""The multi-round agent loop: tool dispatch, spans, persistence, round cap."""

from __future__ import annotations

import json

from jarvis_backend.agent.loop import MAX_TOOL_ROUNDS, run_exchange
from jarvis_backend.llm.base import ChatBackend, ModelInfo, TextDelta, ToolCall
from jarvis_backend.security.permissions import SAFE, Decision, SafeOnlyGate
from jarvis_backend.tools.registry import Registry

MODEL = "fake:3b"


class ScriptedBackend(ChatBackend):
    """Replays a canned list of events per round, recording what it was sent."""

    name = "scripted"

    def __init__(self, *rounds):
        self.rounds = [list(r) for r in rounds]
        self.seen: list[tuple[list, list | None]] = []

    async def list_models(self):
        return [ModelInfo(id=MODEL, parameter_size="3B")]

    async def stream_chat(self, model, messages, tools=None):
        self.seen.append((list(messages), tools))
        events = self.rounds.pop(0) if self.rounds else [TextDelta("out of script")]
        for e in events:
            yield e


class AllowAll:
    async def check(self, name, risk, arguments):
        return Decision.allow()


def _registry(fn=None, *, risk=SAFE, name="get_datetime", gate=None):
    r = Registry(gate or AllowAll())
    r.register(fn or (lambda: "Tuesday 21 July"), risk=risk, name=name, description="d")
    return r


async def _run(store, backend, registry=None, text="what day is it?", **kw):
    cid = store.create_conversation(title="t")
    deltas: list[str] = []
    spans = []
    result = await run_exchange(
        store=store,
        backend=backend,
        model=MODEL,
        conversation_id=cid,
        user_text=text,
        on_delta=lambda d: _append(deltas, d),
        registry=registry,
        on_span=lambda s: _append(spans, s),
        **kw,
    )
    return result, deltas, spans, cid


async def _append(target, item):
    target.append(item)


# -- the round trip ---------------------------------------------------------


async def test_tool_round_trip(store):
    backend = ScriptedBackend(
        [TextDelta("Let me check. "), ToolCall("c1", "get_datetime", {})],
        [TextDelta("It's Tuesday.")],
    )
    result, deltas, spans, _ = await _run(store, backend, _registry())

    assert "".join(deltas) == "Let me check. It's Tuesday."
    assert result.text == "Let me check. It's Tuesday."
    assert len(spans) == 1 and spans[0].name == "get_datetime"
    assert spans[0].content == "Tuesday 21 July"
    # Round 2 must actually carry the tool result back to the model.
    second_round_messages = backend.seen[1][0]
    assert second_round_messages[-1].role == "tool"
    assert second_round_messages[-1].content == "Tuesday 21 July"


async def test_tool_span_is_persisted_in_the_turn(store):
    backend = ScriptedBackend(
        [ToolCall("c1", "get_datetime", {})],
        [TextDelta("Tuesday.")],
    )
    result, _, _, cid = await _run(store, backend, _registry())

    turn = store.path(cid)[-1]
    roles = [m.role for m in turn.messages]
    assert roles == ["user", "tool", "assistant"], roles
    payload = json.loads(turn.messages[1].content)
    assert payload["name"] == "get_datetime"
    assert payload["ok"] is True
    assert result.turn_id == turn.id


async def test_prose_before_and_after_a_tool_keeps_its_order(store):
    backend = ScriptedBackend(
        [TextDelta("Checking. "), ToolCall("c1", "get_datetime", {})],
        [TextDelta("It's Tuesday.")],
    )
    _, _, _, cid = await _run(store, backend, _registry())
    turn = store.path(cid)[-1]
    assert [m.role for m in turn.messages] == ["user", "assistant", "tool", "assistant"]
    assert turn.messages[1].content == "Checking. "
    assert turn.messages[3].content == "It's Tuesday."


# -- history replay ---------------------------------------------------------


async def test_tool_rows_are_not_replayed_to_the_model(store):
    """Deliberate v1 choice (see the loop's docstring): replaying every
    historical tool result grows the prompt without bound, and prompt length is
    TTFT on a machine with ~650ms for the whole LLM leg."""
    backend = ScriptedBackend(
        [ToolCall("c1", "get_datetime", {})],
        [TextDelta("Tuesday.")],
    )
    _, _, _, cid = await _run(store, backend, _registry())

    second = ScriptedBackend([TextDelta("Sure.")])
    await run_exchange(
        store=store,
        backend=second,
        model=MODEL,
        conversation_id=cid,
        user_text="thanks",
        on_delta=lambda d: _append([], d),
        registry=_registry(),
    )
    replayed = second.seen[0][0]
    assert all(m.role != "tool" for m in replayed)
    assert [m.role for m in replayed] == ["system", "user", "assistant", "user"]


# -- the round cap ----------------------------------------------------------


async def test_round_cap_stops_a_tool_loop_and_forces_an_answer(store):
    """A model that keeps asking must not ping-pong forever — every round is a
    round trip the user waits through."""
    asking = [[ToolCall(f"c{i}", "get_datetime", {})] for i in range(MAX_TOOL_ROUNDS + 2)]
    backend = ScriptedBackend(*asking, [TextDelta("fine, Tuesday.")])
    result, _, spans, _ = await _run(store, backend, _registry())

    assert len(backend.seen) == MAX_TOOL_ROUNDS + 1
    assert len(spans) == MAX_TOOL_ROUNDS
    # The last pass is offered NO tools, so the model cannot ask again.
    assert backend.seen[-1][1] is None
    assert all(sent is not None for _, sent in backend.seen[:-1])


async def test_no_registry_means_no_tools_on_the_wire(store):
    backend = ScriptedBackend([TextDelta("hello")])
    result, _, spans, _ = await _run(store, backend, registry=None)
    assert backend.seen[0][1] is None
    assert spans == []
    assert result.text == "hello"


async def test_empty_registry_offers_no_tools(store):
    backend = ScriptedBackend([TextDelta("hello")])
    await _run(store, backend, Registry(AllowAll()))
    assert backend.seen[0][1] is None


# -- security --------------------------------------------------------------


async def test_gate_refusal_becomes_a_failed_span_the_model_can_see(store):
    """The refusal must reach the model, or it will invent a result."""
    ran = []
    registry = _registry(
        lambda: ran.append(1) or "secret", risk="dangerous", name="rm_rf", gate=SafeOnlyGate()
    )
    backend = ScriptedBackend(
        [ToolCall("c1", "rm_rf", {})],
        [TextDelta("I can't do that yet.")],
    )
    _, _, spans, cid = await _run(store, backend, registry)

    assert ran == [], "a refused tool must never execute"
    assert spans[0].ok is False
    assert spans[0].code == "TOOL_CONFIRMATION_UNAVAILABLE"
    assert backend.seen[1][0][-1].content == "TOOL_CONFIRMATION_UNAVAILABLE"


async def test_malformed_tool_call_never_reaches_the_user(store):
    """llama3.2:3b prints botched calls as prose; that text would render in the
    transcript AND be spoken aloud by Kokoro."""
    leak = '{"name":"get_datetime","parameters\\":{\\"x":"1"}}'
    backend = ScriptedBackend([TextDelta(leak)])
    result, deltas, spans, cid = await _run(store, backend, _registry())

    assert deltas == [], f"leaked to the user: {deltas}"
    assert result.text == ""
    # Surfaced as a failed span, so the user isn't left thinking it worked.
    assert len(spans) == 1 and spans[0].code == "TOOL_CALL_MALFORMED"
    turn = store.path(cid)[-1]
    assert [m.role for m in turn.messages] == ["user", "tool"]


# -- the system prompt must match reality ----------------------------------


async def test_prompt_says_no_tools_when_none_are_offered(store):
    backend = ScriptedBackend([TextDelta("hi")])
    await _run(store, backend, registry=None)
    assert "no tools yet" in backend.seen[0][0][0].content


async def test_prompt_does_not_deny_tools_when_they_are_offered(store):
    """Handing over a tool schema while saying "you have no tools" is a lie
    that also suppresses the tools."""
    backend = ScriptedBackend([TextDelta("hi")])
    await _run(store, backend, _registry())
    system = backend.seen[0][0][0].content
    assert "no tools yet" not in system
    assert "never claim an action you did not actually take" in system.lower()
