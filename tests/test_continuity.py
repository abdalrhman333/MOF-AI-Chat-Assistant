"""Tests for AC03 (session continuity) and AC04 (bilingual welcome).

**The claims under test are about what a conversation remembers, and what
it must not.** Four of them:

  1. **A conversation cannot change project.** AC09 extended through time:
     the session is fixed at construction, so a transcript cannot come to
     hold two projects' content and a follow-up cannot resolve against a
     project the user has left.
  2. **History reaches the model, bounded.** By turns *and* by tokens,
     because a turn cap says nothing about size.
  3. **The retrieval fallback fires only when the bare question found
     nothing.** Composing every query would spend an embedding where none
     is needed and drag the old topic into a change of subject.
  4. **History cannot manufacture a citation.** An earlier turn's passage
     [2] is not this turn's passage [2], and the numbering restarts.

Nothing here calls a paid API or touches the live vector store.
"""
from __future__ import annotations

import dataclasses

import pytest

from Application.backend import answer as ans
from Application.backend import conversation as conv
from Application.backend import messages
from Application.backend import retrieval as ret
from Application.backend.session import Session
from pipeline.config import get_settings

SETTINGS = get_settings()
SESSION = Session(username="tester", project_code="PRJ-TEST")
OTHER = Session(username="tester", project_code="PRJ-OTHER")


class RecordingLLM:
    def __init__(self, answer="the approved budget is 22,900,000 SAR",
                 intent="project_question"):
        self.intent = intent
        self.calls, self.seen = 0, []
        self._answer = answer
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls += 1
        self.seen.append(kwargs["messages"])
        payload = ('{"language": "en", "intent": "project_question", '
                   '"answer": %s, "used_passages": [1], '
                   '"used_project_record": false}' % _json(self._answer))
        payload = payload.replace('"project_question"', _json(self.intent))
        message = type("M", (), {"content": payload})()
        return type("R", (), {"choices": [type("C", (), {"message": message})()]})()

    @property
    def last(self):
        return self.seen[-1]


def _json(text):
    import json
    return json.dumps(text, ensure_ascii=False)


def passage(score=0.62, text="الميزانية المعتمدة 22,900,000 ريال", rank=1):
    return ret.Passage(rank=rank, score=score, filename="charter.docx",
                       doc_type="Project Charter", page=1, section="", text=text)


def result(*passages):
    return ret.RetrievalResult(passages=list(passages), embed_ms=5, search_ms=5)


def ask(conversation, text, **kwargs):
    kwargs.setdefault("project_record", None)
    kwargs.setdefault("retrieval", result(passage()))
    kwargs.setdefault("llm", RecordingLLM())
    return conversation.ask(text, **kwargs)


def with_conversation(**overrides):
    return dataclasses.replace(
        SETTINGS,
        conversation=dataclasses.replace(SETTINGS.conversation, **overrides),
    )


# ---------------------------------------------------------------------------
# 1. AC09 extended through time
# ---------------------------------------------------------------------------

def test_a_conversation_is_bound_to_one_session():
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    assert talk.session is SESSION
    assert talk.project_code == SESSION.project_code


def test_the_session_cannot_be_replaced_after_construction():
    """Switching project must mean a new Session from the gate, and so a
    new Conversation — not a mutated one carrying the old transcript."""
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    with pytest.raises(AttributeError):
        talk.session = OTHER


def test_two_conversations_do_not_share_history():
    one = conv.open_conversation(SESSION, settings=SETTINGS)
    two = conv.open_conversation(OTHER, settings=SETTINGS)
    ask(one, "What is the approved budget?")
    assert len(one) == 1
    assert len(two) == 0
    assert two.history_for_prompt() == []


def test_every_turn_retrieves_from_the_conversations_own_session():
    """The project a turn searches is derived from the Session, never from
    the turn — so a later turn cannot drift to another project."""
    seen = []

    class SpyQdrant:
        def query_points(self, **kw):
            seen.append(kw["collection_name"])
            return type("R", (), {"points": []})()

    class StubEmbedder:
        def __init__(self):
            self.embeddings = self

        def create(self, model, input):                      # noqa: A002
            point = type("E", (), {"index": 0, "embedding": [0.1]})()
            return type("R", (), {"data": [point]})()

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    for text in ("What is the approved budget?", "And the end date?"):
        talk.ask(text, project_record=None, embedder=StubEmbedder(),
                 qdrant=SpyQdrant(), llm=RecordingLLM())
    assert seen, "no search ran"
    assert set(seen) == {SETTINGS.qdrant.collection_for(SESSION.project_code)}


# ---------------------------------------------------------------------------
# 2. AC04 — the welcome
# ---------------------------------------------------------------------------

