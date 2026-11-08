"""The streaming route must be the ordinary route, delivered differently.

Everything that makes `/messages` safe has to hold here too, and a second
route is exactly where a boundary gets forgotten: the owner check, the
rate limit, the refusal that does not say whether a conversation exists.
So most of this file is those three things again, against the new path.

The rest covers what only streaming can get wrong: half an answer left on
screen when the model dies mid-sentence, text that does not match the
reply that settles, and a turn that is not recorded because it was
streamed.

⚠️ `Conversation.ask` is stubbed, so nothing here calls a paid API. What
is under test is the route, not the answering already covered by
`test_answer_streaming.py`.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from Application.backend import conversation as conv
from Application.backend.api import app as api_app
from Application.backend.api.store import ConversationStore
from Application.backend.errors import AccessDenied, BackendUnavailable
from Application.backend.session import AssignedProject, Session
from pipeline.config import get_settings

SETTINGS = get_settings()
OWNER = "noura.alharbi"
STRANGER = "mallory.attacker"
PIECES = ["الميزانية ", "المعتمدة ", "هي 22,900,000."]
WHOLE = "".join(PIECES)

STREAM = "/api/conversations/{}/messages/stream"


def api_settings(**overrides):
    base = dict(auth_mode="dev_fixed", dev_username=OWNER)
    base.update(overrides)
    return dataclasses.replace(SETTINGS,
                               api=dataclasses.replace(SETTINGS.api, **base))


def _reply(text=WHOLE):
    return conv.Reply(text=text, outcome=conv.OUTCOME_ANSWERED,
                      intent="project_question", language="ar",
                      reply_language="ar")


@pytest.fixture
def client(monkeypatch):
    settings = api_settings()
    monkeypatch.setattr(api_app, "SETTINGS", settings)
    monkeypatch.setattr(api_app, "STORE", ConversationStore(settings.api))

    def fake_open_session(username, project_code, **_kw):
        if project_code != "PRJ-06":
            raise AccessDenied("not_assigned", username, project_code)
        return Session(username=username, project_code=project_code)

    def fake_ask(self, text, on_text=None, **_kw):
        for piece in PIECES:
            if on_text is not None:
                on_text(piece)
        reply = _reply()
        self._turns.append(conv.Turn(question=text, reply=reply))
        return reply

    monkeypatch.setattr(api_app, "open_session", fake_open_session)
    monkeypatch.setattr(
        api_app, "list_assigned_projects",
        lambda username, **_kw: [AssignedProject("PRJ-06", "م", "P6", "active")])
    monkeypatch.setattr(conv.Conversation, "ask", fake_ask)
    return TestClient(api_app.app)


def open_chat(client, project="PRJ-06"):
    response = client.post("/api/conversations", json={"project_code": project})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def events(response):
    """Parse an SSE body into [(event, payload), ...]."""
    out = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        out.append((name, data))
    return out


def ask_stream(client, token, text="ما الميزانية؟"):
    return client.post(STREAM.format(token), json={"text": text})


# ---------------------------------------------------------------------------
# It streams
# ---------------------------------------------------------------------------

def test_the_response_is_an_event_stream(client):
    response = ask_stream(client, open_chat(client))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_a_proxy_is_told_not_to_buffer(client):
    """⚠️ Buffering holds every event until the turn ends, which undoes the
    entire point of this route and is invisible in development."""
    response = ask_stream(client, open_chat(client))
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"


def test_each_piece_arrives_as_its_own_event(client):
    response = ask_stream(client, open_chat(client))
    texts = [p["text"] for name, p in events(response) if name == "text"]
    assert texts == PIECES


def test_the_streamed_text_equals_the_final_answer(client):
    """What the reader watched and what they are left with must match."""
    response = ask_stream(client, open_chat(client))
    parsed = events(response)
    streamed = "".join(p["text"] for name, p in parsed if name == "text")
    done = [p for name, p in parsed if name == "done"][0]
    assert streamed == done["text"] == WHOLE


def test_the_done_event_carries_the_whole_reply(client):
    response = ask_stream(client, open_chat(client))
    done = [p for name, p in events(response) if name == "done"][0]
    assert done["outcome"] == conv.OUTCOME_ANSWERED
    assert done["intent"] == "project_question"
    assert "citations" in done and "timings" in done


def test_text_containing_newlines_survives_the_encoding(client, monkeypatch):
    """⚠️ A bare newline terminates an SSE field. This is the encoding
    mistake the format invites, and a multi-line answer is normal."""
    def fake_ask(self, text, on_text=None, **_kw):
        on_text("line one\nline two\n\nline three")
        reply = _reply("line one\nline two\n\nline three")
        self._turns.append(conv.Turn(question=text, reply=reply))
        return reply

    monkeypatch.setattr(conv.Conversation, "ask", fake_ask)
    response = ask_stream(client, open_chat(client))
    texts = [p["text"] for name, p in events(response) if name == "text"]
    assert texts == ["line one\nline two\n\nline three"]


# ---------------------------------------------------------------------------
# The turn is still recorded
# ---------------------------------------------------------------------------

def test_a_streamed_turn_appears_in_the_transcript(client):
    """AC03's continuity does not depend on which route was used."""
    token = open_chat(client)
    ask_stream(client, token)
    transcript = client.get(f"/api/conversations/{token}").json()
    assert [t["question"] for t in transcript["turns"]] == ["ما الميزانية؟"]
    assert transcript["turns"][0]["reply"]["text"] == WHOLE


