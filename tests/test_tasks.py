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


@pytest.fixture(autouse=True)
def recorded(monkeypatch):
    """No test may write the REAL run record.

    Found by the live check after this record was introduced: the suite's
    ingest tests, running fake stages against the real Redis, wrote a fake
    "success" that `pipeline.status` then reported as a genuine run — a
    test making the health check lie. Every test gets a recorder instead;
    the ones about recording read it.
    """
    runs: list[dict] = []
    monkeypatch.setattr(tasks.run_record, "record", lambda result, **_kw: runs.append(result))
    return runs


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
            self.candidates = []
            self.classified = []
            self.left_unclassified = []

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

    def classify_run(*_a, **_kw):
        calls.append(("classify", None))
        return Report()

    def index_run(project=None, **_kw):
        calls.append(("index", project))
        return Report()

    monkeypatch.setattr(tasks.scanner, "scan", scan)
    monkeypatch.setattr(tasks.ocr, "run", ocr_run)
    monkeypatch.setattr(tasks.classify, "run", classify_run)
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
    assert [s["stage"] for s in result["stages"]] == ["scan", "ocr",
                                                      "classify", "index"]
    assert all(isinstance(s, dict) for s in result["stages"])


def test_ingest_runs_the_stages_in_order(fake_stages):
    tasks.do_ingest()
    assert [name for name, _ in fake_stages] == ["scan", "ocr", "classify",
                                                 "index"]


def test_ingest_passes_the_project_to_every_stage(fake_stages):
    tasks.do_ingest("PRJ-03")
    # `classify` takes no project: its pending set is "every row still
    # marked Unclassified", which is already the cheap and correct scope.
    assert fake_stages == [("scan", "PRJ-03"), ("ocr", "PRJ-03"),
                           ("classify", None), ("index", "PRJ-03")]


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

def test_every_stage_is_registered_as_a_task():
    registered = {n for n in tasks.app.tasks if n.startswith("pipeline.")}
    assert registered == {"pipeline.scan", "pipeline.ocr", "pipeline.classify",
                          "pipeline.index", "pipeline.ingest"}


def test_classify_runs_between_ocr_and_indexing(fake_stages):
    """It needs text to read, and the indexer's citations carry the label it
    writes — so it belongs after one and before the other."""
    tasks.do_ingest()
    order = [name for name, _ in fake_stages]
    assert order.index("ocr") < order.index("classify") < order.index("index")


def test_the_schedule_is_a_daily_wall_clock_time(fake_stages=None):
    """Not an interval. An interval drifts against the clock every time
    beat restarts, so "1 PM" slowly stops being 1 PM."""
    from celery.schedules import crontab
    from pipeline.config import get_settings

    entry = tasks.app.conf.beat_schedule["ingest-corpus"]
    settings = get_settings()
    assert entry["task"] == "pipeline.ingest"
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {settings.celery.schedule_hour}
    assert entry["schedule"].minute == {settings.celery.schedule_minute}


def test_the_schedule_timezone_is_applied_to_the_app():
    """REGRESSION-GUARD: containers run on UTC, so a schedule of hour=13
    without this fires at 16:00 in a UTC+3 deployment — three hours from
    what anyone asking for "1 PM" meant."""
    from pipeline.config import get_settings

    assert str(tasks.app.conf.timezone) == get_settings().celery.schedule_timezone
    assert str(tasks.app.conf.timezone) != "UTC" or         get_settings().celery.schedule_timezone == "UTC"


def test_a_queued_run_expires_if_the_worker_is_down():
    """What `expires` actually guarantees: a run left in the queue while the
    worker is away is discarded, not replayed once per missed day on its
    return.

    It was previously documented as stopping a missed run from firing late.
    It does not — `expires` counts from dispatch, and beat dispatches a
    missed run as soon as beat itself starts (the 13:00 run of 2026-09-13
    fired at 00:17 the next day). That catch-up is kept on purpose; the
    retry tests below are what make it survive services still starting."""
    entry = tasks.app.conf.beat_schedule["ingest-corpus"]
    assert 0 < entry["options"]["expires"] <= 12 * 60 * 60


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
# Connection retries inside ingest
# ---------------------------------------------------------------------------

