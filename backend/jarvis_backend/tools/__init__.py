"""The default tool set.

`get_datetime` reads the clock and always has. M4.3 adds the first tools with
real side effects — read/list/write/delete — and they arrive **with** their
security layer, never before it: the sandbox is a required argument of
`filesystem.build`, so a file tool that skips path resolution is not something
this module can express.

The layering, milestone by milestone: M4.1 built the wire, M4.2 built the gate
(`default_registry` takes one, no default — a Registry without a security layer
must stay impossible to construct), M4.3 hands that gate something worth
guarding, M4.4 adds `run_command` — the sharpest tool in the project — and M4.5
adds `web_fetch`, closing Phase 4's tool list (files, shell, web_fetch).

Neither `run_command` nor `web_fetch` takes a sandbox — they are not file tools.
A shell escapes the filesystem sandbox by design (`cat ~/.ssh/id_rsa` ignores
every root), so `run_command` registers unconditionally, is `dangerous` (same
class as `delete_file`), and is guarded only by the unconditional confirmation +
`[tools] allow_dangerous`. `web_fetch` registers unconditionally too, is `ask`
(every fetch confirms, showing the URL — the exfiltration defense), and enforces
the SSRF guard (security/ssrf.py) internally. See docs/security-model.md §1/§4,
tools/shell.py, and tools/web.py.

**The dev tool.** Under `JARVIS_DEV_TOOLS=1` an `ask`-risk `echo` is registered.
It exists because the permission engine ships a milestone before the first tool
that needs it, and a confirmation dialog that has never been seen in the real
WKWebView is not a verified dialog. It grants no capability whatsoever — its
body returns the string it was handed — but it traverses the full gate, so the
dialog, the timeout, the session grant and the spoken voice prompt can all be
exercised end to end. The packaged app never sets the variable; see
docs/security-model.md §1.
"""

from __future__ import annotations

import os
from datetime import datetime

from ..security.permissions import ASK, SAFE, Gate
from ..security.sandbox import Sandbox
from . import filesystem, shell, web
from .registry import Registry

DEV_TOOLS_ENV = "JARVIS_DEV_TOOLS"


def get_datetime() -> str:
    """Get the user's current local date and time."""
    # Local time, not UTC: the model is answering a human sitting in a
    # timezone, and %Z/%z come from the OS so this stays correct anywhere.
    return datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")


def echo(text: str) -> str:
    """Repeat text back. Dev-only; see the module docstring."""
    return text


def dev_tools_enabled() -> bool:
    return os.environ.get(DEV_TOOLS_ENV) == "1"


def default_registry(gate: Gate, sandbox: Sandbox | None = None) -> Registry:
    """The tool set the agent loop is given.

    The gate is not optional and not defaulted: "run a tool without consulting
    the security layer" must remain an inexpressible operation, not a
    discouraged one.

    `sandbox=None` ships no file tools at all — the honest state for a backend
    built without one. Note that a Sandbox with **no roots** is a different
    thing: the tools exist and refuse every path, which is what a user who
    configured `roots = []` asked for.
    """
    registry = Registry(gate)
    if sandbox is not None:
        filesystem.register(registry, sandbox)
    # Not gated on the sandbox: the shell is not a file tool and escapes it by
    # design. Governed by [tools] allow_dangerous (dangerous risk) and the
    # per-call confirmation, never by the filesystem roots.
    shell.register(registry)
    # web_fetch is `ask` (every fetch confirms, showing the URL — the exfiltration
    # defense) and enforces the SSRF guard internally. Also sandbox-independent.
    web.register(registry)
    registry.register(
        get_datetime,
        risk=SAFE,
        description=(
            "Get the current local date and time on the user's computer. "
            "Use this whenever the answer depends on what day or time it is."
        ),
    )
    if dev_tools_enabled():
        registry.register(
            echo,
            risk=ASK,
            description=(
                "Repeat a piece of text back verbatim. Use it when the user "
                "explicitly asks you to echo something."
            ),
            params={"text": "The exact text to repeat back"},
        )
    return registry
