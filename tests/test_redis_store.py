"""Tests for the durable conversation store.

**The claim that needed a durable store to be thought about, rather than
just written, is re-authorisation.** A stored conversation carries a project
code, and a project code is a claim the user made when they opened the chat
— not a standing permission. In memory that barely mattered; a process
rarely outlived an assignment change. A durable store can hand back a
conversation opened days ago, and serving it after the user was unassigned
would be an AC09 breach behind a perfectly valid-looking session.

So the gate runs again on every read, and these tests prove it does.

Everything else the in-memory store guaranteed still has to hold: the id is
a handle rather than a credential, and a wrong id, an expired id, someone
else's id and a now-forbidden project are all the same answer.

Redis is faked here — the point is the store's logic, not the driver's.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from Application.backend.api import redis_store
from Application.backend.api.redis_store import RedisConversationStore
from Application.backend.conversation import Reply, Turn
from Application.backend.errors import AccessDenied, BackendUnavailable
from Application.backend.session import Session
from pipeline.config import get_settings

SETTINGS = get_settings()
OWNER = "noura.alharbi"
STRANGER = "mallory.attacker"
SESSION = Session(username=OWNER, project_code="PRJ-06")


class FakeRedis:
    """Enough Redis to exercise the store, and nothing more."""

    def __init__(self, fail=False):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.fail = fail

    def setex(self, key, ttl, value):
        if self.fail:
            raise ConnectionError("redis down")
        self.data[key] = value
        self.ttl[key] = ttl

    def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.data.get(key)

    def expire(self, key, ttl):
        self.ttl[key] = ttl

    def delete(self, key):
        self.data.pop(key, None)

    def scan_iter(self, _pattern):
        return list(self.data)

    def ping(self):
        if self.fail:
            raise ConnectionError("redis down")
        return True


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(redis_store, "open_session",
                        lambda u, p, **k: Session(username=u, project_code=p))
    return RedisConversationStore(SETTINGS.api, settings=SETTINGS,
                                  client=FakeRedis())


def with_api(**overrides):
    return dataclasses.replace(
        SETTINGS, api=dataclasses.replace(SETTINGS.api, **overrides))


# ---------------------------------------------------------------------------
# 1. Re-authorisation — the reason this file exists
# ---------------------------------------------------------------------------

def test_the_gate_runs_again_on_every_read(store, monkeypatch):
    calls = []
    monkeypatch.setattr(redis_store, "open_session",
                        lambda u, p, **k: calls.append((u, p))
                        or Session(username=u, project_code=p))
    token, _ = store.open(SESSION)
    store.get(token, OWNER)
    store.get(token, OWNER)
    assert calls == [(OWNER, "PRJ-06"), (OWNER, "PRJ-06")], \
        "a stored project code must be re-authorised, not trusted"


def test_a_revoked_assignment_stops_the_conversation_working(store, monkeypatch):
    """The failure a durable store newly makes possible: a chat opened
    before the user was unassigned, replayed after."""
    token, _ = store.open(SESSION)
    assert store.get(token, OWNER) is not None

    def revoked(username, project_code, **_kw):
        raise AccessDenied("not_assigned", username, project_code)

    monkeypatch.setattr(redis_store, "open_session", revoked)
    assert store.get(token, OWNER) is None


def test_a_revoked_project_is_indistinguishable_from_a_wrong_id(store, monkeypatch):
    """Same answer for the same reason as everything else here — a caller
    who can tell them apart learns what they used to be allowed to see."""
    token, _ = store.open(SESSION)
    monkeypatch.setattr(
        redis_store, "open_session",
        lambda u, p, **k: (_ for _ in ()).throw(AccessDenied("gone", u, p)))
    assert store.get(token, OWNER) is None
    assert store.get("no-such-token", OWNER) is None


# ---------------------------------------------------------------------------
# 2. Everything the in-memory store guaranteed
# ---------------------------------------------------------------------------

def test_the_owner_can_read_their_own_conversation(store):
    token, _ = store.open(SESSION)
    restored = store.get(token, OWNER)
    assert restored is not None
    assert restored.project_code == "PRJ-06"


def test_another_user_cannot_read_it(store):
    token, _ = store.open(SESSION)
    assert store.get(token, STRANGER) is None


def test_another_user_cannot_close_it(store):
    token, _ = store.open(SESSION)
    assert store.close(token, STRANGER) is False
    assert store.get(token, OWNER) is not None


def test_the_owner_can_close_it(store):
    token, _ = store.open(SESSION)
    assert store.close(token, OWNER) is True
    assert store.get(token, OWNER) is None


def test_ids_are_unguessable(store):
    tokens = {store.open(SESSION)[0] for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(t) >= 24 for t in tokens)


@pytest.mark.parametrize("token,username", [("", OWNER), ("t", ""), ("", "")])
def test_a_blank_token_or_user_is_refused_without_a_lookup(store, token, username):
    assert store.get(token, username) is None


# ---------------------------------------------------------------------------
# 3. The transcript survives a restart
# ---------------------------------------------------------------------------

def test_turns_round_trip_through_storage(store):
    token, conversation = store.open(SESSION)
    conversation._turns.append(Turn(                                # noqa: SLF001
        question="ما الميزانية؟",
        reply=Reply(text="22,900,000 ريال", outcome="answered",
                    intent="project_question", language="ar",
                    reply_language="ar")))
    store.save(token, conversation)

    restored = store.get(token, OWNER)
    assert [t.question for t in restored.turns] == ["ما الميزانية؟"]
    assert restored.turns[0].reply.text == "22,900,000 ريال"
    assert restored.turns[0].reply.intent == "project_question"


def test_a_restored_conversation_still_feeds_history_to_the_prompt(store):
    """A transcript that survives but cannot be used as history would be a
    transcript that only looks restored."""
    token, conversation = store.open(SESSION)
    conversation._turns.append(Turn(                                # noqa: SLF001
        question="what is the budget?",
        reply=Reply(text="22,900,000 SAR", outcome="answered",
                    intent="project_question")))
    store.save(token, conversation)
    restored = store.get(token, OWNER)
    assert restored.history_for_prompt() == [("what is the budget?",
                                              "22,900,000 SAR")]


def test_the_stored_payload_does_not_carry_retrieved_passages(store):
    """Document text has no reason to sit in a second datastore with its
    own retention — nothing above the store reads the Answer object."""
    token, conversation = store.open(SESSION)
    conversation._turns.append(Turn(                                # noqa: SLF001
        question="q",
        reply=Reply(text="a", outcome="answered", intent="project_question")))
    store.save(token, conversation)
    raw = store._client.data[redis_store.KEY_PREFIX + token]        # noqa: SLF001
    assert "passage" not in raw.lower()
    assert "answer" not in json.loads(raw)["turns"][0]


# ---------------------------------------------------------------------------
# 4. Bounds and failure
# ---------------------------------------------------------------------------

def test_every_write_sets_an_expiry(store):
    """TTL is Redis's job — the in-memory store needed eviction code only
    because nothing else would do it."""
    token, _ = store.open(SESSION)
    assert store._client.ttl[redis_store.KEY_PREFIX + token] > 0     # noqa: SLF001


def test_reading_refreshes_the_expiry(store):
    token, _ = store.open(SESSION)
    store._client.ttl[redis_store.KEY_PREFIX + token] = 1            # noqa: SLF001
    store.get(token, OWNER)
    assert store._client.ttl[redis_store.KEY_PREFIX + token] > 1     # noqa: SLF001


def test_an_unreachable_redis_is_an_outage_not_an_empty_result():
    """Returning None would read as "no such conversation" and send the
    user to start a new chat that also would not save."""
    store = RedisConversationStore(SETTINGS.api, settings=SETTINGS,
                                   client=FakeRedis(fail=True))
    with pytest.raises(BackendUnavailable):
        store.open(SESSION)


def test_an_unreadable_payload_is_discarded_not_raised(store):
    token, _ = store.open(SESSION)
    store._client.data[redis_store.KEY_PREFIX + token] = "{not json"  # noqa: SLF001
    assert store.get(token, OWNER) is None


def test_build_store_refuses_to_downgrade_to_memory(monkeypatch):
    """Silently falling back would work on one machine and then split
    conversations across workers the moment a second one started."""
    from Application.backend.api import store as store_module

    monkeypatch.setattr(redis_store.RedisConversationStore, "ping",
                        lambda self: False)
    monkeypatch.setattr(redis_store, "_client", lambda s: FakeRedis())
    with pytest.raises(BackendUnavailable, match="unreachable"):
        store_module.build_store(with_api(conversation_store="redis").api,
                                 settings=SETTINGS)


def test_build_store_honours_an_explicit_memory_choice():
    from Application.backend.api import store as store_module

    built = store_module.build_store(with_api(conversation_store="memory").api,
                                     settings=SETTINGS)
    assert isinstance(built, store_module.ConversationStore)


def test_an_empty_store_is_not_falsy_to_a_caller(store):
    """Found by running it. Both stores define `__len__`, so an EMPTY store
    is falsy — and `if STORE:` in the health route reported "not started"
    for a store that had started perfectly well and simply had no
    conversations yet. Anything asking whether the store exists must ask
    `is not None`.
    """
    assert len(store) == 0
    assert not store, "an empty store IS falsy — that is the trap"
    assert store is not None


def test_health_reports_a_started_store_with_no_conversations(monkeypatch):
    """The same bug, at the level it actually showed up."""
    from fastapi.testclient import TestClient

    from Application.backend.api import app as api_app
    from Application.backend.api import store as store_module

    empty = store_module.ConversationStore(SETTINGS.api)
    assert len(empty) == 0
    monkeypatch.setattr(api_app, "STORE", empty)
    body = TestClient(api_app.app).get("/health").json()
    assert body["conversation_store"] == "ConversationStore"
    assert body["conversations"] == 0