@pytest.fixture
def slept(monkeypatch):
    """Record the waits instead of performing them."""
    delays: list[float] = []
    monkeypatch.setattr(tasks, "_sleep", delays.append)
    return delays


def pg_error(sqlstate, message="the database system is starting up"):
    """The exception psycopg itself raises for a SQLSTATE — the real class,
    so the test exercises the attribute the code actually reads."""
    import psycopg

    if sqlstate is None:
        return psycopg.OperationalError(message)
    return psycopg.errors.lookup(sqlstate)(message)


def flaky(failures: list[BaseException]):
    """A stage that raises each given error once, then succeeds."""
    calls = {"n": 0}

    def stage(_project):
        calls["n"] += 1
        if failures:
            raise failures.pop(0)
        return {"stage": "scan", "ok": True}
    stage.calls = calls
    return stage


def test_a_stage_that_cannot_connect_is_retried_until_it_can(slept):
    """REGRESSION: at 00:17 on 2026-09-14 beat fired the missed run as Docker
    came back, Postgres said "the database system is starting up", and all
    three stages failed instantly with no retry — the stage tasks' autoretry
    never applies inside ingest, which calls the stage functions directly."""
    stage = flaky([ConnectionError("refused"), ConnectionError("refused")])

    result = tasks._safe("scan", stage, None)

    assert result["ok"] is True
    assert result["attempts"] == 3
    assert slept == list(tasks.TRANSIENT_DELAYS[:2]), "waits grow between attempts"


def test_a_stage_that_never_connects_gives_up_and_says_how_hard_it_tried(slept):
    """About a minute, not forever: a real outage must still be reported."""
    stage = flaky([ConnectionError("refused")] * 10)

    result = tasks._safe("scan", stage, None)

    assert result["ok"] is False
    assert result["attempts"] == len(tasks.TRANSIENT_DELAYS) + 1
    assert "refused" in result["error"]
    assert sum(slept) <= 120


def test_a_real_error_is_not_retried(slept):
    """A bug or a bad setting will not fix itself in a minute. Retrying it
    only delays the report."""
    stage = flaky([ValueError("DOCUMENT_ROOT does not exist")])

    result = tasks._safe("scan", stage, None)

    assert result["ok"] is False
    assert slept == [] and stage.calls["n"] == 1


@pytest.mark.parametrize("sqlstate,retried", [
    (None, True),       # the client never got an answer: refused, reset
    ("57P03", True),    # cannot_connect_now — "the database system is starting up"
    ("08006", True),    # connection_failure
    ("57014", False),   # query_canceled — an OperationalError, but not a connection problem
    ("40P01", False),   # deadlock_detected — likewise
])
def test_only_database_connection_failures_count_as_transient(sqlstate, retried):
    """psycopg's OperationalError also covers real query failures, which
    must not be retried as though the server were unreachable."""
    assert tasks._is_transient(pg_error(sqlstate)) is retried


def test_a_connection_error_wrapped_by_a_stage_is_still_recognised():
    try:
        try:
            raise ConnectionError("refused")
        except ConnectionError as inner:
            raise RuntimeError("could not read the corpus") from inner
    except RuntimeError as outer:
        assert tasks._is_transient(outer)


# ---------------------------------------------------------------------------
# Recording the run for the status check
# ---------------------------------------------------------------------------

def test_a_full_ingest_records_its_outcome(fake_stages, recorded):
    result = tasks.do_ingest()
    assert recorded == [result]


def test_a_single_project_ingest_does_not_speak_for_the_schedule(fake_stages, recorded):
    """A manual repair over one project must not hide that the scheduled
    run over all of them has stopped."""
    tasks.do_ingest(project="PRJ-01")
    assert recorded == []


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
