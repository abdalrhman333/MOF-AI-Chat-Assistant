"""Tests for the HTTP layer.

**The HTTP layer adds no behaviour, so these test the things it could
newly break.** Three of them, and the middle one is the reason this file
is longer than the routes:

  1. **Identity never comes from the request.** A body or query field
     naming a user would make AC09 one `curl` away from meaningless.
  2. **A conversation id is a handle, not a credential.** Guessing, seeing
     or being given one must not let a caller use it — the gate validated
     the *owner's* assignment, not theirs.
  3. **Refusals do not become an oracle.** "Not yours" and "does not
     exist" must be indistinguishable to a client, or a 403 enumerates the
     ministry's project list.

The gate, retrieval and the model are stubbed: what is under test is the
routing and the boundary, not work already covered by 542 other tests.
"""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from Application.backend import conversation as conv
from Application.backend.api import app as api_app
from Application.backend.api import identity as identity_module
from Application.backend.api.store import ConversationStore
from Application.backend.errors import AccessDenied, BackendUnavailable
from Application.backend.session import AssignedProject, Session
from pipeline.config import get_settings

SETTINGS = get_settings()
OWNER = "noura.alharbi"
STRANGER = "mallory.attacker"


def api_settings(**overrides):
    base = dict(auth_mode="dev_fixed", dev_username=OWNER)
    base.update(overrides)
    return dataclasses.replace(SETTINGS,
                               api=dataclasses.replace(SETTINGS.api, **base))


@pytest.fixture
def client(monkeypatch):
    """A client with the gate, the switcher and the model all stubbed."""
    settings = api_settings()
    monkeypatch.setattr(api_app, "SETTINGS", settings)
    monkeypatch.setattr(api_app, "STORE", ConversationStore(settings.api))

    def fake_open_session(username, project_code, **_kw):
        if project_code != "PRJ-06":
            raise AccessDenied("not_assigned", username, project_code)
        return Session(username=username, project_code=project_code)

    def fake_projects(username, **_kw):
        return [AssignedProject("PRJ-06", "مشروع", "Project Six", "active")]

    def fake_ask(self, text, **_kw):
        reply = conv.Reply(text=f"echo: {text}",
                           outcome=conv.OUTCOME_ANSWERED,
                           intent="general_retrieval", language="en",
                           reply_language="en")
        self._turns.append(conv.Turn(question=text, reply=reply))
        return reply

    monkeypatch.setattr(api_app, "open_session", fake_open_session)
    monkeypatch.setattr(api_app, "list_assigned_projects", fake_projects)
    monkeypatch.setattr(conv.Conversation, "ask", fake_ask)
    # No lifespan: startup would warm real clients and open real sockets.
    return TestClient(api_app.app)


def open_chat(client, project="PRJ-06"):
    response = client.post("/api/conversations", json={"project_code": project})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


# ---------------------------------------------------------------------------
# 1. Identity never comes from the request
# ---------------------------------------------------------------------------

def test_no_request_model_has_a_username_field():
    """Structural, not behavioural: the surest way to never accept a
    username from a client is to have nowhere to put one."""
    from Application.backend.api import schemas

    for name in ("OpenConversationIn", "AskIn"):
        fields = set(getattr(schemas, name).model_fields)
        assert not {"username", "user", "user_id", "as_user"} & fields, name


def test_a_username_in_the_body_is_ignored(client, monkeypatch):
    seen = []
    monkeypatch.setattr(api_app, "list_assigned_projects",
                        lambda username, **kw: seen.append(username) or [])
    client.get("/api/projects", params={"username": STRANGER})
    assert seen == [OWNER], "the query string must not choose the identity"


def test_dev_fixed_mode_ignores_an_identity_header(client, monkeypatch):
    """In dev_fixed the header is not a source of identity at all."""
    seen = []
    monkeypatch.setattr(api_app, "list_assigned_projects",
                        lambda username, **kw: seen.append(username) or [])
    client.get("/api/projects", headers={"X-Remote-User": STRANGER})
    assert seen == [OWNER]


