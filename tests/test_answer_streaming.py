"""Streaming a turn must change *when* text appears and nothing else.

`answer_question(..., on_text=cb)` switches the completion to a streamed
one. There is deliberately no second code path: the raw JSON is still
accumulated, still parsed by `parse_response`, and still produces the same
`Answer`. These tests exist to keep that true, because the moment the two
modes diverge every latency measurement taken on the non-streamed path
becomes a claim about something users do not use.

The property most worth pinning is the boring one: **stream a turn and
answer it normally, and the two must agree** — same text, same citations,
same language, same intent. Everything else here is a way that could stop
being true.

⚠️ Streaming does not make a slow answer fast. `total_ms` is unchanged;
`first_token_ms` is a second, separate number. A test below pins that
distinction so AC08 cannot be quietly redefined into passing.

Nothing here calls a paid API.
"""
from __future__ import annotations

import json

import pytest

from Application.backend import answer as ans
from Application.backend.errors import BackendUnavailable
from tests.test_answering import (CORPUS, SESSION, StubEmbedder, StubLLM,
                                  StubQdrant, settings)

PAYLOAD = {"language": "ar", "intent": "project_question",
           "answer": "الميزانية المعتمدة هي 22,900,000 ريال.",
           "used_passages": [2], "used_project_record": False}


class StreamingLLM:
    """Replies in chunks, the way the provider does.

    `usage_event` mirrors the provider's final usage-only event, which
    carries no choices — the shape that makes token accounting work for a
    streamed call.
    """

    def __init__(self, payload=None, size=7, usage=True, raw=None):
        self.body = raw if raw is not None else json.dumps(
            payload or PAYLOAD, ensure_ascii=False)
        self.size = size
        self.usage = usage
        self.calls = []
        self.chat = type("C", (), {"completions": self})()

    def create(self, model, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not kwargs.get("stream"):
            raise AssertionError("on_text must switch the call to streaming")
        return self._events()

    def _events(self):
        for i in range(0, len(self.body), self.size):
            yield _chunk(self.body[i:i + self.size])
        if self.usage:
            yield _usage(prompt_tokens=120, completion_tokens=35)


def _chunk(text):
    delta = type("D", (), {"content": text})()
    choice = type("Ch", (), {"delta": delta})()
    return type("E", (), {"choices": [choice], "usage": None})()


def _usage(**kw):
    usage = type("U", (), kw)()
    return type("E", (), {"choices": [], "usage": usage})()


def run(llm, on_text=None, **kw):
    kw.setdefault("project_record", None)
    collected = []
    answer = ans.answer_question(
        SESSION, "ما الميزانية؟", settings=settings(),
        embedder=StubEmbedder(), qdrant=StubQdrant(CORPUS), llm=llm,
        on_text=on_text if on_text is not None else collected.append, **kw)
    return answer, collected


# ---------------------------------------------------------------------------
# The two modes agree
# ---------------------------------------------------------------------------

def test_streaming_and_not_streaming_produce_the_same_answer():
    """The test this file exists for."""
    body = json.dumps(PAYLOAD, ensure_ascii=False)
    streamed, _ = run(StreamingLLM())
    normal = ans.answer_question(
        SESSION, "ما الميزانية؟", settings=settings(),
        embedder=StubEmbedder(), qdrant=StubQdrant(CORPUS),
        llm=StubLLM(raw=body), project_record=None)

    assert streamed.text == normal.text
    assert [c.label for c in streamed.citations] == \
           [c.label for c in normal.citations]
    assert (streamed.language, streamed.intent) == \
           (normal.language, normal.intent)


def test_the_pieces_joined_are_the_answer():
    """What the reader watched and what they are left with must match."""
    answer, pieces = run(StreamingLLM())
    assert "".join(pieces) == answer.text
    assert answer.text == PAYLOAD["answer"]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 11, 100, 10_000])
def test_any_chunk_size_gives_the_same_text(size):
    """Chunk boundaries are the provider's business, not ours."""
    answer, pieces = run(StreamingLLM(size=size))
    assert "".join(pieces) == PAYLOAD["answer"]
    assert answer.text == PAYLOAD["answer"]


def test_an_escaped_payload_streams_correctly():
    """A provider that escapes Arabic sends `\\u0645…`, and the reader has
    to decode it before the browser sees it."""
    body = json.dumps(PAYLOAD, ensure_ascii=True)
    assert "\\u" in body
    answer, pieces = run(StreamingLLM(raw=body, size=3))
    assert "".join(pieces) == PAYLOAD["answer"]
    assert answer.text == PAYLOAD["answer"]


