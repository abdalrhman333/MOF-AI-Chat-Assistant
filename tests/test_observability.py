"""Tests for client caching (AC08) and tracing.

**Two claims, and the second one matters more than it looks.**

  1. **Clients are built once per settings.** This is an AC08 fix, not
     tidiness: rebuilding them cost roughly 2.3 seconds per question. The
     cache key is the settings object, so a test using
     `dataclasses.replace` still gets its own client rather than silently
     inheriting one built for a different endpoint.
  2. **Tracing is fail-open.** It is the only thing in this repository that
     is, and deliberately: an unreachable collector, a bad endpoint or a
     missing package must never stop a project manager getting an answer.
     Every test that breaks the tracer asserts the *answer still arrives*.

Nothing here calls a paid API, touches the live vector store, or sends a
span to a real collector — the exporter is in-memory.
"""
from __future__ import annotations

import dataclasses

import pytest

from Application.backend import clients
from Application.backend import conversation as conv
from Application.backend import retrieval as ret
from Application.backend.session import Session
from evaluation import latency_bench
from observability import tracing
from pipeline.config import get_settings

SETTINGS = get_settings()
SESSION = Session(username="tester", project_code="PRJ-TEST")


@pytest.fixture(autouse=True)
def _isolate():
    """No test may leak a provider or a cached client into the next."""
    clients.reset()
    tracing.shutdown()
    yield
    clients.reset()
    tracing.shutdown()


def traced(**overrides):
    base = dict(enabled=True, langfuse_enabled=False, otlp_enabled=True)
    base.update(overrides)
    return dataclasses.replace(
        SETTINGS,
        observability=dataclasses.replace(SETTINGS.observability, **base),
    )