def test_dev_fixed_with_no_username_configured_refuses(monkeypatch):
    """Fail closed: a blank identity must not reach the gate as ''."""
    with pytest.raises(AccessDenied) as caught:
        identity_module.resolve({}, api_settings(dev_username="").api)
    assert caught.value.reason == "no_identity_configured"


def test_trusted_header_mode_reads_the_configured_header():
    who = identity_module.resolve(
        {"X-Remote-User": "fahad.alqahtani"},
        api_settings(auth_mode="trusted_header").api)
    assert who.username == "fahad.alqahtani"
    assert "trusted_header" in who.source


def test_trusted_header_mode_refuses_when_the_header_is_absent():
    with pytest.raises(AccessDenied) as caught:
        identity_module.resolve({}, api_settings(auth_mode="trusted_header").api)
    assert caught.value.reason == "no_identity_header"


def test_an_unknown_auth_mode_refuses_everything():
    """A configuration error in an auth path means nobody is authenticated."""
    with pytest.raises(AccessDenied) as caught:
        identity_module.resolve({}, api_settings(auth_mode="magic").api)
    assert caught.value.reason == "unknown_auth_mode"


def test_the_startup_line_names_the_risk_of_trusted_header():
    described = identity_module.describe(api_settings(auth_mode="trusted_header").api)
    assert "ONLY SAFE" in described
    assert "overwrites" in described


# ---------------------------------------------------------------------------
# 2. A conversation id is a handle, not a credential
# ---------------------------------------------------------------------------

def test_another_user_cannot_use_a_conversation_id_they_hold(client, monkeypatch):
    """The gate validated the owner's assignment, not the stranger's — so
    without this check a leaked id is a complete AC09 bypass."""
    token = open_chat(client)
    monkeypatch.setattr(api_app, "SETTINGS", api_settings(dev_username=STRANGER))
    response = client.post(f"/api/conversations/{token}/messages",
                           json={"text": "what is the budget?"})
    assert response.status_code == 403


def test_another_user_cannot_read_the_transcript(client, monkeypatch):
    token = open_chat(client)
    monkeypatch.setattr(api_app, "SETTINGS", api_settings(dev_username=STRANGER))
    assert client.get(f"/api/conversations/{token}").status_code == 403


def test_another_user_cannot_close_someone_elses_conversation(client, monkeypatch):
    token = open_chat(client)
    monkeypatch.setattr(api_app, "SETTINGS", api_settings(dev_username=STRANGER))
    client.delete(f"/api/conversations/{token}")
    monkeypatch.setattr(api_app, "SETTINGS", api_settings(dev_username=OWNER))
    assert client.get(f"/api/conversations/{token}").status_code == 200, \
        "the owner's conversation was destroyed by someone else"


def test_an_unknown_id_and_someone_elses_id_are_indistinguishable(client, monkeypatch):
    token = open_chat(client)
    monkeypatch.setattr(api_app, "SETTINGS", api_settings(dev_username=STRANGER))
    theirs = client.get(f"/api/conversations/{token}")
    nonsense = client.get("/api/conversations/does-not-exist")
    assert theirs.status_code == nonsense.status_code == 403
    assert theirs.json() == nonsense.json(), \
        "a caller that can tell these apart can probe for live conversations"


def test_the_owner_can_use_their_own_conversation(client):
    token = open_chat(client)
    response = client.post(f"/api/conversations/{token}/messages",
                           json={"text": "what is the budget?"})
    assert response.status_code == 200
    assert response.json()["outcome"] == conv.OUTCOME_ANSWERED


# ---------------------------------------------------------------------------
# 3. Refusals are uniform
# ---------------------------------------------------------------------------

def test_an_unassigned_project_is_refused(client):
    response = client.post("/api/conversations", json={"project_code": "PRJ-01"})
    assert response.status_code == 403


