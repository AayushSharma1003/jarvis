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


async def test_default_registry_ships_run_command_but_it_cannot_run_unconfirmed():
    """M4.4 ships the first tool that needs no sandbox: run_command escapes the
    filesystem sandbox by design, so it registers unconditionally. It is
    `dangerous`, and under the no-broker SafeOnlyGate the sharpest tool in the
    project still cannot run — the fallback holds even for shell."""
    r = default_registry(SafeOnlyGate())
    names = [s["function"]["name"] for s in r.schemas()]
    assert "get_datetime" in names
    assert r.get("get_datetime").risk == SAFE
    assert r.get("run_command").risk == DANGEROUS
    result = await r.invoke("c", "run_command", {"command": "echo hi"})
    assert (result.ok, result.code) == (False, "TOOL_CONFIRMATION_UNAVAILABLE")


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


# -- file tools (M4.3) ------------------------------------------------------

from jarvis_backend.security.sandbox import Sandbox  # noqa: E402
from jarvis_backend.tools import filesystem  # noqa: E402
from jarvis_backend.tools.filesystem import MAX_ENTRIES, MAX_READ_BYTES  # noqa: E402


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _fs_registry(workspace, gate=None) -> Registry:
    r = Registry(gate or AllowAll())
    filesystem.register(r, Sandbox([workspace]))
    return r


async def test_read_file_returns_contents_and_taints(workspace):
    (workspace / "notes.txt").write_text("hello from a file")
    result = await _fs_registry(workspace).invoke(
        "c", "read_file", {"path": str(workspace / "notes.txt")}
    )
    assert result.ok
    assert result.content == "hello from a file"
    # The whole point: the content is untrusted from here on.
    assert result.taint_source == str(workspace / "notes.txt")


async def test_list_dir_lists_and_does_not_taint(workspace):
    (workspace / "a.txt").write_text("x")
    (workspace / "sub").mkdir()
    result = await _fs_registry(workspace).invoke("c", "list_dir", {"path": str(workspace)})
    assert result.ok
    assert "a.txt" in result.content
    assert "sub/" in result.content
    assert result.taint_source == "", "a listing is structure, not content"


async def test_write_file_creates_parents_and_overwrites(workspace):
    target = workspace / "deep" / "nested" / "out.txt"
    r = _fs_registry(workspace)
    assert (await r.invoke("c", "write_file", {"path": str(target), "content": "one"})).ok
    assert target.read_text() == "one"
    assert (await r.invoke("c", "write_file", {"path": str(target), "content": "two"})).ok
    assert target.read_text() == "two"


async def test_delete_file_removes_it(workspace):
    doomed = workspace / "doomed.txt"
    doomed.write_text("x")
    result = await _fs_registry(workspace).invoke("c", "delete_file", {"path": str(doomed)})
    assert result.ok
    assert not doomed.exists()


async def test_delete_refuses_a_directory(workspace):
    """One confirmation cannot honestly stand for an unbounded set of files."""
    (workspace / "sub").mkdir()
    (workspace / "sub" / "keep.txt").write_text("x")
    result = await _fs_registry(workspace).invoke(
        "c", "delete_file", {"path": str(workspace / "sub")}
    )
    assert (result.ok, result.code) == (False, "IS_A_DIRECTORY")
    assert (workspace / "sub" / "keep.txt").exists()


# -- the sandbox is not optional -------------------------------------------


@pytest.mark.parametrize("tool", ["read_file", "list_dir", "delete_file"])
async def test_a_path_outside_the_sandbox_is_a_failed_result(workspace, tmp_path, tool):
    """SandboxError.code surfaces through the registry as a failed span the
    model can react to, rather than an exception that ends the exchange."""
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    result = await _fs_registry(workspace).invoke("c", tool, {"path": str(outside)})
    assert (result.ok, result.code) == (False, "PATH_OUTSIDE_SANDBOX")