@pytest.fixture
def spans(monkeypatch):
    """Configure tracing against an in-memory exporter and yield the sink."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    sink = InMemorySpanExporter()
    monkeypatch.setattr(tracing, "_exporters", lambda _c: [("memory", sink)])
    tracing.configure(traced(), force=True)
    yield sink
    tracing.shutdown()


# ---------------------------------------------------------------------------
# 1. Client caching — the AC08 fix
# ---------------------------------------------------------------------------

def test_a_client_is_built_once_per_settings(monkeypatch):
    built = []
    monkeypatch.setattr("pipeline.indexer.build_embedder",
                        lambda s: built.append(s) or object())
    first = clients.embedder(SETTINGS)
    second = clients.embedder(SETTINGS)
    assert first is second
    assert len(built) == 1, "rebuilding cost ~2.3s per question — see clients.py"


def test_different_settings_get_different_clients(monkeypatch):
    """The cache key is the settings object, so a test that changes an
    endpoint is not silently handed a client built for the old one."""
    monkeypatch.setattr("pipeline.indexer.build_embedder", lambda s: object())
    other = dataclasses.replace(
        SETTINGS, models=dataclasses.replace(SETTINGS.models,
                                             llm_deployment="other-model"))
    assert clients.embedder(SETTINGS) is not clients.embedder(other)


def test_reset_drops_every_cached_client(monkeypatch):
    monkeypatch.setattr("pipeline.indexer.build_embedder", lambda s: object())
    first = clients.embedder(SETTINGS)
    clients.reset()
    assert clients.embedder(SETTINGS) is not first


def test_retrieval_uses_the_cache_rather_than_building_per_question(monkeypatch):
    built = []
    monkeypatch.setattr("pipeline.indexer.build_embedder",
                        lambda s: built.append(1) or object())
    ret._build_embedder(SETTINGS)
    ret._build_embedder(SETTINGS)
    assert len(built) == 1


def test_warmup_reports_timings_for_every_client(monkeypatch):
    monkeypatch.setattr("pipeline.indexer.build_embedder", lambda s: object())
    monkeypatch.setattr("pipeline.indexer.build_qdrant", lambda s: object())
    monkeypatch.setattr("Application.backend.answer.build_llm", lambda s: object())
    result = clients.warmup(SETTINGS, probe=False)
    assert {"embedder", "vector_store", "llm"} <= set(result)
    assert isinstance(result["total_ms"], int)


def test_warmup_never_raises_when_a_client_cannot_be_built(monkeypatch):
    """An assistant that refuses to start because a pre-warm failed is worse
    than one that pays the cost on the first question."""
    def boom(_settings):
        raise ConnectionError("endpoint unreachable")

    monkeypatch.setattr("pipeline.indexer.build_embedder", boom)
    monkeypatch.setattr("pipeline.indexer.build_qdrant", lambda s: object())
    monkeypatch.setattr("Application.backend.answer.build_llm", lambda s: object())
    result = clients.warmup(SETTINGS, probe=False)
    assert "failed" in str(result["embedder"])
    assert isinstance(result["vector_store"], int)


def test_warmup_never_raises_when_the_probe_fails(monkeypatch):
    class Broken:
        def __init__(self):
            self.embeddings = self

        def create(self, **_kwargs):
            raise TimeoutError("slow")

    monkeypatch.setattr("pipeline.indexer.build_embedder", lambda s: Broken())
    monkeypatch.setattr("pipeline.indexer.build_qdrant", lambda s: object())
    monkeypatch.setattr("Application.backend.answer.build_llm", lambda s: object())
    assert "failed" in str(clients.warmup(SETTINGS, probe=True)["probe"])


# ---------------------------------------------------------------------------
# 2. Tracing is off unless asked for
# ---------------------------------------------------------------------------

def test_tracing_stays_off_when_it_is_not_enabled():
    """`OBSERVABILITY_ENABLED=false` must configure nothing at all — a
    developer running the suite should not need a collector."""
    assert tracing.configure(traced(enabled=False), force=True) == ()
    assert not tracing.is_active()


def test_a_span_is_a_working_no_op_when_tracing_is_off():
    tracing.configure(traced(enabled=False), force=True)
    ran = []
    with tracing.span("x", a=1) as current:
        current.set_attribute("b", 2)          # must not raise
        ran.append(True)
    assert ran == [True], "the block must run whether or not tracing is on"


def test_shutdown_is_safe_when_tracing_was_never_configured():
    tracing.shutdown()
    tracing.shutdown()


# ---------------------------------------------------------------------------
# 3. Tracing is fail-open — the claim that matters
# ---------------------------------------------------------------------------

def test_configure_never_raises_on_a_broken_backend(monkeypatch):
    def boom(_config):
        raise RuntimeError("exporter package missing")

    monkeypatch.setattr(tracing, "_exporters", boom)
    assert tracing.configure(traced(), force=True) == ()
    assert not tracing.is_active()


def test_a_backend_that_yields_no_exporter_leaves_tracing_inactive(monkeypatch):
    monkeypatch.setattr(tracing, "_exporters", lambda _c: [])
    assert tracing.configure(traced(), force=True) == ()


def test_langfuse_is_skipped_when_its_keys_are_missing():
    config = traced(langfuse_enabled=True, otlp_enabled=False,
                    langfuse_public_key="", langfuse_secret_key="")
    assert not config.observability.langfuse_ready
    assert tracing._exporters(config.observability) == []


def test_langfuse_is_used_when_its_keys_are_present():
    config = traced(langfuse_enabled=True, otlp_enabled=False,
                    langfuse_public_key="pk", langfuse_secret_key="sk")
    assert [n for n, _ in tracing._exporters(config.observability)] == ["langfuse"]


def test_a_turn_is_still_answered_when_the_tracer_explodes(monkeypatch):
    """The whole point of fail-open, asserted end to end rather than by
    reading the code."""
    class ExplodingTracer:
        def start_as_current_span(self, *_a, **_kw):
            raise RuntimeError("tracing backend on fire")

    tracing.configure(traced(), force=True)
    monkeypatch.setattr(tracing, "_tracer", ExplodingTracer())

    class StubLLM:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **_kwargs):
            payload = ('{"language": "ar", "intent": "project_question", '
                       '"answer": "22,900,000", "used_passages": [1], '
                       '"used_project_record": false}')
            message = type("M", (), {"content": payload})()
            return type("R", (), {
                "choices": [type("C", (), {"message": message})()]})()

    passage = ret.Passage(rank=1, score=0.6, filename="c.docx",
                          doc_type="Project Charter", page=1, section="",
                          text="budget 22,900,000")
    reply = conv.handle_question(
        SESSION, "ما الميزانية؟", settings=traced(), project_record=None,
        retrieval=ret.RetrievalResult(passages=[passage]), llm=StubLLM())
    assert reply.outcome == conv.OUTCOME_ANSWERED
    assert "22,900,000" in reply.text


def test_span_swallows_a_tracer_failure_and_still_runs_the_block(monkeypatch):
    class ExplodingTracer:
        def start_as_current_span(self, *_a, **_kw):
            raise RuntimeError("tracer broken")

    monkeypatch.setattr(tracing, "_tracer", ExplodingTracer())
    ran = []
    with tracing.span("x") as current:
        current.set_attribute("a", 1)
        ran.append(True)
    assert ran == [True]


# ---------------------------------------------------------------------------
# 4. Spans carry what makes them useful
# ---------------------------------------------------------------------------

def test_a_span_records_its_attributes(spans):
    with tracing.span("chat.turn", project="PRJ-06", turn=1):
        pass
    tracing.shutdown()
    recorded = spans.get_finished_spans()
    assert [s.name for s in recorded] == ["chat.turn"]
    assert recorded[0].attributes["project"] == "PRJ-06"


def test_none_attributes_do_not_reach_the_exported_span(spans):
    with tracing.span("x", present="yes", missing=None):
        pass
    tracing.shutdown()
    attributes = spans.get_finished_spans()[0].attributes
    assert attributes["present"] == "yes"
    assert "missing" not in attributes


def test_filtering_none_attributes_keeps_the_log_quiet(spans, caplog):
    """What that filter actually guards, found by mutation testing.

    OpenTelemetry drops a `None` attribute by itself, so removing the
    filter changes no exported span and the test above cannot see it. What
    it does change is a warning per attribute per span per turn — which is
    how a log stops being read. Assert the thing that actually differs.
    """
    import logging
    with caplog.at_level(logging.WARNING):
        with tracing.span("x", present="yes", missing=None):
            pass
    assert not [r for r in caplog.records if "missing" in r.getMessage()]


def test_spans_are_exported_in_the_background_never_on_the_request_thread(spans):
    """A synchronous processor would put the collector's latency straight
    into the AC08 budget — the failure mode of naive tracing."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    tracing.configure(traced(), force=True)
    processors = tracing._provider._active_span_processor._span_processors
    assert processors, "no span processor was installed"
    assert all(isinstance(p, BatchSpanProcessor) for p in processors), \
        f"a non-batching processor exports on the caller's thread: {processors}"


