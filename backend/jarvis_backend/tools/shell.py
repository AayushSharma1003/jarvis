"""run_command: run a shell command, verbatim, after an unconditional confirm.

docs/security-model.md §1 is normative and unusually strict here: run_command
**always confirms, full command text shown, no exceptions**, and there is no
command classifier and no denylist — both are bypass generators. This module
therefore does not inspect the command at all. It runs the exact string the user
approved and reports what came back.

What the shell escapes, said plainly (also §"Known limitations"): the filesystem
sandbox is a policy check *inside the file tools*, not around the process, so
`cat ~/.ssh/id_rsa` ignores every root. The shell's protection is the
confirmation, not the sandbox. cwd (home) and the JARVIS_*-scrubbed environment
are usability and hygiene — a shell you cannot point at your tools will not get
used, and the app's own auth token has no business in a subprocess — **not** a
containment boundary. `cd /anywhere` works, by design.

The subprocess lifecycle is where the care goes:

- **Bounded, incremental read.** `communicate()` buffers the child's entire
  output before returning, so `yes` or `cat /dev/urandom` would balloon RAM on an
  8GB machine long before any timeout fired. Output is read in chunks against a
  byte budget and the producer is killed the moment the budget is hit.
- **A real timeout.** One command holds the single generation slot, so a
  non-exiting one must be killed. run_command is a quick-command tool, not a
  build runner: there is no output-streaming protocol, and a minutes-long command
  would block the whole app with no feedback.
- **The whole process group dies.** A shell backgrounds children; killing only
  `/bin/sh` reparents them to init. A new session at spawn + killpg at teardown
  means a barge-in or a timeout leaves nothing running.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

from ..security.permissions import DANGEROUS
from .registry import Registry

# One command holds the single generation slot; 30s fits git/ls/grep/short
# scripts without turning the tool into a build runner. Overridable for headless
# verification via JARVIS_SHELL_TIMEOUT_S — the packaged app never sets it, the
# same contract as the confirm broker's timeout.
SHELL_TIMEOUT_S = 30.0

# The memory / DoS guard, applied WHILE reading (see the module docstring). It
# sits above the registry's MAX_RESULT_CHARS (8000), which does the final,
# model-facing trim — so a normal command is never cut by this layer. When output
# does overflow, the registry's own "(truncated)" marker survives even though the
# note below is trimmed away with the rest (HANDOFF gotcha 15); the model still
# sees that it was truncated.
MAX_OUTPUT_BYTES = 64 * 1024

_READ_CHUNK = 4096


class ShellError(Exception):
    """Raised with a machine-readable code; the frontend translates codes.

    Mirrors security/sandbox.py's SandboxError: the registry reads `.code` and
    turns it into a failed ToolResult, so a command that could not run is a
    result the model can react to, never a crash that ends the exchange.
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def shell_timeout_s() -> float:
    """The timeout, honouring JARVIS_SHELL_TIMEOUT_S if it parses to > 0."""
    raw = os.environ.get("JARVIS_SHELL_TIMEOUT_S")
    if not raw:
        return SHELL_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return SHELL_TIMEOUT_S
    return value if value > 0 else SHELL_TIMEOUT_S


def _child_env() -> dict[str, str]:
    """The parent environment minus Jarvis's own namespace.

    The user's PATH/HOME/etc. are exactly what make the shell useful on their
    machine, so the env is inherited — but every JARVIS_* var is dropped, above
    all JARVIS_WS_TOKEN: the WebSocket auth secret must never reach a subprocess.
    Stripping the whole prefix rather than one name future-proofs the token class
    and keeps the app's private control vars out of the child.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}


def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill the command and everything it spawned. Idempotent, best-effort."""
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            # The whole group, not just the shell — see the module docstring.
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Already gone, or we cannot signal it — nothing more to do.
        pass


async def run_command(command: str) -> str:
    """Run a shell command on the user's computer and return its output."""
    if not command or not command.strip():
        raise ShellError("COMMAND_REQUIRED")

    kwargs: dict = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,  # merged, so errors read in order
        "stdin": asyncio.subprocess.DEVNULL,  # a stdin-reading command gets EOF
        "cwd": str(Path.home()),
        "env": _child_env(),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # own process group, so killpg works

    try:
        proc = await asyncio.create_subprocess_shell(command, **kwargs)
    except OSError as e:
        raise ShellError("COMMAND_FAILED", str(e)) from e

    buffer = bytearray()
    truncated = False

    async def _read() -> None:
        nonlocal truncated
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(_READ_CHUNK)
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) >= MAX_OUTPUT_BYTES:
                truncated = True
                break

    try:
        await asyncio.wait_for(_read(), shell_timeout_s())
    except TimeoutError:
        # Translate to the tool's code. The kill itself is the finally's job.
        raise ShellError("COMMAND_TIMEOUT") from None
    finally:
        # The single termination path, covering every way the read can end with
        # the child still alive: the output cap breaking out of the loop, the
        # timeout above, and a CancelledError from a barge-in / stop / delete
        # (which propagates through here, killing the command, before reaching
        # the caller). A clean EOF leaves returncode set, so this is a no-op.
        if proc.returncode is None:
            _terminate(proc)
            await proc.wait()

    output = buffer.decode("utf-8", errors="replace").rstrip("\n")
    if truncated:
        # Hitting the cap is success-with-truncation, not a failure — and the
        # returncode is our own kill signal, meaningless, so it is not reported.
        return f"{output}\n… (output truncated)"
    if proc.returncode:
        # Non-zero exit is a result the model must see, alongside the output.
        tail = f"[exit code {proc.returncode}]"
        return f"{output}\n{tail}" if output else tail
    return output


def register(registry: Registry) -> None:
    registry.register(
        run_command,
        risk=DANGEROUS,
        description=(
            "Run a shell command on the user's computer and return its output. "
            "Runs verbatim in a shell, so pipes and redirects work. Every call "
            "asks the user to confirm the exact command first."
        ),
        params={"command": "The exact shell command to run"},
    )