async def test_writing_outside_the_sandbox_writes_nothing(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    result = await _fs_registry(workspace).invoke(
        "c", "write_file", {"path": str(outside), "content": "planted"}
    )
    assert (result.ok, result.code) == (False, "PATH_OUTSIDE_SANDBOX")
    assert not outside.exists(), "the refusal must happen before anything is written"


async def test_a_symlink_out_of_the_sandbox_is_refused(workspace, tmp_path):
    """The escape that path-string checking would miss, at the tool level."""
    secret = tmp_path / "secrets"
    secret.mkdir()
    (secret / "id_rsa").write_text("PRIVATE KEY")
    (workspace / "shortcut").symlink_to(secret)
    result = await _fs_registry(workspace).invoke(
        "c", "read_file", {"path": str(workspace / "shortcut" / "id_rsa")}
    )
    assert (result.ok, result.code) == (False, "PATH_OUTSIDE_SANDBOX")
    assert "PRIVATE KEY" not in result.content


# -- resource guards --------------------------------------------------------


async def test_an_oversized_file_is_refused_before_it_is_read(workspace):
    """MAX_RESULT_CHARS truncates what reaches the model, but only after the
    whole file is in memory. On an 8GB machine that is the wrong order."""
    big = workspace / "big.bin"
    big.write_bytes(b"x" * (MAX_READ_BYTES + 1))
    result = await _fs_registry(workspace).invoke("c", "read_file", {"path": str(big)})
    assert (result.ok, result.code) == (False, "FILE_TOO_LARGE")


async def test_a_huge_listing_is_capped_and_says_so(workspace):
    """The cap must bind before the registry's MAX_RESULT_CHARS truncation, or
    the "and N more" line is itself truncated away and the model is silently
    shown a partial directory."""
    for i in range(MAX_ENTRIES + 10):
        (workspace / f"f{i:04d}.txt").write_text("x")
    result = await _fs_registry(workspace).invoke("c", "list_dir", {"path": str(workspace)})
    assert result.ok
    assert "and 10 more" in result.content
    assert not result.content.endswith("(truncated)")


async def test_missing_files_and_wrong_types_report_distinct_codes(workspace):
    r = _fs_registry(workspace)
    (workspace / "sub").mkdir()
    missing = await r.invoke("c", "read_file", {"path": str(workspace / "nope.txt")})
    assert missing.code == "FILE_NOT_FOUND"
    a_dir = await r.invoke("c", "read_file", {"path": str(workspace / "sub")})
    assert a_dir.code == "IS_A_DIRECTORY"
    a_file = await r.invoke("c", "list_dir", {"path": str(workspace / "sub")})
    assert a_file.ok
    (workspace / "f.txt").write_text("x")
    not_dir = await r.invoke("c", "list_dir", {"path": str(workspace / "f.txt")})
    assert not_dir.code == "NOT_A_DIRECTORY"


async def test_undecodable_bytes_do_not_crash_a_read(workspace):
    """A binary file the model asked for by mistake is a result, not a stack
    trace — errors='replace' is what keeps that true."""
    (workspace / "blob.bin").write_bytes(b"\xff\xfe\x00hello")
    result = await _fs_registry(workspace).invoke(
        "c", "read_file", {"path": str(workspace / "blob.bin")}
    )
    assert result.ok
    assert "hello" in result.content


# -- registration -----------------------------------------------------------


def test_file_tools_ride_the_normal_risk_levels(workspace):
    r = _fs_registry(workspace)
    assert r.get("read_file").risk == SAFE
    assert r.get("list_dir").risk == SAFE
    assert r.get("write_file").risk == ASK
    assert r.get("delete_file").risk == DANGEROUS


def test_the_sandbox_is_never_an_argument_the_model_can_fill(workspace):
    """It is bound at registration. A sandbox in the JSON schema would be a
    sandbox the model gets to choose."""
    schema = _fs_registry(workspace).get("read_file").schema()
    assert list(schema["function"]["parameters"]["properties"]) == ["path"]


def test_default_registry_ships_file_tools_only_with_a_sandbox(workspace):
    without = default_registry(SafeOnlyGate())
    assert without.get("read_file") is None
    # run_command is not a file tool, so it is present with OR without a sandbox.
    assert without.get("run_command") is not None
    with_sandbox = default_registry(SafeOnlyGate(), Sandbox([workspace]))
    assert with_sandbox.get("read_file") is not None
    assert with_sandbox.get("get_datetime") is not None


async def test_a_sandbox_with_no_roots_refuses_everything(workspace):
    """`roots = []` means file access is off — the tools exist and say no,
    which is more honest than pretending they were never installed."""
    r = Registry(AllowAll())
    filesystem.register(r, Sandbox([]))
    (workspace / "a.txt").write_text("x")
    result = await r.invoke("c", "read_file", {"path": str(workspace / "a.txt")})
    assert (result.ok, result.code) == (False, "PATH_OUTSIDE_SANDBOX")


async def test_write_and_delete_cannot_run_under_the_safe_only_gate(workspace):
    """M4.2's fallback still holds: no confirmation machinery, no side effects."""
    r = _fs_registry(workspace, gate=SafeOnlyGate())
    target = workspace / "nope.txt"
    result = await r.invoke("c", "write_file", {"path": str(target), "content": "x"})
    assert (result.ok, result.code) == (False, "TOOL_CONFIRMATION_UNAVAILABLE")
    assert not target.exists()
