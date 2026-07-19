"""The message tree is the product's memory — these tests are the contract."""

import pytest

from jarvis_backend.storage.conversations import Message, StorageError


def _turn(store, cid, user, assistant, parent=None):
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


def test_root_branching(store):
    cid = store.create_conversation()
    r1 = _turn(store, cid, "a", "1")
    r2 = _turn(store, cid, "b", "2", parent=None)  # None = append to active leaf (r1)
    assert store.path(cid)[-1].id == r2
    # An explicit root sibling requires a fresh conversation-level branch:
    # emulate "edit the first message" by appending a turn with no parent.
    # Not supported via parent=None (that means active leaf), so root siblings
    # are created by set_active_leaf + explicit parent chain in higher layers.
    assert store.siblings(r1) == [r1]


def test_parent_from_other_conversation_rejected(store):
    c1 = store.create_conversation()
    c2 = store.create_conversation()
    t1 = _turn(store, c1, "hi", "yo")
    with pytest.raises(StorageError) as e:
        _turn(store, c2, "x", "y", parent=t1)
    assert e.value.code == "PARENT_TURN_MISMATCH"


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