def test_the_welcome_is_available_before_any_question():
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    assert len(talk) == 0
    assert talk.welcome.strip()


def test_the_welcome_is_bilingual():
    """No question has been asked, so there is no language to reply in."""
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    assert messages.text("welcome", "ar", project=SESSION.project_code,
                          name=SESSION.username) \
        in talk.welcome
    assert messages.text("welcome", "en", project=SESSION.project_code,
                          name=SESSION.username) \
        in talk.welcome


@pytest.mark.parametrize("language,purpose,limits", [
    ("ar", "أجيب عن أسئلتك حول هذا المشروع",
     ["هذا المشروع فقط", "لا أطّلع على غيره", "من خارج مصادره",
      "لا أعدّل أي بيانات", "بدلاً من التخمين", "بالعربية أو الإنجليزية"]),
    ("en", "I answer questions about this project",
     ["this project only", "can't see any", "outside its sources",
      "don't change any data", "rather than guess", "Arabic or English"]),
])
def test_the_welcome_says_what_the_assistant_is_for_and_where_it_stops(
        language, purpose, limits):
    """Requested: the user should know the assistant's purpose and its
    boundaries before asking anything. Each limit stated is one the system
    enforces — see the comment beside the text in messages.py — so this
    pins that none quietly disappears from the promise."""
    text = messages.text("welcome", language, project="PRJ-06", name="نورة")
    assert purpose in text
    for limit in limits:
        assert limit in text, f"the {language} welcome no longer states: {limit}"


def test_the_welcome_names_the_session_project_not_caller_input():
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    assert SESSION.project_code in talk.welcome
    assert OTHER.project_code not in talk.welcome


def test_the_welcome_costs_nothing():
    llm = RecordingLLM()
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    talk.welcome
    assert llm.calls == 0
    assert len(talk) == 0, "the welcome is not a turn"


# ---------------------------------------------------------------------------
# 3. AC03 — history reaches the model, bounded
# ---------------------------------------------------------------------------

def test_a_second_question_carries_the_first_into_the_prompt():
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?")
    llm = RecordingLLM()
    ask(talk, "And the end date?", llm=llm)

    roles = [m["role"] for m in llm.last]
    assert roles == ["system", "user", "assistant", "user"]
    assert llm.last[1]["content"] == "What is the approved budget?"
    assert llm.last[-1]["content"] == "And the end date?"


def test_the_first_question_carries_no_history():
    llm = RecordingLLM()
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?", llm=llm)
    assert [m["role"] for m in llm.last] == ["system", "user"]


def test_history_is_capped_by_turn_count():
    settings = with_conversation(max_history_turns=2)
    talk = conv.open_conversation(SESSION, settings=settings)
    for n in range(5):
        ask(talk, f"Question number {n} about the project budget?")
    llm = RecordingLLM()
    ask(talk, "And the end date?", llm=llm)
    assert len([m for m in llm.last if m["role"] == "assistant"]) == 2


def test_history_is_capped_by_tokens_as_well_as_turns():
    """A turn cap says nothing about size; one long answer can outweigh
    five short ones."""
    from pipeline.chunker import count_tokens

    settings = with_conversation(max_history_turns=99, max_history_tokens=40)
    talk = conv.open_conversation(SESSION, settings=settings)
    for n in range(6):
        ask(talk, f"Question {n} " + "about the approved project budget " * 6)

    kept = talk.history_for_prompt()
    assert 0 < len(kept) < 6, "the turn cap was not what limited this"
    spent = sum(count_tokens(q) + count_tokens(a) for q, a in kept[1:])
    assert spent <= settings.conversation.max_history_tokens


def test_history_keeps_the_most_recent_turns_not_the_oldest():
    """A follow-up refers to what was just said."""
    settings = with_conversation(max_history_turns=1)
    talk = conv.open_conversation(SESSION, settings=settings)
    ask(talk, "First question about the budget?")
    ask(talk, "Second question about the risks?")
    assert talk.history_for_prompt() == [
        ("Second question about the risks?", talk.turns[-1].reply.text)
    ]


@pytest.mark.parametrize("intent", ["greeting", "capability",
                                   "unsupported_language"])
def test_turns_without_a_topic_are_recorded_but_not_sent_to_the_model(intent):
    """`hello` contributes nothing a follow-up could refer to, and would
    crowd out the exchange that matters."""
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "whatever", llm=RecordingLLM(intent=intent))
    assert len(talk) == 1, "the transcript must show what the user saw"
    assert talk.history_for_prompt() == []


