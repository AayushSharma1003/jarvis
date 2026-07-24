"""The filesystem sandbox: where file tools may operate, and nowhere else.

docs/security-model.md §2 is normative, and the load-bearing sentence is this:
enforcement happens on **`Path.resolve()`-ed (symlink-resolved) absolute paths**
— checking the path the model handed us is not enforcement, it is a spell-check.

Three attacks this closes, all of which look like ordinary paths:

  ~/Documents/../../.ssh/id_rsa     `..` traversal out of a root
  ~/Documents/notes -> /etc         a symlink INSIDE a root pointing outside
  <config dir>/config.toml          self-escalation: rewriting our own config,
                                    or dropping a file in the extensions dir,
                                    to widen the sandbox from inside it

`resolve()` handles the first two by construction: it collapses `..` and follows
every symlink, so what gets range-checked is the real destination rather than the
spelling. The third is a separate check, because the config and data directories
can legitimately sit *inside* a configured root (they do on Linux, where both
live under ~/.config and ~/.local) — so exclusion has to win even when the path
is otherwise in range.

**Empty roots means deny everything.** A user who explicitly configures no roots
has switched file access off, and that must not silently degrade into "allow
everything" the way an empty allowlist so often does.

Known limitation, documented rather than papered over (§"Known limitations"):
there is a TOCTOU window between resolving a path and opening it. Closing it
properly needs openat2/O_NOFOLLOW plumbing that does not exist cross-platform in
Python; v1 accepts the window and says so.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path


class SandboxError(Exception):
    """Raised with a machine-readable code; the frontend translates codes."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class Sandbox:
    """Resolves model-supplied paths, or refuses them."""

    def __init__(self, roots: list[Path] | None = None, excluded: list[Path] | None = None):
        # Resolved once, here: a root that is itself a symlink (~/Documents is
        # one on plenty of setups) must be compared in the same coordinate
        # system as the paths being checked, or every lookup misses.
        self._roots = tuple(self._normalize(r) for r in (roots or []))
        self._excluded = tuple(self._normalize(e) for e in (excluded or []))
        # Precomputed because every resolve() compares against all of them.
        self._excluded_folded = tuple(self._fold(e) for e in self._excluded)

    @staticmethod
    def _normalize(path: Path) -> Path:
        return Path(path).expanduser().resolve()

    @staticmethod
    def _fold(path: Path) -> tuple[str, ...]:
        """A path's components, flattened for comparisons where a match DENIES.

        `resolve()` settles symlinks and `..`, but it does not settle *spelling*,
        and two spellings of one file are the whole game here:

          Jarvis-Data/ vs jarvis-data/   macOS and Windows are case-insensitive
          José/        vs José/          APFS is normalisation-insensitive (NFC
                                         written, NFD readable — and vice versa)

        In both cases the OS opens the same file while `Path.parts` reports two
        different paths, so the exclusion missed and the root test then said yes.

        **The asymmetry is deliberate and load-bearing.** Fold only where a match
        means *deny*:

          exclusions (match ⇒ deny)   folded — maximally inclusive
          roots      (match ⇒ allow)  exact  — maximally strict

        Folding the roots test too would *widen* the sandbox on a case-sensitive
        filesystem, where `~/documents` and `~/Documents` genuinely are two
        directories and the user configured one of them. Folding the exclusion
        test can only ever refuse a path that used to be allowed — safe on every
        filesystem, and the false positive it costs on Linux (a directory whose
        name differs from Jarvis's own only by case or accent) is not a real
        scenario. Pinned by test_sandbox.py's mangled-spelling tests and by
        test_a_root_is_still_matched_case_sensitively.
        """
        return tuple(unicodedata.normalize("NFC", p).casefold() for p in path.parts)

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def resolve(self, raw: str) -> Path:
        """The real path this argument names, if the tools may touch it.

        Raises SandboxError with a machine-readable code otherwise. Never
        touches the filesystem beyond what resolution requires — the caller
        decides whether the path must exist.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise SandboxError("PATH_REQUIRED")

        path = Path(raw).expanduser()
        if not path.is_absolute():
            # Deliberately not resolved against the process's cwd. The backend's
            # cwd is an implementation detail the model knows nothing about, so
            # guessing would make the same argument mean different files on
            # different runs — and one of those guesses would eventually land
            # outside the sandbox.
            raise SandboxError("PATH_NOT_ABSOLUTE", raw)

        # strict=False: a write target need not exist yet. Existing ancestors
        # still get their symlinks followed, which is what the check needs.
        resolved = path.resolve()

        # Folded, not `is_relative_to`: see _fold. A prefix match over the
        # component tuples covers both "is the excluded dir" and "is under it".
        folded = self._fold(resolved)
        for excluded in self._excluded_folded:
            if folded[: len(excluded)] == excluded:
                # Checked before the root test, so "inside a root" can never
                # override "inside Jarvis's own directories".
                raise SandboxError("PATH_OUTSIDE_SANDBOX", str(resolved))

        for root in self._roots:
            if resolved == root or resolved.is_relative_to(root):
                return resolved

        raise SandboxError("PATH_OUTSIDE_SANDBOX", str(resolved))
