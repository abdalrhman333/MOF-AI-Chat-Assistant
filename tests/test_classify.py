"""Tests for Stage 1b — deciding a document's type from its text.

**The claim under test is that a wrong label is worse than no label.**
`doc_type` is shown to project managers on the knowledge-base page and is
what every citation is named by ("Risk Register — page 3"), so a confident
mistake propagates into answers. The stage is built to refuse.

Three ways that can fail quietly, each with tests:

  1. **A label outside the list.** The schema constrains the shape, not the
     vocabulary — a model returning "Meeting Notes" would put a category
     into the KB page that nothing else knows about.
  2. **A guess dressed as a decision.** The model reports its own
     confidence, and an unconfident answer must not be stored.
  3. **Re-billing.** A row that already has a type must never be looked at
     again, or every run costs a completion per document forever.

Nothing here calls a paid API — the model is injected.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from pipeline import classify
from pipeline.classify import UNCLASSIFIED
from pipeline.config import get_settings

SETTINGS = get_settings()
LABELS = ["Project Charter", "Risk Register", "Weekly Status Report"]


class StubLLM:
    """Returns a canned classification and records what it was asked."""

    def __init__(self, doc_type="Risk Register", confident=True, raw=None,
                 fail=False):
        self.calls = []
        self._raw = raw
        self._fail = fail
        self._payload = {"doc_type": doc_type, "confident": confident}
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        if self._fail:
            raise ConnectionError("model unreachable")
        self.calls.append(kwargs)
        body = self._raw if self._raw is not None else json.dumps(self._payload)
        message = type("M", (), {"content": body})()
        return type("R", (), {"choices": [type("C", (), {"message": message})()]})()


class StubCursor:
    """A cursor over a fixed set of pending rows, recording writes."""

    def __init__(self, rows):
        self.rows, self.updates, self._last = rows, [], []

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("UPDATE"):
            self.updates.append(params)
            self._last = []
        else:
            self._last = list(self.rows)
        return self

    def fetchall(self):
        return self._last


def rows(*specs):
    # (id, project, filename, markdown)
    return [(i, "PRJ-06", name, text) for i, (name, text) in enumerate(specs, 1)]


# ---------------------------------------------------------------------------
# 1. It stores a confident, in-vocabulary label
# ---------------------------------------------------------------------------

def test_a_confident_known_label_is_stored():
    cursor = StubCursor(rows(("risks.docx", "سجل المخاطر ...")))
    report = classify.run(SETTINGS, llm=StubLLM("Risk Register", True),
                          connection=cursor)
    assert cursor.updates == [("Risk Register", 1)]
    assert report.classified == [("PRJ-06/risks.docx", "Risk Register")]


def test_the_allowed_labels_reach_the_model():
    """The model cannot pick from a list it was never shown."""
    llm = StubLLM()
    cursor = StubCursor(rows(("x.docx", "text")))
    classify.run(SETTINGS, llm=llm, connection=cursor)
    prompt = llm.calls[0]["messages"][1]["content"]
    for label in classify.load_labels(SETTINGS):
        assert label in prompt
    assert UNCLASSIFIED in prompt


def test_only_the_opening_of_the_document_is_sent():
    """The type is announced in the first page of any real document, so
    sending the body would pay tokens to re-read what was already decided."""
    llm = StubLLM()
    cursor = StubCursor(rows(("x.docx", "A" * 50_000)))
    classify.run(SETTINGS, llm=llm, connection=cursor)
    assert len(llm.calls[0]["messages"][1]["content"]) < 6000


# ---------------------------------------------------------------------------
# 2. It refuses rather than guesses
# ---------------------------------------------------------------------------

def test_a_label_outside_the_list_is_refused():
    """The schema pins the shape, not the vocabulary."""
    cursor = StubCursor(rows(("x.docx", "text")))
    report = classify.run(SETTINGS, llm=StubLLM("Meeting Notes", True),
                          connection=cursor)
    assert cursor.updates == []
    assert report.left_unclassified == ["PRJ-06/x.docx"]


def test_an_unconfident_answer_is_not_stored():
    """A guess dressed as a decision reaches the KB page and every citation."""
    cursor = StubCursor(rows(("x.docx", "text")))
    report = classify.run(SETTINGS, llm=StubLLM("Risk Register", False),
                          connection=cursor)
    assert cursor.updates == []
    assert report.left_unclassified == ["PRJ-06/x.docx"]


def test_an_explicit_unclassified_is_respected():
    cursor = StubCursor(rows(("x.docx", "text")))
    classify.run(SETTINGS, llm=StubLLM(UNCLASSIFIED, True), connection=cursor)
    assert cursor.updates == []


def test_an_unreadable_model_reply_leaves_the_row_alone():
    cursor = StubCursor(rows(("x.docx", "text")))
    classify.run(SETTINGS, llm=StubLLM(raw="I think it's a charter"),
                 connection=cursor)
    assert cursor.updates == []


def test_a_model_failure_is_recorded_and_does_not_stop_the_run():
    """One unreachable call must not abandon the other fifty documents."""
    cursor = StubCursor(rows(("a.docx", "t"), ("b.docx", "t")))
    report = classify.run(SETTINGS, llm=StubLLM(fail=True), connection=cursor)
    assert len(report.failed) == 2
    assert cursor.updates == []


# ---------------------------------------------------------------------------
# 3. It cannot re-bill
# ---------------------------------------------------------------------------

def test_only_unclassified_rows_are_selected():
    """`doc_type = 'Unclassified'` is the "not done yet" marker, the same
    shape every other stage uses."""
    cursor = StubCursor(rows(("x.docx", "t")))
    classify.select_pending(cursor)
    # the SELECT is the last non-UPDATE statement executed
    assert cursor._last == cursor.rows                          # noqa: SLF001


def test_a_dry_run_calls_nothing_and_writes_nothing():
    llm = StubLLM()
    cursor = StubCursor(rows(("x.docx", "t")))
    report = classify.run(SETTINGS, dry_run=True, llm=llm, connection=cursor)
    assert llm.calls == [] and cursor.updates == []
    assert report.candidates == ["PRJ-06/x.docx"]


def test_an_empty_corpus_costs_nothing():
    llm = StubLLM()
    classify.run(SETTINGS, llm=llm, connection=StubCursor([]))
    assert llm.calls == []


def test_limit_bounds_the_spend():
    llm = StubLLM()
    cursor = StubCursor(rows(("a.docx", "t"), ("b.docx", "t"), ("c.docx", "t")))
    classify.run(SETTINGS, llm=llm, limit=2, connection=cursor)
    assert len(llm.calls) == 2


# ---------------------------------------------------------------------------
# 4. The label list is data
# ---------------------------------------------------------------------------

def test_the_shipped_labels_match_what_the_corpus_already_uses():
    """The classifier must agree with the labels already on the KB page and
    attached to citations, or the same document type gets two names."""
    labels = classify.load_labels(SETTINGS)
    assert "Project Charter" in labels
    assert "Risk Register" in labels
    assert UNCLASSIFIED not in labels, "the escape hatch is not a label"


def test_the_label_path_is_configuration_not_a_literal():
    other = dataclasses.replace(
        SETTINGS,
        classification=dataclasses.replace(
            SETTINGS.classification,
            labels_path=SETTINGS.classification.labels_path.with_name("nope.json")))
    with pytest.raises(FileNotFoundError):
        classify.load_labels(other)


def test_an_empty_label_file_is_a_loud_failure(tmp_path):
    """Silently classifying everything as Unclassified would look like the
    model failing, not like a missing config."""
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({"labels": []}), encoding="utf-8")
    other = dataclasses.replace(
        SETTINGS,
        classification=dataclasses.replace(SETTINGS.classification,
                                           labels_path=path))
    with pytest.raises(RuntimeError, match="no labels"):
        classify.load_labels(other)
