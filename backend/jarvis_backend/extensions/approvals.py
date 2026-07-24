"""What the user approved, and what "the same extension" means.

docs/security-model.md §5. An approval is a record that a human looked at a
manifest and agreed to run **those exact bytes**. Two halves:

`tree_digest()` turns an extension folder into one hash. This is what makes the
approval mean something: without it, "approve timers-reminders" would approve
whatever that folder becomes afterwards, and an extension could be swapped out
from under its own approval. Editing any file — including `manifest.toml`, which
is how a declared risk level would be lowered after the fact — produces a
different digest and sends the extension back to pending.

`ApprovalStore` holds those records in `<data dir>/extensions.toml`. Its two
important properties:

- **It lives under the data directory**, which `main.py` passes to
  `Sandbox(excluded=...)`. No file tool can write it, so an extension cannot
  approve itself or another one — the self-escalation §2 exists to stop.
- **A file it cannot read approves nothing.** Junk, a truncated write, a
  hand-edit gone wrong: all read as an empty set. The opposite failure — an
  unreadable file meaning "allow everything" — is the classic this project
  refuses elsewhere (`roots = []`, a missing model catalog).

**Residual, stated rather than hidden:** `__pycache__` is excluded from the
digest, because it is written by importing the very files that *are* hashed and
including it would void every approval on the next start. An attacker who can
write into an already-approved extension's folder could therefore plant a
hash-based unchecked `.pyc` that the digest does not see. That attacker can
already edit `extension.py` — which the digest *does* see — so this narrows to a
detection gap, not a new capability, and it sits well inside §5's stated reality
that an approved extension runs arbitrary code as the user.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .manifest import Manifest

# Excluded from the identity of an extension. Both are derived or metadata, and
# neither is ever imported: `__pycache__` is generated from files already
# hashed, `.git` is what `jarvis install` leaves behind (M5.3) and changes on
# every fetch. Everything else in the folder counts.
DIGEST_EXCLUDED_DIRS = frozenset({"__pycache__", ".git"})


class ApprovalError(Exception):
    """Raised with a machine-readable code; the frontend translates codes."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class Approval:
    name: str
    version: str
    digest: str
    source: str = ""  # where it came from, when installed by URL (M5.3)
    commit: str = ""  # the pinned commit SHA — §5: no auto-update
    approved_at: str = ""


def tree_digest(directory: Path) -> str:
    """SHA-256 over every file in an extension folder: its exact identity.

    Both the path and the contents are hashed, and each file's bytes are
    **length-framed**, so content cannot slide between two files without
    changing the result — hashing a plain concatenation would let `a.py`+`b.py`
    collide with a single file holding both.

    A symlink anywhere in the tree refuses the whole extension
    (`EXTENSION_UNSAFE_TREE`). Skipping symlinks would be a digest bypass: a
    symlinked `extension.py` gets imported while its real bytes live outside the
    folder, free to change after approval. There is no legitimate use for one in
    a folder of source files, so refusing costs nothing.
    """
    h = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda p: p.relative_to(directory).as_posix()):
        if any(part in DIGEST_EXCLUDED_DIRS for part in path.relative_to(directory).parts):
            continue
        if path.is_symlink():
            raise ApprovalError("EXTENSION_UNSAFE_TREE", str(path))
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix().encode("utf-8")
        data = path.read_bytes()
        h.update(len(rel).to_bytes(8, "big"))
        h.update(rel)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


class ApprovalStore:
    """The approved-extensions record, read and written as TOML."""

    def __init__(self, path: Path):
        self._path = path

    def _read(self) -> dict[str, Approval]:
        """Every failure reads as "nothing approved". See the module docstring."""
        try:
            raw = tomllib.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            return {}
        approved = raw.get("approved")
        if not isinstance(approved, dict):
            return {}
        out: dict[str, Approval] = {}
        for name, entry in approved.items():
            if not isinstance(entry, dict):
                continue
            digest = entry.get("digest")
            # An entry with no digest cannot identify any bytes. Dropping it is
            # the only safe reading — the alternative is an approval that either
            # matches nothing or, compared loosely, matches anything.
            if not isinstance(digest, str) or not digest:
                continue
            out[name] = Approval(
                name=name,
                version=str(entry.get("version", "")),
                digest=digest,
                source=str(entry.get("source", "")),
                commit=str(entry.get("commit", "")),
                approved_at=str(entry.get("approved_at", "")),
            )
        return out

    def get(self, name: str) -> Approval | None:
        return self._read().get(name)

    def all(self) -> tuple[Approval, ...]:
        return tuple(self._read().values())

    def approve(
        self, manifest: Manifest, digest: str, *, source: str = "", commit: str = ""
    ) -> Approval:
        """Record that these exact bytes were approved. Replaces any prior entry."""
        approval = Approval(
            name=manifest.name,
            version=manifest.version,
            digest=digest,
            source=source,
            commit=commit,
            approved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        records = self._read()
        records[approval.name] = approval
        self._write(records)
        return approval

    def revoke(self, name: str) -> bool:
        """Forget an approval. False if there was nothing to forget."""
        records = self._read()
        if name not in records:
            return False
        del records[name]
        self._write(records)
        return True

    def _write(self, records: dict[str, Approval]) -> None:
        import tomli_w

        payload = {
            "approved": {
                a.name: {
                    "version": a.version,
                    "digest": a.digest,
                    "source": a.source,
                    "commit": a.commit,
                    "approved_at": a.approved_at,
                }
                for a in records.values()
            }
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Same tmp-then-replace as config.save_wake_enabled: a crash mid-write
        # must not leave a half-parsed file, which would read as "nothing
        # approved" and silently disable every extension the user had approved.
        tmp = self._path.with_suffix(".toml.tmp")
        tmp.write_bytes(tomli_w.dumps(payload).encode("utf-8"))
        tmp.replace(self._path)
