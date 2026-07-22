"""Tool registry, its security gate, and the malformed-call filter.

The gate tests are the important ones: M4.1 deliberately cannot run an `ask` or
`dangerous` tool, because the confirmation machinery that would make that
honest is M4.2. If these ever start passing a non-safe tool, the project has
shipped the half-built permission engine docs/security-model.md warns against.
"""

from __future__ import annotations

import pytest

from jarvis_backend.agent.toolfilter import DECISION_WINDOW, MalformedToolCallFilter
from jarvis_backend.security.permissions import ASK, DANGEROUS, SAFE, Decision, SafeOnlyGate
from jarvis_backend.tools import DEV_TOOLS_ENV, default_registry, get_datetime
from jarvis_backend.tools.registry import MAX_RESULT_CHARS, Registry, build_parameters


class AllowAll:
    """Stand-in for M4.2's engine, so non-gate behaviour can be tested."""

    async def check(self, name, risk, arguments, context):
        return Decision.allow()


def _registry(gate=None) -> Registry:
    return Registry(gate or AllowAll())


# -- the gate ---------------------------------------------------------------


def test_registry_cannot_be_built_without_a_gate():
    """Structural, not conventional: there must be no way to get a Registry
    that skips the security layer (docs/architecture.md)."""
    with pytest.raises(TypeError):
        Registry()  # type: ignore[call-arg]


async def test_safe_tool_runs():
    r = _registry(SafeOnlyGate())
    r.register(lambda: "42", risk=SAFE, name="answer", description="d")
    assert (await r.invoke("c", "answer", {})).content == "42"


@pytest.mark.parametrize("risk", [ASK, DANGEROUS])
async def test_safe_only_gate_refuses_everything_needing_confirmation(risk):
    """The M4.1 promise: nothing side-effectful can run yet."""
    ran = []
    r = _registry(SafeOnlyGate())
    r.register(lambda: ran.append(1) or "done", risk=risk, name="dangerous_op", description="d")
    result = await r.invoke("c", "dangerous_op", {})
    assert not result.ok
    assert result.code == "TOOL_CONFIRMATION_UNAVAILABLE"
    assert ran == [], "the tool body must never execute when the gate refuses"


async def test_gate_is_consulted_before_the_tool_body():
    order = []

    class Recording:
        async def check(self, name, risk, arguments, context):
            order.append("gate")
            return Decision.allow()

    r = _registry(Recording())
    r.register(lambda: order.append("tool") or "x", risk=SAFE, name="t", description="d")
    await r.invoke("c", "t", {})
    assert order == ["gate", "tool"]


# -- invocation failure modes ----------------------------------------------


async def test_unknown_tool_is_a_result_not_an_exception():
    result = await _registry().invoke("c", "imaginary", {})
    assert (result.ok, result.code) == (False, "TOOL_NOT_FOUND")


async def test_bad_arguments_are_reported():
    r = _registry()
    r.register(lambda path: path, risk=SAFE, name="t", description="d")
    result = await r.invoke("c", "t", {"wrong": "kwarg"})
    assert (result.ok, result.code) == (False, "TOOL_BAD_ARGUMENTS")


async def test_a_raising_tool_becomes_a_failed_result():
    """A broken tool must not end the exchange — the model should see the
    failure and be able to react to it."""

    def boom():
        raise RuntimeError("kaboom")

    r = _registry()
    r.register(boom, risk=SAFE, description="d")
    result = await r.invoke("c", "boom", {})
    assert (result.ok, result.code) == (False, "TOOL_FAILED")
    assert "kaboom" in result.content


async def test_huge_output_is_truncated():
    """Unbounded tool output is a context-window denial of service."""
    r = _registry()
    r.register(lambda: "x" * (MAX_RESULT_CHARS * 2), risk=SAFE, name="big", description="d")
    result = await r.invoke("c", "big", {})
    assert len(result.content) < MAX_RESULT_CHARS + 100
    assert result.content.endswith("(truncated)")


async def test_async_tools_are_supported():
    async def fetch():
        return "async result"

    r = _registry()
    r.register(fetch, risk=SAFE, description="d")
    assert (await r.invoke("c", "fetch", {})).content == "async result"


# -- schema generation ------------------------------------------------------


def test_schema_from_signature():
    def sample(path: str, limit: int = 10, deep: bool = False, note: str | None = None):
        """Do a thing."""

    schema = build_parameters(sample)
    assert schema["properties"]["path"] == {"type": "string"}
    assert schema["properties"]["limit"]["type"] == "integer"
    assert schema["properties"]["deep"]["type"] == "boolean"
    assert schema["properties"]["note"]["type"] == "string"
    # Defaulted and optional arguments are not required.
    assert schema["required"] == ["path"]


