"""The message tree is the product's memory — these tests are the contract."""

import pytest

from jarvis_backend.storage.conversations import ACTIVE_LEAF, Message, StorageError


def _turn(store, cid, user, assistant, parent=ACTIVE_LEAF):
    """Append a turn. The default extends the live branch; pass `parent=None`
    for a root sibling and `parent=<id>` to fork."""
    return store.append_turn(
        cid, [Message("user", user), Message("assistant", assistant)], parent_turn_id=parent
    )


def test_linear_path(store):
    cid = store.create_conversation(title="t")
    t1 = _turn(store, cid, "one", "1")
    t2 = _turn(store, cid, "two", "2")
    path = store.path(cid)
    assert [t.id for t in path] == [t1, t2]
    assert [m.content for t in path for m in t.messages] == ["one", "1", "two", "2"]
    assert store.get_conversation(cid).active_leaf_turn_id == t2


def test_branching_moves_active_leaf_and_preserves_original(store):
    cid = store.create_conversation()
    t1 = _turn(store, cid, "one", "1")
    t2 = _turn(store, cid, "two", "2")
    # Branch: regenerate from t1 (sibling of t2).
    t2b = _turn(store, cid, "two-edited", "2b", parent=t1)
    assert store.get_conversation(cid).active_leaf_turn_id == t2b
    assert [t.id for t in store.path(cid)] == [t1, t2b]
    # The original branch is intact and reachable by explicit leaf.
    assert [t.id for t in store.path(cid, t2)] == [t1, t2]
    assert set(store.siblings(t2)) == {t2, t2b}
    # And we can switch back.
    store.set_active_leaf(cid, t2)
    assert [t.id for t in store.path(cid)] == [t1, t2]


def test_appending_with_no_parent_given_extends_the_active_branch(store):
    cid = store.create_conversation()
    _turn(store, cid, "a", "1")
    r2 = _turn(store, cid, "b", "2")  # ACTIVE_LEAF default
    assert store.path(cid)[-1].id == r2


def test_an_explicit_none_parent_makes_a_root_sibling(store):
    """**Changed in M5.5.** This used to assert that root siblings were not
    expressible — `parent=None` meant "append to the active leaf", so *editing
    the first message of a conversation* had no representation at all.

    `None` now means what it reads as: no parent, i.e. a root turn. "Wherever
    the conversation currently is" moved to the explicit `ACTIVE_LEAF`
    sentinel, which is the default, so every existing caller is unaffected.
    """
    cid = store.create_conversation()
    r1 = _turn(store, cid, "a", "1")

    r1b = _turn(store, cid, "a-edited", "1b", parent=None)

    assert store.siblings(r1) == [r1, r1b]
    assert [t.id for t in store.path(cid)] == [r1b]
    assert [t.id for t in store.path(cid, r1)] == [r1]


def test_the_active_leaf_sentinel_is_not_a_usable_turn_id(store):
    """It must be impossible to smuggle the sentinel in from the wire as a
    string and have it read as "the active leaf"."""
    from jarvis_backend.storage.conversations import ACTIVE_LEAF

    assert not isinstance(ACTIVE_LEAF, str)


def test_parent_from_other_conversation_rejected(store):
    c1 = store.create_conversation()
    c2 = store.create_conversation()
    t1 = _turn(store, c1, "hi", "yo")
    with pytest.raises(StorageError) as e:
        _turn(store, c2, "x", "y", parent=t1)
    assert e.value.code == "PARENT_TURN_MISMATCH"


# -- tip(): where a branch actually is -------------------------------------
#
# Switching to a sibling has to land on that branch's *tip*, not on the fork
# point. Landing on the fork would silently truncate a branch the user had
# continued — the conversation would look like it had lost turns, which is
# exactly what the immutable tree exists to make impossible.


def test_a_turn_with_no_children_is_its_own_tip(store):
    cid = store.create_conversation()
    t1 = _turn(store, cid, "one", "1")

    assert store.tip(t1) == t1


def test_tip_descends_to_the_end_of_a_branch(store):
    cid = store.create_conversation()
    t1 = _turn(store, cid, "one", "1")
    _turn(store, cid, "two", "2")
    t3 = _turn(store, cid, "three", "3")

    assert store.tip(t1) == t3


def test_tip_follows_the_most_recent_child_when_a_branch_forks_again(store):
    """"Where you left off on that branch" — the newest child at each level.
    Any other rule needs per-branch state nobody has asked for."""
    cid = store.create_conversation()
    t1 = _turn(store, cid, "one", "1")
    _turn(store, cid, "two", "2")
    newer = _turn(store, cid, "two-again", "2b", parent=t1)

    assert store.tip(t1) == newer


