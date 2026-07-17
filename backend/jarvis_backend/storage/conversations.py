"""Conversation store: append-only operations over the turn-grouped message tree.

There is deliberately no update or delete for turns/messages — immutability is a
schema-level promise (docs/architecture.md). Editing a message in the UI means
appending a sibling turn and moving the active leaf.
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
            " FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        return [ConversationSummary(**r) for r in rows]

    def set_title(self, conversation_id: str, title: str) -> None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now(), conversation_id),
            )
        if cur.rowcount == 0:
            raise StorageError("CONVERSATION_NOT_FOUND", conversation_id)

    # -- turns --------------------------------------------------------------

    def append_turn(
        self,
        conversation_id: str,
        messages: list[Message],
        parent_turn_id: str | None = None,
        *,
        make_active: bool = True,
    ) -> str:
        """Append one atomic turn. parent_turn_id=None appends to the current
        active leaf; branching passes an explicit earlier turn id."""
        if not messages:
            raise StorageError("EMPTY_TURN")
        conv = self.get_conversation(conversation_id)
        if parent_turn_id is None:
            parent_turn_id = conv.active_leaf_turn_id
        elif self._turn_conversation(parent_turn_id) != conversation_id:
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

    def set_active_leaf(self, conversation_id: str, turn_id: str) -> None:
        if self._turn_conversation(turn_id) != conversation_id:
            raise StorageError("PARENT_TURN_MISMATCH", turn_id)
        with self._conn:
            self._conn.execute(
                "UPDATE conversations SET active_leaf_turn_id = ?, updated_at = ? WHERE id = ?",
                (turn_id, _now(), conversation_id),
            )

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
                " ORDER BY created_at, id",
                (row["conversation_id"],),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id FROM turns WHERE parent_turn_id = ? ORDER BY created_at, id",
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
