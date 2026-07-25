"""`jarvis install <url>`: getting an extension onto the machine (M5.3).

docs/security-model.md §5. This module **delivers bytes; it does not bless
them.** Everything it produces goes through the same content-keyed approval as a
folder dropped in by hand — the CLI prints the same declaration block and asks
the same question, and an extension that is installed but not approved is
`pending` and never imported. Install is not a second trust path, and the moment
it becomes one is the moment this file is wrong.

**It is not a tool, and must never become one.** Nothing here is registered with
the registry. An extension installer the model can reach is arbitrary remote code
execution with a single confirmation in front of it, which is a different
security posture from every tool in §1 and not one this project takes.

Three things carry the safety, in the order they happen:

1. **The URL is validated before `git` ever sees it.** This is not hygiene.
   `git clone 'ext::sh -c "..."'` **executes that command** — the `ext::`
   transport is a remote-helper feature, and it turns a pasted string into RCE.
   `file://`, `ssh://` and scp-style `user@host:path` are all things an
   extension URL has no business being either. An allowlist of `http`/`https` is
   the only form of this check that does not need to anticipate the next
   transport git adds.
2. **Staging is outside `extensions/`.** `loader.discover()` lists every
   subdirectory there, so a clone in progress would surface in the panel as a
   broken extension, and a failed one would linger. Staging lives beside the
   extensions directory instead, which also keeps the final move a
   same-filesystem rename rather than a copy.
3. **The destination name comes from the manifest, never the URL.** A repository
   that could choose its own installed name could install itself over one the
   user had already approved. `manifest.name` is validated against `NAME_RE`
   (manifest.py) — no separators, no `..` — so path traversal is closed by a
   check that already existed, and `_inspect` requires folder-name == manifest-
   name anyway, so this is the only destination that would ever load.

The digest is computed in staging, so a repository containing a symlink
(`EXTENSION_UNSAFE_TREE`, a digest bypass — see approvals.py) is refused before
anything is moved into place.

**No auto-update, ever** (§5). `--force` replaces the folder and deliberately
leaves any existing approval record alone: the new bytes hash differently, so the
extension reads as `changed` and will not load until a human approves it again.
Silently revoking or silently re-approving would each be worse, and updates need
no special case at all — the content-keyed approval already is the mechanism.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..tools.shell import child_env
from .approvals import ApprovalError, tree_digest
from .manifest import Manifest, ManifestError
from .manifest import load as load_manifest

# The only transports an extension URL may use. An allowlist rather than a
# denylist because the thing being excluded (`ext::`, which runs a command) is a
# feature git can extend, and a denylist would have to keep up with it.
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Generous next to the shell tool's 30s: this is a network clone a human just
# asked for and is watching, not a tool call holding the generation slot.
CLONE_TIMEOUT_S = 120.0

# What gets *installed* is bounded here; what gets *downloaded* is bounded by the
# timeout above. Said plainly because the difference matters — a repository with
# one enormous blob is still fetched before this refuses it. An extension is a
# manifest and one Python file, so anything near this is already wrong.
MAX_EXTENSION_BYTES = 10 * 1024 * 1024

# Staging directories are created beside the extensions directory, never inside
# it. The prefix exists so a crashed run leaves something recognisable.
STAGING_PREFIX = ".jarvis-install-"


class InstallError(Exception):
    """Raised with a machine-readable code, like ManifestError/ApprovalError."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Installed:
    """What landed on disk. Nothing here is approved yet."""

    path: Path
    manifest: Manifest
    digest: str
    commit: str


def install(url: str, root: Path, *, ref: str = "", force: bool = False) -> Installed:
    """Clone an extension from a URL into `root`. Never approves anything.

    Raises InstallError; the caller owns all printing and asking, the same
    division as `loader.discover()` and its callers.
    """
    _check_url(url)
    if shutil.which("git") is None:
        raise InstallError("GIT_NOT_FOUND", "install git and try again")
    return _install_from(url, root, ref=ref, force=force)


def _check_url(url: str) -> None:
    """Refuse anything git should never be handed. See the module docstring.

    **One check, deliberately.** Earlier drafts also refused a missing scheme and
    a leading `-` separately, and mutation testing showed neither branch could
    ever be the one that decided: a scheme must start with a letter at position
    0, so a flag-shaped string (`--upload-pack=…`) parses to *no* scheme and a
    scp-style `git@host:path` does too — both already fail the allowlist. A
    branch no test can distinguish is decoration, and decoration in a security
    check is worse than nothing because it reads like defense. The `--` in
    `_clone()` is the real second layer, and that one is asserted.
    """
    if not isinstance(url, str) or not url.strip():
        raise InstallError("URL_INVALID", "no URL given")
    scheme = urlsplit(url.strip()).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise InstallError(
            "URL_SCHEME_BLOCKED", f"{scheme + '://' if scheme else 'that'} is not allowed"
        )


