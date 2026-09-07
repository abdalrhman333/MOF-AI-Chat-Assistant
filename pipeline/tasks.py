"""Celery tasks — the ingestion pipeline as background jobs.

Until now the three stages were commands a human typed. That is fine for
building them and useless for operating them: dropping a file into a
project folder did nothing until somebody remembered to run the scanner.
Redis has been running unused since the infrastructure was built precisely
for this (`SOLUTION_ARCHITECTURE.md` §4.3).

**One task per stage, not one per document.** The coarse granularity is
safe *because* the stages are idempotent and skip completed work — a
retried OCR task over a finished corpus calls Azure zero times, so a retry
costs nothing rather than re-billing. Per-document fan-out would buy
parallelism a 58-document corpus does not need, at the price of moving
per-document commit logic into the task layer.

**Locking lives in `pipeline.locking`, not here.** The worker is not the
only thing that runs a stage — a developer typing `python -m pipeline.ocr`
during a scheduled run is exactly the collision worth preventing, and a
lock only the worker took would not prevent it. Each stage is locked for
its own duration; `ingest` acquires the same three locks in sequence
rather than one lock around everything, so an individually-triggered stage
still contends correctly and nothing can deadlock.
"""
from __future__ import annotations

import logging
import time

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from pipeline import classify, indexer, ocr, scanner
from pipeline.config import get_settings
from pipeline.locking import AlreadyRunning, stage_lock

log = logging.getLogger(__name__)

# Celery needs a broker URL before it can be constructed, so settings are
# read at import. A missing variable raises the same actionable message
# `config.py` gives everywhere else ("X is not set in ... copy .env.example
# and fill it in") rather than a connection error later.
_settings = get_settings()