# ---------------------------------------------------------------------------
# The boundary holds, exactly as on the non-streaming route
# ---------------------------------------------------------------------------

def test_a_stranger_cannot_use_someone_elses_conversation(client, monkeypatch):
    token = open_chat(client)
    monkeypatch.setattr(api_app, "SETTINGS",
                        api_settings(dev_username=STRANGER))
    assert ask_stream(client, token).status_code == 403


def test_an_unknown_conversation_is_refused_the_same_way(client, monkeypatch):
    """A 403 that distinguished "not yours" from "no such thing" would
    enumerate conversations."""
    token = open_chat(client)
    monkeypatch.setattr(api_app, "SETTINGS",
                        api_settings(dev_username=STRANGER))
    stranger = ask_stream(client, token)
    missing = ask_stream(client, "does-not-exist")
    assert stranger.status_code == missing.status_code == 403
    assert stranger.json() == missing.json()


def test_the_rate_limit_applies_to_this_route_too(client, monkeypatch):
    """⚠️ A cost guard on one of two routes is not a cost guard."""
    class Blocked:
        def check(self, username):
            return type("D", (), {"allowed": False, "retry_after": 30})()

    monkeypatch.setattr(api_app, "LIMITER", Blocked())
    response = ask_stream(client, open_chat(client))
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


def test_identity_cannot_be_supplied_in_the_body(client):
    """The AC09 boundary: no field names a user, on any route."""
    token = open_chat(client)
    response = client.post(STREAM.format(token),
                           json={"text": "hi", "username": STRANGER})
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        done = [p for name, p in events(response) if name == "done"][0]
        assert done["text"] == WHOLE


# ---------------------------------------------------------------------------
# Failure mid-stream
# ---------------------------------------------------------------------------

def test_a_model_failure_mid_stream_sends_an_error_event(client, monkeypatch):
    """The status line is already 200 by then, so the failure has to arrive
    in the stream — a half-written answer with no explanation is the worst
    outcome available here."""
    def fake_ask(self, text, on_text=None, **_kw):
        on_text("الميزانية ")
        raise BackendUnavailable("the language model call failed")

    monkeypatch.setattr(conv.Conversation, "ask", fake_ask)
    response = ask_stream(client, open_chat(client))
    parsed = events(response)
    assert [name for name, _ in parsed] == ["text", "error"]
    assert parsed[-1][1]["error"] == "backend_unavailable"


def test_an_error_event_leaks_no_internal_detail(client, monkeypatch):
    """Errors stay uniform here too."""
    def fake_ask(self, text, on_text=None, **_kw):
        raise BackendUnavailable("endpoint https://secret.openai.azure.com 401")

    monkeypatch.setattr(conv.Conversation, "ask", fake_ask)
    response = ask_stream(client, open_chat(client))
    assert "secret" not in response.text
    assert "401" not in response.text


def test_a_failed_turn_sends_no_done_event(client, monkeypatch):
    def fake_ask(self, text, on_text=None, **_kw):
        raise BackendUnavailable("down")

    monkeypatch.setattr(conv.Conversation, "ask", fake_ask)
    response = ask_stream(client, open_chat(client))
    assert "event: done" not in response.text


def test_a_durable_store_is_written_back_after_a_streamed_turn(client, monkeypatch):
    """⚠️ The in-memory store holds the live conversation, so a turn is
    recorded there whether or not anything saves it — which means the
    transcript test above passes even with the save deleted. A durable
    store holds a snapshot and must be written back, or a streamed turn
    vanishes on the next request.
    """
    saved = []

    class Durable:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def save(self, token, conversation):
            saved.append(token)

    monkeypatch.setattr(api_app, "STORE", Durable(api_app.STORE))
    token = open_chat(client)
    ask_stream(client, token)
    assert saved == [token]
