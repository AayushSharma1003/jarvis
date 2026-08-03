"""`jarvis install <url>`: cloning an extension, and refusing to (M5.3).

docs/security-model.md §5. Install is a **delivery mechanism that ends in the
existing approval prompt**, not a new trust path — so most of what matters here
is what it refuses to do before `git` is ever invoked, and that the folder it
produces goes through exactly the same content-keyed approval as one dropped in
by hand.

**How these tests reach real `git` without a network, and without weakening the
shipped check.** The scheme allowlist is tested at the public `install()`, where
`file://` is refused exactly as it will be in production. The clone plumbing is
tested against a genuine local repository — `git init`, one commit — driven
through the internal `_clone()`, which takes a path. Real git runs; the shipped
validation never gets a test-only exemption.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis_backend.extensions import install as installer
from jarvis_backend.extensions.approvals import ApprovalStore, tree_digest

MANIFEST = """
[extension]
name = "timers"
version = "0.1.0"
description = "A test extension"

[[tools]]
name = "set_timer"
risk = "safe"
"""

CODE = "def set_timer(minutes: int) -> str:\n    return 'ok'\n"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(cwd),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


@pytest.fixture
def repo(tmp_path):
    """A real one-commit git repository holding a valid extension.

    Its directory is deliberately NOT named after the manifest — that is what
    proves the destination name comes from the manifest rather than the URL.
    """
    source = tmp_path / "some-repo-name"
    source.mkdir()
    (source / "manifest.toml").write_text(MANIFEST)
    (source / "extension.py").write_text(CODE)
    _git("init", "-b", "main", cwd=source)
    _git("add", ".", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    return source


@pytest.fixture
def root(tmp_path):
    """A scratch extensions directory."""
    path = tmp_path / "data" / "extensions"
    path.mkdir(parents=True)
    return path


def _install_local(repo: Path, root: Path, **kwargs):
    """Install from a local repo, bypassing only the scheme check.

    The scheme check is a *boundary*, tested exhaustively below against the
    public entry point. Bypassing it here is what lets the plumbing be tested
    with real git; it is not a production code path.
    """
    return installer._install_from(str(repo), root, **kwargs)


# -- what never reaches git -------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh -c 'touch /tmp/pwned'",  # git's ext:: transport RUNS this
        "file:///etc/passwd",
        "ssh://git@github.com/x/y.git",
        "git://github.com/x/y.git",
        "git@github.com:x/y.git",  # scp-style, no scheme
        "/etc/passwd",  # a bare path
        "github.com/x/y",  # no scheme at all
        "",
        "   ",
    ],
)
def test_a_url_git_should_never_see_is_refused(url, root, monkeypatch):
    """The one that matters is `ext::`: `git clone 'ext::sh -c ...'` executes
    the command. A scheme allowlist is not cosmetic here, it is the difference
    between an installer and arbitrary code execution on a pasted string."""
    called = []
    monkeypatch.setattr(installer, "_run_git", lambda *a, **k: called.append(a))

    with pytest.raises(installer.InstallError) as e:
        installer.install(url, root)

    assert e.value.code in ("URL_SCHEME_BLOCKED", "URL_INVALID")
    assert called == [], "git was invoked for a URL that should have been refused"


@pytest.mark.parametrize("url", ["--upload-pack=evil", "-c", "--config=x"])
def test_a_url_that_is_really_an_argument_is_refused(url, root, monkeypatch):
    called = []
    monkeypatch.setattr(installer, "_run_git", lambda *a, **k: called.append(a))

    with pytest.raises(installer.InstallError):
        installer.install(url, root)
    assert called == []


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_http_and_https_are_the_allowed_schemes(scheme, root, monkeypatch):
    """Not https-only: refusing http would block a self-hosted git server on a
    LAN, and the transport is not what this check is defending."""
    monkeypatch.setattr(
        installer,
        "_install_from",
        lambda url, root, **kw: ("reached", url),
    )

    assert installer.install(f"{scheme}://example.com/x/y.git", root)[0] == "reached"


def test_the_clone_command_carries_the_flags_that_bound_it(monkeypatch):
    """Asserted on the argv because nothing else can see them.

    `--no-recurse-submodules` only matters against a repo *with* submodules and
    a user with `submodule.recurse=true`; `--` only matters for a URL that got
    past the allowlist. Both are one-word decisions that a behavioural test
    silently passes without — mutation testing showed exactly that — so the
    command line is where they get pinned.
    """
    seen: list[list[str]] = []
    monkeypatch.setattr(installer, "_run_git", lambda args: seen.append(args) or "")

    installer._clone("https://example.com/x/y.git", Path("/tmp/dest"), "")

    args = seen[0]
    assert "--no-recurse-submodules" in args
    assert args[:3] == ["clone", "--depth", "1"]
    # The URL must never be reachable as an option, whatever it looks like.
    assert args[args.index("--") + 1] == "https://example.com/x/y.git"


def test_a_ref_is_passed_as_a_branch(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(installer, "_run_git", lambda args: seen.append(args) or "")

    installer._clone("https://example.com/x/y.git", Path("/tmp/dest"), "v1.2.0")

    assert seen[0][seen[0].index("--branch") + 1] == "v1.2.0"


def test_git_missing_is_a_clear_code_not_a_traceback(root, monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda _: None)

    with pytest.raises(installer.InstallError) as e:
        installer.install("https://example.com/x/y.git", root)
    assert e.value.code == "GIT_NOT_FOUND"


# -- the clone --------------------------------------------------------------


def test_a_repo_is_installed_and_its_commit_pinned(repo, root):
    result = _install_local(repo, root)

    assert result.path == root / "timers"
    assert (result.path / "extension.py").read_text() == CODE
    assert result.commit == _head(repo)
    assert len(result.commit) == 40


def test_the_destination_name_comes_from_the_manifest_not_the_url(repo, root):
    """The repo directory is `some-repo-name`; the manifest says `timers`.

    Taking the name from the URL would let a repository install itself as any
    name it liked — including one the user had already approved.
    """
    result = _install_local(repo, root)

    assert result.path.name == "timers"
    assert not (root / "some-repo-name").exists()


def test_the_digest_matches_what_landed_on_disk(repo, root):
    result = _install_local(repo, root)

    assert result.digest == tree_digest(result.path)


def test_a_clone_that_fails_installs_nothing_and_leaves_no_staging(repo, root, tmp_path):
    with pytest.raises(installer.InstallError) as e:
        installer._install_from(str(tmp_path / "does-not-exist"), root)

    assert e.value.code == "CLONE_FAILED"
    assert list(root.iterdir()) == []
    assert _staging_dirs(tmp_path) == []


def test_git_history_is_not_part_of_the_extension_identity(repo, root):
    """`.git` is excluded from the digest (approvals.py), which is what lets a
    shallow clone keep its provenance without the identity changing."""
    result = _install_local(repo, root)

    assert (result.path / ".git").exists()
    assert result.digest == tree_digest(result.path)


# -- what a hostile repo cannot do ------------------------------------------


def test_a_repo_whose_manifest_name_would_escape_is_refused(tmp_path, root):
    """`root / manifest.name` is only safe because `manifest.name` cannot
    contain a separator — NAME_RE refuses this before install ever joins it."""
    source = tmp_path / "hostile-repo"
    source.mkdir()
    (source / "manifest.toml").write_text(MANIFEST.replace('"timers"', '"../../../pwned"'))
    (source / "extension.py").write_text(CODE)
    _git("init", "-b", "main", cwd=source)
    _git("add", ".", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    before = _tree(tmp_path)

    with pytest.raises(installer.InstallError) as e:
        _install_local(source, root)

    assert e.value.code == "MANIFEST_NAME_INVALID"
    assert list(root.iterdir()) == []
    # Nothing appeared anywhere — not beside the extensions dir, not above it.
    assert _tree(tmp_path) == before


def test_a_repo_containing_a_symlink_is_refused_before_anything_moves(tmp_path, root):
    """A symlinked file is a digest bypass — its real bytes live outside the
    folder, free to change after approval. `tree_digest` refuses the whole tree,
    and install runs that check in staging so the refusal costs nothing."""
    source = tmp_path / "sneaky"
    source.mkdir()
    (source / "manifest.toml").write_text(MANIFEST)
    (source / "extension.py").write_text(CODE)
    (source / "link.py").symlink_to(tmp_path / "outside.py")
    (tmp_path / "outside.py").write_text("print('elsewhere')\n")
    _git("init", "-b", "main", cwd=source)
    _git("add", ".", cwd=source)
    _git("commit", "-m", "initial", cwd=source)

    with pytest.raises(installer.InstallError) as e:
        _install_local(source, root)

    assert e.value.code == "EXTENSION_UNSAFE_TREE"
    assert list(root.iterdir()) == []


def test_a_repo_with_no_manifest_is_refused(tmp_path, root):
    source = tmp_path / "empty"
    source.mkdir()
    (source / "readme.md").write_text("not an extension")
    _git("init", "-b", "main", cwd=source)
    _git("add", ".", cwd=source)
    _git("commit", "-m", "initial", cwd=source)

    with pytest.raises(installer.InstallError) as e:
        _install_local(source, root)

    assert e.value.code == "MANIFEST_MISSING"
    assert list(root.iterdir()) == []


def test_an_oversized_repo_is_refused(repo, root, monkeypatch):
    """Bounds what gets *installed*; the clone timeout is what bounds the
    download. Said plainly rather than implied."""
    monkeypatch.setattr(installer, "MAX_EXTENSION_BYTES", 10)

    with pytest.raises(installer.InstallError) as e:
        _install_local(repo, root)

    assert e.value.code == "EXTENSION_TOO_LARGE"
    assert list(root.iterdir()) == []


def test_staging_never_appears_inside_the_extensions_directory(repo, root, monkeypatch):
    """`discover()` lists every subdirectory of `extensions/`, so a half-written
    clone there would surface in the panel as a broken extension."""
    seen: list[list[str]] = []
    real_clone = installer._clone

    def watching(url, dest, ref):
        seen.append(sorted(p.name for p in root.iterdir()))
        return real_clone(url, dest, ref)

    monkeypatch.setattr(installer, "_clone", watching)
    _install_local(repo, root)

    assert seen == [[]], "something was in extensions/ while the clone ran"


# -- reinstalling -----------------------------------------------------------


def test_installing_over_an_existing_extension_is_refused(repo, root):
    _install_local(repo, root)

    with pytest.raises(installer.InstallError) as e:
        _install_local(repo, root)
    assert e.value.code == "EXTENSION_ALREADY_INSTALLED"


def test_a_refused_reinstall_leaves_the_original_untouched(repo, root):
    first = _install_local(repo, root)
    (first.path / "extension.py").write_text("# edited by hand\n")

    with pytest.raises(installer.InstallError):
        _install_local(repo, root)

    assert (first.path / "extension.py").read_text() == "# edited by hand\n"


def test_force_replaces_the_folder(repo, root):
    first = _install_local(repo, root)
    (first.path / "extension.py").write_text("# edited by hand\n")

    result = _install_local(repo, root, force=True)

    assert (result.path / "extension.py").read_text() == CODE


def test_force_leaves_the_old_approval_alone_so_the_new_bytes_are_changed(
    repo, root, tmp_path
):
    """The content-keyed approval already handles updates: new bytes ⇒ a
    different digest ⇒ `changed` ⇒ not loaded until re-approved. Silently
    revoking or silently re-approving would both be worse."""
    from jarvis_backend.extensions.loader import discover

    first = _install_local(repo, root)
    store = ApprovalStore(tmp_path / "approvals.toml")
    store.approve(first.manifest, first.digest)
    assert discover(root, store)[0].status == "approved"

    (repo / "extension.py").write_text(CODE + "# a new upstream version\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "update", cwd=repo)
    _install_local(repo, root, force=True)

    assert store.get("timers") is not None, "force must not silently revoke"
    assert discover(root, store)[0].status == "changed"


# -- provenance --------------------------------------------------------------


def test_the_source_and_commit_are_recordable(repo, root, tmp_path):
    """The fields have existed on `Approval` since M5.1 waiting for this."""
    result = _install_local(repo, root)
    store = ApprovalStore(tmp_path / "approvals.toml")

    store.approve(result.manifest, result.digest, source="https://x/y", commit=result.commit)

    record = store.get("timers")
    assert record.source == "https://x/y"
    assert record.commit == _head(repo)


def test_an_installed_extension_is_pending_until_approved(repo, root, tmp_path):
    """Install delivers; it does not bless. A freshly installed extension is
    indistinguishable from one dropped in by hand."""
    from jarvis_backend.extensions.loader import discover

    _install_local(repo, root)
    store = ApprovalStore(tmp_path / "approvals.toml")

    assert discover(root, store)[0].status == "pending"


# -- the subprocess environment ---------------------------------------------


def test_the_auth_token_never_reaches_git(monkeypatch):
    """Same rule as the shell tool, and now literally the same function."""
    monkeypatch.setenv("JARVIS_WS_TOKEN", "secret")

    env = installer._git_env()

    assert "JARVIS_WS_TOKEN" not in env


def test_git_is_stopped_from_asking_for_a_password(monkeypatch):
    """A private repo must fail, not hang forever on a prompt nobody can see —
    the CLI would look frozen with no way to tell why."""
    assert installer._git_env()["GIT_TERMINAL_PROMPT"] == "0"


# -- the CLI ----------------------------------------------------------------
#
# `install()` is monkeypatched in most of these: what is under test here is the
# consent flow around it — what gets printed, what is recorded, and what happens
# when the answer is no. The clone itself is covered above, against real git.


@pytest.fixture
def cli_install(repo, monkeypatch, tmp_path):
    """`jarvis install` pointed at the local repo, with a scratch data dir."""
    from jarvis_backend import cli
    from jarvis_backend.extensions import install as mod

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    # `cli._install` resolves `install` off the module at call time, so patching
    # the attribute redirects it at the local repo without touching the CLI.
    monkeypatch.setattr(
        mod, "install", lambda url, root, **kw: mod._install_from(str(repo), root, **kw)
    )

    def run(*args):
        return cli.main(["install", "https://example.com/x/y.git", *args])

    return run


def test_installing_shows_the_declaration_before_asking(cli_install, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")

    assert cli_install() == 0

    out = capsys.readouterr().out
    assert "same access Jarvis has" in out
    assert "set_timer" in out


def test_the_install_and_approve_prompts_are_the_same_text(cli_install, capsys, monkeypatch):
    """One copy, two callers — asserted by driving **both real commands**.

    Calling `_print_declaration` twice and comparing would prove nothing; the
    claim is that `install` and `extensions approve` each go through it, so the
    text is captured from the two commands themselves.
    """
    from jarvis_backend import cli

    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli_install()
    from_install = _declaration(capsys.readouterr().out)

    cli.main(["extensions", "revoke", "timers"])
    capsys.readouterr()
    cli.main(["extensions", "approve", "timers", "--yes"])
    from_approve = _declaration(capsys.readouterr().out)

    assert from_install == from_approve
    assert "not a sandbox" in from_install
    assert "set_timer (safe)" in from_install


def test_installing_records_the_source_and_the_commit(cli_install, repo, monkeypatch):
    from jarvis_backend.config import approvals_path

    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert cli_install() == 0

    record = ApprovalStore(approvals_path()).get("timers")
    assert record.source == "https://example.com/x/y.git"
    assert record.commit == _head(repo)


def test_declining_leaves_it_installed_but_unapproved(cli_install, capsys, monkeypatch):
    """The owner's call: the folder stays, exactly like one dropped in by hand,
    so changing your mind does not cost another clone."""
    from jarvis_backend.config import approvals_path, extensions_dir

    monkeypatch.setattr("builtins.input", lambda _: "n")

    assert cli_install() == 1
    assert (extensions_dir() / "timers").is_dir()
    assert ApprovalStore(approvals_path()).get("timers") is None
    assert "jarvis extensions approve timers" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["", "  ", "n", "no", "later", "Y E S", "yeah"])
def test_anything_that_is_not_a_clear_yes_is_a_no(cli_install, answer, monkeypatch):
    """A bare Enter is the one that matters: the prompt is `[y/N]`, and someone
    hitting return to get past it must not have approved anything."""
    from jarvis_backend.config import approvals_path

    monkeypatch.setattr("builtins.input", lambda _: answer)

    assert cli_install() == 1
    assert ApprovalStore(approvals_path()).get("timers") is None


def test_approving_later_still_records_where_it_came_from(cli_install, repo, monkeypatch):
    """**Live-caught.** Install, decline, approve later — the provenance must
    survive that, and `extensions approve` used to blank it.

    It cannot be *remembered* between the two commands (they are separate
    processes and nothing persists a declined install), so it is read back off
    the checkout `install` left behind.
    """
    from jarvis_backend import cli
    from jarvis_backend.config import approvals_path

    monkeypatch.setattr("builtins.input", lambda _: "n")
    cli_install()

    cli.main(["extensions", "approve", "timers", "--yes"])

    record = ApprovalStore(approvals_path()).get("timers")
    assert record.commit == _head(repo)
    assert record.source.endswith("some-repo-name")


def test_reapproving_after_a_force_records_the_new_commit(cli_install, repo, monkeypatch):
    """The stale commit would be worse than none: it would claim these bytes
    are a commit they are not."""
    from jarvis_backend import cli
    from jarvis_backend.config import approvals_path

    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli_install()
    first = ApprovalStore(approvals_path()).get("timers").commit

    (repo / "extension.py").write_text(CODE + "# upstream v2\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "v2", cwd=repo)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    cli_install("--force")
    cli.main(["extensions", "approve", "timers", "--yes"])

    record = ApprovalStore(approvals_path()).get("timers")
    assert record.commit == _head(repo)
    assert record.commit != first


def test_a_hand_dropped_extension_approves_with_no_provenance(tmp_path, monkeypatch):
    """Not every extension came from a URL, and one that did not must approve
    cleanly rather than erroring on a missing `.git`."""
    from jarvis_backend import cli
    from jarvis_backend.config import approvals_path, extensions_dir

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    folder = extensions_dir() / "timers"
    folder.mkdir(parents=True)
    (folder / "manifest.toml").write_text(MANIFEST)
    (folder / "extension.py").write_text(CODE)

    assert cli.main(["extensions", "approve", "timers", "--yes"]) == 0

    record = ApprovalStore(approvals_path()).get("timers")
    assert record.source == "" and record.commit == ""


def test_provenance_is_empty_rather_than_fatal_without_git(repo, root, monkeypatch):
    result = _install_local(repo, root)
    monkeypatch.setattr(installer.shutil, "which", lambda _: None)

    assert installer.provenance(result.path) == ("", "")


def test_declining_leaves_it_listed_as_pending(cli_install, capsys, monkeypatch):
    from jarvis_backend import cli

    monkeypatch.setattr("builtins.input", lambda _: "n")
    cli_install()
    capsys.readouterr()

    cli.main(["extensions", "list"])
    assert "pending" in capsys.readouterr().out


def test_yes_skips_the_question_not_the_printing(cli_install, capsys, monkeypatch):
    def refuse(_):
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr("builtins.input", refuse)

    assert cli_install("--yes") == 0
    assert "same access Jarvis has" in capsys.readouterr().out


def test_a_failed_install_reports_its_code(root, capsys, monkeypatch, tmp_path):
    from jarvis_backend import cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))

    assert cli.main(["install", "ext::sh -c evil"]) == 1
    assert "URL_SCHEME_BLOCKED" in capsys.readouterr().out


def test_a_second_install_says_how_to_replace_it(cli_install, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli_install()
    capsys.readouterr()

    assert cli_install() == 1
    out = capsys.readouterr().out
    assert "EXTENSION_ALREADY_INSTALLED" in out
    assert "--force" in out


def test_the_restart_caveat_is_stated(cli_install, capsys, monkeypatch):
    """The CLI is a different process from the sidecar, so a running app has no
    idea this happened. Better said out loud than discovered."""
    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli_install()

    assert "Restart Jarvis" in capsys.readouterr().out


# -- helpers ----------------------------------------------------------------


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _staging_dirs(tmp_path: Path) -> list[Path]:
    return [p for p in tmp_path.rglob(f"{installer.STAGING_PREFIX}*") if p.is_dir()]


def _tree(path: Path) -> set[str]:
    """Every path under `path`, for asserting that nothing new appeared."""
    return {str(p.relative_to(path)) for p in path.rglob("*")}


def _declaration(output: str) -> str:
    """The consent block out of a command's full stdout, from the extension's
    name down to the line about which files the approval covers."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("timers 0.1.0"))
    end = next(i for i, line in enumerate(lines) if "asks again" in line)
    return "\n".join(lines[start : end + 1])


