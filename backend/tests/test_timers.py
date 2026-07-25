"""The timers-reminders extension (M5.4), driven through the real loader.

Loading it with `loader.load_approved` rather than importing the file directly
is the point: it proves the manifest, the approval, the allowlist and the risk
floors agree with the code, not just that the functions work. If a tool is
renamed in one place and not the other, these fail.

Nothing here sleeps. `_Schedule.tick(now)` is the whole scheduler minus the
`time.sleep`, so every "and then it fires" is a function call with an injected
clock — which is also the only way to test a deadline a week out.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from jarvis_backend.extensions import host
from jarvis_backend.extensions.approvals import ApprovalStore, tree_digest
from jarvis_backend.extensions.loader import discover, load_approved
from jarvis_backend.security.permissions import SafeOnlyGate
from jarvis_backend.tools.registry import Registry

SOURCE = Path(__file__).resolve().parents[2] / "extensions" / "timers-reminders"


@pytest.fixture
def timers(tmp_path, monkeypatch):
    """The extension, installed into a scratch dir, approved, and loaded.

    A fresh copy per test so one test's `timers.json` cannot leak into the
    next, and a fresh module object because the loader re-executes the file.
    """
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    host.unbind()

    installed = tmp_path / "data" / "extensions" / "timers-reminders"
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, installed)

    store = ApprovalStore(tmp_path / "data" / "extensions.toml")
    found = discover(installed.parent, store)
    entry = next(d for d in found if d.name == "timers-reminders")
    store.approve(entry.manifest, entry.digest)

    registry = Registry(SafeOnlyGate())
    results = load_approved(registry, discover(installed.parent, store))
    assert results[0].ok, results[0].detail

    import sys

    module = sys.modules["jarvis_ext_timers_reminders"]
    yield module, registry
    host.unbind()


class Recorder:
    """Catches what the extension announces, without a running event loop."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def __call__(self, source, code, data=None, *, speak=False):
        self.sent.append((code, data or {}))
        return "id"


@pytest.fixture
def announced(timers, monkeypatch):
    module, _ = timers
    recorder = Recorder()
    monkeypatch.setattr(module, "notify", recorder)
    return recorder


# -- the loader agrees with the manifest ------------------------------------


DECLARED = ("set_timer", "set_reminder", "list_timers", "cancel_timer")


def test_the_four_declared_tools_are_registered(timers):
    _, registry = timers

    assert [n for n in DECLARED if registry.get(n) is not None] == list(DECLARED)


def test_every_tool_is_safe_but_not_read_only(timers):
    """Gotcha 21, on the extension that motivated it. `safe` here means "runs
    without a prompt while the conversation is clean" — the taint escalation
    still applies, and it applies because read_only is False."""
    _, registry = timers

    for name in DECLARED:
        tool = registry.get(name)
        assert tool.risk == "safe", name
        assert tool.read_only is False, name


def test_helpers_are_not_registered(timers):
    """The manifest is an allowlist: an importable function is not a tool."""
    _, registry = timers

    for helper in ("_announce", "_parse_when", "_schedule", "_format_local"):
        assert registry.get(helper) is None


def test_the_model_is_told_what_format_a_reminder_time_takes(timers):
    """Extension tools get no per-argument descriptions, so the format has to
    live in the description or the model has to guess it."""
    _, registry = timers

    assert "ISO" in registry.get("set_reminder").description


# -- setting one -------------------------------------------------------------


def test_a_timer_is_scheduled_and_reported(timers):
    module, _ = timers

    result = module.set_timer(10, "tea")

    assert result.startswith("id=t1 ")
    assert "label=tea" in result


def test_a_timer_fires_when_it_comes_due(timers, announced):
    module, _ = timers
    module.set_timer(10, "tea")

    fired = module._schedule().tick(time.time() + 601)

    assert len(fired) == 1
    assert announced.sent == [("TIMER_FINISHED_LABELED", {"label": "tea"})]


def test_an_unlabelled_timer_reports_its_length(timers, announced):
    module, _ = timers
    module.set_timer(10)

    module._schedule().tick(time.time() + 601)

    assert announced.sent == [("TIMER_FINISHED", {"minutes": 10})]


def test_a_timer_does_not_fire_early(timers, announced):
    module, _ = timers
    module.set_timer(10, "tea")

    assert module._schedule().tick(time.time() + 599) == []
    assert announced.sent == []


def test_a_fired_timer_fires_exactly_once(timers, announced):
    module, _ = timers
    module.set_timer(10, "tea")

    module._schedule().tick(time.time() + 601)
    module._schedule().tick(time.time() + 900)

    assert len(announced.sent) == 1


def test_a_timer_asks_to_be_spoken(timers, timers_speak):
    """A toast alone does not reach a user who minimised the window; the spoken
    line is what does (the backend owns the speaker)."""
    module, _ = timers
    module.set_timer(1, "tea")

    module._schedule().tick(time.time() + 61)

    assert timers_speak == [True]


