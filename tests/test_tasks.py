"""Tests for pipeline/tasks.py — the background-job layer.

The stage logic itself is covered by test_scanner / test_ocr / test_indexer.
What is tested here is only what the task layer adds, which is where its
own bugs live:

  * a busy lock must be a **clean skip**, not a failure that triggers
    retries for something working as intended;
  * `ingest` must return an **outcome**, not a graph of task ids — the
    original implementation dispatched a Celery chain and returned
    immediately, so a scheduled run could not report whether it worked;
  * a failing stage must not stop the stages after it, and must still be
    visible in the result.

The stage functions are monkeypatched, so nothing here touches Postgres,
Azure or Qdrant — this is about orchestration, not ingestion.
"""
from __future__ import annotations

import uuid

import pytest

from pipeline import tasks
from pipeline.locking import AlreadyRunning, _client, stage_lock


@pytest.fixture(autouse=True)
def redis_available():
    try:
        _client().ping()
    except Exception as exc:                            # pragma: no cover
        pytest.skip(f"Redis not reachable — is mof-redis up? ({exc})")


@pytest.fixture
def fake_stages(monkeypatch):
    """Replace the three stage entry points with recorders."""
    calls: list[tuple[str, str | None]] = []

    class Report:
        def __init__(self):
            self.files_seen = 3
            self.missing_on_disk = []
            self.unknown_projects = []
            self.succeeded = ["a"]
            self.failed = []
            self.recovered = []
            self.thin = []
            self.pages_billed = 7
            self.indexed = [("a", 2)]
            self.chunks = 2
            self.tokens = 100
            self.collections_created = []

        @property
        def writes(self):
            return 1

        def of(self, _action):
            return []

    def scan(project=None, **_kw):
        calls.append(("scan", project))
        return Report()

    def ocr_run(project=None, **_kw):
        calls.append(("ocr", project))
        return Report()

    def index_run(project=None, **_kw):
        calls.append(("index", project))
        return Report()

    monkeypatch.setattr(tasks.scanner, "scan", scan)
    monkeypatch.setattr(tasks.ocr, "run", ocr_run)
    monkeypatch.setattr(tasks.indexer, "run", index_run)
    return calls


# ---------------------------------------------------------------------------
# Locking behaviour at the task layer
# ---------------------------------------------------------------------------

def test_a_busy_stage_is_skipped_cleanly_not_failed(fake_stages):
    """A scheduled run finding the previous one still going is normal.

    Raising would mark the task failed and trigger the retry policy for
    something behaving exactly as designed — turning a healthy overlap into
    log noise and three pointless retries.
    """
    with stage_lock("scan"):
        result = tasks.do_scan()

    assert result["skipped"] is True
    assert result["ok"] is True              # a skip is a success
    assert fake_stages == []                 # the stage never ran


def test_a_free_stage_runs_and_reports(fake_stages):
    result = tasks.do_scan()
    assert result["skipped"] is False
    assert result["stage"] == "scan"
    assert result["files"] == 3
    assert "seconds" in result
    assert fake_stages == [("scan", None)]


def test_the_lock_is_released_after_a_stage(fake_stages):
    tasks.do_scan()
    with stage_lock("scan"):                 # would raise if still held
        pass


def test_project_scope_is_passed_through(fake_stages):
    tasks.do_ocr("PRJ-05")
    assert fake_stages == [("ocr", "PRJ-05")]


# ---------------------------------------------------------------------------
# ingest — the part that was broken
# ---------------------------------------------------------------------------

def test_ingest_returns_an_outcome_not_task_ids(fake_stages):
    """REGRESSION: `ingest` used to dispatch a Celery chain and return
    immediately, so the caller got a nested list of task ids like
    `[['3dfdf99d…', [[…]]]]` and no way to know whether the pipeline
    worked. A scheduled ingest that cannot report failure is not a
    scheduled ingest.
    """
    result = tasks.do_ingest()

    assert result["ok"] is True
    assert [s["stage"] for s in result["stages"]] == ["scan", "ocr", "index"]
    assert all(isinstance(s, dict) for s in result["stages"])


def test_ingest_runs_the_stages_in_order(fake_stages):
    tasks.do_ingest()
    assert [name for name, _ in fake_stages] == ["scan", "ocr", "index"]


def test_ingest_passes_the_project_to_every_stage(fake_stages):
    tasks.do_ingest("PRJ-03")
    assert fake_stages == [("scan", "PRJ-03"), ("ocr", "PRJ-03"), ("index", "PRJ-03")]