def test_an_unassigned_and_a_nonexistent_project_look_identical(client):
    unassigned = client.post("/api/conversations", json={"project_code": "PRJ-01"})
    nonexistent = client.post("/api/conversations",
                              json={"project_code": "NO-SUCH-PROJECT"})
    assert unassigned.status_code == nonexistent.status_code == 403
    assert unassigned.json() == nonexistent.json(), \
        "a distinguishable 403 is a project directory"


def test_the_reason_slug_never_reaches_the_client(client):
    body = client.post("/api/conversations",
                       json={"project_code": "PRJ-01"}).json()
    assert "not_assigned" not in str(body)
    assert body["detail"] == api_app.DENIED


def test_a_backend_outage_is_503_not_403(client, monkeypatch):
    """An outage must never read as a permission decision."""
    def down(*_a, **_kw):
        raise BackendUnavailable("database unreachable")

    monkeypatch.setattr(api_app, "open_session", down)
    response = client.post("/api/conversations", json={"project_code": "PRJ-06"})
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# 4. The routes themselves
# ---------------------------------------------------------------------------

def test_health_needs_no_identity_and_names_the_auth_mode(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["auth_mode"] == "dev_fixed"


def test_the_switcher_returns_only_assigned_projects(client):
    body = client.get("/api/projects").json()
    assert [p["code"] for p in body] == ["PRJ-06"]
    assert body[0]["display_name"] == "Project Six"


def test_opening_a_conversation_returns_the_bilingual_welcome(client):
    body = client.post("/api/conversations",
                       json={"project_code": "PRJ-06"}).json()
    assert body["project_code"] == "PRJ-06"
    assert "PRJ-06" in body["welcome"]
    assert "إنجاز" in body["welcome"] and "Enjaz" in body["welcome"]


def test_the_transcript_accumulates_across_turns(client):
    token = open_chat(client)
    for text in ("first question", "second question"):
        client.post(f"/api/conversations/{token}/messages", json={"text": text})
    body = client.get(f"/api/conversations/{token}").json()
    assert [t["question"] for t in body["turns"]] == ["first question",
                                                      "second question"]


def test_an_over_long_question_is_truncated_not_rejected(client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        conv.Conversation, "ask",
        lambda self, text, **kw: seen.append(text) or conv.Reply(
            text="ok", outcome=conv.OUTCOME_ANSWERED))
    token = open_chat(client)
    client.post(f"/api/conversations/{token}/messages",
                json={"text": "x" * 9000})
    assert len(seen[0]) == SETTINGS.api.max_question_chars


def test_an_empty_question_is_a_validation_error(client):
    token = open_chat(client)
    assert client.post(f"/api/conversations/{token}/messages",
                       json={"text": ""}).status_code == 422


def test_the_knowledge_base_is_scoped_by_the_gate(client):
    """AC10 reads through a Session, so an unassigned project is refused
    before a single row is fetched."""
    assert client.get("/api/projects/PRJ-01/documents").status_code == 403


# ---------------------------------------------------------------------------
# 5. The store's bounds
# ---------------------------------------------------------------------------

def test_the_store_evicts_the_least_recently_used_when_full():
    store = ConversationStore(api_settings(max_conversations=2).api)
    first, _ = store.open(Session(username=OWNER, project_code="PRJ-06"))
    second, _ = store.open(Session(username=OWNER, project_code="PRJ-06"))
    store.get(second, OWNER)                       # touch it, so first is LRU
    store.open(Session(username=OWNER, project_code="PRJ-06"))
    assert store.get(first, OWNER) is None
    assert store.get(second, OWNER) is not None


def test_an_idle_conversation_expires():
    store = ConversationStore(api_settings(conversation_idle_minutes=0).api)
    token, _ = store.open(Session(username=OWNER, project_code="PRJ-06"))
    import time
    time.sleep(0.01)
    assert store.get(token, OWNER) is None


def test_the_store_issues_unguessable_ids():
    store = ConversationStore(api_settings().api)
    tokens = {store.open(Session(username=OWNER, project_code="PRJ-06"))[0]
              for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(t) >= 24 for t in tokens)
