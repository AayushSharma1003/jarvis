"""Tool risk levels and the gate every tool call must pass through.

docs/security-model.md §1 is normative:

  safe       runs freely (list_dir, send_notification)
  ask        native confirmation showing the exact action and arguments
             (write_file, get_clipboard — clipboards contain passwords)
  dangerous  per-call confirmation, globally disableable (delete, run_command)

**M4.1 ships only the SafeOnlyGate.** The confirmation machinery — the async
broker, correlation ids, the dialog, timeouts, "no UI connected means deny" —
is M4.2, and until it exists there is no honest way to run an `ask` or
`dangerous` tool. So this gate refuses them. That is the project's rule made
literal: shipping a half-built permission engine is worse than shipping none,
so the incomplete half simply cannot be reached.

The gate is a constructor argument of Registry, not an optional check inside
it, so "call a tool without consulting the security layer" is not an
expressible operation rather than a discouraged one (docs/architecture.md:
"the registry enforces this structurally; tools cannot opt out").
"""

from __future__ import annotations

from dataclasses import dataclass
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


class Gate(Protocol):
    """Consulted before every tool invocation. M4.2 replaces the M4.1
    implementation with one that can actually ask the user."""

    async def check(
        self, name: str, risk: RiskLevel, arguments: dict[str, Any]
    ) -> Decision: ...


class SafeOnlyGate:
    """Permits `safe` tools; refuses everything that would need a confirmation.

    Deliberately not configurable. A flag to loosen it would be the exact hole
    this class exists to keep shut until M4.2 lands the real engine.
    """

    async def check(
        self, name: str, risk: RiskLevel, arguments: dict[str, Any]
    ) -> Decision:
        if risk == SAFE:
            return Decision.allow()
        return Decision.deny("TOOL_CONFIRMATION_UNAVAILABLE")