def test_a_failing_stage_does_not_stop_the_rest(monkeypatch, fake_stages):
    """OCR failing for one document must not stop the other 57 being
    indexed — and the failure must still be reported, not swallowed."""
    def boom(project=None, **_kw):
        raise RuntimeError("Azure exploded")
    monkeypatch.setattr(tasks.ocr, "run", boom)

    result = tasks.do_ingest()

    assert result["ok"] is False
    stages = {s["stage"]: s for s in result["stages"]}
    assert stages["ocr"]["ok"] is False
    assert "Azure exploded" in stages["ocr"]["error"]
    assert stages["index"]["skipped"] is False        # index still ran


def test_ingest_result_is_json_serialisable(fake_stages):
    """Celery's result backend is configured json-only. A result it cannot
    serialise fails the task *after* the work succeeded, which is the most
    confusing failure available."""
    import json

    json.dumps(tasks.do_ingest())


def test_a_skipped_stage_keeps_ingest_ok(fake_stages):
    """Overlapping with a manual run is not an ingest failure."""
    with stage_lock("ocr"):
        result = tasks.do_ingest()

    stages = {s["stage"]: s for s in result["stages"]}
    assert stages["ocr"]["skipped"] is True
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Registration and configuration
# ---------------------------------------------------------------------------

def test_all_four_tasks_are_registered():
    registered = {n for n in tasks.app.tasks if n.startswith("pipeline.")}
    assert registered == {"pipeline.scan", "pipeline.ocr",
                          "pipeline.index", "pipeline.ingest"}


def test_the_schedule_is_configured_from_settings():
    from pipeline.config import get_settings

    entry = tasks.app.conf.beat_schedule["ingest-corpus"]
    assert entry["task"] == "pipeline.ingest"
    assert entry["schedule"] == float(get_settings().celery.schedule_seconds)


def test_only_json_is_accepted():
    """Pickle would let a malformed message execute arbitrary code."""
    assert tasks.app.conf.accept_content == ["json"]
    assert tasks.app.conf.task_serializer == "json"


def test_late_acknowledgement_is_on():
    """A worker killed mid-task must re-queue it. Safe only because every
    stage is idempotent — the replay skips whatever already completed."""
    assert tasks.app.conf.task_acks_late is True


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_name", ["pipeline.scan", "pipeline.ocr", "pipeline.index"])
def test_stage_tasks_retry_transient_failures(task_name):
    """A network blip or an Azure throttle should not lose a scheduled run
    until the next tick. Retrying is safe only because every stage is
    idempotent — the replay skips whatever already completed."""
    task = tasks.app.tasks[task_name]
    assert task.max_retries == 3
    assert Exception in task.autoretry_for


@pytest.mark.parametrize("task_name", ["pipeline.scan", "pipeline.ocr", "pipeline.index"])
def test_retries_back_off_rather_than_hammering(task_name):
    """Immediate retries against a throttling API make the throttling
    worse. Jitter stops several stages retrying in lockstep."""
    task = tasks.app.tasks[task_name]
    assert task.retry_backoff == 30
    assert task.retry_backoff_max == 600
    assert task.retry_jitter is True


def test_ingest_does_not_retry_the_whole_pipeline():
    """REGRESSION-GUARD: ingest already reports each stage's outcome and
    the stages retry internally. Retrying ingest would repeat work that
    already succeeded — including paid OCR."""
    assert not getattr(tasks.app.tasks["pipeline.ingest"], "autoretry_for", ())


# ---------------------------------------------------------------------------
# Progress logging
# ---------------------------------------------------------------------------

class FakeExtraction:
    """Shaped like pipeline.ocr.Extraction, including the huge field."""
    markdown = "# عنوان\n\nنص عربي."
    page_count = 9
    seconds = 1.92
    via_fallback = False
    raw_response = {"words": [{"content": "w", "polygon": [1, 2, 3, 4]}] * 5000}


def test_ocr_progress_does_not_log_the_whole_provider_response():
    """REGRESSION: the first version logged the Extraction object itself.
    `raw_response` carries word-level geometry, so every document wrote
    megabytes into the log — observed doing exactly that during a real
    scheduled run. A log that large is a disk-space incident, and it buries
    the line anyone was actually reading."""
    line = tasks._summarise(FakeExtraction())

    assert "polygon" not in line
    assert len(line) < 80
    assert "9p" in line and "seconds" not in line


def test_ocr_progress_reports_what_matters():
    line = tasks._summarise(FakeExtraction())
    assert "9p" in line          # pages billed
    assert "1.92s" in line       # how long it took


def test_a_fallback_extraction_is_marked_as_such():
    """Which extractor ran is worth seeing in the log, not only in the row."""
    extraction = FakeExtraction()
    extraction.via_fallback = True
    assert "via fallback" in tasks._summarise(extraction)


def test_the_indexer_detail_is_a_chunk_count():
    assert tasks._summarise(7) == "7 chunks"
