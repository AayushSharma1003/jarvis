"""Conversation store: append-only operations over the turn-grouped message tree.

There is deliberately no update or delete for turns/messages — immutability is a
schema-level promise (docs/architecture.md). Editing a message in the UI means
appending a sibling turn and moving the active leaf.

The one exception is `delete_conversation`, which removes a whole conversation
*container* and everything under it. That is user control over their own data,
not a hole in the immutability promise: no turn or message is ever rewritten or
selectively removed, and a conversation is either wholly present or wholly gone.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


class StorageError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class _ActiveLeaf:
    """Sentinel type for ACTIVE_LEAF. Not a str, and that is the point."""

    def __repr__(self) -> str:  # pragma: no cover - debugging affordance
        return "ACTIVE_LEAF"


# "Wherever this conversation currently is", as a parent for a new turn.
#
# **This used to be what `None` meant, and the change is what makes editing the
# first message of a conversation expressible at all** (M5.5). With `None`
# overloaded, there was no way to say "a turn with no parent" — so a root
# sibling, which is exactly what editing the opening question produces, could
# not be represented. `test_root_branching` said as much in a comment and
# asserted the limitation.
#
# `None` now means what it reads as: no parent, a root turn. This sentinel is
# the default, so no caller that omits the argument changes behaviour, and it
# is deliberately **not a string** — a turn id arriving from the wire can never
# be mistaken for it.
ACTIVE_LEAF = _ActiveLeaf()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Message:
    role: str  # 'user' | 'assistant' | 'tool'
    content: str
    id: str = field(default_factory=_new_id)


@dataclass(frozen=True)
class Turn:
    id: str
    conversation_id: str
    parent_turn_id: str | None
    created_at: str
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class ConversationSummary:
    id: str
    title: str | None
    created_at: str
    updated_at: str
    active_leaf_turn_id: str | None


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # -- conversations ------------------------------------------------------

    def create_conversation(
        self, title: str | None = None, system_prompt: str | None = None
    ) -> str:
        cid = _new_id()
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO conversations (id, title, system_prompt, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (cid, title, system_prompt, now, now),
            )
        return cid

    def get_conversation(self, conversation_id: str) -> ConversationSummary:
        row = self._conn.execute(
            "SELECT id, title, created_at, updated_at, active_leaf_turn_id"
            " FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise StorageError("CONVERSATION_NOT_FOUND", conversation_id)
        return ConversationSummary(**row)

    def get_system_prompt(self, conversation_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT system_prompt FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            raise StorageError("CONVERSATION_NOT_FOUND", conversation_id)
        return row["system_prompt"]

    def list_conversations(self) -> list[ConversationSummary]:
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at, active_leaf_turn_id"
            # rowid breaks a tie here too: two conversations created in the
            # same instant would otherwise sort by random uuid, so the sidebar
            # order would change between launches.
            " FROM conversations ORDER BY updated_at DESC, rowid DESC"
        ).fetchall()
        return [ConversationSummary(**r) for r in rows]

    def set_title(self, conversation_id: str, title: str, *, touch: bool = True) -> None:
        """Rename a conversation.

        `touch=False` leaves `updated_at` alone. The sidebar sorts by it, so a
        rename would otherwise jump the conversation to the top — but renaming
        is not activity, and every other assistant sorts by last *activity*.
        Appending turns still bumps it, which is the behaviour that matters.
        """
        sql = "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?"
        params: tuple = (title, _now(), conversation_id)
        if not touch:
            sql = "UPDATE conversations SET title = ? WHERE id = ?"
            params = (title, conversation_id)
        with self._conn:
            cur = self._conn.execute(sql, params)
        if cur.rowcount == 0:
            raise StorageError("CONVERSATION_NOT_FOUND", conversation_id)

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation and everything beneath it, atomically.

        The FKs in schema.sql have no ON DELETE CASCADE and db.py enables
        `PRAGMA foreign_keys = ON`, so a bare DELETE on conversations fails the
        constraint — children must go first, in one transaction. Adding CASCADE
        to schema.sql would NOT help: it uses CREATE TABLE IF NOT EXISTS, so
        databases that already exist would never pick the change up, and there
        is no migration framework.
        """
        with self._conn:
            self._conn.execute(
                "DELETE FROM messages WHERE turn_id IN"
                " (SELECT id FROM turns WHERE conversation_id = ?)",
                (conversation_id,),
            )
            self._conn.execute("DELETE FROM turns WHERE conversation_id = ?", (conversation_id,))
            cur = self._conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
        if cur.rowcount == 0:
            raise StorageError("CONVERSATION_NOT_FOUND", conversation_id)

    # -- turns --------------------------------------------------------------

    def append_turn(
        self,
        conversation_id: str,
        messages: list[Message],
        parent_turn_id: str | None | _ActiveLeaf = ACTIVE_LEAF,
        *,
        make_active: bool = True,
    ) -> str:
        """Append one atomic turn.

        Three distinct parents, and keeping them distinct is what makes editing
        the first message possible (see ACTIVE_LEAF):

            ACTIVE_LEAF (default)  extend whatever branch is live
            None                   a root turn — no parent at all
            "<turn id>"            fork from that turn
        """
        if not messages:
            raise StorageError("EMPTY_TURN")
        conv = self.get_conversation(conversation_id)
        if isinstance(parent_turn_id, _ActiveLeaf):
            parent_turn_id = conv.active_leaf_turn_id
        elif parent_turn_id is not None:
            if self._turn_conversation(parent_turn_id) != conversation_id:
                raise StorageError("PARENT_TURN_MISMATCH", parent_turn_id)

        tid = _new_id()
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO turns (id, conversation_id, parent_turn_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (tid, conversation_id, parent_turn_id, now),
            )
            self._conn.executemany(
                "INSERT INTO messages (id, turn_id, idx, role, content, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(m.id, tid, i, m.role, m.content, now) for i, m in enumerate(messages)],
            )
            if make_active:
                self._conn.execute(
                    "UPDATE conversations SET active_leaf_turn_id = ?, updated_at = ?"
                    " WHERE id = ?",
                    (tid, now, conversation_id),
                )
        return tid

    def active_leaf(self, conversation_id: str) -> str | None:
        """The turn the live branch currently ends at. None on an empty chat.

        Exposed so a caller can resolve the leaf **once** and keep using that
        answer — see run_exchange, where reading it twice was a race.
        """
        return self.get_conversation(conversation_id).active_leaf_turn_id

    def set_active_leaf(self, conversation_id: str, turn_id: str, *, touch: bool = True) -> None:
        """Point the conversation at a different leaf.

        `touch=False` moves the leaf without bumping `updated_at`, for the same
        reason `set_title` grew the flag in M3.3: the sidebar is ordered by last
        *activity*, and moving between branches of a conversation you are
        already reading is navigation, not work. The default keeps the original
        contract for every other caller.
        """
        if self._turn_conversation(turn_id) != conversation_id:
            raise StorageError("PARENT_TURN_MISMATCH", turn_id)
        with self._conn:
            if touch:
                self._conn.execute(
                    "UPDATE conversations SET active_leaf_turn_id = ?, updated_at = ?"
                    " WHERE id = ?",
                    (turn_id, _now(), conversation_id),
                )
            else:
                self._conn.execute(
                    "UPDATE conversations SET active_leaf_turn_id = ? WHERE id = ?",
                    (turn_id, conversation_id),
                )

    def tip(self, turn_id: str) -> str:
        """The end of the branch this turn is on — itself if it has no children.

        Switching to a sibling has to land *here*, not on the turn that was
        clicked. Branch A being `T1→T2a→T3→T4`, choosing `T2a` must show `T4`:
        landing on `T2a` would present a conversation that appears to have lost
        its last two turns, which is precisely what an immutable tree exists to
        make impossible.

        Where a branch itself forks again, the **most recently created** child
        wins — "where you left off on that branch". Any finer rule needs
        per-branch state persisted somewhere, and nobody has asked for a
        conversation to remember more than one position per branch.
        """
        self._turn_conversation(turn_id)  # raises TURN_NOT_FOUND
        cursor = turn_id
        seen = {cursor}
        while True:
            row = self._conn.execute(
                # rowid, not id, as the tie-break: `id` is a random uuid4, so
                # two turns sharing a `created_at` — routine on Windows, whose
                # clock is far coarser than macOS's — picked a branch by coin
                # flip. rowid is insertion order and needs no schema change,
                # which matters because schema.sql has no migration path.
                "SELECT id FROM turns WHERE parent_turn_id = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (cursor,),
            ).fetchone()
            if row is None:
                return cursor
            if row["id"] in seen:  # corrupt data guard, same as path()
                raise StorageError("TREE_CYCLE", row["id"])
            cursor = row["id"]
            seen.add(cursor)

    def path(self, conversation_id: str, leaf_turn_id: str | None = None) -> list[Turn]:
        """The root→leaf list of turns for the active (or given) leaf."""
        if leaf_turn_id is None:
            leaf_turn_id = self.get_conversation(conversation_id).active_leaf_turn_id
            if leaf_turn_id is None:
                return []
        chain: list[str] = []
        seen: set[str] = set()
        cursor: str | None = leaf_turn_id
        while cursor is not None:
            if cursor in seen:  # corrupt data guard; a healthy tree cannot cycle
                raise StorageError("TREE_CYCLE", cursor)
            seen.add(cursor)
            row = self._conn.execute(
                "SELECT id, conversation_id, parent_turn_id FROM turns WHERE id = ?",
                (cursor,),
            ).fetchone()
            if row is None:
                raise StorageError("TURN_NOT_FOUND", cursor)
            if row["conversation_id"] != conversation_id:
                raise StorageError("PARENT_TURN_MISMATCH", cursor)
            chain.append(row["id"])
            cursor = row["parent_turn_id"]
        chain.reverse()
        return [self._load_turn(tid) for tid in chain]

    def siblings(self, turn_id: str) -> list[str]:
        """Turn ids sharing this turn's parent (branch alternatives), oldest first."""
        row = self._conn.execute(
            "SELECT conversation_id, parent_turn_id FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
        if row is None:
            raise StorageError("TURN_NOT_FOUND", turn_id)
        if row["parent_turn_id"] is None:
            rows = self._conn.execute(
                "SELECT id FROM turns WHERE conversation_id = ? AND parent_turn_id IS NULL"
                " ORDER BY created_at, rowid",
                (row["conversation_id"],),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id FROM turns WHERE parent_turn_id = ? ORDER BY created_at, rowid",
                (row["parent_turn_id"],),
            ).fetchall()
        return [r["id"] for r in rows]

    # -- internal -----------------------------------------------------------

    def _turn_conversation(self, turn_id: str) -> str:
        row = self._conn.execute(
            "SELECT conversation_id FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
        if row is None:
            raise StorageError("TURN_NOT_FOUND", turn_id)
        return row["conversation_id"]

    def _load_turn(self, turn_id: str) -> Turn:
        trow = self._conn.execute(
            "SELECT id, conversation_id, parent_turn_id, created_at FROM turns WHERE id = ?",
            (turn_id,),
        ).fetchone()
        mrows = self._conn.execute(
            "SELECT id, role, content FROM messages WHERE turn_id = ? ORDER BY idx",
            (turn_id,),
        ).fetchall()
        return Turn(
            id=trow["id"],
            conversation_id=trow["conversation_id"],
            parent_turn_id=trow["parent_turn_id"],
            created_at=trow["created_at"],
            messages=tuple(
                Message(id=m["id"], role=m["role"], content=m["content"]) for m in mrows
            ),
        )
