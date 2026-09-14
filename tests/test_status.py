"""Tests for pipeline/status.py.

The value of a status command is entirely in whether it goes RED when
something is wrong. A check that only ever prints green is worse than no
check, because it manufactures confidence — so most of this file is about
detection, and the happy path gets one test.

The consistency check is the one worth the most attention: Postgres and
Qdrant are written in the same per-document transaction but they are
separate systems, so a worker killed between the Qdrant upsert and the
Postgres commit leaves them disagreeing and **nothing else in the pipeline
would ever notice**. Every other check here restates something a stage
already knows.
"""
from __future__ import annotations

import datetime as dt

import pytest

from pipeline import status as status_mod
from pipeline.config import get_settings

TEST_CODE = "ZZ-STATUS-TEST"


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class StubCount:
    def __init__(self, count):
        self.count = count


class StubCollection:
    def __init__(self, name):
        self.name = name


class StubCollections:
    def __init__(self, names):
        self.collections = [StubCollection(n) for n in names]


class StubQdrant:
    """Point counts per collection, exactly as Qdrant would report them."""

    def __init__(self, counts: dict[str, int]):
        self.counts = counts

    def get_collections(self):
        return StubCollections(list(self.counts))

    def count(self, name):
        return StubCount(self.counts[name])


class BrokenQdrant:
    def get_collections(self):
        raise ConnectionError("connection refused")


@pytest.fixture
def db():
    psycopg = pytest.importorskip("psycopg")
    settings = get_settings()
    try:
        conn = psycopg.connect(settings.postgres.dsn, connect_timeout=3)
    except psycopg.OperationalError as exc:              # pragma: no cover
        pytest.skip(f"Postgres not reachable — is mof-postgres-spm up? ({exc})")
    try:
        conn.execute("INSERT INTO projects (code, name_ar, name_en, status) "
                     "VALUES (%s,%s,%s,%s)",
                     (TEST_CODE, "حالة", "Status test", "testing"))
        # Isolate from the real corpus so counts are predictable.
        conn.execute("DELETE FROM document_content")
        conn.execute("DELETE FROM document_links")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def add(db, name, *, status="indexed", chunks=10, ocr=True, error=None, hashed=True):
    link_id = db.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type, "
        "indexing_status, chunk_count, indexed_at, error_message, file_hash) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (TEST_CODE, name, f"{TEST_CODE}/{name}", "Charter", status,
         chunks if status == "indexed" else None,
         dt.datetime.now(dt.timezone.utc) if status == "indexed" else None,
         error, "h" * 64 if hashed else None),
    ).fetchone()[0]
    if ocr:
        db.execute("INSERT INTO document_content (document_link_id, markdown) "
                   "VALUES (%s,%s)", (link_id, "# md"))
    return link_id


def run(hours_ago: float, ok: bool = True, error: str | None = None) -> dict:
    """A recorded full ingest run, as run_record.read() returns it."""
    stages = [{"stage": "scan", "ok": ok, **({"error": error} if error else {})},
              {"stage": "index", "ok": True}]
    return {"finished_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago),
            "ok": ok, "stages": stages}


RECENT = run(2)


def collect(db, qdrant, runs=(RECENT, RECENT)):
    """Runs are passed explicitly, so the real Redis record — whatever the
    worker last wrote — cannot change what these tests see."""
    return status_mod.collect(settings=get_settings(), connection=db, qdrant=qdrant,
                              runs=runs)


def messages(status):
    return " | ".join(p.message for p in status.problems)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_a_consistent_corpus_is_healthy(db):
    add(db, "a.docx", chunks=10)
    add(db, "b.docx", chunks=5)

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 15}))

    assert status.healthy
    assert status.corpus["documents"] == 2
    assert status.corpus["chunks"] == 15
    assert all(row["match"] for row in status.consistency)


# ---------------------------------------------------------------------------
# Detection — the reason this command exists
# ---------------------------------------------------------------------------

def test_qdrant_holding_fewer_points_than_postgres_expects_is_an_error(db):
    """A worker killed between the Qdrant upsert and the Postgres commit.
    Nothing else in the pipeline notices this — the row says 'indexed' and
    every stage agrees there is no work left."""
    add(db, "a.docx", chunks=10)

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 7}))

    assert not status.healthy
    assert "disagree" in messages(status)
    assert status.consistency[0]["match"] is False


def test_qdrant_holding_more_points_than_expected_is_also_an_error(db):
    """Surplus points are the stale-retrieval case: vectors from a longer
    previous version still being returned to users."""
    add(db, "a.docx", chunks=10)

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 14}))

    assert not status.healthy
    assert "disagree" in messages(status)


def test_a_missing_collection_is_an_error(db):
    add(db, "a.docx", chunks=10)

    status = collect(db, StubQdrant({}))

    assert not status.healthy
    assert status.consistency[0]["qdrant_points"] is None


def test_an_orphaned_collection_is_flagged(db):
    """Vectors with no documents behind them — a project removed from the
    database while its collection stayed. Warned, not errored: it does not
    corrupt anything, but it is exactly the stale-data risk that deferring
    cleanup accepted."""
    add(db, "a.docx", chunks=10)

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10,
                                     "project_GHOST": 42}))

    assert "orphaned collection" in messages(status)
    assert status.healthy          # warning, not error


def test_failed_documents_are_reported_with_their_reason(db):
    """A batch is debugged from error_message; a count alone would send an
    operator to the database anyway."""
    add(db, "bad.docx", status="failed", ocr=False, error="RateLimitError: throttled")

    status = collect(db, StubQdrant({}))

    assert not status.healthy
    assert status.failures[0]["filename"] == "bad.docx"
    assert "RateLimitError" in status.failures[0]["error"]
    assert "--retry-failed" in messages(status)