app = Celery(
    "enjaz_ingestion",
    broker=_settings.celery.broker_url,
    backend=_settings.celery.result_backend,
)
app.conf.update(
    # The schedule is a wall-clock time, so the app's timezone IS part of
    # the schedule. Containers run on UTC; leaving this at UTC would fire a
    # "1 PM" job at 16:00 in a UTC+3 deployment.
    timezone=_settings.celery.schedule_timezone,
    enable_utc=True,
    task_track_started=True,
    # A worker killed mid-task re-queues it. Safe precisely because every
    # stage is idempotent: the replay skips whatever already completed.
    task_acks_late=True,
    worker_prefetch_multiplier=1,     # long tasks: don't hoard queued work
    result_expires=60 * 60 * 24,
    # Reject rather than silently accept anything but JSON.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)


@setup_logging.connect
def _configure_logging(**_kwargs):
    """Let the pipeline's own loggers through at INFO.

    Without this Celery installs its own handlers and the per-document
    progress below never appears, leaving a five-minute OCR run looking
    like a hung worker.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


# ---------------------------------------------------------------------------
# Shared stage runner
# ---------------------------------------------------------------------------

def _run_stage(name: str, work) -> dict:
    """Run one stage under its lock, returning a JSON-safe summary.

    A busy lock is **not** an error. A scheduled run that finds the
    previous one still going should record that it stood down and exit
    cleanly — raising would mark the task failed and trigger retries for
    something that is working exactly as intended.
    """
    started = time.time()
    try:
        with stage_lock(name):
            summary = work()
    except AlreadyRunning as exc:
        log.warning("%s skipped: %s", name, exc)
        return {"stage": name, "ok": True, "skipped": True, "reason": str(exc)}
    elapsed = round(time.time() - started, 2)
    log.info("%s finished in %ss: %s", name, elapsed, summary)
    return {"stage": name, "ok": True, "skipped": False, "seconds": elapsed, **summary}


def _summarise(detail) -> str:
    """One short line describing what a stage produced for one document.

    The stages hand back different things — Stage 1 an `Extraction`, the
    indexer a chunk count — and logging the object itself is not an option
    for the former: `Extraction.raw_response` holds the provider's full
    word-level geometry, so `"%s" % extraction` writes megabytes per
    document into the log. Found by watching a real scheduled run do
    exactly that.
    """
    markdown = getattr(detail, "markdown", None)
    if markdown is not None:
        pages = getattr(detail, "page_count", "?")
        seconds = getattr(detail, "seconds", "?")
        via = " via fallback" if getattr(detail, "via_fallback", False) else ""
        return f"{pages}p, {len(markdown):,} chars, {seconds}s{via}"
    return f"{detail} chunks"


def _progress(stage: str):
    """Per-document logging, so a long run is visible in `docker logs`."""
    def report(doc, detail, error):
        if error:
            log.error("%s: %s FAILED — %s", stage, doc.label, error)
        else:
            log.info("%s: %s ok (%s)", stage, doc.label, _summarise(detail))
    return report


# Transient failures worth retrying: a network blip, Azure throttling, a
# database restart. Retrying is safe because the stages are idempotent —
# but capped, because retrying a genuine misconfiguration forever just
# fills the log. AlreadyRunning never reaches here; it returns cleanly.
_RETRY = dict(
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

# The stage bodies are plain functions so that both the Celery tasks and
# `ingest` can call them directly. Invoking one bound task from inside
# another (`task.run(...)`) relies on how Celery binds `self`, which is an
# implementation detail worth not depending on.

def do_scan(project: str | None = None) -> dict:
    """Stage 0 — discover documents and detect changes. Costs nothing."""
    def work():
        report = scanner.scan(project=project)
        return {"files": report.files_seen, "written": report.writes,
                "inserted": len(report.of(scanner.Action.INSERT)),
                "modified": len(report.of(scanner.Action.MODIFIED)),
                "missing_on_disk": len(report.missing_on_disk),
                "unknown_projects": report.unknown_projects}
    return _run_stage("scan", work)


def do_ocr(project: str | None = None) -> dict:
    """Stage 1 — OCR. **Billed per page**; skips anything already done."""
    def work():
        report = ocr.run(project=project, progress=_progress("ocr"))
        return {"succeeded": len(report.succeeded), "failed": len(report.failed),
                "pages_billed": report.pages_billed,
                "recovered": len(report.recovered), "thin": len(report.thin)}
    return _run_stage("ocr", work)


def do_classify(project: str | None = None) -> dict:
    """Stage 1b — give newly discovered documents a type.

    Sits between OCR and indexing because it needs text to read and the
    indexer's citations carry the label. Costs one completion per
    *unclassified* document and nothing at all on an unchanged corpus, so it
    is safe on the daily schedule.

    `project` is accepted for signature symmetry with the other stages and
    ignored: the pending set is "every row still marked Unclassified", which
    is already the cheap and correct scope.
    """
    def work():
        report = classify.run()
        return {"candidates": len(report.candidates),
                "classified": len(report.classified),
                "left_unclassified": len(report.left_unclassified),
                "failed": len(report.failed)}
    return _run_stage("classify", work)


def do_index(project: str | None = None) -> dict:
    """Stages 2+3 — chunk, embed, index. Billed per token, but pennies."""
    def work():
        report = indexer.run(project=project, progress=_progress("index"))
        return {"indexed": len(report.indexed), "failed": len(report.failed),
                "chunks": report.chunks, "tokens": report.tokens,
                "collections_created": report.collections_created}
    return _run_stage("index", work)


def do_ingest(project: str | None = None) -> dict:
    """Every stage, in order, synchronously.

    Deliberately not a Celery chain. A chain dispatches and returns
    immediately, so the caller receives a nested graph of task ids rather
    than an outcome — a scheduled ingest could not report whether it
    worked, and a failure in stage two would be invisible to whatever
    triggered it. Running the stages inline gives one result, one log
    entry, and one place for an error to surface.

    Each stage still takes its own lock for its own duration, so an
    individually-triggered `pipeline.ocr` contends correctly with the OCR
    portion of an ingest instead of running alongside it.

    A failing stage does not stop the ones after it: OCR failing for one
    document should not stop the other 57 being indexed.
    """
    results = [
        _safe(name, fn, project)
        for name, fn in (("scan", do_scan), ("ocr", do_ocr),
                         ("classify", do_classify), ("index", do_index))
    ]
    return {"ok": all(r.get("ok") for r in results), "stages": results}


def _safe(name: str, fn, project: str | None) -> dict:
    try:
        return fn(project)
    except Exception as exc:                           # noqa: BLE001
        log.exception("ingest: stage %s raised", name)
        return {"stage": name, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}


@app.task(name="pipeline.scan", **_RETRY)
def scan(project: str | None = None) -> dict:
    return do_scan(project)


@app.task(name="pipeline.ocr", **_RETRY)
def extract(project: str | None = None) -> dict:
    return do_ocr(project)


@app.task(name="pipeline.classify", **_RETRY)
def classify_task(project: str | None = None) -> dict:
    return do_classify(project)


@app.task(name="pipeline.index", **_RETRY)
def index(project: str | None = None) -> dict:
    return do_index(project)


@app.task(name="pipeline.ingest")
def ingest(project: str | None = None) -> dict:
    # No autoretry: do_ingest already reports each stage's outcome, and the
    # individual stages have retried internally. Retrying the whole thing
    # would repeat work that already succeeded.
    return do_ingest(project)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

app.conf.beat_schedule = {
    "ingest-corpus": {
        "task": "pipeline.ingest",
        # Once a day at a fixed wall-clock time, in the timezone configured
        # above — not an interval. An interval drifts relative to the clock
        # every time beat restarts, so "1 PM" would slowly stop being 1 PM.
        "schedule": crontab(
            hour=_settings.celery.schedule_hour,
            minute=_settings.celery.schedule_minute,
        ),
        "args": (),
        # If beat was down when the tick was due, run once on return rather
        # than firing a run that is now most of a day stale. 6 hours is
        # generous enough to cover a restart, short enough that a machine
        # switched on the next morning does not trigger yesterday's job.
        "options": {"expires": 6 * 60 * 60},
    }
}


# ---------------------------------------------------------------------------
# Manual trigger
# ---------------------------------------------------------------------------

_TASKS = {"scan": scan, "ocr": extract, "classify": classify_task,
          "index": index, "ingest": ingest}


def main(argv: list[str] | None = None) -> int:
    """Queue a task by hand — `python -m pipeline.tasks ingest`.

    Exists so triggering a run does not require remembering a
    `python -c "from pipeline.tasks import app; app.send_task(...)"`
    incantation. `--wait` blocks for the result, which is what you want
    when running it interactively; without it the task is queued and the id
    printed, which is what you want from a script.
    """
    import argparse
    import json
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Queue an ingestion task.")
    parser.add_argument("task", choices=sorted(_TASKS), help="which stage to run")
    parser.add_argument("--project", help="restrict to one project code")
    parser.add_argument("--wait", action="store_true", help="block for the result")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="seconds to wait when --wait is given")
    args = parser.parse_args(argv)

    async_result = _TASKS[args.task].delay(args.project)
    print(f"queued {args.task} -> {async_result.id}")
    if not args.wait:
        return 0

    result = async_result.get(timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # A skipped stage is a success; a stage that errored is not.
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
