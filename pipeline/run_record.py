"""The outcome of the last full ingest run, kept where the status check can read it.

`pipeline.status` used to judge the schedule by the newest `indexed_at` in
the corpus. That is the date a *document* last changed, not the date the
pipeline last *ran* — so an unchanged corpus, processed correctly every day,
reported "nothing has been indexed for 33 days — check that beat and the
worker are running" while both were. A warning that is always on is ignored
on the day it is true.

So each full run now records what it did, and the check asks the question
it meant to ask: when did a run last succeed, and did the latest one fail?

**Two keys, not one.** The latest attempt and the latest success are kept
separately, because the useful report on a bad day is both — "the run at
00:17 failed, and the last good one was yesterday at 13:00".

**Redis, because the locks already live there.** Every process that can run
a stage already needs Redis for its lock, and the status check already reads
it. A table would add a migration for two rows of history nobody queries.

**Only full-corpus runs are recorded.** A single-project run is a manual
repair, and letting it count would let a run over one project hide that the
scheduled run over all of them has stopped.

**Recording fails soft.** A run that did its work must not be reported as
failed because the note about it could not be written; the status check
will say no run was recorded, which is true.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

log = logging.getLogger(__name__)

KEY_PREFIX = "enjaz:pipeline:"
LAST_RUN_KEY = KEY_PREFIX + "last_run"
LAST_SUCCESS_KEY = KEY_PREFIX + "last_success"


def _default_client():
    from pipeline.locking import _client
    return _client()


def _summary(result: dict, finished_at: dt.datetime) -> dict:
    """Small enough to read at a glance: each stage's outcome and, if it
    failed, why. The full per-document detail belongs in the worker's log."""
    stages = []
    for stage in result.get("stages", []):
        entry = {"stage": stage.get("stage"), "ok": bool(stage.get("ok"))}
        if stage.get("skipped"):
            entry["skipped"] = True
        if stage.get("error"):
            entry["error"] = str(stage["error"])[:300]
        if stage.get("attempts", 1) > 1:
            entry["attempts"] = stage["attempts"]
        stages.append(entry)
    return {"finished_at": finished_at.isoformat(), "ok": bool(result.get("ok")),
            "stages": stages}


def record(result: dict, *, client=None, now: dt.datetime | None = None) -> None:
    """Store a full run's outcome. Never raises."""
    finished_at = now or dt.datetime.now(dt.timezone.utc)
    try:
        client = client or _default_client()
        payload = json.dumps(_summary(result, finished_at))
        client.set(LAST_RUN_KEY, payload)
        if result.get("ok"):
            client.set(LAST_SUCCESS_KEY, payload)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not record the ingest run outcome (%s: %s)",
                    type(exc).__name__, exc)


def read(*, client=None) -> tuple[dict | None, dict | None]:
    """`(last_run, last_success)`, each `None` if never recorded.

    Raises if Redis cannot be reached — the caller already reports an
    unreachable Redis, and "no run recorded" would be the wrong diagnosis.
    """
    client = client or _default_client()
    return _load(client.get(LAST_RUN_KEY)), _load(client.get(LAST_SUCCESS_KEY))


def _load(raw) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        data["finished_at"] = dt.datetime.fromisoformat(data["finished_at"])
        return data
    except (ValueError, KeyError, TypeError):
        # A corrupt record is treated as no record, which the status check
        # reports — rather than crashing the one command meant to diagnose.
        return None