def test_nested_spans_share_one_trace(spans):
    with tracing.span("chat.turn"):
        with tracing.span("retrieval.embed"):
            pass
    tracing.shutdown()
    recorded = spans.get_finished_spans()
    assert len({s.context.trace_id for s in recorded}) == 1, \
        "a turn and its phases must be one trace, not two"


# ---------------------------------------------------------------------------
# 5. capture_content is a real switch, because traces carry document text
# ---------------------------------------------------------------------------

def _answer_once(settings, spans_sink):
    from Application.backend import answer as ans

    class StubLLM:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **_kwargs):
            payload = ('{"language": "ar", "intent": "project_question", '
                       '"answer": "22,900,000", "used_passages": [1], '
                       '"used_project_record": false}')
            message = type("M", (), {"content": payload})()
            return type("R", (), {
                "choices": [type("C", (), {"message": message})()],
                "usage": type("U", (), {"prompt_tokens": 120,
                                        "completion_tokens": 8})()})()

    passage = ret.Passage(rank=1, score=0.6, filename="charter.docx",
                          doc_type="Project Charter", page=1, section="",
                          text="SECRET-PASSAGE-TEXT budget 22,900,000")
    ans.answer_question(
        SESSION, "ما الميزانية؟", settings=settings,
        retrieval=ret.RetrievalResult(passages=[passage]),
        project_record=None, llm=StubLLM())
    tracing.shutdown()
    return spans_sink.get_finished_spans()


def test_capture_content_off_keeps_document_text_out_of_the_trace(spans):
    """A trace store has no notion of AC09: anyone who can read it can read
    every project's passages. This switch is how that is contained.

    Both directions are asserted. Checking only the prompt let a mutation
    survive that captured the completion regardless of the switch — and a
    completion quotes the passages back.
    """
    recorded = _answer_once(traced(capture_content=False), spans)
    blob = repr([dict(s.attributes) for s in recorded])
    assert "SECRET-PASSAGE-TEXT" not in blob
    assert tracing.LF_INPUT not in blob, "the prompt leaked"
    assert tracing.LF_OUTPUT not in blob, "the completion leaked"
    assert "22,900,000" not in blob