def test_param_descriptions_reach_the_schema():
    r = _registry()
    tool = r.register(
        lambda path: path,
        risk=SAFE,
        name="read",
        description="Read a file.",
        params={"path": "Absolute path to the file"},
    )
    prop = tool.schema()["function"]["parameters"]["properties"]["path"]
    assert prop["description"] == "Absolute path to the file"


def test_description_falls_back_to_the_docstring():
    def documented():
        """First line becomes the description.

        Later paragraphs are for humans reading the source.
        """

    tool = _registry().register(documented, risk=SAFE)
    assert tool.description == "First line becomes the description."


# -- the shipped tool -------------------------------------------------------


def test_default_registry_ships_only_safe_tools():
    """The shipped set still has no side effects: the permission engine landing
    in M4.2 did not smuggle a real tool in with it."""
    r = default_registry(SafeOnlyGate())
    names = [s["function"]["name"] for s in r.schemas()]
    assert names == ["get_datetime"]
    assert r.get("get_datetime").risk == SAFE


def test_default_registry_requires_a_gate():
    """Structural, like Registry itself: there is no gate-less shortcut."""
    with pytest.raises(TypeError):
        default_registry()  # type: ignore[call-arg]


def test_dev_echo_tool_is_absent_by_default(monkeypatch):
    """It exists to exercise the confirmation path during development. A
    packaged app never sets the variable, so it must not be registered."""
    monkeypatch.delenv(DEV_TOOLS_ENV, raising=False)
    assert default_registry(SafeOnlyGate()).get("echo") is None


def test_dev_echo_tool_is_ask_risk_when_enabled(monkeypatch):
    """If it were `safe` it would run without a dialog and verify nothing."""
    monkeypatch.setenv(DEV_TOOLS_ENV, "1")
    tool = default_registry(SafeOnlyGate()).get("echo")
    assert tool is not None
    assert tool.risk == ASK


async def test_dev_echo_tool_still_cannot_run_without_confirmation(monkeypatch):
    """It is a mirror, not a backdoor: the gate governs it like anything else."""
    monkeypatch.setenv(DEV_TOOLS_ENV, "1")
    r = default_registry(SafeOnlyGate())
    result = await r.invoke("c", "echo", {"text": "hi"})
    assert (result.ok, result.code) == (False, "TOOL_CONFIRMATION_UNAVAILABLE")


def test_get_datetime_returns_something_speakable():
    out = get_datetime()
    assert out and not out.startswith("{"), out


# -- malformed tool-call filter --------------------------------------------

TOOLS = {"run_command", "read_file"}

# Exactly what llama3.2:3b produced during the M4.0 probe.
REAL_LEAK = '{"name":"run_command","parameters\\":{\\"command":"git status"}}'


def _feed(text: str, chunk: int = 7, tools=TOOLS) -> tuple[str, list[str]]:
    f = MalformedToolCallFilter(tools)
    out = "".join(f.feed(text[i : i + chunk]) for i in range(0, len(text), chunk))
    return out + f.flush(), f.dropped


def test_ordinary_prose_passes_through():
    text = "Mount Everest is the tallest mountain, about 8,849 metres."
    assert _feed(text)[0] == text


def test_the_real_leak_is_dropped():
    out, dropped = _feed(REAL_LEAK)
    assert out == ""
    assert len(dropped) == 1


def test_json_naming_an_unregistered_tool_is_kept():
    """False-positive guard: a user asking for JSON must still get their
    answer. It only looks like a tool call if it names a tool we HAVE."""
    text = '{"name":"Aayush","role":"engineer"}'
    assert _feed(text)[0] == text


def test_json_answer_mentioning_a_real_tool_name_elsewhere_is_kept():
    text = '{"topic":"shells","example":"run_command is a tool name"}'
    assert _feed(text)[0] == text


def test_prose_starting_with_a_brace_is_released_promptly():
    """Withholding must end quickly or it becomes latency the voice path pays."""
    text = "{ this is not JSON at all, just an odd opening } " + "and more. " * 40
    out, dropped = _feed(text)
    assert out == text
    assert dropped == []


def test_long_non_call_json_is_released_after_the_window():
    text = '{"data":"' + "y" * (DECISION_WINDOW * 2) + '"}'
    assert _feed(text)[0] == text


def test_leading_whitespace_does_not_defeat_detection():
    out, dropped = _feed("\n\n  " + REAL_LEAK)
    assert out.strip() == ""
    assert len(dropped) == 1


def test_filter_without_tools_never_drops():
    """No tools offered means no tool call is possible, so nothing should be
    suppressed — the model is just talking."""
    assert _feed(REAL_LEAK, tools=set())[0] == REAL_LEAK