def _install_from(url: str, root: Path, *, ref: str = "", force: bool = False) -> Installed:
    """Clone, validate, and move into place. Assumes the URL was checked.

    Split from `install()` so the plumbing can be exercised against a real local
    repository in the tests without the scheme allowlist needing a test-only
    exemption — the check stays exactly as strict as it ships.
    """
    root.mkdir(parents=True, exist_ok=True)
    # Beside the extensions directory, not inside it (docstring point 2), and on
    # the same filesystem so the final move is a rename.
    staging_parent = root.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=staging_parent))
    clone = staging / "repo"

    try:
        _clone(url, clone, ref)
        commit = _head(clone)
        _check_size(clone)

        try:
            manifest = load_manifest(clone)
        except ManifestError as e:
            raise InstallError(e.code, e.detail) from e

        try:
            digest = tree_digest(clone)
        except ApprovalError as e:
            raise InstallError(e.code, e.detail) from e

        destination = root / manifest.name
        if destination.exists():
            if not force:
                raise InstallError("EXTENSION_ALREADY_INSTALLED", manifest.name)
            shutil.rmtree(destination)
        shutil.move(str(clone), str(destination))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return Installed(path=destination, manifest=manifest, digest=digest, commit=commit)


def provenance(path: Path) -> tuple[str, str]:
    """(source, commit) for an installed extension, read back off its checkout.

    `jarvis install` records these directly when it approves in one breath, but
    the two commands can be separated — install and decline, then approve
    tomorrow; or `--force` onto a new commit, then re-approve — and those are
    different *processes*, so nothing is remembered between them. Reading them
    back from the `.git` the install left behind (excluded from the digest, so
    keeping it costs the identity nothing) is what stops `extensions approve`
    from blanking the provenance of an extension that plainly came from a URL,
    and what keeps it *correct* rather than stale after a forced reinstall.

    **Informational, never authoritative.** A folder's own `.git` is as editable
    as the rest of it, so this is a label, not evidence. The digest is the
    security control; nothing here is consulted when deciding what may run.

    Silent on every failure: no git, no checkout, a detached or remote-less
    repo. An approval must never fail because a label could not be looked up.
    """
    if shutil.which("git") is None or not (path / ".git").exists():
        return "", ""
    source = commit = ""
    try:
        source = _run_git(["-C", str(path), "remote", "get-url", "origin"]).strip()
    except InstallError:
        pass
    try:
        commit = _run_git(["-C", str(path), "rev-parse", "HEAD"]).strip()
    except InstallError:
        pass
    return source, commit


def _clone(url: str, dest: Path, ref: str = "") -> None:
    """`git clone`, shallow, with the flags that keep it from doing more.

    `--depth 1` keeps the retained `.git` small (it is excluded from the digest,
    so history is provenance the identity does not depend on).
    `--no-recurse-submodules` is explicit rather than relied upon: a user with
    `submodule.recurse=true` in their global config would otherwise fetch more
    remote code than the URL named.
    `--` ends the option list, so a URL that survived `_check_url` still cannot
    be read as a flag.
    """
    args = ["clone", "--depth", "1", "--no-recurse-submodules"]
    if ref:
        args += ["--branch", ref]
    args += ["--", url, str(dest)]
    _run_git(args)


def _head(repo: Path) -> str:
    """The commit that was actually fetched — §5's pin."""
    return _run_git(["-C", str(repo), "rev-parse", "HEAD"]).strip()


def _run_git(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_S,
            env=_git_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise InstallError("CLONE_TIMEOUT", f"git took longer than {CLONE_TIMEOUT_S:.0f}s") from e
    except OSError as e:  # git vanished between which() and here
        raise InstallError("GIT_NOT_FOUND", str(e)) from e
    if proc.returncode != 0:
        # git's own message is better than anything paraphrased here: "remote
        # branch not found", "repository not found", "authentication failed".
        raise InstallError("CLONE_FAILED", (proc.stderr or proc.stdout).strip()[:500])
    return proc.stdout


def _git_env() -> dict[str, str]:
    """The shell tool's child environment, plus one git-specific setting.

    `child_env()` is shared rather than duplicated so there is one definition of
    what a Jarvis subprocess may see — above all that `JARVIS_WS_TOKEN` never
    reaches one (tools/shell.py).

    `GIT_TERMINAL_PROMPT=0` turns a private repository into a clean failure
    instead of a hang: git would otherwise block on a credential prompt that the
    user may not even see, and the CLI would look frozen with nothing to explain
    it.
    """
    return {**child_env(), "GIT_TERMINAL_PROMPT": "0"}


def _check_size(path: Path) -> None:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            total += entry.stat().st_size
            if total > MAX_EXTENSION_BYTES:
                raise InstallError(
                    "EXTENSION_TOO_LARGE", f"over {MAX_EXTENSION_BYTES // (1024 * 1024)}MB"
                )
