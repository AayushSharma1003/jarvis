"""Finding extensions, and running only the ones a human approved.

docs/security-model.md §5. Two phases, and keeping them apart is the whole
design:

    discover()      reads manifests and hashes folders. Imports nothing.
    load_approved() imports and registers, and only what discover() blessed.

**Importing is executing.** An extension's module body runs the moment it is
imported, so any design that inspects an extension by importing it has already
run the code it was about to ask permission for. `discover()` therefore works
entirely from `manifest.toml` (data — see manifest.py) plus a hash of the bytes
on disk, which is enough to build an approval dialog. Only `load_approved()`
touches Python, and only for extensions whose current digest matches a recorded
approval. Pinned by test_an_unapproved_extension_is_never_imported, which fails
if an unapproved module body ever runs.

What this module enforces once an extension *is* approved:

- **The manifest is an allowlist.** Only functions named in `[[tools]]` are
  registered. An importable helper is not a tool, and neither is a function
  added after the user read the manifest.
- **Risk levels are floors.** §5: the core may raise a declared level and never
  lower it. `network = true` raises the floor to `ask`, which is the only thing
  the (otherwise advisory) permissions block buys that can actually be enforced.
- **No name hijacking.** A tool name already in the registry — a core tool, or
  one an earlier extension claimed — is refused, so `read_file` cannot be
  redefined out from under the sandbox.
- **Never read-only.** Every extension tool registers with `read_only=False`:
  the core cannot verify a third party's claim to change nothing, so §3's taint
  escalation applies to all of them (security/permissions.py).
- **A bad extension is a result, not a crash.** An import that raises would
  otherwise take the sidecar down at startup, which the user sees only as
  "backend didn't start in time".

**What it does not enforce, restated because it is the thing most likely to be
misread:** an approved extension runs arbitrary Python in this process. The
`[permissions]` block is a declaration shown at approval, not a capability
boundary. `sys.path` is deliberately left alone — an extension shipping
`json.py` would shadow the stdlib for everything — so v1 supports a single
`extension.py` per extension and nothing multi-file.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..security.permissions import ASK, RISK_ORDER, SAFE, RiskLevel
from ..tools.registry import Registry
from .approvals import ApprovalError, ApprovalStore, tree_digest
from .manifest import Manifest, ManifestError
from .manifest import load as load_manifest

log = logging.getLogger(__name__)

CODE_FILENAME = "extension.py"

Status = Literal["approved", "pending", "changed", "unsupported_platform", "invalid"]


@dataclass(frozen=True)
class Discovered:
    """One folder in the extensions directory, and whether it may run."""

    path: Path
    name: str
    status: Status
    manifest: Manifest | None = None
    digest: str = ""
    code: str = ""  # why it is invalid; empty otherwise


@dataclass(frozen=True)
class LoadResult:
    """What happened when an approved extension was imported."""

    name: str
    ok: bool
    tools: tuple[str, ...] = field(default_factory=tuple)
    code: str = ""
    detail: str = ""


def _effective_risk(declared: RiskLevel, floor: RiskLevel) -> RiskLevel:
    """§5: the core raises a declared level, never lowers it."""
    return max(declared, floor, key=RISK_ORDER.index)


def _floor(manifest: Manifest) -> RiskLevel:
    """The lowest risk any tool of this extension may be registered at.

    `network = true` ⇒ `ask`. An extension that reaches the network can carry
    data out, and an unconfirmed tool is the exfiltration path — so the one
    enforceable consequence we can attach to that declaration, we attach. It
    does nothing about an extension that lies, and it is not meant to: it makes
    the honest declaration also the enforced one, instead of leaving `network`
    as a field with no behaviour behind it at all.
    """
    return ASK if manifest.network else SAFE


def discover(
    root: Path, approvals: ApprovalStore, platform: str = sys.platform
) -> list[Discovered]:
    """Survey the extensions directory. **Reads data; imports nothing.**"""
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        # No extensions directory yet is the ordinary first-run state.
        return []

    found: list[Discovered] = []
    for path in entries:
        found.append(_inspect(path, approvals, platform))
    return found


def _inspect(path: Path, approvals: ApprovalStore, platform: str) -> Discovered:
    try:
        manifest = load_manifest(path)
    except ManifestError as e:
        return Discovered(path=path, name=path.name, status="invalid", code=e.code)

    if manifest.name != path.name:
        # One name, one folder. The approval record is keyed on the manifest
        # name, so two folders claiming the same name would leave it ambiguous
        # which bytes were blessed.
        return Discovered(
            path=path, name=path.name, status="invalid", code="EXTENSION_NAME_MISMATCH"
        )

    try:
        digest = tree_digest(path)
    except ApprovalError as e:
        return Discovered(path=path, name=manifest.name, status="invalid", code=e.code)

    if not manifest.supports(platform):
        # Reported rather than dropped, so a platform typo is visible instead of
        # looking like the extension was never installed.
        return Discovered(
            path=path,
            name=manifest.name,
            status="unsupported_platform",
            manifest=manifest,
            digest=digest,
        )

    approval = approvals.get(manifest.name)
    if approval is None:
        status: Status = "pending"
    elif approval.digest != digest:
        # Approved once, edited since. Back to unapproved: the approval was for
        # bytes, not for a name.
        status = "changed"
    else:
        status = "approved"
    return Discovered(
        path=path, name=manifest.name, status=status, manifest=manifest, digest=digest
    )


def load_approved(registry: Registry, discovered: Iterable[Discovered]) -> list[LoadResult]:
    """Import every approved extension and register its declared tools.

    Never raises: one broken extension must not stop the others, and must never
    stop the sidecar from starting.
    """
    results: list[LoadResult] = []
    for entry in discovered:
        if entry.status != "approved" or entry.manifest is None:
            continue
        results.append(_load_one(registry, entry, entry.manifest))
    return results


def _load_one(registry: Registry, entry: Discovered, manifest: Manifest) -> LoadResult:
    code_path = entry.path / CODE_FILENAME
    if not code_path.is_file():
        return LoadResult(manifest.name, False, code="EXTENSION_CODE_MISSING")

    try:
        module = _import(code_path, manifest.name)
    except Exception as e:  # noqa: BLE001 - a broken extension is a result, not a crash
        log.warning("extension %s failed to import: %s", manifest.name, e)
        return LoadResult(manifest.name, False, code="EXTENSION_IMPORT_FAILED", detail=str(e)[:200])

    floor = _floor(manifest)
    registered: list[str] = []
    problems: list[str] = []
    for decl in manifest.tools:
        fn = getattr(module, decl.name, None)
        if not callable(fn):
            # Declared but absent. Skipped rather than fatal — the extension's
            # other tools are still exactly what the user approved.
            problems.append(f"EXTENSION_TOOL_MISSING:{decl.name}")
            continue
        if registry.get(decl.name) is not None:
            # A core tool, or one an earlier extension already claimed. Refusing
            # is the only safe resolution: silently replacing would hand this
            # extension every call the model makes to that name.
            problems.append(f"EXTENSION_TOOL_CONFLICT:{decl.name}")
            continue
        registry.register(
            fn,
            name=decl.name,
            risk=_effective_risk(decl.risk, floor),
            # Never read-only: an extension's claim to change nothing is not
            # something the core can check, so it does not get the exemption
            # that would skip §3's taint escalation.
            read_only=False,
        )
        registered.append(decl.name)

    return LoadResult(
        manifest.name, True, tools=tuple(registered), detail="; ".join(problems)
    )


def _import(code_path: Path, name: str):
    """Execute an extension's module body. Only ever called for approved bytes.

    `sys.path` is untouched (see the module docstring), so this imports exactly
    one file. The module is registered in `sys.modules` under a namespaced key
    so that `dataclasses`, `pickle` and tracebacks resolve it, and removed again
    if the body raises — a half-initialised module left behind would be found by
    a later import and look loaded.
    """
    module_name = f"jarvis_ext_{name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, code_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {code_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module