def test_switching_to_a_sibling_lands_on_its_own_tip_not_the_fork(store):
    """The scenario the whole method exists for: branch A was continued after
    the fork, so coming back to it must show all of it."""
    cid = store.create_conversation()
    t1 = _turn(store, cid, "one", "1")
    a1 = _turn(store, cid, "branch-a", "a1")
    a2 = _turn(store, cid, "still-a", "a2")
    b1 = _turn(store, cid, "branch-b", "b1", parent=t1)

    assert store.tip(a1) == a2
    assert store.tip(b1) == b1

    store.set_active_leaf(cid, store.tip(a1))
    assert [t.id for t in store.path(cid)] == [t1, a1, a2]


def test_tip_of_an_unknown_turn_is_refused(store):
    with pytest.raises(StorageError) as e:
        store.tip("nope")
    assert e.value.code == "TURN_NOT_FOUND"


# -- navigation is not activity ---------------------------------------------


def test_switching_branches_does_not_reorder_the_sidebar(store):
    """The M3.3 tripwire, one layer down: renaming stopped bumping
    `updated_at` because reading a list is not doing work. Moving between
    branches of a conversation you are already in is the same kind of nothing."""
    cid = store.create_conversation()
    t1 = _turn(store, cid, "one", "1")
    _turn(store, cid, "two", "2")
    before = store.get_conversation(cid).updated_at

    store.set_active_leaf(cid, t1, touch=False)

    assert store.get_conversation(cid).updated_at == before
    assert store.get_conversation(cid).active_leaf_turn_id == t1


def test_setting_the_leaf_still_touches_by_default(store):
    """The default keeps the old contract for any other caller."""
    cid = store.create_conversation()
    t1 = _turn(store, cid, "one", "1")
    _turn(store, cid, "two", "2")
    before = store.get_conversation(cid).updated_at

    store.set_active_leaf(cid, t1)

    assert store.get_conversation(cid).updated_at != before


def test_active_leaf_reports_the_current_tip(store):
    cid = store.create_conversation()
    assert store.active_leaf(cid) is None
    t1 = _turn(store, cid, "one", "1")
    assert store.active_leaf(cid) == t1


def test_empty_turn_rejected(store):
    cid = store.create_conversation()
    with pytest.raises(StorageError) as e:
        store.append_turn(cid, [])
    assert e.value.code == "EMPTY_TURN"


def test_unknown_conversation(store):
    with pytest.raises(StorageError) as e:
        store.path("nope")
    assert e.value.code == "CONVERSATION_NOT_FOUND"


def test_immutability_no_update_api(store):
    """The Store exposes no way to change or selectively remove a persisted
    message or turn. `delete_conversation` is the deliberate exception: it drops
    a whole conversation container (user data control), never a piece of one."""
    mutators = [
        m
        for m in dir(store)
        if not m.startswith("_") and ("update" in m or "delete" in m or "remove" in m)
    ]
    assert mutators == ["delete_conversation"]


def test_delete_conversation_removes_turns_and_messages(store):
    cid = store.create_conversation(title="t")
    _turn(store, cid, "one", "1")
    _turn(store, cid, "two", "2")
    other = store.create_conversation(title="keep")
    keep_turn = _turn(store, other, "mine", "ok")

    store.delete_conversation(cid)

    with pytest.raises(StorageError) as e:
        store.get_conversation(cid)
    assert e.value.code == "CONVERSATION_NOT_FOUND"
    assert [c.id for c in store.list_conversations()] == [other]
    # The FKs carry no ON DELETE CASCADE, so a naive delete would have raised;
    # assert the children are really gone and the neighbour is untouched.
    conn = store._conn
    assert conn.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 2
    assert [t.id for t in store.path(other)] == [keep_turn]


def test_delete_conversation_with_branches(store):
    """A branched tree has turns that aren't on the active path — they go too."""
    cid = store.create_conversation()
    t1 = _turn(store, cid, "one", "1")
    _turn(store, cid, "two", "2")
    _turn(store, cid, "two-edited", "2b", parent=t1)  # off-path sibling

    store.delete_conversation(cid)

    conn = store._conn
    assert conn.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 0


def test_delete_unknown_conversation(store):
    with pytest.raises(StorageError) as e:
        store.delete_conversation("nope")
    assert e.value.code == "CONVERSATION_NOT_FOUND"


def test_delete_empty_conversation(store):
    cid = store.create_conversation()
    store.delete_conversation(cid)
    assert store.list_conversations() == []