def test_capture_content_on_records_the_prompt_and_completion(spans):
    recorded = _answer_once(traced(capture_content=True), spans)
    chat = [s for s in recorded if s.name.startswith("chat ")]
    assert chat, "the LLM call must produce a span"
    assert "SECRET-PASSAGE-TEXT" in chat[0].attributes[tracing.LF_INPUT]
    assert "22,900,000" in chat[0].attributes[tracing.LF_OUTPUT]


def test_token_usage_is_recorded_even_when_content_is_not(spans):
    """Timing and token counts are most of the operational value, and they
    survive turning content capture off."""
    recorded = _answer_once(traced(capture_content=False), spans)
    chat = [s for s in recorded if s.name.startswith("chat ")][0]
    assert chat.attributes[tracing.GEN_AI_USAGE_INPUT] == 120
    assert chat.attributes[tracing.GEN_AI_USAGE_OUTPUT] == 8
    assert chat.attributes[tracing.GEN_AI_REQUEST_MODEL]


# ---------------------------------------------------------------------------
# 6. The AC08 harness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("values,fraction,expected", [
    ([100], 0.95, 100),
    ([1, 2, 3, 4, 5], 0.50, 3),
    ([1, 2, 3, 4, 5], 0.95, 5),
    ([], 0.95, 0),
])
def test_percentile(values, fraction, expected):
    assert latency_bench.percentile(values, fraction) == expected


def test_the_budget_verdict_uses_p95_and_a_slow_tail_fails_it():
    """A mean hides exactly the turns that break the budget.

    Nineteen fast turns and one very slow one average under a second, so a
    mean-based verdict passes a run a user would call broken.
    """
    assert latency_bench.BUDGET_SECONDS == 5.0
    tail = [500] * 12 + [9000] * 8
    assert sum(tail) / len(tail) < 5000, "the mean would pass this"
    assert not latency_bench.verdict(tail, 5000), "p95 must fail it"


def test_the_budget_verdict_passes_a_run_that_is_genuinely_fast():
    assert latency_bench.verdict([1800] * 20, 5000)


def test_an_empty_run_is_never_reported_as_meeting_the_budget():
    """Zero measurements is not a pass."""
    assert not latency_bench.verdict([], 5000)


def test_a_lone_outlier_does_not_fail_the_budget_but_is_still_reported():
    """AC08 says "under normal conditions", which is a percentile
    statement — one slow turn in twenty is not the normal condition.

    A deliberate reading, recorded rather than left implicit: `max_ms` is
    reported alongside p95 precisely so the outlier stays visible instead
    of being averaged away.
    """
    values = [500] * 19 + [30000]
    assert latency_bench.verdict(values, 5000)
    assert max(values) > 5000


# ---------------------------------------------------------------------------
# 7. The harness must not flatter itself
# ---------------------------------------------------------------------------

def _question(qid, project):
    from evaluation.dataset import Question
    return Question(id=qid, project=project, lang="ar", text="س؟",
                    expect_documents=())


def test_a_project_with_no_assignment_is_reported_not_dropped():
    """AC09 forbids opening a session there at all, so it cannot be
    measured — but a benchmark whose denominator quietly shrinks is a
    benchmark that flatters itself."""
    questions = [_question("a", "PRJ-06"), _question("b", "PRJ-08")]
    runnable, skipped = latency_bench.plan(questions, {"PRJ-06": "noura"})
    assert [q.id for q, _ in runnable] == ["a"]
    assert [s["id"] for s in skipped] == ["b"]
    assert "AC09" in skipped[0]["why"]


def test_the_plan_pairs_each_question_with_its_assigned_user():
    questions = [_question("a", "PRJ-06"), _question("b", "PRJ-01")]
    runnable, skipped = latency_bench.plan(
        questions, {"PRJ-06": "noura", "PRJ-01": "fahad"})
    assert skipped == []
    assert [user for _, user in runnable] == ["noura", "fahad"]
