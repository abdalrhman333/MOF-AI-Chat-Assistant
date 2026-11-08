"""A conversation store that outlives the process.

The in-memory store (`store.py`) was always a placeholder: it made the API
single-process and lost every open chat on restart. This is the durable
version, on the Redis that is already in the stack for the pipeline's locks.

**It keeps every guarantee the in-memory store had, and adds one.**

  - **The id is still a handle, not a credential.** Every read is
    owner-checked, and a wrong id, an expired id and someone else's id are
    still indistinguishable to a caller.
  - **⚠️ The gate is re-run on every restore.** This is the new one, and it
    is the whole reason durability needed thinking about rather than just
    a `SET`. A stored conversation carries a project code, and a project
    code is a *claim*, not a permission. In memory that mattered little —
    the process rarely outlived an assignment change. A durable store can
    hand back a conversation opened days ago, after the user was unassigned
    from that project, and serving it would be an AC09 breach with a
    perfectly valid-looking session object.

    So `open_session` runs again on load. It costs one indexed query — the
    SPM read next to it measures 2 ms against a ~2 s turn — and it means a
    revoked assignment takes effect on the user's **next message** rather
    than whenever the conversation happens to expire. That is stricter than
    the in-memory store ever was.

**What is stored, and what is not.** The session identity, the display
names, and each turn's question and reply. Not the `Answer` object: nothing
above this reads it, and it carries the retrieved passages — document text
that has no reason to sit in a second datastore with its own retention.

**TTL is Redis's job, not ours.** Every write sets an expiry and every read
refreshes it, so idle conversations disappear without a sweeper — the
in-memory store needed eviction code precisely because nothing else would
do it.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import secrets

from Application.backend.answer import Citation, Timings
from Application.backend.conversation import Conversation, Reply, Turn
from Application.backend.errors import AccessError, BackendUnavailable
from Application.backend.session import Session, open_session
from pipeline.config import ApiSettings, Settings, get_settings

log = logging.getLogger(__name__)

KEY_PREFIX = "enjaz:conv:"


def _client(settings: Settings):
    import redis
    return redis.Redis.from_url(settings.celery.broker_url)


def _reply_to_dict(reply: Reply) -> dict:
    return {
        "text": reply.text, "outcome": reply.outcome, "intent": reply.intent,
        "language": reply.language, "reply_language": reply.reply_language,
        "reason": reply.reason,
        "timings": reply.timings.as_dict(),
        "follow_ups": list(reply.follow_ups),
        "citations": [
            {"source": c.source, "number": c.number, "filename": c.filename,
             "doc_type": c.doc_type, "page": c.page, "section": c.section,
             "as_of": c.as_of}
            for c in reply.citations
        ],
    }


def _reply_from_dict(raw: dict) -> Reply:
    # `as_dict` includes derived values (`total_ms`, and the first-text
    # numbers added with streaming) which are properties, not fields.
    # Asking the dataclass which names it accepts beats listing them here:
    # the previous version excluded `total_ms` by name and broke the moment
    # a second derived value appeared.
    accepted = {f.name for f in dataclasses.fields(Timings)}
    timings = Timings(**{k: v for k, v in raw.get("timings", {}).items()
                         if k in accepted})
    return Reply(
        text=raw.get("text", ""), outcome=raw.get("outcome", ""),
        intent=raw.get("intent", ""), language=raw.get("language", ""),
        reply_language=raw.get("reply_language", ""),
        reason=raw.get("reason", ""), timings=timings,
        follow_ups=tuple(raw.get("follow_ups", ())),
        citations=[Citation(**c) for c in raw.get("citations", [])],
    )


class RedisConversationStore:
    """Durable, owner-checked, and re-authorised on every read."""

    def __init__(self, config: ApiSettings, *, settings: Settings | None = None,
                 client=None):
        self._config = config
        self._settings = settings or get_settings()
        self._client = client if client is not None else _client(self._settings)

    # ---- lifecycle ---------------------------------------------------------

    @property
    def _ttl(self) -> int:
        return max(60, self._config.conversation_idle_minutes * 60)

    def open(self, session: Session, *, settings: Settings | None = None,
             user_name: str | tuple[str, str] = "",
             ui_language: str = "ar") -> tuple[str, Conversation]:
        token = secrets.token_urlsafe(24)
        conversation = Conversation(session, settings=settings or self._settings,
                                    conversation_id=token, user_name=user_name,
                                    ui_language=ui_language)
        self._save(token, conversation, user_name, ui_language)
        return token, conversation

    def _save(self, token: str, conversation: Conversation,
              user_name, ui_language: str) -> None:
        names = list(user_name) if isinstance(user_name, tuple) else [user_name,
                                                                     user_name]
        payload = {
            "username": conversation.session.username,
            "project_code": conversation.session.project_code,
            "user_name": names,
            "ui_language": ui_language,
            "turns": [{"question": t.question, "reply": _reply_to_dict(t.reply)}
                      for t in conversation.turns],
        }
        try:
            self._client.setex(KEY_PREFIX + token, self._ttl,
                               json.dumps(payload, ensure_ascii=False))
        except Exception as exc:                              # noqa: BLE001
            # A chat that cannot be stored cannot be continued, so this is an
            # outage rather than something to swallow.
            raise BackendUnavailable(
                f"could not store the conversation: {exc}") from exc

    def get(self, token: str, username: str) -> Conversation | None:
        """The caller's conversation, re-authorised, or None.

        `None` still covers "no such id", "expired" and "not yours"
        identically — and now also "the project is no longer theirs", which
        is the same answer for the same reason.
        """
        if not token or not username:
            return None
        try:
            raw = self._client.get(KEY_PREFIX + token)
        except Exception as exc:                              # noqa: BLE001
            raise BackendUnavailable(
                f"could not read the conversation: {exc}") from exc
        if raw is None:
            return None

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            log.warning("discarding an unreadable conversation payload")
            return None

        if payload.get("username") != username:
            # The id is a handle, not a credential.
            return None

        # ⚠️ Re-authorise. The stored project code is a claim the user made
        # when they opened the chat, not a standing permission.
        try:
            session = open_session(payload["username"], payload["project_code"],
                                   settings=self._settings)
        except AccessError:
            log.info("a stored conversation was refused: the project is no "
                     "longer assigned to that user")
            return None

        turns = [Turn(question=t["question"], reply=_reply_from_dict(t["reply"]))
                 for t in payload.get("turns", [])]
        names = payload.get("user_name") or ["", ""]
        conversation = Conversation(
            session, settings=self._settings, conversation_id=token,
            user_name=(names[0], names[-1]),
            ui_language=payload.get("ui_language", "ar"), turns=turns)
        try:
            self._client.expire(KEY_PREFIX + token, self._ttl)
        except Exception:                                     # noqa: BLE001
            pass                                              # a lost refresh is not a failure
        return conversation

    def save(self, token: str, conversation: Conversation) -> None:
        """Persist a conversation after a turn was added."""
        names = conversation._names                            # noqa: SLF001
        self._save(token, conversation, names, "ar")

    def close(self, token: str, username: str) -> bool:
        if self.get(token, username) is None:
            return False
        try:
            self._client.delete(KEY_PREFIX + token)
        except Exception:                                     # noqa: BLE001
            return False
        return True

    def __len__(self) -> int:
        try:
            return sum(1 for _ in self._client.scan_iter(KEY_PREFIX + "*"))
        except Exception:                                     # noqa: BLE001
            return 0

    def clear(self) -> None:
        try:
            for key in self._client.scan_iter(KEY_PREFIX + "*"):
                self._client.delete(key)
        except Exception:                                     # noqa: BLE001
            pass

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:                                     # noqa: BLE001
            return False
