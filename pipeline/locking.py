"""Cross-process stage locking.

Idempotence protects against running a stage again *after* it finished —
a second OCR run over a complete corpus calls Azure zero times. It says
nothing about two runs happening *at the same time*, which is a different
failure entirely: both see the same document as un-OCR'd, both call
Document Intelligence, and both are billed. Postgres would serialise the
row writes; the money is already gone by then.

This lives in its own module rather than inside `tasks.py` because the
Celery worker is not the only thing that runs a stage. A developer typing
`python -m pipeline.ocr` while the scheduled worker is mid-run is exactly
the collision worth preventing, and a lock only the worker takes would not
prevent it.

**Where the lock is taken matters.** It is acquired by the *entry points*
— each module's `main()` and each Celery task — never inside `run()`
itself. `run()` is called by both, and a non-reentrant lock taken there
would deadlock the worker against itself.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from contextlib import contextmanager
from functools import lru_cache

from pipeline.config import get_settings

log = logging.getLogger(__name__)

_LOCK_PREFIX = "enjaz:lock:"


class AlreadyRunning(RuntimeError):
    """Another process holds this stage's lock."""

    def __init__(self, stage: str, holder: str | None = None):
        self.stage = stage
        self.holder = holder
        super().__init__(
            f"stage {stage!r} is already running"
            + (f" (held by {holder})" if holder else "")
        )


@lru_cache(maxsize=1)
def _client():
    """One pooled Redis client per process.

    Building `Redis.from_url` per acquisition creates a fresh connection
    pool every time and never disposes of it — harmless in a one-shot CLI
    run, a slow leak in a worker that runs a task every hour for months.
    """
    import redis

    return redis.Redis.from_url(get_settings().celery.broker_url)


def _identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@contextmanager
def stage_lock(stage: str, timeout_seconds: int | None = None):
    """Hold an exclusive lock for `stage`, or raise `AlreadyRunning`.

    Deliberately **not** blocking. A scheduled trigger that finds the
    previous run still going should stand down rather than queue an
    identical run behind it — by the time it started, its work would
    already have been done by the run it was waiting for.

    The lock carries an expiry so a killed process cannot deadlock the
    pipeline forever, and an owner token so a run that overran its own
    expiry cannot delete the lock now legitimately held by someone else.
    """
    settings = get_settings()
    ttl = timeout_seconds or settings.celery.lock_timeout_seconds
    client = _client()
    key = f"{_LOCK_PREFIX}{stage}"
    token = _identity()

    if not client.set(key, token, nx=True, ex=ttl):
        holder = client.get(key)
        raise AlreadyRunning(stage, holder.decode() if holder else None)

    log.debug("acquired lock %s as %s (ttl %ss)", key, token, ttl)
    try:
        yield token
    finally:
        # Compare-and-delete. Without the token check, a run that overran
        # its TTL would delete the lock belonging to the run that replaced
        # it, letting a third run start alongside the second.
        try:
            if client.get(key) == token.encode():
                client.delete(key)
                log.debug("released lock %s", key)
            else:
                log.warning(
                    "lock %s expired mid-run and is now held elsewhere; not releasing. "
                    "Consider raising CELERY_LOCK_TIMEOUT (currently %ss).", key, ttl
                )
        except Exception:                              # noqa: BLE001
            # Never let lock cleanup mask the real outcome of the stage.
            log.exception("failed to release lock %s", key)


@contextmanager
def optional_stage_lock(stage: str, enabled: bool = True):
    """`stage_lock` when `enabled`, otherwise a no-op.

    Dry runs write nothing and send nothing, so making them wait on — or
    fail because of — a concurrent real run would be pure obstruction.
    """
    if not enabled:
        yield None
        return
    with stage_lock(stage) as token:
        yield token
