"""run_command: the shell tool, and the subprocess lifecycle it demands.

docs/security-model.md §1: run_command always confirms, full text shown, no
classifier, no denylist. This file tests the *tool*, not the confirmation — the
gate is exercised in test_confirm.py, which already uses `run_command` as its
dangerous example (session-grant refusal, disable-without-asking). What is
shell-specific and lives here: it runs commands verbatim, captures merged
output, survives a non-zero exit, and — the part a shell makes load-bearing —
bounds output, times out, and kills the whole process group on timeout or
cancellation so nothing is orphaned.

The signal / process-group tests are POSIX-only: Windows process-group semantics
differ, and per the security model only macOS has been exercised by hand.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from jarvis_backend.security.permissions import DANGEROUS, Decision, PermissionGate
from jarvis_backend.tools import shell
from jarvis_backend.tools.registry import Registry

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process-group semantics"
)


class AllowAll:
    async def check(self, name, risk, arguments, context):
        return Decision.allow()


def _registry(gate=None) -> Registry:
    r = Registry(gate or AllowAll())
    shell.register(r)
    return r


async def _run(command: str, gate=None):
    return await _registry(gate).invoke("c", "run_command", {"command": command})


# -- running commands -------------------------------------------------------


async def test_stdout_is_returned():
    result = await _run("echo hello")
    assert result.ok
    assert "hello" in result.content


async def test_stderr_is_captured_too():
    # Merged like a terminal: the model needs to see errors where they happened.
    result = await _run("echo oops 1>&2")
    assert result.ok
    assert "oops" in result.content


async def test_a_nonzero_exit_is_a_result_not_a_failure():
    # The model must see the output AND the code to react — a failed span would
    # send it only the code (agent/loop.py sends result.content only when ok).
    result = await _run("echo nope; exit 3")
    assert result.ok
    assert "nope" in result.content
    assert "exit code 3" in result.content


async def test_a_clean_exit_does_not_announce_its_exit_code():
    result = await _run("echo fine")
    assert result.ok
    assert "exit code" not in result.content


async def test_an_empty_command_is_refused_before_spawning():
    result = await _run("   ")
    assert (result.ok, result.code) == (False, "COMMAND_REQUIRED")


# -- the parts a shell makes load-bearing -----------------------------------


async def test_a_command_that_never_exits_times_out(monkeypatch):
    monkeypatch.setenv("JARVIS_SHELL_TIMEOUT_S", "0.3")
    start = time.monotonic()
    result = await _run("sleep 5")
    elapsed = time.monotonic() - start
    assert (result.ok, result.code) == (False, "COMMAND_TIMEOUT")
    assert elapsed < 3.0, "the timeout must not wait for the command to finish"


@posix_only
async def test_timeout_kills_the_whole_process_group(tmp_path, monkeypatch):
    """A backgrounded child must die with its parent. Killing only the shell
    reparents it to init and it lives on to write the sentinel."""
    monkeypatch.setenv("JARVIS_SHELL_TIMEOUT_S", "0.3")
    sentinel = tmp_path / "child_survived"
    result = await _run(f"(sleep 1.5; touch '{sentinel}') & sleep 5")
    assert result.code == "COMMAND_TIMEOUT"
    await asyncio.sleep(2.0)  # well past when the child would have written
    assert not sentinel.exists()


async def test_runaway_output_is_capped_not_read_to_exhaustion(monkeypatch):
    """`yes` never ends. A communicate()-style read would balloon RAM until the
    timeout fired; the incremental cap must stop it long before that."""
    monkeypatch.setenv("JARVIS_SHELL_TIMEOUT_S", "1")
    start = time.monotonic()
    result = await _run("yes")
    elapsed = time.monotonic() - start
    assert result.ok, "hitting the cap is success-with-truncation, not a timeout"
    assert elapsed < 1.0, "the cap must fire before the timeout"
    assert "truncated" in result.content


@posix_only
async def test_cancellation_kills_the_command(tmp_path):
    """A barge-in / stop / delete cancels the generation; the running command
    must not outlive it (HANDOFF gotcha 18 class)."""
    sentinel = tmp_path / "child_survived"
    task = asyncio.ensure_future(_run(f"(sleep 1.5; touch '{sentinel}') & sleep 5"))
    await asyncio.sleep(0.4)  # let it spawn
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.0)
    assert not sentinel.exists()


# -- the execution context (owner decisions) --------------------------------


async def test_the_jarvis_namespace_is_scrubbed_from_the_child(monkeypatch):
    """The WebSocket auth token has no business in a subprocess; the user's own
    PATH does, or the shell can't find their tools."""
    monkeypatch.setenv("JARVIS_WS_TOKEN", "supersecret-token")
    result = await _run("echo tok=[$JARVIS_WS_TOKEN] path=[$PATH]")
    assert result.ok
    assert "supersecret-token" not in result.content
    assert "tok=[]" in result.content
    assert "path=[]" not in result.content  # the real PATH survived


async def test_the_shell_starts_in_the_home_directory():
    result = await _run("pwd")
    assert result.ok
    assert Path(result.content.strip()).resolve() == Path.home().resolve()


# -- registration & the gate ------------------------------------------------


async def test_the_body_never_runs_when_dangerous_tools_are_disabled(tmp_path):
    """`allow_dangerous = false` refuses without asking (§1). Proven end to end:
    the confirmer is never consulted and the command never executes."""
    sentinel = tmp_path / "ran"

    class NeverAsked:
        def __init__(self):
            self.asked = False

        async def request(self, *a, **k):
            self.asked = True
            return Decision.allow()

    confirmer = NeverAsked()
    gate = PermissionGate(confirmer, allow_dangerous=lambda: False)
    result = await _run(f"touch '{sentinel}'", gate=gate)
    assert (result.ok, result.code) == (False, "TOOL_DANGEROUS_DISABLED")
    assert not confirmer.asked
    assert not sentinel.exists()


def test_run_command_is_registered_as_dangerous():
    assert _registry().get("run_command").risk == DANGEROUS


def test_run_command_ships_with_and_without_a_sandbox(tmp_path):
    """It is not a file tool, so it does not depend on a sandbox the way the
    file tools do — it ships either way."""
    from jarvis_backend.security.permissions import SafeOnlyGate
    from jarvis_backend.security.sandbox import Sandbox
    from jarvis_backend.tools import default_registry

    without = default_registry(SafeOnlyGate())
    assert without.get("run_command") is not None
    with_sandbox = default_registry(SafeOnlyGate(), Sandbox([tmp_path]))
    assert with_sandbox.get("run_command") is not None
