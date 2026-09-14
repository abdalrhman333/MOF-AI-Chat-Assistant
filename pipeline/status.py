"""Operational status — is the pipeline healthy right now?

The pipeline runs unattended on a schedule, which means nobody is watching
it. Full observability (OTLP traces and metrics into Grafana) belongs with
the chat backend, which is the bigger consumer and will need Langfuse
alongside it — designing that layer for the pipeline alone would mean
designing it blind and probably building it twice.

This is the cheap 80% in the meantime: one command answering the three
questions an operator actually asks, with **no design decisions that get
thrown away** when real observability arrives.

  1. When did ingestion last succeed, and how long ago?
  2. Is anything failed or stuck?
  3. Do the three stores still agree with each other?

Question 3 is the one nothing else checks. Postgres and Qdrant are written
in the same per-document transaction, but they are separate systems: a
worker killed between the Qdrant upsert and the Postgres commit leaves them
disagreeing, and nothing else in the pipeline would ever notice. Every
other check here restates something a stage already knows.

Exit code is the point: **0 healthy, 1 needs attention**, so this can be a
cron check or a container healthcheck without anyone parsing the output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field

from pipeline.config import Settings, get_settings
from pipeline.db import admin_connection

# The schedule is daily, so a day plus a margin for a long run without a
# successful full ingest means beat, the worker or Docker is not doing its
# job. Judged on the last successful RUN, not on the newest indexed document:
# an unchanged corpus is processed correctly every day and indexes nothing,
# and judging by documents raised "nothing indexed for 33 days" while both
# processes were fine. See run_record.py.
STALE_AFTER_HOURS = 26


@dataclass
class Problem:
    severity: str          # 'error' | 'warning'
    message: str


@dataclass
class Status:
    corpus: dict = field(default_factory=dict)
    last_indexed_at: dt.datetime | None = None
    last_run: dict | None = None
    last_success: dict | None = None
    runs_unreadable: bool = False
    failures: list[dict] = field(default_factory=list)
    consistency: list[dict] = field(default_factory=list)
    locks: list[dict] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not any(p.severity == "error" for p in self.problems)

    def add(self, severity: str, message: str) -> None:
        self.problems.append(Problem(severity, message))


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

_CORPUS_SQL = """
    SELECT count(*)                                              AS total,
           count(file_hash)                                      AS hashed,
           count(*) FILTER (WHERE indexing_status = 'indexed')   AS indexed,
           count(*) FILTER (WHERE indexing_status = 'pending')   AS pending,
           count(*) FILTER (WHERE indexing_status = 'failed')    AS failed,
           count(*) FILTER (WHERE indexing_status = 'indexing')  AS in_progress,
           coalesce(sum(chunk_count), 0)                         AS chunks,
           max(indexed_at)                                       AS last_indexed_at
    FROM document_links
"""

_OCR_SQL = "SELECT count(*) FROM document_content"

_FAILURES_SQL = """
    SELECT project_code, filename, left(coalesce(error_message, ''), 160)
    FROM document_links
    WHERE indexing_status = 'failed'
    ORDER BY project_code, filename
"""

_PER_PROJECT_SQL = """
    SELECT project_code, coalesce(sum(chunk_count), 0)
    FROM document_links
    WHERE indexing_status = 'indexed'
    GROUP BY project_code
    ORDER BY project_code
