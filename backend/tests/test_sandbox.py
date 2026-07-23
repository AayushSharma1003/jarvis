"""The filesystem sandbox — docs/security-model.md §2, executable.

The whole module exists for the escape cases. A test that only proves
"a path under a root is allowed" proves nothing: the interesting question is
always whether something that *looks* like it is under a root actually is,
after `..` and symlinks have had their say.
"""

from __future__ import annotations

import pytest

from jarvis_backend.security.sandbox import Sandbox, SandboxError


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "workspace"
    r.mkdir()
    return r


def _code(exc_info) -> str:
    return exc_info.value.code


# -- the ordinary case ------------------------------------------------------


def test_a_path_under_a_root_resolves(root):
    (root / "notes.txt").write_text("hi")
    assert Sandbox([root]).resolve(str(root / "notes.txt")) == root / "notes.txt"


def test_the_root_itself_is_inside_the_sandbox(root):
    """`list_dir("~/Documents")` is the first thing anyone asks for."""
    assert Sandbox([root]).resolve(str(root)) == root


def test_a_file_that_does_not_exist_yet_resolves(root):
    """write_file's target: resolution must not require existence, or creating
    a new file would be impossible."""
    assert Sandbox([root]).resolve(str(root / "new" / "file.txt")).name == "file.txt"


# -- the escapes ------------------------------------------------------------


def test_a_path_outside_every_root_is_refused(root, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("secret")
    with pytest.raises(SandboxError) as e:
        Sandbox([root]).resolve(str(outside))
    assert _code(e) == "PATH_OUTSIDE_SANDBOX"


def test_dot_dot_traversal_is_refused(root, tmp_path):
    """The spelling is inside the root; the destination is not."""
    (tmp_path / "elsewhere.txt").write_text("secret")
    with pytest.raises(SandboxError) as e:
        Sandbox([root]).resolve(str(root / ".." / "elsewhere.txt"))
    assert _code(e) == "PATH_OUTSIDE_SANDBOX"


def test_a_symlink_inside_a_root_pointing_out_is_refused(root, tmp_path):
    """**The load-bearing test.** Checking the path as typed would allow this:
    every component is under the root. Only resolution sees where it lands."""
    secret = tmp_path / "outside"
    secret.mkdir()
    (secret / "id_rsa").write_text("PRIVATE KEY")
    (root / "shortcut").symlink_to(secret)

    with pytest.raises(SandboxError) as e:
        Sandbox([root]).resolve(str(root / "shortcut" / "id_rsa"))
    assert _code(e) == "PATH_OUTSIDE_SANDBOX"


def test_a_symlinked_parent_defeats_a_write_to_a_new_file(root, tmp_path):
    """Same attack aimed at write_file: the file does not exist yet, so only
    the *ancestors* can be resolved — which is exactly enough."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "shortcut").symlink_to(outside)
    with pytest.raises(SandboxError) as e:
        Sandbox([root]).resolve(str(root / "shortcut" / "planted.txt"))
    assert _code(e) == "PATH_OUTSIDE_SANDBOX"


def test_a_relative_path_is_refused(root):
    """Never resolved against the process cwd: the model has no idea what that
    is, so the same argument would mean different files on different runs."""
    with pytest.raises(SandboxError) as e:
        Sandbox([root]).resolve("notes.txt")
    assert _code(e) == "PATH_NOT_ABSOLUTE"


@pytest.mark.parametrize("raw", ["", "   ", None, 5])
def test_a_missing_or_non_string_path_is_refused(root, raw):
    with pytest.raises(SandboxError) as e:
        Sandbox([root]).resolve(raw)
    assert _code(e) == "PATH_REQUIRED"


# -- permanent exclusions ---------------------------------------------------


def test_jarvis_own_directories_are_excluded_even_inside_a_root(root):
    """Self-escalation: rewriting config.toml to widen the sandbox, or dropping
    a file in the extensions directory. The data dir legitimately lives inside
    the home directory on Linux, so 'inside a root' must not win here."""
    data = root / "jarvis-data"
    data.mkdir()
    (data / "config.toml").write_text("roots = ['/']")
    sandbox = Sandbox([root], excluded=[data])

    with pytest.raises(SandboxError) as e:
        sandbox.resolve(str(data / "config.toml"))
    assert _code(e) == "PATH_OUTSIDE_SANDBOX"
    # ...while the rest of the root is unaffected.
    assert sandbox.resolve(str(root / "fine.txt")).name == "fine.txt"


def test_the_excluded_directory_itself_is_refused(root):
    data = root / "jarvis-data"
    data.mkdir()
    with pytest.raises(SandboxError):
        Sandbox([root], excluded=[data]).resolve(str(data))


def test_a_symlink_into_an_excluded_directory_is_refused(root):
    """The exclusion has to survive the same indirection the roots do."""
    data = root / "jarvis-data"
    data.mkdir()
    (data / "jarvis.sqlite3").write_text("db")
    (root / "shortcut").symlink_to(data)
    with pytest.raises(SandboxError) as e:
        Sandbox([root], excluded=[data]).resolve(str(root / "shortcut" / "jarvis.sqlite3"))
    assert _code(e) == "PATH_OUTSIDE_SANDBOX"


# -- degenerate configurations ----------------------------------------------


def test_no_roots_denies_everything(root):
    """`roots = []` in config.toml means file access is off. An empty allowlist
    that quietly means 'allow everything' is a classic, and not ours."""
    (root / "notes.txt").write_text("hi")
    with pytest.raises(SandboxError) as e:
        Sandbox([]).resolve(str(root / "notes.txt"))
    assert _code(e) == "PATH_OUTSIDE_SANDBOX"


def test_a_root_that_is_itself_a_symlink_still_matches(tmp_path):
    """~/Documents is a symlink on plenty of real machines (iCloud Drive does
    this). If roots were not resolved too, every lookup under them would miss
    and the sandbox would deny everything it is supposed to allow."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "notes.txt").write_text("hi")
    link = tmp_path / "linked"
    link.symlink_to(real)

    sandbox = Sandbox([link])
    assert sandbox.resolve(str(link / "notes.txt")) == real / "notes.txt"
    assert sandbox.resolve(str(real / "notes.txt")) == real / "notes.txt"


def test_several_roots_are_all_honoured(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    sandbox = Sandbox([a, b])
    assert sandbox.resolve(str(a / "x.txt")).parent == a
    assert sandbox.resolve(str(b / "y.txt")).parent == b


def test_a_sibling_root_prefix_is_not_a_root(tmp_path):
    """/tmp/work must not admit /tmp/work-secrets. String prefixes would;
    is_relative_to does not."""
    work = tmp_path / "work"
    work.mkdir()
    sneaky = tmp_path / "work-secrets"
    sneaky.mkdir()
    (sneaky / "creds").write_text("k")
    with pytest.raises(SandboxError):
        Sandbox([work]).resolve(str(sneaky / "creds"))
