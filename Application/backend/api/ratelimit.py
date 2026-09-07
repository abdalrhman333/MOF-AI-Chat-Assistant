"""A per-user cap on how fast questions may be asked.

Every answered turn costs an embedding and a completion. Nothing stopped one
signed-in client asking as fast as it could type, which is a bill rather
than a breach — but a demo that can be made expensive by a loop is a demo
with an obvious hole in it.

**Keyed on the authenticated username, not the IP.** An IP is shared by
everyone behind a ministry NAT, so limiting by it would throttle a building
because one person was busy. The username is established by
`identity.py` before this runs and cannot be chosen by the caller.

**A fixed window in Redis, not a token bucket in memory.** In-process
counters stop being a limit the moment a second worker starts — each one
would allow the full quota — and the store is already Redis, so there is no
new dependency. A fixed window can allow up to 2× the quota across a window
boundary; that is a known and acceptable imprecision for a cost guard, and
saying so is better than implying a precision it does not have.

⚠️ **Fail-open, like tracing and unlike everything else here.** If Redis is
unreachable the request is allowed. The alternative is that a limiter
outage becomes a total outage — and the thing being protected is a budget,
not a boundary. The AC09 checks around it fail closed and are unaffected.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

KEY_PREFIX = "enjaz:rate:"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    retry_after: int


class RateLimiter:
    """Fixed-window counter, shared across workers through Redis."""

    def __init__(self, client, *, limit: int, window_seconds: int):
        self._client = client
        self._limit = max(0, limit)
        self._window = max(1, window_seconds)

    @property
    def enabled(self) -> bool:
        """A limit of 0 means "no limit", which is what lets a demo be run
        without one without needing a second flag to say so."""
        return self._limit > 0

    def check(self, username: str) -> Decision:
        if not self.enabled or not username:
            return Decision(True, -1, 0)

        # The window is part of the key, so an expired window is a different
        # key — no reset job, and no chance of reading a stale count.
        window = int(time.time()) // self._window
        key = f"{KEY_PREFIX}{username}:{window}"
        try:
            used = self._client.incr(key)
            if used == 1:
                # Only the first caller in a window sets the expiry, so a
                # busy user cannot keep pushing it forward and make the
                # window never end.
                self._client.expire(key, self._window)
        except Exception as exc:                              # noqa: BLE001
            log.warning("rate limiter unavailable (%s); allowing the request",
                        exc)
            return Decision(True, -1, 0)

        if used > self._limit:
            elapsed = int(time.time()) % self._window
            return Decision(False, 0, self._window - elapsed)
        return Decision(True, self._limit - used, 0)
