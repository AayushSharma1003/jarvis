"""The default tool set.

M4.2 still ships exactly one real tool, and it is deliberately the most boring
one imaginable: `get_datetime` reads the clock. No filesystem, no network, no
subprocess, nothing to sandbox. The real tools (files, shell, web_fetch) arrive
in M4.3-M4.5, each with its security layer, never before it.

What changed in M4.2 is the gate: `default_registry` now takes one, because the
confirmation engine that makes an `ask` tool honest finally exists. The gate is
still a required argument with no default — a Registry without a security layer
must stay impossible to construct.

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


def default_registry(gate: Gate) -> Registry:
    """The tool set the agent loop is given.

    The gate is not optional and not defaulted: "run a tool without consulting
    the security layer" must remain an inexpressible operation, not a
    discouraged one.
    """
    registry = Registry(gate)
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
