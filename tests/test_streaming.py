"""The incremental reader that makes streaming possible.

`AnswerTextStream` reads the `answer` field out of a JSON document that is
still arriving. Everything it does is boundary handling, so it is tested by
feeding the same document split in every awkward way: one character at a
time, split inside an escape, split inside a `\\uXXXX`, split between the
key and its colon.

⚠️ It is a reader, not an authority. `parse_response` still parses the
complete document at the end and produces the citations, language and
intent. These tests pin the preview; they do not pin meaning.
"""
from __future__ import annotations

import json

import pytest

from Application.backend.streaming import AnswerTextStream

DOC = ('{"language":"ar","intent":"question",'
       '"answer":"الميزانية '
       '22,900,000","used_passages":[2,5],"used_project_record":true}')
EXPECTED = "الميزانية 22,900,000"


def drain(chunks) -> str:
    reader = AnswerTextStream()
    return "".join(reader.feed(c) for c in chunks)


# ---------------------------------------------------------------------------
# It finds the answer, however the document is cut up
# ---------------------------------------------------------------------------

def test_the_whole_document_at_once():
    assert drain([DOC]) == EXPECTED


def test_one_character_at_a_time():
    """The worst case, and the one that catches state kept in the wrong
    place."""
    assert drain(list(DOC)) == EXPECTED


@pytest.mark.parametrize("cut", range(1, len(DOC)))
def test_split_at_every_possible_point(cut):
    """Every boundary, not a sampled few — a reader is exactly the kind of
    code that works for all but one offset."""
    assert drain([DOC[:cut], DOC[cut:]]) == EXPECTED


def test_nothing_is_emitted_before_the_answer_begins():
    reader = AnswerTextStream()
    assert reader.feed('{"language":"ar","intent":"question","ans') == ""


def test_nothing_is_emitted_after_the_answer_ends():
    reader = AnswerTextStream()
    reader.feed('{"answer":"hi"')
    assert reader.feed(',"used_passages":[1,2,3]}') == ""


# ---------------------------------------------------------------------------
# Escapes
# ---------------------------------------------------------------------------

def test_an_escaped_quote_is_decoded_not_treated_as_the_end():
    doc = json.dumps({"answer": 'he said "no"'})
    assert drain([doc]) == 'he said "no"'


def test_a_newline_escape_is_decoded():
    assert drain([json.dumps({"answer": "a\nb"})]) == "a\nb"


def test_a_backslash_escape_is_decoded():
    assert drain([json.dumps({"answer": "a\\b"})]) == "a\\b"


def test_a_unicode_escape_is_decoded():
    """`ensure_ascii` escapes every Arabic character, which is the normal
    case for a provider that does it."""
    doc = json.dumps({"answer": "مرحبا"})          # مر...
    assert "\\u" in doc
    assert drain([doc]) == "مرحبا"


@pytest.mark.parametrize("cut", range(1, 12))
def test_a_unicode_escape_split_across_chunks(cut):
    """⚠️ Half an escape must never reach the browser. `\\u06` rendered
    literally is visible corruption."""
    doc = json.dumps({"answer": "مرحبا"}, separators=(",", ":"))
    head = '{"answer":"'
    assert doc.startswith(head), doc
    body = doc[len(head):]
    assert drain([head + body[:cut], body[cut:]]) == "مرحبا"


def test_a_malformed_escape_does_not_raise():
    """A reader that threw would turn a cosmetic problem into a failed
    answer, when the authoritative parse has not even run yet."""
    reader = AnswerTextStream()
    out = reader.feed('{"answer":"a\\uZZ')
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# It reads the right field
# ---------------------------------------------------------------------------

def test_a_key_named_answer_inside_a_value_is_not_mistaken_for_the_field():
    """The enum fields cannot contain this today. The scanner does not rely
    on that, so a schema change cannot quietly break it."""
    doc = json.dumps({"intent": '"answer":"decoy"', "answer": "real"})
    assert drain([doc]) == "real"


def test_an_answer_shaped_substring_in_an_earlier_value_is_ignored():
    doc = json.dumps({"language": 'answer', "answer": "real"})
    assert drain([doc]) == "real"


def test_the_empty_answer_yields_nothing():
    assert drain([json.dumps({"answer": "", "used_passages": []})]) == ""


def test_finished_reports_when_the_answer_closed():
    reader = AnswerTextStream()
    reader.feed('{"answer":"done"')
    assert reader.finished
    assert reader.feed(',"used_passages":[]}') == ""


def test_it_is_not_finished_while_the_answer_is_still_open():
    reader = AnswerTextStream()
    reader.feed('{"answer":"stil')
    assert not reader.finished


# ---------------------------------------------------------------------------
# What streams matches what the final parse will find
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "plain", "مرحبا بك", 'with "quotes"', "with\nnewline",
    "mixed عربي and English", "tab\there", "back\\slash",
    "22,900,000 ريال", "", "‏RTL mark",
])
def test_the_stream_reproduces_what_json_parsing_gives(text):
    """The property that matters: streaming and parsing must agree, or the
    user watches one answer appear and a different one settle."""
    for ensure_ascii in (True, False):
        doc = json.dumps({"language": "ar", "intent": "question",
                          "answer": text, "used_passages": []},
                         ensure_ascii=ensure_ascii)
        assert drain(list(doc)) == json.loads(doc)["answer"]
