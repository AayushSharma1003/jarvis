# Next session prompt — M3.5 Chat management

> Paste the block below into a fresh Claude Code session in this repo.
> Everything outside the block is notes for the human.

---

Continuing the JARVIS project. Read `docs/HANDOFF.md` first — it's the full
orientation (vision, stack, security model, what's done, gotchas, phase plan).
Then read your Claude memory files (auto-loaded): `git-commit-block-workflow`,
`standing-authorization`, `jarvis-project-context`, `phase2-voice-loop`,
`phase3-wake-word`, `phase3-sphere-ui`.

**State of play:** Phase 1 (text chat), Phase 2 (voice loop), and Phase 3's
M3.1 (always-on "Hey Jarvis" wake word + barge-in) and M3.2 (the sphere UI) are
all DONE and verified live by me in the real Tauri app on the 8GB M2. 78 backend
tests green. Do not rebuild any of it.

**This milestone is M3.5 — chat management.** It is sequenced deliberately
BEFORE Phase 4 (agency + security). Do not start Phase 4.

## The problem

Every conversation I've ever had is already persisted in SQLite, but the
frontend can't reach any of it. Each launch starts a fresh chat and the old ones
are invisible. I want what Claude and ChatGPT have: a list of my past chats,
click to switch, start a new one, rename, and delete.

## Scope (all four, plus the backend gap)

1. **List + switch + new chat** — pure frontend; both WS messages already exist.
2. **Rename** — backend `Store.set_title()` exists; needs a WS message + dispatch + UI.
3. **Delete** — **does not exist anywhere.** Needs `Store.delete_conversation()`,
   a WS message + dispatch, and a confirm affordance in the UI.
4. Keep **branch navigation** (sibling/tree UI) OUT of scope — that stays in
   Phase 5. `Store.siblings()` already exists; don't surface it yet.

## Exact surface (verified 2026-07-19 — trust this over your assumptions)

**Backend, already working:**
- `Store` (`backend/jarvis_backend/storage/conversations.py`): `create_conversation`,
  `get_conversation`, `get_system_prompt`, `list_conversations` (ordered by
  `updated_at DESC`), `set_title`, `append_turn`, `set_active_leaf`, `path`,
  `siblings`. **No delete of any kind.**
- WS (`backend/jarvis_backend/server/app.py`, `_dispatch`): `conversations.list`
  → `{type:"conversations", conversations:[{id,title,created_at,updated_at}]}`;
  `conversation.history` → `{type:"history", conversation_id, turns:[{id,
  parent_turn_id, messages:[{id,role,content}]}]}`. Both are implemented and
  **the frontend never calls either one.**
- Titles are auto-set to the first 80 chars of the first message (in both
  `_generate` and `run_voice_exchange`) — which is exactly why rename matters.

**Frontend, the gap:**
- `app/src/App.tsx` renders only `<ChatView/>`.
- `app/src/state/conversation.ts` is the zustand store; it boots with
  `conversationId: null`, never sends `conversations.list`, and `handleMessage`
  has no `conversations`/`history` cases.
- `app/src/lib/types.ts` mirrors the protocol — keep both sides in sync.
  Note `HistoryMessage.role` includes `"tool"` but `UiMessage` is only
  `user|assistant`; loading history has to handle that.

## The delete trap (this WILL bite you — I verified it)

`schema.sql` declares the FKs **without `ON DELETE CASCADE`**, and `db.py:19`
sets `PRAGMA foreign_keys = ON`. So `DELETE FROM conversations WHERE id=?`
**fails on the FK constraint.** Do ordered deletes inside one transaction:
messages (by their turns) → turns → the conversation row.

Do **not** "fix" this by editing `schema.sql` to add CASCADE: it uses
`CREATE TABLE IF NOT EXISTS`, so my existing database (which has my real chat
history in it) would never pick the change up, and there is no migration
framework (`SCHEMA_VERSION = "1"`).

Also: `conversations.py`'s module docstring currently says "There is deliberately
no update or delete for turns/messages", and `docs/architecture.md` makes the
same immutability promise. Deleting a whole conversation *container* is
defensible user-data control rather than a breach — but say so explicitly in
both places, or the code contradicts its own documentation.

## Constraints that are easy to break

- **i18n is a hard rule**: the backend emits machine-readable error CODES only;
  the frontend owns all wording via `app/src/i18n/en.json`. No hardcoded UI
  strings, no English in backend responses.
- **Keep the text AND voice paths working throughout.** A spoken turn persists
  through the same conversation as a typed one.
- **Don't break the sphere.** `SphereOrb` decides docked vs. centered from
  `messages.length` / `streamingText`; switching to an empty conversation should
  re-center the orb, and switching to a full one should dock it. Verify this
  still feels right rather than assuming.
- Watch the in-flight generation: decide what switching or deleting does while a
  reply is streaming (the connection allows one generation at a time and answers
  `BUSY`). Deleting the conversation you're currently generating into is the
  sharp edge — handle it deliberately.
- Add backend tests for every new WS message (`backend/tests/test_ws.py` has the
  patterns; keep all 78 existing tests green).

## How I work with you

- You're technical lead; I'm product owner. **Push back** with honest technical
  judgment instead of complying — especially on the immutability tension, on
  anything that risks the existing voice/wake/sphere behavior, and on scope.
  Cutting scope is a valid answer.
- Standing authorization applies (memory: `standing-authorization`): improvise
  improvements without asking, priority order security → reliability →
  cross-platform → latency/smoothness → UX → code quality → community. Small
  changes: just do them and note them. Large ones: pause and ask.
- **I run all git myself.** Never run git commands. At milestone end, emit the
  "📦 Milestone Commit" block in my format (files changed, 2–4 line summary,
  then a bash block with `cd`, `git add .`, `git status`, `git commit -m`,
  `git log --oneline -5`).
- Verify in the real app before calling anything done, and tell me plainly what
  you did and did not verify.

**Confirm the plan with me before you start building** — especially the sidebar
vs. other list UI, and how delete should behave (confirm dialog? undo?).

---

## Notes for the human (not part of the prompt)

- Also queued, deliberately not in this milestone: **M3.3** (RAM tiering
  surfacing + onboarding v1), then **Phase 4** (agency + security).
- **Known issue** worth mentioning whenever tools come up: with no tools wired,
  the model confabulates actions — it claimed "Starting your playlist now" and
  named a song it can't play. Real tools are Phase 4; a one-line guard in
  `agent/prompts.py` (never claim to have performed an action you cannot) is a
  cheap interim fix.
