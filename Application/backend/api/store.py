"""Server-side conversation storage, keyed by an opaque id.

**The id is a handle, not a credential.** Knowing one must never be enough
to use one: every lookup takes the caller's identity and refuses a
conversation belonging to someone else. Without that check an id guessed,
logged, shared or left in a browser history would be a complete bypass of
AC09 — the gate validated the *owner's* assignment, not the caller's.

`get()` therefore answers "does this caller have this conversation?" and
returns the same "not found" for a wrong id and for someone else's id, for
the same reason `AccessDenied` is non-enumerable: telling them apart turns
an error into an oracle.

**In memory, deliberately and temporarily.** Conversations hold no data
that survives a restart being useful — a transcript is a convenience, not a
record — and a durable store would need its own AC09 story. Bounded two
ways, because an unbounded one is a slow leak: by idle time, and by count
with least-recently-used eviction.

⚠️ **This is per-process.** A second worker will not see the first's
conversations, so the API must run single-process until this moves to
Redis, which is already in the stack for exactly this kind of thing.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

from Application.backend.conversation import Conversation
from Application.backend.session import Session
from pipeline.config import ApiSettings, Settings


@dataclass
class Entry:
    conversation: Conversation
    username: str
    last_used: float = field(default_factory=time.monotonic)


class ConversationStore:
    """Thread-safe, bounded, owner-checked."""

    def __init__(self, config: ApiSettings):
        self._config = config
        self._entries: dict[str, Entry] = {}
        self._lock = threading.Lock()

    # ---- lifecycle ---------------------------------------------------------

    def open(self, session: Session, *, settings: Settings | None = None) -> tuple[str, Conversation]:
        """Store a conversation for an already-authorised session.

        Takes a `Session`, never a username and project code, for the same
        reason the rest of the application does: it is obtainable only from
        the gate, so nothing here can invent one.
        """
        token = secrets.token_urlsafe(24)
        conversation = Conversation(session, settings=settings,
                                    conversation_id=token)
        with self._lock:
            self._evict_locked()
            self._entries[token] = Entry(conversation=conversation,
                                         username=session.username)
        return token, conversation

    def get(self, token: str, username: str) -> Conversation | None:
        """The caller's conversation, or None.

        **None covers three different situations on purpose**: no such id,
        an expired id, and someone else's id. A caller that can tell them
        apart can probe for live conversations belonging to other people.
        """
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            if self._expired(entry):
                self._entries.pop(token, None)
                return None
            if entry.username != username:
                # The id is a handle, not a credential.
                return None
            entry.last_used = time.monotonic()
            return entry.conversation

    def close(self, token: str, username: str) -> bool:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry.username != username:
                return False
            self._entries.pop(token, None)
            return True

    # ---- bounds ------------------------------------------------------------

    def _expired(self, entry: Entry) -> bool:
        idle = time.monotonic() - entry.last_used
        return idle > self._config.conversation_idle_minutes * 60

    def _evict_locked(self) -> None:
        """Drop expired entries, then the least recently used if still full."""
        for token in [t for t, e in self._entries.items() if self._expired(e)]:
            self._entries.pop(token, None)
        while len(self._entries) >= self._config.max_conversations:
            oldest = min(self._entries.items(), key=lambda kv: kv[1].last_used)
            self._entries.pop(oldest[0], None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