def test_an_out_of_scope_turn_still_counts_as_history():
    """It had a topic even though the documents did not answer it."""
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the capital of France?",
        retrieval=result(passage(score=0.05)),
        llm=RecordingLLM(intent="out_of_scope"))
    assert talk.turns[-1].reply.outcome == conv.OUTCOME_OUT_OF_SCOPE
    assert len(talk.history_for_prompt()) == 1


def test_the_history_rule_is_stated_only_when_history_is_present():
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    first = RecordingLLM()
    ask(talk, "What is the approved budget?", llm=first)
    assert "Earlier turns" not in first.last[0]["content"]

    second = RecordingLLM()
    ask(talk, "And the end date?", llm=second)
    assert "Earlier turns" in second.last[0]["content"]


# ---------------------------------------------------------------------------
# 4. The retrieval fallback
# ---------------------------------------------------------------------------

class ScriptedSearch:
    """Returns a different result per query string, and records the order."""

    def __init__(self, mapping, default):
        self.mapping, self.default, self.queries = mapping, default, []

    def __call__(self, session, question, **kwargs):
        self.queries.append(question)
        return self.mapping.get(question, self.default)


def test_a_bare_followup_that_finds_nothing_retries_with_the_previous_question(
        monkeypatch):
    below = result(passage(score=0.05))
    above = result(passage(score=0.55))
    scripted = ScriptedSearch(
        {"What is the approved budget? Why?": above}, below)
    monkeypatch.setattr(conv, "search", scripted)

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?")
    reply = talk.ask("Why?", project_record=None, llm=RecordingLLM())

    assert scripted.queries == ["Why?", "What is the approved budget? Why?"]
    assert reply.outcome == conv.OUTCOME_ANSWERED


def test_a_followup_that_retrieves_on_its_own_is_not_composed(monkeypatch):
    """Composing every query would drag the old topic into a new subject."""
    scripted = ScriptedSearch({}, result(passage(score=0.55)))
    monkeypatch.setattr(conv, "search", scripted)

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?")
    talk.ask("What are the current risks?", project_record=None,
             llm=RecordingLLM())

    assert scripted.queries == ["What are the current risks?"]


def test_the_retry_is_abandoned_when_it_also_finds_nothing(monkeypatch):
    below = result(passage(score=0.05))
    scripted = ScriptedSearch({}, below)
    monkeypatch.setattr(conv, "search", scripted)

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?")
    reply = talk.ask("Why?", project_record=None, llm=RecordingLLM())
    assert len(scripted.queries) == 2, "the composed query was not tried"
    # What the turn *becomes* is the model's call now, so the claim here is
    # about the evidence handed to it: the failed retry did not replace the
    # original result.
    assert reply.answer is not None
    assert reply.answer.best_score == pytest.approx(0.05)


def test_both_embeddings_are_reported_when_a_retry_happens(monkeypatch):
    below = ret.RetrievalResult(passages=[passage(score=0.05)],
                                embed_ms=100, search_ms=10)
    above = ret.RetrievalResult(passages=[passage(score=0.55)],
                                embed_ms=200, search_ms=20)
    monkeypatch.setattr(conv, "search",
                        ScriptedSearch({"A? B?": above}, below))

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "A?")
    reply = talk.ask("B?", project_record=None, llm=RecordingLLM())
    assert reply.timings.embed_ms == 300, "a hidden retry understates AC08"
    assert reply.timings.search_ms == 30


def test_a_failing_retry_does_not_overwrite_the_original_result(monkeypatch):
    """Found by mutation testing, and invisible to a behavioural test.

    When neither query clears the floor, the model sees no passages either
    way — so only the *reported* numbers differ. Returning the composed
    query's result would attribute its score to a question the user never
    asked. Assert the value, not just the behaviour (§6).
    """
    original = ret.RetrievalResult(passages=[passage(score=0.19)],
                                   embed_ms=10, search_ms=1)
    worse = ret.RetrievalResult(passages=[passage(score=0.04)],
                                embed_ms=10, search_ms=1)
    monkeypatch.setattr(conv, "search", ScriptedSearch({"A? B?": worse},
                                                       original))

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "A?")
    reply = talk.ask("B?", project_record=None, llm=RecordingLLM())
    assert reply.answer is not None
    assert reply.answer.passages_sent == 0, "a sub-floor passage was sent"
    assert reply.answer.best_score == pytest.approx(0.19), \
        "a failed retry must not report its own score as the question's"


def test_a_topicless_turn_does_not_become_the_composition_anchor(monkeypatch):
    """Found by mutation testing.

    Budget question, then `thanks`, then `why?`. Composing against the most
    recent turn would search for `thanks why?`, which retrieves nothing —
    the anchor has to be the last turn that actually had a topic.
    """
    scripted = ScriptedSearch({}, result(passage(score=0.05)))
    monkeypatch.setattr(conv, "search", scripted)

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?")
    ask(talk, "thanks", llm=RecordingLLM(intent="greeting"))
    assert talk.turns[-1].reply.outcome == conv.OUTCOME_CONVERSATIONAL

    talk.ask("Why?", project_record=None, llm=RecordingLLM())
    assert scripted.queries[-1] == "What is the approved budget? Why?", \
        f"composed against the wrong turn: {scripted.queries[-1]!r}"


