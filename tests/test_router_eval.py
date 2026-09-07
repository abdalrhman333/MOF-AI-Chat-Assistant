"""Tests for the router eval.

**A harness that cannot fail measures nothing**, so most of what is asserted
here is that it reports misses, errors and below-threshold runs honestly
rather than flattering the system it grades.

The model is injected, so none of this costs a completion — which is the
whole reason `run()` takes an `ask` callable.
"""
from __future__ import annotations

import json

import pytest

from evaluation import router_eval


def probe(text, language, intent, note=""):
    return {"text": text, "language": language, "intent": intent, "note": note}


PROBES = [
    probe("مرحبا", "ar", "greeting"),
    probe("what is the budget?", "en", "project_question"),
    probe("Wie hoch ist das Budget?", "other", "unsupported_language"),
]


def perfect(text):
    return {"مرحبا": ("ar", "greeting"),
            "what is the budget?": ("en", "project_question"),
            "Wie hoch ist das Budget?": ("other", "unsupported_language")}[text]


# ---------------------------------------------------------------------------
# It can pass
# ---------------------------------------------------------------------------

def test_a_perfect_run_passes():
    summary = router_eval.run(probes=PROBES, ask=perfect)
    assert summary["ok"]
    assert summary["language_accuracy"] == 1.0
    assert summary["intent_accuracy"] == 1.0
    assert summary["misses"] == []


# ---------------------------------------------------------------------------
# It can fail — the half that matters
# ---------------------------------------------------------------------------

def test_a_wrong_language_is_reported():
    summary = router_eval.run(probes=PROBES,
                              ask=lambda t: ("en", perfect(t)[1]))
    assert summary["language_accuracy"] < 1.0
    assert not summary["ok"]


def test_a_wrong_intent_is_reported_with_a_confusion_row():
    """The confusion matrix is the useful part: knowing questions are being
    called out-of-scope points at retrieval, where a bare accuracy number
    points nowhere."""
    summary = router_eval.run(
        probes=PROBES, ask=lambda t: (perfect(t)[0], "out_of_scope"))
    assert summary["intent_accuracy"] < 1.0
    assert {"expected": "greeting", "actual": "out_of_scope", "count": 1} \
        in summary["confusion"]


def test_a_run_below_either_threshold_fails():
    """Both thresholds are load-bearing; passing one is not passing."""
    lang_only = router_eval.run(probes=PROBES,
                                ask=lambda t: (perfect(t)[0], "greeting"))
    assert lang_only["language_accuracy"] == 1.0
    assert not lang_only["ok"], "a bad intent score must fail the run"


def test_a_probe_that_raises_is_an_error_not_a_miss():
    """An unreachable model is an outage, and reporting it as 0% accuracy
    would send someone to edit the prompt instead of the network."""
    def boom(_text):
        raise ConnectionError("model unreachable")

    summary = router_eval.run(probes=PROBES, ask=boom)
    assert len(summary["errors"]) == len(PROBES)
    assert not summary["ok"]


def test_an_empty_run_is_never_a_pass():
    assert not router_eval.run(probes=[], ask=perfect)["ok"]


def test_limit_shortens_the_run():
    summary = router_eval.run(probes=PROBES, ask=perfect, limit=2)
    assert summary["n"] == 2


# ---------------------------------------------------------------------------
# The shipped probe set
# ---------------------------------------------------------------------------

def test_the_shipped_probes_use_only_taxonomy_the_schema_allows():
    """A probe expecting an intent the model cannot return would be a
    permanent, unfixable miss."""
    from Application.backend import answer as ans

    for row in router_eval.load_probes():
        assert row["intent"] in ans.INTENTS, row
        assert row["language"] in ans.LANGUAGES, row


def test_the_shipped_probes_cover_every_intent_and_language():
    from Application.backend import answer as ans

    probes = router_eval.load_probes()
    assert {p["intent"] for p in probes} == set(ans.INTENTS)
    assert {"ar", "en", "other"} <= {p["language"] for p in probes}


def test_no_probe_expects_an_answer_for_a_letterless_input():
    """Dropped rather than retagged: with no deterministic rule there is no
    defensible expectation for what the model should call `?!?!`, and a
    probe whose expected answer is a guess measures the tagger."""
    for row in router_eval.load_probes():
        assert row["text"].strip(), "an empty probe has no defensible expectation"
        assert any(c.isalpha() for c in row["text"]), row["text"]


def test_the_probe_file_is_valid_json_with_a_comment_block():
    import pathlib
    raw = json.loads(router_eval.PROBES_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw["_comment"], list) and raw["_comment"]
    assert isinstance(pathlib.Path(router_eval.PROBES_PATH), pathlib.Path)


def test_the_thresholds_are_stated_in_the_summary():
    """So a run's verdict can be read without knowing the module."""
    summary = router_eval.run(probes=PROBES, ask=perfect)
    assert summary["thresholds"]["language"] == router_eval.MIN_LANGUAGE_ACCURACY
    assert summary["thresholds"]["intent"] == router_eval.MIN_INTENT_ACCURACY
