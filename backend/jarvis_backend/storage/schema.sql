-- Normative schema. See docs/architecture.md: messages are IMMUTABLE rows in a
-- turn-grouped tree. A "turn" is the atomic branching unit: one user message
-- plus the full assistant response span (including, later, tool calls/results).
-- Branching = inserting a new turn with the same parent_turn_id. Rows are never
-- updated or deleted; conversations.active_leaf_turn_id selects the live path.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id                  TEXT PRIMARY KEY,
    title               TEXT,
    system_prompt       TEXT,
    active_leaf_turn_id TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    parent_turn_id  TEXT REFERENCES turns(id),
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id);
CREATE INDEX IF NOT EXISTS idx_turns_parent ON turns(parent_turn_id);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    turn_id    TEXT NOT NULL REFERENCES turns(id),
    idx        INTEGER NOT NULL,  -- order within the turn
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (turn_id, idx)
);
