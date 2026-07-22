"""The default tool set.

M4.1 ships exactly one tool, and it is deliberately the most boring one
imaginable: `get_datetime` reads the clock. No filesystem, no network, no
subprocess, nothing to sandbox and nothing to confirm.

That is the point. This milestone builds the *plumbing* — structured stream
events, the multi-round loop, tool spans in the message tree, the registry and
its gate — and a tool with genuinely zero attack surface proves the whole path
end to end without any of it depending on a permission engine that does not
exist yet (that is M4.2). The real tools (files, shell, web_fetch) arrive in
M4.3-M4.5, each with its security layer, never before it.

The registry is constructed with SafeOnlyGate, so even if somebody registers an
`ask` or `dangerous` tool here today, it cannot run.
"""

from __future__ import annotations

from datetime import datetime

from ..security.permissions import SAFE, SafeOnlyGate
from .registry import Registry


def get_datetime() -> str:
    """Get the user's current local date and time."""
    # Local time, not UTC: the model is answering a human sitting in a
    # timezone, and %Z/%z come from the OS so this stays correct anywhere.
    return datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")


def default_registry() -> Registry:
    """The tool set the agent loop is given.

    SafeOnlyGate is not a placeholder to be swapped for `None` — it is what
    makes it impossible to ship a side-effectful tool before M4.2's
    confirmation engine exists.
    """
    registry = Registry(SafeOnlyGate())
    registry.register(
        get_datetime,
        risk=SAFE,
        description=(
            "Get the current local date and time on the user's computer. "
            "Use this whenever the answer depends on what day or time it is."
        ),
    )
    return registry