"""


_UNREAD = object()


def collect(settings: Settings | None = None, connection=None,
            qdrant=None, runs=_UNREAD) -> Status:
    """Gather everything, then judge. Injectable for tests.

    `runs` is `(last_run, last_success)`; left out, it is read from Redis.
    """
    settings = settings or get_settings()
    status = Status()

    if connection is not None:
        _collect_db(connection, status)
    else:
        with admin_connection() as conn:
            _collect_db(conn, status)

    _collect_locks(settings, status)
    _collect_runs(status, runs)
    _collect_consistency(settings, status, qdrant)
    _judge(settings, status)
    return status


def _collect_db(conn, status: Status) -> None:
    cursor = conn.cursor()
    row = cursor.execute(_CORPUS_SQL).fetchone()
    (total, hashed, indexed, pending, failed, in_progress,
     chunks, last_indexed_at) = row
    ocr_rows = cursor.execute(_OCR_SQL).fetchone()[0]

    status.corpus = {
        "documents": total, "hashed": hashed, "with_ocr": ocr_rows,
        "indexed": indexed, "pending": pending, "failed": failed,
        "in_progress": in_progress, "chunks": chunks,
    }
    status.last_indexed_at = last_indexed_at
    status.failures = [
        {"project": p, "filename": f, "error": e}
        for p, f, e in cursor.execute(_FAILURES_SQL).fetchall()
    ]
    status._per_project = dict(cursor.execute(_PER_PROJECT_SQL).fetchall())


def _collect_locks(settings: Settings, status: Status) -> None:
    """A held lock is normal during a run and suspicious outside one.

    Reported with its remaining TTL rather than judged, because this
    command cannot tell a healthy 4-minute OCR run from a wedged one — and
    pretending otherwise would produce false alarms on every scheduled run.
    """
    try:
        from pipeline.locking import _client, _LOCK_PREFIX

        client = _client()
        for key in client.scan_iter(f"{_LOCK_PREFIX}*"):
            name = key.decode().removeprefix(_LOCK_PREFIX)
            status.locks.append({
                "stage": name,
                "holder": (client.get(key) or b"").decode(),
                "ttl_seconds": client.ttl(key),
            })
    except Exception as exc:                            # noqa: BLE001
        status.add("error", f"Redis unreachable ({type(exc).__name__}: {exc}) — "
                            "the worker cannot run and stage locks are unavailable")


def _collect_runs(status: Status, runs) -> None:
    """The last full ingest attempt and the last success.

    An unreachable Redis is already reported by `_collect_locks`, so it is
    not reported twice — and not mistaken for "no run recorded".
    """
    if runs is _UNREAD:
        try:
            from pipeline import run_record
            runs = run_record.read()
        except Exception:                                # noqa: BLE001
            status.runs_unreadable = True
            return
    status.last_run, status.last_success = runs


def _collect_consistency(settings: Settings, status: Status, qdrant) -> None:
    """Compare Postgres's chunk_count against Qdrant's actual point count.

    The check nothing else performs. They are written in the same
    per-document transaction, but they are separate systems — a worker
    killed between the upsert and the commit leaves them disagreeing, and
    every other signal in the pipeline would still look fine.
    """
    per_project = getattr(status, "_per_project", {})
    try:
        if qdrant is None:
            from pipeline.indexer import build_qdrant
            qdrant = build_qdrant(settings)
        collections = {c.name for c in qdrant.get_collections().collections}
    except Exception as exc:                            # noqa: BLE001
        status.add("error", f"Qdrant unreachable ({type(exc).__name__}: {exc}) — "
                            "cannot verify the vector store matches the database")
        return

    for project, expected in sorted(per_project.items()):
        name = settings.qdrant.collection_for(project)
        actual = qdrant.count(name).count if name in collections else None
        status.consistency.append({
            "project": project, "collection": name,
            "postgres_chunks": int(expected),
            "qdrant_points": actual,
            "match": actual == int(expected),
        })

    # A collection with no documents behind it is an orphan — usually a
    # project deleted from the database while its vectors stayed behind,
    # which is exactly the stale-retrieval risk cleanup was deferred on.
    expected_names = {settings.qdrant.collection_for(p) for p in per_project}
    for orphan in sorted(collections - expected_names):
        if orphan.startswith(settings.qdrant.collection_prefix):
            status.consistency.append({
                "project": None, "collection": orphan,
                "postgres_chunks": 0, "qdrant_points": qdrant.count(orphan).count,
                "match": False,
            })


# ---------------------------------------------------------------------------
# Judgement
# ---------------------------------------------------------------------------

def _judge(settings: Settings, status: Status) -> None:
    corpus = status.corpus

    if corpus.get("documents", 0) == 0:
        status.add("warning", "No documents recorded at all — has the scanner run?")

    if status.failures:
        status.add("error",
                   f"{len(status.failures)} document(s) failed. They are skipped by "
                   f"default on the next run; use --retry-failed once the cause is fixed")

    for row in status.consistency:
        if row["match"]:
            continue
        if row["project"] is None:
            status.add("warning",
                       f"{row['collection']} holds {row['qdrant_points']} vectors but no "
                       f"indexed documents point at it — an orphaned collection")
        else:
            status.add("error",
                       f"{row['collection']}: Qdrant has {row['qdrant_points']} points, "
                       f"Postgres expects {row['postgres_chunks']} — the stores disagree")

    unhashed = corpus.get("documents", 0) - corpus.get("hashed", 0)
    if unhashed:
        status.add("warning", f"{unhashed} document(s) have never been scanned")

    missing_ocr = corpus.get("documents", 0) - corpus.get("with_ocr", 0) - corpus.get("failed", 0)
    if missing_ocr > 0:
        status.add("warning", f"{missing_ocr} document(s) still awaiting OCR")

    if corpus.get("in_progress"):
        status.add("warning",
                   f"{corpus['in_progress']} document(s) stuck in 'indexing' — a run "
                   f"died mid-flight; the next run will pick them up")

    _judge_runs(status)


def _judge_runs(status: Status) -> None:
    if status.runs_unreadable:
        return                          # Redis already reported as unreachable
    run, success = status.last_run, status.last_success
    now = dt.datetime.now(dt.timezone.utc)

    if run is None:
        status.add("warning",
                   "No full ingest run has been recorded yet. Runs are recorded "
                   "since 2026-09-14; the next scheduled run will create the record")
        return

    if not run.get("ok"):
        failed = [s for s in run.get("stages", []) if not s.get("ok")]
        reason = failed[0].get("error", "unknown error") if failed else "unknown error"
        status.add("error",
                   f"The last ingest run ({_when(run['finished_at'])}) failed: "
                   f"{', '.join(s['stage'] for s in failed) or 'a stage'} — {reason}")

    if success is None:
        status.add("warning", "No full ingest run has succeeded since runs began "
                              "being recorded")
        return

    age = now - success["finished_at"]
    if age > dt.timedelta(hours=STALE_AFTER_HOURS):
        status.add("warning",
                   f"No ingest run has succeeded for {_age(age)}. The schedule is "
                   f"daily, so check that Docker, beat and the worker are running")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def format_status(status: Status, settings: Settings) -> str:
    corpus = status.corpus
    schedule = (f"{settings.celery.schedule_hour:02d}:"
                f"{settings.celery.schedule_minute:02d} "
                f"{settings.celery.schedule_timezone}")
    verdict = "HEALTHY" if status.healthy else "NEEDS ATTENTION"

    lines = [
        f"Pipeline status — {verdict}",
        "",
        f"  documents   : {corpus.get('documents', 0)}"
        f"  (hashed {corpus.get('hashed', 0)},"
        f" ocr {corpus.get('with_ocr', 0)},"
        f" indexed {corpus.get('indexed', 0)})",
        f"  chunks      : {corpus.get('chunks', 0)}",
        f"  pending     : {corpus.get('pending', 0)}"
        f"   failed: {corpus.get('failed', 0)}"
        f"   in-progress: {corpus.get('in_progress', 0)}",
        f"  last run    : {_run_line(status.last_run)}",
        f"  last success: {_run_line(status.last_success)}",
        f"  last indexed: {_ago(status.last_indexed_at)}"
        f"   (when a document last changed — not a health signal)",
        f"  schedule    : daily at {schedule}",
    ]

    if status.consistency:
        lines.append("")
        lines.append("  postgres vs qdrant:")
        for row in status.consistency:
            mark = "ok " if row["match"] else "!! "
            actual = "missing" if row["qdrant_points"] is None else row["qdrant_points"]
            lines.append(f"      {mark}{row['collection']:<28} "
                         f"pg={row['postgres_chunks']:<5} qdrant={actual}")

    if status.locks:
        lines.append("")
        lines.append("  stages currently locked (a run in flight, or a stale lock):")
        for lock in status.locks:
            lines.append(f"      {lock['stage']:<8} held by {lock['holder']} "
                         f"(expires in {lock['ttl_seconds']}s)")

    if status.failures:
        lines.append("")
        lines.append(f"  failed documents ({len(status.failures)}):")
        for failure in status.failures:
            lines.append(f"      {failure['project']}/{failure['filename']}")
            lines.append(f"          {failure['error']}")

    if status.problems:
        lines.append("")
        for problem in status.problems:
            lines.append(f"  [{problem.severity.upper()}] {problem.message}")
    else:
        lines.append("")
        lines.append("  No problems detected.")

    return "\n".join(lines)


def _age(delta: dt.timedelta) -> str:
    hours = delta.total_seconds() / 3600
    return f"{hours:.0f} hours" if hours < 48 else f"{delta.days} days"


def _when(when: dt.datetime) -> str:
    return f"{when.astimezone(dt.timezone.utc):%Y-%m-%d %H:%M UTC}"


def _run_line(run: dict | None) -> str:
    if run is None:
        return "never recorded"
    verdict = "ok" if run.get("ok") else "FAILED"
    return f"{_ago(run['finished_at'])} — {verdict}"


def _ago(when: dt.datetime | None) -> str:
    if when is None:
        return "never"
    delta = dt.datetime.now(dt.timezone.utc) - when
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{when:%Y-%m-%d %H:%M UTC} ({int(delta.total_seconds() / 60)} min ago)"
    if hours < 48:
        return f"{when:%Y-%m-%d %H:%M UTC} ({hours:.1f} h ago)"
    return f"{when:%Y-%m-%d %H:%M UTC} ({delta.days} days ago)"


def _run_json(run: dict | None) -> dict | None:
    if run is None:
        return None
    return {**run, "finished_at": run["finished_at"].isoformat()}


def to_dict(status: Status) -> dict:
    return {
        "healthy": status.healthy,
        "corpus": status.corpus,
        "last_indexed_at": status.last_indexed_at.isoformat() if status.last_indexed_at else None,
        "last_run": _run_json(status.last_run),
        "last_success": _run_json(status.last_success),
        "consistency": status.consistency,
        "locks": status.locks,
        "failures": status.failures,
        "problems": [{"severity": p.severity, "message": p.message} for p in status.problems],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Report ingestion pipeline health. Exit 0 healthy, 1 needs attention."
    )
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output, for a monitor or a healthcheck")
    args = parser.parse_args(argv)

    settings = get_settings()
    status = collect(settings)
    print(json.dumps(to_dict(status), ensure_ascii=False, indent=2, default=str)
          if args.json else format_status(status, settings))
    return 0 if status.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
