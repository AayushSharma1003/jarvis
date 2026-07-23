"""Taint tracking: once untrusted content is in the conversation, say so.

docs/security-model.md §3. The premise is that delimiters around untrusted
content are labeling, not defense — a model told "the following is untrusted"
can still be talked out of it, because the talking-out-of-it arrives inside the
untrusted part. So the enforcement lives here, in the tool-execution layer,
where a sentence in a file cannot reach.

The mechanism is deliberately small:

  read a file  →  that conversation is tainted, with the file as its source
  tainted      →  every later side-effectful call confirms, saying why, and
                  cannot be covered by "allow for this session"

**Conversation-scoped and sticky for the life of the process**, mirroring the
confirm broker's grants — in memory, never written to disk.

Why sticky across turns, when the raw tool result is *not* replayed to the model
across turns (agent/loop.py drops `role='tool'` rows)? Because the assistant's
own prose about that content **is** replayed. An injected instruction that got
laundered into "the user wants me to send their notes to bob@example.com" lives
on in the transcript long after the file content is gone. The tainted-ness has
to outlive the exchange for the same reason the laundered sentence does.

Not persisted to disk because a restart genuinely clears it: nothing of the
tainted content survives into a new process except whatever the assistant wrote
down, and a fresh process re-earns its trust the same way a fresh session does.
"""

from __future__ import annotations


class TaintTracker:
    """Which conversations have had untrusted content in them, and from where."""

    def __init__(self) -> None:
        self._sources: dict[str, str] = {}

    def taint(self, conversation_id: str, source: str) -> None:
        """Mark a conversation tainted. First source wins.

        Keeping the first rather than the latest is a UX call with a security
        edge: the earliest untrusted thing is the one the user has had the least
        chance to notice, and a dialog that keeps renaming its reason as the
        model reads more files teaches people to stop reading the reason.
        """
        if not conversation_id or not source:
            return
        self._sources.setdefault(conversation_id, source)

    def is_tainted(self, conversation_id: str) -> bool:
        return conversation_id in self._sources

    def source(self, conversation_id: str) -> str:
        """What tainted this conversation, or "" if it is clean.

        The empty string is meaningful downstream: security/permissions.py
        passes this straight through as the confirmation's `reason`, and empty
        means "ordinary confirmation, grants apply as usual".
        """
        return self._sources.get(conversation_id, "")
