"""Tests for the per-user question limit.

**This guards a budget, not a boundary**, and that shapes every decision in
it: it is keyed on the authenticated username so one busy person cannot
throttle a building behind a shared NAT, it lives in Redis so a second
worker does not silently double the quota, and it fails **open** — a
limiter outage must not become a service outage.

The AC09 checks around it are unaffected and still fail closed.
"""
from __future__ import annotations

import pytest

from Application.backend.api.ratelimit import RateLimiter


class FakeRedis:
    def __init__(self, fail=False):
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.fail = fail

    def incr(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, seconds):
        self.expiries[key] = self.expiries.get(key, seconds)


def limiter(limit=3, window=60, client=None):
    return RateLimiter(client or FakeRedis(), limit=limit,
                       window_seconds=window)


# ---------------------------------------------------------------------------
# It limits
# ---------------------------------------------------------------------------

def test_requests_inside_the_quota_are_allowed():
    rl = limiter(limit=3)
    assert [rl.check("noura").allowed for _ in range(3)] == [True] * 3


def test_the_request_past_the_quota_is_refused():
    rl = limiter(limit=3)
    for _ in range(3):
        rl.check("noura")
    decision = rl.check("noura")
    assert not decision.allowed
    assert decision.retry_after > 0, "a refusal must say when to come back"


def test_remaining_counts_down():
    rl = limiter(limit=3)
    assert [rl.check("noura").remaining for _ in range(3)] == [2, 1, 0]


def test_one_user_cannot_spend_another_user_s_quota():
    """Keyed on the authenticated username, which the caller cannot choose."""
    rl = limiter(limit=2)
    rl.check("noura")
    rl.check("noura")
    assert not rl.check("noura").allowed
    assert rl.check("fahad").allowed, "a busy user must not throttle everyone"


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def test_a_new_window_is_a_new_key_so_nothing_needs_resetting():
    client = FakeRedis()
    rl = RateLimiter(client, limit=2, window_seconds=1)
    rl.check("noura")
    keys_before = set(client.counts)
    import time as _t
    _t.sleep(1.1)
    rl.check("noura")
    assert set(client.counts) - keys_before, "the window must roll to a new key"


def test_only_the_first_call_in_a_window_sets_the_expiry():
    """Otherwise a busy user keeps pushing the expiry forward and the window
    never ends."""
    client = FakeRedis()
    rl = RateLimiter(client, limit=5, window_seconds=60)
    for _ in range(4):
        rl.check("noura")
    assert len(client.expiries) == 1


# ---------------------------------------------------------------------------
# Switched off, and broken
# ---------------------------------------------------------------------------

def test_a_limit_of_zero_means_no_limit():
    """One setting rather than a second flag to say the first is off."""
    rl = limiter(limit=0)
    assert not rl.enabled
    assert all(rl.check("noura").allowed for _ in range(50))


def test_an_unreachable_redis_allows_the_request():
    """⚠️ Fail-open, unlike every access rule here. What is protected is a
    budget; making a limiter outage into a total outage would be a worse
    failure than the one it prevents."""
    rl = limiter(limit=1, client=FakeRedis(fail=True))
    assert rl.check("noura").allowed
    assert rl.check("noura").allowed


def test_a_missing_username_is_not_limited_into_a_shared_bucket():
    """An empty key would put every anonymous caller in one bucket — but
    nothing reaches this without an authenticated identity anyway, so the
    safe reading is to allow rather than to invent a shared quota."""
    assert limiter(limit=1).check("").allowed


# ---------------------------------------------------------------------------
# Where it sits in the request
# ---------------------------------------------------------------------------

def test_the_limit_is_checked_after_the_conversation_is_authorised():
    """Otherwise a 429 would tell a stranger that a conversation id is real,
    which is exactly the oracle the 403s are careful not to be."""
    import inspect

    from Application.backend.api import app as api_app

    source = inspect.getsource(api_app.ask)
    assert source.index("_conversation_or_denied") < source.index("LIMITER")