@pytest.fixture
def timers_speak(timers, monkeypatch):
    spoken: list[bool] = []
    module, _ = timers
    monkeypatch.setattr(
        module, "notify", lambda *a, speak=False, **k: spoken.append(speak) or "id"
    )
    return spoken


@pytest.mark.parametrize("minutes", [0, -5, "", None, "soon", 8 * 24 * 60])
def test_an_unusable_duration_is_refused(timers, minutes):
    module, _ = timers

    with pytest.raises(Exception) as e:
        module.set_timer(minutes)
    assert e.value.code == "TIMER_INVALID_DURATION"


def test_a_numeric_string_duration_is_accepted(timers):
    """Small models pass "10" where the schema says number, constantly."""
    module, _ = timers

    assert module.set_timer("10", "tea").startswith("id=")


# -- reminders ---------------------------------------------------------------


def test_a_reminder_is_scheduled_for_an_iso_time(timers, announced):
    module, _ = timers
    due = time.time() + 3600

    module.set_reminder(_iso(due), "call mum")
    fired = module._schedule().tick(due + 1)

    assert announced.sent == [("REMINDER_DUE", {"text": "call mum"})]
    assert len(fired) == 1


def test_a_reminder_missed_while_jarvis_was_off_is_flagged_late(timers, announced):
    """Fired long after it was due means the machine was asleep or the sidecar
    was down. Saying "reminder: X" for something three hours stale reads as a
    bug; saying when it was due reads as a recovery."""
    module, _ = timers
    due = time.time() + 3600
    module.set_reminder(_iso(due), "call mum")

    module._schedule().tick(due + module.LATE_AFTER_S + 1)

    code, data = announced.sent[0]
    assert code == "REMINDER_DUE_LATE"
    assert data["text"] == "call mum" and data["due"]


def test_a_reminder_that_only_just_came_due_is_not_flagged_late(timers, announced):
    module, _ = timers
    due = time.time() + 3600
    module.set_reminder(_iso(due), "call mum")

    module._schedule().tick(due + 1)

    assert announced.sent[0][0] == "REMINDER_DUE"


@pytest.mark.parametrize("when", ["tomorrow", "", "2026-13-45T99:00", "next tuesday", None])
def test_an_unparseable_time_is_refused(timers, when):
    module, _ = timers

    with pytest.raises(Exception) as e:
        module.set_reminder(when, "call mum")
    assert e.value.code == "REMINDER_TIME_INVALID"


def test_a_time_in_the_past_is_refused(timers):
    module, _ = timers

    with pytest.raises(Exception) as e:
        module.set_reminder(_iso(time.time() - 60), "call mum")
    assert e.value.code == "REMINDER_TIME_IN_PAST"


def test_a_reminder_with_no_text_is_refused(timers):
    module, _ = timers

    with pytest.raises(Exception) as e:
        module.set_reminder(_iso(time.time() + 600), "   ")
    assert e.value.code == "REMINDER_TIME_INVALID"


# -- listing and cancelling --------------------------------------------------


def test_listing_reports_nothing_when_empty(timers):
    module, _ = timers

    assert module.list_timers() == "none"


def test_listing_is_ordered_by_when_it_fires(timers):
    module, _ = timers
    module.set_timer(30, "late")
    module.set_timer(5, "soon")

    lines = module.list_timers().splitlines()

    assert "soon" in lines[0] and "late" in lines[1]


def test_cancelling_removes_it(timers, announced):
    module, _ = timers
    module.set_timer(10, "tea")

    assert "cancelled" in module.cancel_timer("t1")
    assert module.list_timers() == "none"
    module._schedule().tick(time.time() + 601)
    assert announced.sent == []


def test_cancelling_an_unknown_id_is_refused(timers):
    module, _ = timers

    with pytest.raises(Exception) as e:
        module.cancel_timer("t99")
    assert e.value.code == "TIMER_NOT_FOUND"


def test_cancelling_leaves_the_others_alone(timers):
    module, _ = timers
    module.set_timer(10, "one")
    module.set_timer(20, "two")

    module.cancel_timer("t1")

    assert "two" in module.list_timers() and "one" not in module.list_timers()


# -- the cap ------------------------------------------------------------------


def test_the_pending_cap_is_enforced(timers):
    """What makes `safe` defensible: a model in a loop hits a wall, not the
    user's notification centre."""
    module, _ = timers
    for i in range(module.MAX_PENDING):
        module.set_timer(60, f"t{i}")

    with pytest.raises(Exception) as e:
        module.set_timer(60, "one too many")
    assert e.value.code == "TIMER_LIMIT_REACHED"


def test_cancelling_frees_a_slot(timers):
    module, _ = timers
    for i in range(module.MAX_PENDING):
        module.set_timer(60, f"t{i}")
    module.cancel_timer("t1")

    assert module.set_timer(60, "now there is room").startswith("id=")


# -- persistence --------------------------------------------------------------


