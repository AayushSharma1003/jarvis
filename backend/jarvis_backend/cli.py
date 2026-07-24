"""The `jarvis` CLI. Phase 1: doctor, version. M5.1 adds `extensions`.

`jarvis extensions approve` is the **approval dialog** for anyone without the
app window open, and it obeys the same rule the GUI one will: the declared
permissions are printed and a human answers before anything is recorded.
`--yes` exists for scripting and tests; it skips the question, not the printing.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS assistant CLI")
    sub = parser.add_subparsers(dest="command")

    doctor_p = sub.add_parser("doctor", help="diagnose the local setup")
    doctor_p.add_argument("--json", action="store_true", help="machine-readable output")
    doctor_p.add_argument(
        "--latency", action="store_true", help="measure the voice pipeline (needs models + Ollama)"
    )

    sub.add_parser("version", help="print version")

    ext_p = sub.add_parser("extensions", help="list, approve and revoke extensions")
    ext_sub = ext_p.add_subparsers(dest="ext_command")
    ext_sub.add_parser("list", help="show installed extensions and their status")
    approve_p = ext_sub.add_parser("approve", help="approve an extension's current contents")
    approve_p.add_argument("name")
    approve_p.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt (scripting)"
    )
    revoke_p = ext_sub.add_parser("revoke", help="withdraw an extension's approval")
    revoke_p.add_argument("name")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        if args.latency:
            return _latency()
        return _doctor(json_output=args.json)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "extensions":
        if args.ext_command == "list":
            return _extensions_list()
        if args.ext_command == "approve":
            return _extensions_approve(args.name, assume_yes=args.yes)
        if args.ext_command == "revoke":
            return _extensions_revoke(args.name)
        ext_p.print_help()
        return 2
    parser.print_help()
    return 2


def _latency() -> int:
    from .doctor.latency import format_latency, run_latency

    print(f"jarvis doctor --latency (v{__version__}) — measuring, ~30s…")
    try:
        stages, first_audio, status = run_latency()
    except Exception as e:  # noqa: BLE001 - doctor reports, never crashes
        print(f" FAIL: {e}")
        return 1
    print(format_latency(stages, first_audio, status))
    return 1 if status == "fail" else 0


def _doctor(json_output: bool) -> int:
    from .doctor.checks import FAIL, format_checks, run_checks

    checks = run_checks()
    if json_output:
        print(json.dumps([dataclasses.asdict(c) for c in checks], indent=2))
    else:
        print(f"jarvis doctor (v{__version__})")
        print(format_checks(checks, color=sys.stdout.isatty()))
    return 1 if any(c.status == FAIL for c in checks) else 0


# -- extensions (M5.1) ------------------------------------------------------


def _survey():
    """Everything in the extensions directory, with its approval status."""
    from .config import approvals_path, extensions_dir
    from .extensions.approvals import ApprovalStore
    from .extensions.loader import discover

    store = ApprovalStore(approvals_path())
    return store, discover(extensions_dir(), store)


def _extensions_list() -> int:
    from .config import extensions_dir

    _, found = _survey()
    if not found:
        print(f"No extensions installed in {extensions_dir()}")
        return 0
    width = max(len(d.name) for d in found)
    for d in found:
        note = f"  ({d.code})" if d.code else ""
        version = d.manifest.version if d.manifest else "?"
        print(f"{d.name:<{width}}  {d.status:<20}  {version}{note}")
    return 0


def _extensions_approve(name: str, *, assume_yes: bool) -> int:
    from .extensions.loader import _effective_risk, _floor

    store, found = _survey()
    entry = next((d for d in found if d.name == name), None)
    if entry is None:
        print(f"No extension named {name!r} is installed.")
        return 1
    if entry.manifest is None:
        # invalid: there is nothing coherent to show, so nothing to consent to.
        print(f"{name} cannot be approved: {entry.code}")
        return 1

    manifest = entry.manifest
    floor = _floor(manifest)
    print(f"\n{manifest.name} {manifest.version}")
    if manifest.description:
        print(f"  {manifest.description}")
    os_access = ", ".join(manifest.os_permissions) or "none declared"
    print(f"  platforms:  {', '.join(manifest.platforms) or 'any'}")
    print(f"  OS access:  {os_access}")
    print(f"  network:    {'yes' if manifest.network else 'no'}")
    print("  tools it will add:")
    for decl in manifest.tools:
        # The EFFECTIVE level, not the declared one: under `network = true` a
        # tool declared `safe` is registered `ask`, and printing "safe" here
        # would tell the user the opposite of what happens.
        print(f"    {decl.name} ({_effective_risk(decl.risk, floor)})")
    # §5's honest half. Listing permissions without this would imply they are a
    # boundary; they are a declaration, and the code runs unrestricted.
    print(
        "\n  Approving runs this extension's code with the same access Jarvis has —\n"
        "  the permissions above are what it SAYS it needs, not a sandbox.\n"
        f"  Approval covers these exact files ({entry.digest[:12]}…); editing them asks again."
    )

    if not assume_yes:
        try:
            answer = input("\nApprove? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        # Anything that is not a clear yes is a no, including a bare Enter.
        if answer.strip().lower() not in ("y", "yes"):
            print("Not approved; nothing was recorded.")
            return 1

    store.approve(manifest, entry.digest)
    print(f"Approved {manifest.name} {manifest.version}.")
    return 0


def _extensions_revoke(name: str) -> int:
    store, _ = _survey()
    if not store.revoke(name):
        print(f"{name!r} was not approved.")
        return 1
    print(f"Revoked {name}. It will not load until approved again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
