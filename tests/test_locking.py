"""Tests for pipeline/locking.py.

The lock exists for one scenario that idempotence cannot cover: two runs of
the same stage **at the same time**. Both would see the same document as
un-OCR'd, both would call Document Intelligence, and both would be billed.
Postgres serialises the row writes afterwards; the money is already spent.

These run against the real Redis, because the properties worth testing are
the ones Redis provides — atomic set-if-absent, expiry, and the
compare-and-delete that stops a slow run releasing someone else's lock. A
fake dict would test none of them. Every key is namespaced per test and
deleted afterwards.
"""
from __future__ import annotations

import uuid

import pytest

from pipeline.config import get_settings
from pipeline.locking import AlreadyRunning, _client, stage_lock, optional_stage_lock


@pytest.fixture
def stage():
    """A unique stage name, so tests never collide with each other or with
    a real worker running on the same Redis."""
    name = f"pytest-{uuid.uuid4().hex[:12]}"
    try:
        client = _client()
        client.ping()
    except Exception as exc:                            # pragma: no cover
        pytest.skip(f"Redis not reachable — is mof-redis up? ({exc})")
    yield name
    client.delete(f"enjaz:lock:{name}")


def key(stage: str) -> str:
    return f"enjaz:lock:{stage}"


# ---------------------------------------------------------------------------
# Mutual exclusion
# ---------------------------------------------------------------------------

def test_a_second_acquisition_is_refused(stage):
    """The whole point: two OCR runs must not overlap."""
    with stage_lock(stage):
        with pytest.raises(AlreadyRunning):
            with stage_lock(stage):
                pass


def test_the_lock_is_released_on_exit(stage):
    with stage_lock(stage):
        pass
    with stage_lock(stage):          # would raise if the first never released
        pass


def test_the_lock_is_released_even_when_the_stage_raises(stage):
    """A crashed run must not wedge the pipeline until the TTL expires."""
    with pytest.raises(ValueError):
        with stage_lock(stage):
            raise ValueError("stage blew up")
    with stage_lock(stage):
        pass


def test_different_stages_do_not_block_each_other(stage):
    """Scanning while OCR runs is fine — they touch different work."""
    other = f"{stage}-other"
    try:
        with stage_lock(stage):
            with stage_lock(other):
                pass
    finally:
        _client().delete(key(other))


def test_the_error_names_the_holder(stage):
    """A batch is debugged from its logs; "already running" without saying
    where is much less useful than one naming the host and pid."""
    with stage_lock(stage):
        with pytest.raises(AlreadyRunning) as excinfo:
            with stage_lock(stage):
                pass
    assert stage in str(excinfo.value)
    assert "held by" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Expiry and ownership
# ---------------------------------------------------------------------------

def test_the_lock_carries_an_expiry(stage):
    """Without a TTL, a killed worker deadlocks the pipeline forever."""
    with stage_lock(stage, timeout_seconds=120):
        ttl = _client().ttl(key(stage))
    assert 0 < ttl <= 120


def test_a_run_that_lost_its_lock_does_not_delete_the_new_owners(stage):
    """REGRESSION-GUARD for compare-and-delete.

    A run that overruns its TTL loses the lock; another run legitimately
    takes it. When the slow one finally finishes, a naive `delete(key)`
    would remove the *new* owner's lock and let a third run start beside
    the second. Simulated by overwriting the value mid-hold.
    """
    client = _client()
    with stage_lock(stage):
        client.set(key(stage), "someone-else", ex=60)
    assert client.get(key(stage)) == b"someone-else"     # not deleted by us


def test_expiry_is_configurable_from_settings(stage):
    """The TTL must exceed the longest plausible run, so it has to be a
    setting rather than a literal."""
    with stage_lock(stage):
        ttl = _client().ttl(key(stage))
    assert ttl > get_settings().celery.lock_timeout_seconds - 5


# ---------------------------------------------------------------------------
# optional_stage_lock
# ---------------------------------------------------------------------------

def test_disabled_lock_takes_nothing(stage):
    """A dry run writes nothing and sends nothing, so blocking it behind a
    real run would be pure obstruction."""
    with optional_stage_lock(stage, enabled=False):
        assert _client().get(key(stage)) is None


def test_disabled_lock_does_not_block_a_real_one(stage):
    with stage_lock(stage):
        with optional_stage_lock(stage, enabled=False):
            pass          # a dry run proceeds while a real run holds the lock


def test_enabled_lock_behaves_like_stage_lock(stage):
    with optional_stage_lock(stage, enabled=True):
        with pytest.raises(AlreadyRunning):
            with stage_lock(stage):
                pass


def test_the_redis_client_is_reused_across_acquisitions(stage):
    """REGRESSION-GUARD: the first implementation built
    `Redis.from_url(...)` on every acquisition and never disposed of it —
    a fresh connection pool per task, harmless in a one-shot CLI run and a
    slow leak in a worker that runs every hour for months."""
    with stage_lock(stage):
        first = _client()
    with stage_lock(stage):
        second = _client()
    assert first is second
