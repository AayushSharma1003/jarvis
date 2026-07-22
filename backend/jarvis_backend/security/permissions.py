"""Tool risk levels and the gate every tool call must pass through.

docs/security-model.md §1 is normative:

  safe       runs freely (list_dir, send_notification)
  ask        confirmation showing the exact action and arguments
             (write_file, get_clipboard — clipboards contain passwords)
  dangerous  per-call confirmation, globally disableable (delete, run_command)

**M4.2 ships the real gate.** `PermissionGate` asks the user through a
ConfirmBroker (security/confirm.py); `SafeOnlyGate` stays as the fallback for
a backend built without one, and as the thing the tests use when they want a
gate with no moving parts. Neither is configurable into permissiveness: the
only way to run an `ask` tool is for a human to answer a dialog.

The gate is a constructor argument of Registry, not an optional check inside
it, so "call a tool without consulting the security layer" is not an
expressible operation rather than a discouraged one (docs/architecture.md:
"the registry enforces this structurally; tools cannot opt out").

Layering: this module owns the vocabulary (risk levels, Decision, ToolContext)
and both Protocols. security/confirm.py imports from here and never the other
way round, so `PermissionGate` takes a `Confirmer` structurally rather than
importing the broker it will be handed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

RiskLevel = Literal["safe", "ask", "dangerous"]

SAFE: RiskLevel = "safe"
ASK: RiskLevel = "ask"
DANGEROUS: RiskLevel = "dangerous"

# Ordered least → most privileged, so policy can compare rather than match.
RISK_ORDER: tuple[RiskLevel, ...] = (SAFE, ASK, DANGEROUS)


@dataclass(frozen=True)
class Decision:
    """The gate's answer. `code` is machine-readable for i18n, never prose."""

    allowed: bool
    code: str = ""

    @staticmethod
    def allow() -> Decision:
        return Decision(True)

    @staticmethod
    def deny(code: str) -> Decision:
        return Decision(False, code)


@dataclass(frozen=True)
class ToolContext:
    """What the gate knows about a call beyond its name and arguments.

    Built once per exchange by agent/loop.py and passed down through
    Registry.invoke, so the registry itself stays ignorant of what's inside.

    `denied` is a per-exchange memo of refusals. It lives exactly as long as the
    exchange that created it — no cleanup, no cross-turn leakage — and exists so
    a model that re-asks after a refusal cannot manufacture a second dialog.
    docs/security-model.md names confirmation fatigue as a real attack surface,
    and a model nagging until the user clicks Allow is precisely that attack
    with no attacker in it.

    M4.3 adds the taint fields here (tainted, taint_source); they belong in the
    context rather than the arguments because taint is a property of the
    *conversation*, not of the call.
    """

    conversation_id: str = ""
    voice: bool = False
    denied: set[str] = field(default_factory=set)


class Gate(Protocol):
    """Consulted before every tool invocation."""

    async def check(
        self,
        name: str,
        risk: RiskLevel,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> Decision: ...


class Confirmer(Protocol):
    """Whatever can actually ask a human. security/confirm.py's ConfirmBroker
    satisfies this structurally, which is what keeps the import one-way."""

    async def request(
        self,
        name: str,
        risk: RiskLevel,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> Decision: ...


class SafeOnlyGate:
    """Permits `safe` tools; refuses everything that would need a confirmation.

    Deliberately not configurable. This is what a backend gets when it was built
    without a confirmation broker — a headless run, a test, a future embedding
    of the agent loop with no UI attached. Refusing is the honest answer there:
    there is nobody to ask.
    """

    async def check(
        self,
        name: str,
        risk: RiskLevel,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> Decision:
        if risk == SAFE:
            return Decision.allow()
        return Decision.deny("TOOL_CONFIRMATION_UNAVAILABLE")


class PermissionGate:
    """The real engine: safe runs, everything else asks a human.

    `allow_dangerous` is a callable rather than a bool so the config can be
    re-read without rebuilding the registry — and so the answer is fetched at
    call time, when it matters, not at startup.
    """

    def __init__(
        self,
        confirmer: Confirmer,
        *,
        allow_dangerous: Callable[[], bool] = lambda: True,
    ) -> None:
        self._confirmer = confirmer
        self._allow_dangerous = allow_dangerous

    async def check(
        self,
        name: str,
        risk: RiskLevel,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> Decision:
        if risk == SAFE:
            return Decision.allow()
        # §1: dangerous tools are "globally disableable". Off means off — the
        # user is never asked, so there is no dialog to fatigue them into.
        if risk == DANGEROUS and not self._allow_dangerous():
            return Decision.deny("TOOL_DANGEROUS_DISABLED")
        try:
            return await self._confirmer.request(name, risk, arguments, context)
        except asyncio.CancelledError:
            # chat.stop / voice.stop / a delete racing the confirm. The whole
            # generation is going away; turning that into a deny would swallow
            # the cancellation and let the exchange carry on regardless.
            raise
        except Exception:  # noqa: BLE001 - a broken broker must never mean "allowed"
            return Decision.deny("TOOL_CONFIRM_FAILED")
