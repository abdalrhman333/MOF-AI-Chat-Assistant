"""Tests for AC02 — telling special handling from general retrieval.

**The claim under test is that tier 1 fires only when it is certainly
right.** A router that answers `hello` cheaply is a small win; a router
that swallows `مرحبا، ما ميزانية المشروع؟` and replies "hello" has lost a
real question, and that is the failure mode these tests are built around.

The phrase set is data, so the tests treat it as data: they assert the
*rule* (whole-utterance, normalised, word-capped) rather than enumerating
the file's current contents, and separately assert that the shipped file
still parses and covers both languages.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from Application.backend import intent
from pipeline.config import get_settings

SETTINGS = get_settings()

PHRASES = {
    "greeting": ("hello", "مرحبا"),
    "courtesy": ("thanks", "شكرا"),
    "capability": ("what can you do", "ما هي قدراتك"),
    "session_meta": ("which project am i in", "ما هو المشروع الحالي"),
}


def classify(text, *, settings=None, phrases=PHRASES):
    return intent.classify(text, settings=settings or SETTINGS, phrases=phrases)


# ---------------------------------------------------------------------------
# Tier 1 fires on what it should
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("hello", intent.GREETING),
    ("Hello", intent.GREETING),
    ("  hello!  ", intent.GREETING),
    ("مرحبا", intent.GREETING),
    ("مرحباً", intent.GREETING),                 # diacritic stripped
    ("thanks", intent.COURTESY),
    ("شكرا", intent.COURTESY),
    ("What can you do?", intent.CAPABILITY),
    ("ما هي قدراتك؟", intent.CAPABILITY),
    ("Which project am I in?", intent.SESSION_META),
])
def test_unambiguous_utterances_are_routed_without_retrieval(text, expected):
    verdict = classify(text)
    assert verdict.intent == expected
    assert verdict.tier == intent.TIER_PHRASE
    assert not verdict.needs_retrieval


# ---------------------------------------------------------------------------
# ...and not on what it should not. This is the important half.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "مرحبا، ما هي الميزانية المعتمدة للمشروع؟",
    "hello, what is the approved budget for this project?",
    "thanks, but what is the planned completion date?",
    "شكرا، متى ينتهي المشروع حسب الخطة؟",
])
def test_a_question_with_a_greeting_attached_still_reaches_retrieval(text):
    """Substring matching is the obvious way this router fails: it would
    answer the greeting and silently lose the question."""
    verdict = classify(text)
    assert verdict.intent == intent.GENERAL_RETRIEVAL
    assert verdict.tier == intent.TIER_RETRIEVAL
    assert verdict.needs_retrieval


@pytest.mark.parametrize("text", [
    "ما هي الميزانية المعتمدة للمشروع؟",
    "What is the approved budget?",
    "Who is the project manager?",
    "ما هي المخاطر الحالية؟",
])
def test_real_questions_go_to_retrieval(text):
    assert classify(text).needs_retrieval


def test_a_long_utterance_is_never_tier_one_even_if_it_matches():
    """The word cap is a second, independent guard on the same failure."""
    padded = {"greeting": ("hello " * 20,)}
    verdict = classify("hello " * 20, phrases=padded)
    assert verdict.needs_retrieval, "the word cap must win over a phrase match"


def test_the_word_cap_is_configuration_not_a_literal():
    phrases = {"capability": ("what can you help me with today please",)}
    text = "what can you help me with today please"
    assert classify(text, phrases=phrases).needs_retrieval
    generous = dataclasses.replace(
        SETTINGS, intent=dataclasses.replace(SETTINGS.intent, match_max_words=20)
    )
    assert not classify(text, settings=generous, phrases=phrases).needs_retrieval


# ---------------------------------------------------------------------------
# Normalisation — the Arabic half, where a naive matcher quietly fails
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("written,canonical", [
    ("أهلا", "اهلا"),        # hamza forms fold
    ("إهلا", "اهلا"),
    ("آهلا", "اهلا"),
    ("مرحباً", "مرحبا"),      # diacritics dropped
    ("مـرحبا", "مرحبا"),      # tatweel dropped
    ("شكراً!", "شكرا"),
    ("Hello...", "hello"),
    ("  THANKS  ", "thanks"),
])
def test_normalisation_folds_spelling_variants(written, canonical):
    assert intent.normalise(written) == canonical


def test_normalisation_collapses_whitespace():
    assert intent.normalise("what   can\tyou\ndo") == "what can you do"


def test_a_spelling_variant_still_matches_its_phrase():
    """The point of normalisation: the file cannot list every spelling of
    every Arabic greeting, and should not have to."""
    assert classify("أهلاً", phrases={"greeting": ("اهلا",)}).intent == intent.GREETING


# ---------------------------------------------------------------------------
# Degradation and the shipped file
# ---------------------------------------------------------------------------

def test_an_empty_phrase_set_sends_everything_to_retrieval():
    assert classify("hello", phrases={}).needs_retrieval


def test_a_missing_phrase_file_degrades_to_retrieval_not_failure():
    """A missing data file must not take the assistant down: every query
    reaching retrieval is the behaviour that existed before AC02."""
    broken = dataclasses.replace(
        SETTINGS,
        intent=dataclasses.replace(
            SETTINGS.intent,
            phrases_path=SETTINGS.intent.phrases_path.with_name("absent.json"),
        ),
    )
    assert intent.load_phrases(broken) == {}
    assert intent.classify("hello", settings=broken).needs_retrieval


@pytest.mark.parametrize("text", ["", "   ", "?!", "١٢٣"])
def test_contentless_input_is_treated_as_a_normal_query(text):
    """AC01 rejects these before AC02 ever sees them; this module must not
    also try to have an opinion."""
    assert classify(text).needs_retrieval


def test_the_shipped_phrase_file_parses_and_covers_both_languages():
    table = intent.load_phrases(SETTINGS)
    assert set(table) == {intent.GREETING, intent.COURTESY,
                          intent.CAPABILITY, intent.SESSION_META}
    for name, phrases in table.items():
        assert phrases, name
        assert any(any("؀" <= c <= "ۿ" for c in p) for p in phrases), \
            f"{name} has no Arabic phrase"
        assert any(p.isascii() for p in phrases), f"{name} has no English phrase"


def test_shipped_phrases_are_stored_normalised_so_matching_is_exact():
    for phrases in intent.load_phrases(SETTINGS).values():
        for phrase in phrases:
            assert intent.normalise(phrase) == phrase


def test_no_shipped_phrase_could_plausibly_open_a_real_question():
    """A phrase that is also a question opening would swallow questions.

    The word cap already bounds the damage; this keeps the *data* honest
    too, since the file is the part most likely to be edited casually.
    """
    openers = {"what", "which", "when", "who", "how", "why",
               "ما", "ماذا", "بماذا", "متى", "من", "كيف", "اي", "لماذا", "هل"}
    for name, phrases in intent.load_phrases(SETTINGS).items():
        for phrase in phrases:
            words = phrase.split()
            if words[0] in openers:
                assert len(words) >= 3, f"{name}: {phrase!r} is too broad"


def test_the_raw_file_comment_key_is_not_loaded_as_an_intent():
    raw = json.loads(SETTINGS.intent.phrases_path.read_text(encoding="utf-8"))
    assert "_comment" in raw
    assert "_comment" not in intent.load_phrases(SETTINGS)
