"""The `jarvis` CLI. Phase 1: doctor, version. Phase 5 adds `install`."""

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

    sub.add_parser("version", help="print version")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _doctor(json_output=args.json)
    if args.command == "version":
        print(__version__)
        return 0
    parser.print_help()
    return 2


def _doctor(json_output: bool) -> int:
    from .doctor.checks import FAIL, format_checks, run_checks

    checks = run_checks()
    if json_output:
        print(json.dumps([dataclasses.asdict(c) for c in checks], indent=2))
    else:
        print(f"jarvis doctor (v{__version__})")
        print(format_checks(checks, color=sys.stdout.isatty()))
    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