def test_documents_awaiting_ocr_are_flagged(db):
    add(db, "waiting.docx", status="pending", ocr=False)

    status = collect(db, StubQdrant({}))

    assert "awaiting OCR" in messages(status)


def test_unscanned_documents_are_flagged(db):
    add(db, "never.docx", status="pending", ocr=False, hashed=False)

    status = collect(db, StubQdrant({}))

    assert "never been scanned" in messages(status)


def test_documents_stuck_in_indexing_are_flagged(db):
    """A run that died mid-flight leaves this state; the next run recovers
    it, but silence would hide a worker that keeps dying."""
    add(db, "stuck.docx", status="indexing", chunks=None)

    status = collect(db, StubQdrant({}))

    assert "stuck in 'indexing'" in messages(status)


def test_an_empty_corpus_is_flagged(db):
    status = collect(db, StubQdrant({}))
    assert "No documents recorded" in messages(status)


def test_an_unreachable_qdrant_is_an_error_not_a_crash(db):
    """Reporting 'cannot verify' beats a traceback, and beats claiming
    healthy because the check silently did not run."""
    add(db, "a.docx", chunks=10)

    status = collect(db, BrokenQdrant())

    assert not status.healthy
    assert "Qdrant unreachable" in messages(status)


def test_an_unchanged_corpus_is_not_mistaken_for_a_stopped_schedule(db):
    """REGRESSION: the check used to judge the schedule by the newest
    indexed document, so a corpus processed correctly every day — and simply
    unchanged — reported "nothing indexed for 33 days, check beat and the
    worker" while both were running. Old documents are not a symptom."""
    link_id = add(db, "old.docx", chunks=10)
    db.execute("UPDATE document_links SET indexed_at = now() - interval '33 days' "
               "WHERE id=%s", (link_id,))

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}))

    assert status.healthy
    assert not status.problems, messages(status)


def test_no_successful_run_for_over_a_day_is_flagged(db):
    """The schedule is daily; a day and a margin without a success means
    Docker, beat or the worker is not doing its job."""
    add(db, "a.docx", chunks=10)
    # A fixed age, not STALE_AFTER_HOURS + n: measured against the constant,
    # the test would loosen along with it, and a threshold of 30 days passed.
    stale = run(40)     # yesterday's run AND today's run both missed

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}), runs=(stale, stale))

    assert "No ingest run has succeeded for" in messages(status)


def test_a_success_inside_a_day_is_not_flagged(db):
    """The boundary must not fire on a normal day: 13:00 checked at 12:59
    tomorrow is a day minus a minute, not a missed run."""
    add(db, "a.docx", chunks=10)
    yesterday = run(23.9)

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}),
                     runs=(yesterday, yesterday))

    assert not status.problems, messages(status)


def test_a_failed_last_run_is_an_error_that_names_the_stage_and_reason(db):
    """The report that would have explained 00:17 on 2026-09-14: which
    stage, and the database's own words."""
    add(db, "a.docx", chunks=10)
    failed = run(1, ok=False, error="OperationalError: the database system is starting up")

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}),
                     runs=(failed, run(12)))

    assert not status.healthy, "a failed run needs attention: exit code 1"
    assert "failed: scan" in messages(status)
    assert "starting up" in messages(status)


def test_no_run_recorded_is_a_warning_not_an_error(db):
    """A fresh install, or the first day after this check changed, has no
    record yet. That is worth saying, not worth failing a healthcheck on."""
    add(db, "a.docx", chunks=10)

    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}), runs=(None, None))

    assert status.healthy
    assert "No full ingest run has been recorded yet" in messages(status)


def test_an_unreadable_run_record_is_not_reported_as_no_run(db, monkeypatch):
    """If Redis is down, that is the diagnosis — and _collect_locks already
    gives it. "No run recorded" would send an operator looking at the
    schedule instead of at Redis."""
    from pipeline import run_record

    def unreachable(**_kw):
        raise ConnectionError("redis down")

    monkeypatch.setattr(run_record, "read", unreachable)
    add(db, "a.docx", chunks=10)

    status = status_mod.collect(settings=get_settings(), connection=db,
                                qdrant=StubQdrant({f"project_{TEST_CODE}": 10}))

    assert "No full ingest run has been recorded" not in messages(status)


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

def test_exit_code_is_the_machine_readable_signal(db):
    """So this can be a cron check or a container healthcheck without
    anyone parsing prose."""
    add(db, "a.docx", chunks=10)
    healthy = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}))
    broken = collect(db, StubQdrant({f"project_{TEST_CODE}": 3}))

    assert healthy.healthy is True
    assert broken.healthy is False


def test_json_output_is_serialisable(db):
    import json

    add(db, "a.docx", chunks=10)
    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}))
    data = json.loads(json.dumps(status_mod.to_dict(status)))
    assert data["last_success"]["ok"] is True


def test_the_rendered_report_names_the_schedule(db):
    """An operator asking "why hasn't it run?" needs to know when it should
    have — including the timezone, which is the part that goes wrong."""
    add(db, "a.docx", chunks=10)
    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}))

    text = status_mod.format_status(status, get_settings())
    settings = get_settings()
    assert settings.celery.schedule_timezone in text
    assert f"{settings.celery.schedule_hour:02d}:" in text


def test_the_report_shows_the_last_run_and_says_what_last_indexed_means(db):
    add(db, "a.docx", chunks=10)
    status = collect(db, StubQdrant({f"project_{TEST_CODE}": 10}),
                     runs=(run(1, ok=False, error="x"), run(20)))

    text = status_mod.format_status(status, get_settings())

    assert "last run    :" in text and "FAILED" in text
    assert "last success:" in text
    assert "not a health signal" in text,         "an old 'last indexed' date must not read as an alarm"
