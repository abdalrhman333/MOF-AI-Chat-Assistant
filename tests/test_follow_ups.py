"""The suggestions offered under an answer.

The model proposes up to three questions to ask next. They are the first
free-text field besides the answer itself, and they are rendered as
something the user clicks — so what reaches the screen is bounded here,
the same way a citation is.

Two rules carry the weight:

* **A refusal carries none.** Suggestions under "that is outside this
  project" would invite the user straight back into what was just
  declined, which is why they are dropped for the same intents that lose
  their citations.
* **Whatever the model returns, at most three short strings are kept.**
  A list of forty, or a paragraph dressed as a question, is the model's
  mistake and must not become the page's.

⚠️ They are not a privileged channel. A click sends the text as an
ordinary message, through the same gate and the same classification as
anything typed — so the worst a poisoned suggestion can do is propose a
question the assistant will then answer normally, or refuse.

Nothing here calls a paid API.
"""
from __future__ import annotations

import json

import pytest

from Application.backend import answer as ans
from tests.test_answering import (CORPUS, SESSION, StubEmbedder, StubLLM,
                                  StubQdrant, settings)

SUGGESTIONS = ["ما خطط التخفيف للمخاطر العالية؟", "من يملك أعلى مخاطرة؟"]


def run(payload, **kw):
    kw.setdefault("project_record", None)
    return ans.answer_question(
        SESSION, "what?", settings=settings(), embedder=StubEmbedder(),
        qdrant=StubQdrant(CORPUS),
        llm=StubLLM(raw=json.dumps(payload, ensure_ascii=False)), **kw)


def payload(**overrides):
    base = {"language": "ar", "intent": "project_question",
            "answer": "الميزانية 22,900,000 ريال.", "used_passages": [1],
            "used_project_record": False, "follow_ups": list(SUGGESTIONS)}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# They arrive
# ---------------------------------------------------------------------------

def test_the_suggestions_reach_the_answer():
    assert run(payload()).follow_ups == tuple(SUGGESTIONS)


def test_the_model_is_asked_for_them():
    schema = ans.RESPONSE_SCHEMA["json_schema"]["schema"]
    assert "follow_ups" in schema["required"], \
        "an optional field is one the model can quietly stop returning"


def test_they_are_generated_after_the_answer_text():
    """⚠️ A strict schema is generated in order. Anything before `answer`
    delays the first character the reader sees, and these are the least
    urgent part of the reply."""
    properties = list(ans.RESPONSE_SCHEMA["json_schema"]["schema"]["properties"])
    assert properties.index("follow_ups") > properties.index("answer")


# ---------------------------------------------------------------------------
# A refusal carries none
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("intent", ["out_of_scope", "unsupported_language"])
def test_a_refusal_carries_no_suggestions(intent):
    answer = run(payload(intent=intent))
    assert answer.follow_ups == ()
    assert answer.citations == [], "the same rule that drops the citations"


def test_an_ordinary_answer_keeps_them():
    """The mirror: dropping them everywhere would pass the test above."""
    assert run(payload(intent="project_question")).follow_ups


# ---------------------------------------------------------------------------
# What the model returns is bounded here
# ---------------------------------------------------------------------------

def test_at_most_three_are_kept():
    answer = run(payload(follow_ups=[f"question {i}?" for i in range(40)]))
    assert len(answer.follow_ups) == 3


def test_a_long_suggestion_is_trimmed_rather_than_dropped():
    """A cosmetic fault should not cost the whole reply."""
    answer = run(payload(follow_ups=["x" * 500]))
    assert len(answer.follow_ups) == 1
    assert len(answer.follow_ups[0]) <= ans.MAX_FOLLOW_UP_CHARS


def test_blank_and_whitespace_only_suggestions_are_dropped():
    answer = run(payload(follow_ups=["", "   ", "\n", "real?"]))
    assert answer.follow_ups == ("real?",)


def test_duplicates_are_shown_once():
    answer = run(payload(follow_ups=["same?", "same?", "other?"]))
    assert answer.follow_ups == ("same?", "other?")


def test_newlines_are_collapsed():
    """A chip is one line. A suggestion with newlines would break the row."""
    answer = run(payload(follow_ups=["a\nb\n\nc?"]))
    assert answer.follow_ups == ("a b c?",)


@pytest.mark.parametrize("bad", [None, "not a list", 42, {"a": 1}])
def test_a_malformed_field_yields_no_suggestions(bad):
    """Never raises: the answer is still good without them."""
    answer = run(payload(follow_ups=bad))
    assert answer.follow_ups == ()
    assert answer.text


def test_non_string_entries_are_skipped():
    answer = run(payload(follow_ups=[1, None, {"q": "x"}, "real?"]))
    assert answer.follow_ups == ("real?",)


# ---------------------------------------------------------------------------
# They travel to the caller
# ---------------------------------------------------------------------------

def test_the_reply_carries_them():
    from Application.backend import conversation as conv
    reply = conv.handle_question(
        SESSION, "what?", settings=settings(), embedder=StubEmbedder(),
        qdrant=StubQdrant(CORPUS), project_record=None,
        llm=StubLLM(raw=json.dumps(payload(), ensure_ascii=False)))
    assert reply.follow_ups == tuple(SUGGESTIONS)


def test_the_prompt_tells_the_model_when_to_return_none():
    """The rule that keeps a suggestion answerable. Without it the model
    offers what sounds plausible rather than what the sources cover."""
    prompt = ans.SYSTEM_PROMPT
    assert "follow_ups" in prompt
    assert "EMPTY list" in prompt
