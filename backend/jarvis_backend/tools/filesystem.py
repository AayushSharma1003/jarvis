"""File tools. Every one of them goes through the sandbox first.

The tools are closures over a `Sandbox` rather than free functions taking a
sandbox argument, because an argument is something the model fills in. A tool
whose signature the model controls cannot be trusted to be given the right
sandbox — so the sandbox is bound at registration and never appears in the JSON
schema the model sees.

Risk levels, and why each is what it is (docs/security-model.md §1):

  read_file    safe       reads nothing back into the world; changes nothing.
                          But it TAINTS: the content is untrusted, and from
                          here on side-effectful calls confirm with provenance.
  list_dir     safe       structure, not content. See the note on its docstring.
  write_file   ask        creates or overwrites a file the user can see.
  delete_file  dangerous  destroys data, and there is no undo. Per-call
                          confirmation, never session-grantable, and switchable
                          off entirely via [tools] allow_dangerous.
"""

from __future__ import annotations

from pathlib import Path

from ..security.permissions import ASK, DANGEROUS, SAFE
from ..security.sandbox import Sandbox, SandboxError
from .registry import Registry, ToolOutput

# A read big enough to be worth truncating is big enough to be worth refusing.
# MAX_RESULT_CHARS already trims what reaches the model, but that happens after
# the whole file is in memory — this is the guard that keeps a 2GB file from
# being loaded at all on an 8GB machine.
MAX_READ_BYTES = 256 * 1024

# Directory listings are structure, and a directory with 50k entries is a
# context-window denial of service the same way a huge file is.
#
# Sized to fit *inside* MAX_RESULT_CHARS: at 500 the listing overflowed the
# registry's truncation, which then cut off the "… and N more" line — so the
# model was told nothing about what it wasn't shown. The inner cap has to bind
# first for its own message to survive. Long filenames can still overflow, and
# the registry's truncation remains the backstop for that.
MAX_ENTRIES = 200


def _describe(entry: Path) -> str:
    try:
        if entry.is_dir():
            return f"{entry.name}/"
        return f"{entry.name} ({entry.stat().st_size} bytes)"
    except OSError:
        # A broken symlink or a file that vanished mid-listing. Naming it is
        # more useful than dropping it silently.
        return f"{entry.name} (unreadable)"


def build(sandbox: Sandbox) -> list[tuple]:
    """Return (fn, kwargs) registration specs for the file tools."""

    def read_file(path: str) -> ToolOutput:
        """Read a text file from the user's computer."""
        resolved = sandbox.resolve(path)
        if not resolved.exists():
            raise SandboxError("FILE_NOT_FOUND", str(resolved))
        if resolved.is_dir():
            raise SandboxError("IS_A_DIRECTORY", str(resolved))
        if resolved.stat().st_size > MAX_READ_BYTES:
            raise SandboxError("FILE_TOO_LARGE", str(resolved))
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise SandboxError("READ_FAILED", str(e)) from e
        # The taint: everything downstream treats this conversation as holding
        # content the user did not write. See security/taint.py.
        return ToolOutput(content, taint_source=str(resolved))

    def list_dir(path: str) -> str:
        """List the files and folders in a directory."""
        resolved = sandbox.resolve(path)
        if not resolved.exists():
            raise SandboxError("FILE_NOT_FOUND", str(resolved))
        if not resolved.is_dir():
            raise SandboxError("NOT_A_DIRECTORY", str(resolved))
        try:
            entries = sorted(resolved.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            raise SandboxError("READ_FAILED", str(e)) from e
        # Deliberately NOT tainted. Filenames are attacker-controllable too — a
        # file called "ignore previous instructions.txt" is a real thing — but
        # tainting on every listing would taint nearly every session, and a
        # taint that is always on is a taint nobody reads. Names are short,
        # structural, and rendered as a list; content is the vector that
        # matters. Revisit if a listing is ever fed somewhere consequential.
        shown = [_describe(e) for e in entries[:MAX_ENTRIES]]
        if len(entries) > MAX_ENTRIES:
            shown.append(f"… and {len(entries) - MAX_ENTRIES} more")
        return "\n".join(shown) if shown else "(empty directory)"

    def write_file(path: str, content: str) -> str:
        """Write text to a file, creating it or replacing what is there."""
        resolved = sandbox.resolve(path)
        if resolved.is_dir():
            raise SandboxError("IS_A_DIRECTORY", str(resolved))
        # The parent is re-resolved rather than assumed: `resolved.parent` is
        # already inside the sandbox by construction, but mkdir would follow a
        # symlink planted at any level, so the directory we are about to create
        # gets checked as its own path.
        parent = sandbox.resolve(str(resolved.parent))
        try:
            parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as e:
            raise SandboxError("WRITE_FAILED", str(e)) from e
        return f"Wrote {len(content.encode('utf-8'))} bytes to {resolved}"

    def delete_file(path: str) -> str:
        """Delete a file from the user's computer. This cannot be undone."""
        resolved = sandbox.resolve(path)
        if not resolved.exists():
            raise SandboxError("FILE_NOT_FOUND", str(resolved))
        if resolved.is_dir():
            # Deleting a directory means deleting everything under it, and one
            # confirmation cannot honestly represent an unbounded set of files.
            # Refused outright in v1 rather than approximated.
            raise SandboxError("IS_A_DIRECTORY", str(resolved))
        try:
            resolved.unlink()
        except OSError as e:
            raise SandboxError("DELETE_FAILED", str(e)) from e
        return f"Deleted {resolved}"

    return [
        (
            read_file,
            {
                "risk": SAFE,
                "description": (
                    "Read the contents of a text file on the user's computer. "
                    "Use the absolute path."
                ),
                "params": {"path": "Absolute path to the file to read"},
            },
        ),
        (
            list_dir,
            {
                "risk": SAFE,
                "description": (
                    "List the files and folders inside a directory on the user's "
                    "computer. Use the absolute path."
                ),
                "params": {"path": "Absolute path to the directory to list"},
            },
        ),
        (
            write_file,
            {
                "risk": ASK,
                "description": (
                    "Write text to a file on the user's computer, creating it or "
                    "replacing its contents. Use the absolute path."
                ),
                "params": {
                    "path": "Absolute path to the file to write",
                    "content": "The full text to write into the file",
                },
            },
        ),
        (
            delete_file,
            {
                "risk": DANGEROUS,
                "description": (
                    "Permanently delete a file from the user's computer. "
                    "Cannot be undone. Use the absolute path."
                ),
                "params": {"path": "Absolute path to the file to delete"},
            },
        ),
    ]


def register(registry: Registry, sandbox: Sandbox) -> None:
    for fn, kwargs in build(sandbox):
        registry.register(fn, **kwargs)