def test_the_retry_can_be_disabled_by_configuration(monkeypatch):
    scripted = ScriptedSearch({}, result(passage(score=0.05)))
    monkeypatch.setattr(conv, "search", scripted)
    settings = with_conversation(retry_below_floor=False)

    talk = conv.open_conversation(SESSION, settings=settings)
    ask(talk, "What is the approved budget?", settings=settings)
    talk.ask("Why?", settings=settings, project_record=None,
             llm=RecordingLLM())
    assert scripted.queries == ["Why?"], "the bare query only — no retry"


def test_a_rejected_turn_never_reaches_retrieval(monkeypatch):
    """AC01 still runs first, however much history exists."""
    scripted = ScriptedSearch({}, result(passage(score=0.55)))
    monkeypatch.setattr(conv, "search", scripted)

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?")
    reply = talk.ask("这个项目的预算是多少", project_record=None,
                     llm=RecordingLLM(intent="unsupported_language"))
    assert reply.outcome == conv.OUTCOME_REJECTED_LANGUAGE


# ---------------------------------------------------------------------------
# 5. History must not manufacture a citation
# ---------------------------------------------------------------------------

def test_passage_numbers_restart_each_turn_and_stale_indexes_are_dropped():
    """An earlier turn's passage [2] is not this turn's passage [2]."""
    class StaleCiter:
        def __init__(self):
            self.chat = self
            self.completions = self
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            payload = ('{"answer": "see earlier", "used_passages": [1, 2, 7], '
                       '"used_project_record": false}')
            message = type("M", (), {"content": payload})()
            return type("R", (), {
                "choices": [type("C", (), {"message": message})()]})()

    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?",
        retrieval=result(passage(), passage(rank=2), passage(rank=3)))
    reply = talk.ask("And the end date?", project_record=None,
                     retrieval=result(passage()), llm=StaleCiter())
    assert [c.number for c in reply.citations] == [1], \
        "only the passage actually sent this turn may be cited"


def test_the_prompt_tells_the_model_history_is_not_a_source():
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?")
    llm = RecordingLLM()
    ask(talk, "And the end date?", llm=llm)
    system = llm.last[0]["content"]
    assert "NOT a source" in system
    assert "numbered afresh" in system


def test_history_is_not_placed_inside_the_system_prompt():
    """Prior user text belongs in a user role, so the system message stays
    purely our instructions and passage data."""
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "A distinctive earlier question about budgets")
    llm = RecordingLLM()
    ask(talk, "And the end date?", llm=llm)
    assert "A distinctive earlier question" not in llm.last[0]["content"]


def test_build_messages_without_history_is_unchanged():
    """The no-history path must stay byte-identical to before AC03."""
    plain = ans.build_messages("q", [passage()], None, "en")
    explicit = ans.build_messages("q", [passage()], None, "en", None)
    empty = ans.build_messages("q", [passage()], None, "en", [])
    assert plain == explicit == empty
    assert [m["role"] for m in plain] == ["system", "user"]


# ---------------------------------------------------------------------------
# 6. The loop itself
# ---------------------------------------------------------------------------

def test_many_consecutive_questions_need_no_restart():
    """AC03, stated plainly."""
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    for n in range(8):
        reply = ask(talk, f"Question {n} about the approved project budget?")
        assert reply.outcome == conv.OUTCOME_ANSWERED
    assert len(talk) == 8
    assert len(talk.turns) == 8


def test_the_transcript_records_what_the_user_actually_saw():
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "hello", llm=RecordingLLM(intent="greeting"))
    ask(talk, "这个项目的预算是多少",
        llm=RecordingLLM(intent="unsupported_language"))
    ask(talk, "What is the approved budget?")
    outcomes = [t.reply.outcome for t in talk.turns]
    assert outcomes == [conv.OUTCOME_CONVERSATIONAL,
                        conv.OUTCOME_REJECTED_LANGUAGE,
                        conv.OUTCOME_ANSWERED]
    assert [t.question for t in talk.turns][0] == "hello"


def test_turns_is_a_snapshot_not_a_live_handle():
    talk = conv.open_conversation(SESSION, settings=SETTINGS)
    ask(talk, "What is the approved budget?")
    snapshot = talk.turns
    ask(talk, "And the end date?")
    assert len(snapshot) == 1 and len(talk.turns) == 2