def test_pending_timers_are_written_to_the_state_dir(timers):
    module, _ = timers
    module.set_timer(10, "tea")

    saved = json.loads((host.state_dir("timers-reminders") / "timers.json").read_text())

    assert [e["text"] for e in saved["pending"]] == ["tea"]


def test_the_state_file_is_not_inside_the_extension_folder(timers, tmp_path):
    """Writing beside extension.py would change the tree digest and silently
    un-approve the extension on the next start. This is that trap, asserted."""
    module, _ = timers
    module.set_timer(10, "tea")

    installed = tmp_path / "data" / "extensions" / "timers-reminders"
    assert not (installed / "timers.json").exists()
    assert sorted(p.name for p in installed.iterdir() if p.is_file()) == [
        "extension.py",
        "manifest.toml",
    ]


def test_writing_state_does_not_change_the_extension_digest(timers, tmp_path):
    """The same property from the other side, and the one that actually
    matters: the digest before and after must be identical."""
    module, _ = timers
    installed = tmp_path / "data" / "extensions" / "timers-reminders"
    before = tree_digest(installed)

    module.set_timer(10, "tea")

    assert tree_digest(installed) == before


def test_a_timer_survives_a_restart(timers, tmp_path):
    module, _ = timers
    due = time.time() + 3600
    module.set_reminder(_iso(due), "call mum")

    reloaded = _reload(module)

    assert "call mum" in reloaded.list_timers()


def test_a_timer_that_came_due_while_off_fires_on_the_next_start(timers, monkeypatch):
    module, _ = timers
    module.set_reminder(_iso(time.time() + 60), "call mum")

    reloaded = _reload(module)
    recorder = Recorder()
    monkeypatch.setattr(reloaded, "notify", recorder)
    reloaded._schedule().tick(time.time() + 3600)

    assert recorder.sent[0][0] == "REMINDER_DUE_LATE"


def test_a_fired_timer_is_gone_from_the_file(timers, announced):
    """Otherwise every restart replays every timer the user ever set."""
    module, _ = timers
    module.set_timer(10, "tea")

    module._schedule().tick(time.time() + 601)

    saved = json.loads((host.state_dir("timers-reminders") / "timers.json").read_text())
    assert saved["pending"] == []


def test_ids_do_not_restart_from_one_after_a_reload(timers):
    """A reused id makes `cancel_timer` cancel the wrong thing."""
    module, _ = timers
    module.set_timer(60, "first")

    reloaded = _reload(module)
    second = reloaded.set_timer(60, "second")

    assert not second.startswith("id=t1 ")


def test_a_restored_timer_wakes_the_scheduler_on_load(timers):
    """**Live-caught in M5.4** — the timer survived a restart and then sat
    there forever.

    The scheduler thread starts when a timer is *added*, and after a restart
    nobody adds one: the schedule is read back from disk and nothing ever looks
    at it again. A reminder set yesterday would never fire, and the user would
    have to set a new timer to wake up the old one.

    Both halves are asserted deliberately. `_SCHEDULE is not None` proves the
    **module body** resumed it — checking after calling `_schedule()` would
    build it here and pass with the fix reverted. `_thread.is_alive()` proves
    that resuming actually starts the poll.
    """
    module, _ = timers
    module.set_timer(60, "survivor")

    reloaded = _reload(module)

    assert reloaded._SCHEDULE is not None, "the module body did not resume the schedule"
    assert reloaded._SCHEDULE._thread is not None
    assert reloaded._SCHEDULE._thread.is_alive()


def test_an_empty_schedule_starts_no_thread(timers):
    """The other half: a user who never set a timer pays for no thread. The
    body still reads the file — it has to, to find out there is nothing."""
    module, _ = timers

    reloaded = _reload(module)

    assert reloaded._SCHEDULE is not None
    assert reloaded._SCHEDULE._thread is None


def test_a_corrupt_state_file_is_an_empty_schedule_not_a_crash(timers):
    module, _ = timers
    (host.state_dir("timers-reminders") / "timers.json").write_text("{ not json")

    reloaded = _reload(module)

    assert reloaded.list_timers() == "none"


def test_a_state_file_with_junk_entries_keeps_only_the_usable_ones(timers):
    module, _ = timers
    (host.state_dir("timers-reminders") / "timers.json").write_text(
        json.dumps(
            {
                "pending": [
                    "not a dict",
                    {"id": "t9", "kind": "timer", "due_at": "soon", "text": "bad"},
                    {
                        "id": "t8",
                        "seq": 8,
                        "kind": "timer",
                        "due_at": time.time() + 99,
                        "text": "ok",
                    },
                ]
            }
        )
    )

    reloaded = _reload(module)

    assert reloaded.list_timers().count("id=") == 1
    assert "ok" in reloaded.list_timers()


# -- helpers ------------------------------------------------------------------


def _iso(epoch: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def _reload(module):
    """A fresh module object reading the same state dir — a restart, without
    one. The loader builds a new module every time, so this matches it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("jarvis_ext_reloaded", module.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    return fresh