# -- cleanup that actually cleans up -----------------------------------------
#
# Found on a Windows runner the first time this suite ran there (M6.4). `git`
# writes its object files **read-only**, and `shutil.rmtree` on Windows cannot
# delete a read-only file — it raises PermissionError. The staging cleanup used
# `ignore_errors=True`, so it swallowed that and left the entire cloned repo
# behind, silently, in the data directory. Every refused install leaked one, and
# they accumulate.
#
# It also broke `--force`, which rmtree's the destination without
# ignore_errors: that path raised outright, so replacing an installed extension
# could not work on Windows at all. One root cause, four failing tests.
#
# security-model.md §5 says a failed install "leaves nothing behind". This is
# the test that makes that sentence true rather than aspirational.


def _readonly_tree(path):
    """A directory whose contents cannot be removed without clearing a bit —
    read-only files on Windows, a read-only parent on POSIX. Different
    mechanism, same question: does the cleanup clear it and retry?"""
    import os
    import stat

    path.mkdir(parents=True)
    (path / "object").write_text("x")
    os.chmod(path / "object", stat.S_IRUSR)
    os.chmod(path, stat.S_IRUSR | stat.S_IXUSR)
    return path


def test_staging_cleanup_removes_a_tree_git_left_read_only(tmp_path):
    """**Half of this is only provable on Windows, and that is worth stating.**

    Mutating away the target's own chmod comes back NOT CAUGHT here, because on
    POSIX removing an entry needs write permission on the *parent* and nothing
    else — the file's own bit is irrelevant. On Windows it is the only thing
    that matters. So the usual reading of a NOT CAUGHT ("this branch can never
    decide, delete it") is wrong in this one case: it cannot decide *on this
    machine*. The Windows runner in ci.yml is what proves the other half, which
    is most of why that matrix was added.
    """
    from jarvis_backend.extensions.install import _rmtree

    victim = _readonly_tree(tmp_path / "staging")
    _rmtree(victim)
    assert not victim.exists(), (
        "cleanup left a read-only tree behind — on Windows that is every failed "
        "install leaking a full git clone into the data directory"
    )


def test_a_refused_install_leaves_no_staging_behind(tmp_path, root):
    """The property §5 claims, end to end: a rejected repo leaves the data
    directory exactly as it found it."""
    source = tmp_path / "hostile-repo"
    source.mkdir()
    (source / "manifest.toml").write_text(MANIFEST.replace('"timers"', '"../../../pwned"'))
    (source / "extension.py").write_text(CODE)
    _git("init", "-b", "main", cwd=source)
    _git("add", ".", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    before = _tree(tmp_path)

    with pytest.raises(installer.InstallError):
        _install_local(source, root)

    leftovers = sorted(p for p in _tree(tmp_path) - before)
    assert leftovers == [], f"staging survived a refused install: {leftovers}"