# ---------------------------------------------------------------------------
# Citations still come from the final parse, not the stream
# ---------------------------------------------------------------------------

def test_citations_are_resolved_after_the_text_has_streamed():
    answer, _ = run(StreamingLLM())
    assert [c.number for c in answer.citations] == [2]
    assert answer.citations[0].filename == "Charter.docx"


def test_a_hallucinated_passage_number_is_still_dropped_when_streaming():
    """AC06 does not get weaker because the text arrived in pieces."""
    payload = dict(PAYLOAD, used_passages=[1, 99])
    answer, _ = run(StreamingLLM(payload=payload))
    assert [c.number for c in answer.citations] == [1]


def test_a_refusal_streams_its_text_but_carries_no_citations():
    payload = {"language": "en", "intent": "out_of_scope",
               "answer": "That is outside this project.",
               "used_passages": [1], "used_project_record": False}
    answer, pieces = run(StreamingLLM(payload=payload))
    assert "".join(pieces) == payload["answer"]
    assert answer.citations == []


# ---------------------------------------------------------------------------
# Timings
# ---------------------------------------------------------------------------

def test_a_streamed_turn_records_time_to_first_text():
    answer, _ = run(StreamingLLM())
    assert answer.timings.first_token_ms is not None
    assert answer.timings.first_token_ms >= 0


def test_time_to_first_text_is_not_folded_into_total():
    """⚠️ AC08's budget is the whole turn. Streaming changes the wait, not
    the number, and the two must stay separately reported."""
    answer, _ = run(StreamingLLM())
    t = answer.timings
    assert t.total_ms == t.embed_ms + t.search_ms + t.spm_ms + t.llm_ms
    assert t.time_to_first_text_ms == (t.embed_ms + t.search_ms + t.spm_ms
                                       + t.first_token_ms)


def test_an_answer_with_no_text_reports_no_first_token_time():
    """Rather than a zero, which would average into a flattering figure."""
    payload = dict(PAYLOAD, answer="")
    answer, pieces = run(StreamingLLM(payload=payload))
    assert pieces == []
    assert answer.timings.first_token_ms is None
    assert answer.timings.time_to_first_text_ms is None


# ---------------------------------------------------------------------------
# The provider contract
# ---------------------------------------------------------------------------

def test_usage_is_requested_so_token_accounting_survives():
    """⚠️ Without `include_usage` a streamed response carries no usage and
    every streamed turn silently reports zero tokens."""
    llm = StreamingLLM()
    run(llm)
    assert llm.calls[0]["stream"] is True
    assert llm.calls[0]["stream_options"] == {"include_usage": True}


def test_the_schema_is_still_requested_when_streaming():
    """The whole design rests on the response being the strict JSON object,
    with language and intent before the answer."""
    llm = StreamingLLM()
    run(llm)
    assert llm.calls[0]["response_format"] is ans.RESPONSE_SCHEMA


def test_a_stream_that_never_sends_usage_still_answers():
    """Usage is accounting, not correctness."""
    answer, pieces = run(StreamingLLM(usage=False))
    assert answer.text == PAYLOAD["answer"]


def test_a_failure_mid_stream_is_reported_as_backend_unavailable():
    class Breaks(StreamingLLM):
        def _events(self):
            yield _chunk('{"language":"ar","intent":"project_question",')
            raise ConnectionError("stream died")

    with pytest.raises(BackendUnavailable):
        run(Breaks())


def test_without_on_text_the_call_is_not_streamed():
    """The default path must stay exactly as it was."""
    llm = StubLLM()
    answer = ans.answer_question(
        SESSION, "what?", settings=settings(), embedder=StubEmbedder(),
        qdrant=StubQdrant(CORPUS), llm=llm, project_record=None)
    assert "stream" not in llm.calls[0]
    assert answer.timings.first_token_ms is None


def test_total_never_includes_the_first_text_number():
    """⚠️ Asserted on a hand-built `Timings`, not on a stubbed turn.

    A stub answers instantly, so `first_token_ms` measures 0 there and
    folding it into `total_ms` would change nothing — which is exactly how
    that mutation survived the first pass. With a real value the two
    numbers have to stay separate, or AC08's budget quietly starts being
    measured against a smaller total than the user waits.
    """
    t = ans.Timings(embed_ms=300, search_ms=20, spm_ms=10, llm_ms=2000,
                    first_token_ms=1500)
    assert t.total_ms == 2330
    assert t.time_to_first_text_ms == 1830
    assert t.total_ms > t.time_to_first_text_ms
