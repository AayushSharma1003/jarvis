"""The extension manifest: what an extension says it is, before it runs.

docs/security-model.md §5. This module is deliberately **pure data** — it parses
TOML and validates it, and it imports nothing, executes nothing, and touches no
filesystem beyond reading one file. That is the property the whole approval flow
rests on: the dialog a user approves is built entirely from a manifest, so
**consent happens before any of the extension's code has run**. A loader that
had to import an extension to describe it would be asking for permission after
the fact.

What the manifest can and cannot buy is stated plainly in §5 and repeated here
because it is the thing most likely to be misread by someone maintaining this:

  CAN    name the extension, its version, the platforms it claims to run on, and
         the risk level of each tool — which the core treats as a FLOOR it may
         raise and never lower (see loader.py).
  CANNOT enforce `[permissions]`. An approved extension is imported into this
         process and runs with its full privileges; `network = false` cannot
         stop `import socket`. Those fields are a declaration of intent, shown
         to the user at approval so they can judge the thing they will run.

Validation is fail-safe throughout: anything unrecognised is refused rather than
defaulted. An invented risk level like `risk = "trusted"` must never fall
through to `safe`, because falling through is exactly what an attacker would
write it for. The one exception is *unknown keys*, which are ignored so a
manifest written against a later version still loads.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..security.permissions import RISK_ORDER, RiskLevel

MANIFEST_FILENAME = "manifest.toml"

# The name keys the approval record and names a directory. No separators, no
# `..`, no leading dash, and lowercase so two entries cannot differ only by case
# on a case-insensitive filesystem (the class of bug gotcha 17 came from).
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Tool names reach the model as function names and key the registry, so they
# follow Python-identifier shape rather than the extension's dash-separated one.
TOOL_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


class ManifestError(Exception):
    """Raised with a machine-readable code; the frontend translates codes."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ToolDecl:
    """One `[[tools]]` entry: the tool's name and the risk floor it asks for."""

    name: str
    risk: RiskLevel


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    description: str = ""
    platforms: tuple[str, ...] = ()
    os_permissions: tuple[str, ...] = ()
    network: bool = False
    tools: tuple[ToolDecl, ...] = ()

    def supports(self, platform: str) -> bool:
        """Does this extension claim to run here? No declaration means anywhere.

        Platform strings are matched against `sys.platform` verbatim (`darwin`,
        `win32`, `linux`) and are NOT checked against a known list: refusing an
        unrecognised one would block a platform this release has not heard of.
        A typo therefore disables the extension rather than breaking it — which
        is safe, and visible, because discovery reports it as
        `unsupported_platform` rather than dropping it silently.
        """
        return not self.platforms or platform in self.platforms


def _require_str(table: dict[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError("MANIFEST_INVALID", f"[{where}] {key} must be a non-empty string")
    return value


def _str_list(table: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError("MANIFEST_INVALID", f"[{where}] {key} must be a list of strings")
    return tuple(value)


def _tool(entry: Any) -> ToolDecl:
    if not isinstance(entry, dict):
        raise ManifestError("MANIFEST_INVALID", "[[tools]] entries must be tables")
    name = entry.get("name")
    if not isinstance(name, str) or not TOOL_NAME_RE.match(name):
        raise ManifestError("MANIFEST_TOOL_NAME_INVALID", repr(name))
    risk = entry.get("risk")
    # Not defaulted. An omitted or invented level must refuse the manifest, not
    # resolve to the most permissive one — see the module docstring.
    if risk not in RISK_ORDER:
        raise ManifestError("MANIFEST_RISK_INVALID", f"{name}: {risk!r}")
    return ToolDecl(name, risk)


def parse(text: str) -> Manifest:
    """Validate a manifest's text. Raises ManifestError; never executes code."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError("MANIFEST_PARSE_ERROR", str(e)) from e

    extension = raw.get("extension")
    if not isinstance(extension, dict):
        raise ManifestError("MANIFEST_INVALID", "missing [extension] table")

    # Every way the name can be wrong — absent, not a string, empty, malformed —
    # reports the same code. Splitting "missing" from "malformed" would give the
    # author two messages that both mean "fix [extension] name".
    name = extension.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ManifestError("MANIFEST_NAME_INVALID", repr(name))
    version = _require_str(extension, "version", "extension")

    description = extension.get("description", "")
    if not isinstance(description, str):
        raise ManifestError("MANIFEST_INVALID", "[extension] description must be a string")

    permissions = raw.get("permissions", {})
    if not isinstance(permissions, dict):
        raise ManifestError("MANIFEST_INVALID", "[permissions] must be a table")
    network = permissions.get("network", False)
    if not isinstance(network, bool):
        raise ManifestError("MANIFEST_INVALID", "[permissions] network must be a boolean")

    tools_raw = raw.get("tools", [])
    if not isinstance(tools_raw, list):
        raise ManifestError("MANIFEST_INVALID", "[[tools]] must be a list of tables")
    tools = tuple(_tool(t) for t in tools_raw)
    if not tools:
        # Also what a `[[tool]]` typo looks like: valid TOML, zero tools, an
        # extension that loads and does nothing. Refusing says so out loud.
        raise ManifestError("MANIFEST_INVALID", "no tools declared")
    names = [t.name for t in tools]
    if len(set(names)) != len(names):
        # Two levels for one name resolves in the attacker's favour whichever
        # one we pick, so we pick neither.
        raise ManifestError("MANIFEST_INVALID", "duplicate tool name")

    return Manifest(
        name=name,
        version=version,
        description=description,
        platforms=_str_list(extension, "platforms", "extension"),
        os_permissions=_str_list(permissions, "os", "permissions"),
        network=network,
        tools=tools,
    )


def load(directory: Path) -> Manifest:
    """Read and validate `<directory>/manifest.toml`."""
    path = directory / MANIFEST_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestError("MANIFEST_MISSING", str(path)) from e
    return parse(text)
