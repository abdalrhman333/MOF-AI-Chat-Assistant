"""Scoped, pooled database access for the chat application.

**This module exposes only the `spm_app` role**, deliberately, and mirrors
the choice `pipeline/db.py` made in the opposite direction. The pipeline
exposes only `spm_admin` because it must reach across every project; this
exposes only `spm_app` because it must not. An application module reaching
for the admin connection is an application module doing something wrong,
and `_assert_not_admin` turns that from a convention into a startup error.

Three things this module is responsible for, in rising order of subtlety:

  1. **Connection lifecycle.** A pool, sized from settings, opened once and
     closed on shutdown. A request that cannot get a connection fails after
     a bounded wait rather than hanging — an unbounded wait has already
     blown the AC08 five-second budget.

  2. **Transaction boundaries.** Every scoped read happens inside an
     explicit transaction block, because the scope is transaction-local and
     a scope without a transaction is not a scope at all.

  3. **The scope itself, applied safely.** `SET LOCAL` cannot take a bind
     parameter. Building it by string concatenation would interpolate a
     request-supplied project code straight into the statement that
     establishes the security boundary — the one statement where injection
     is least survivable. `set_config(name, value, true)` is the
     parameterised equivalent and is what this module uses.

**Why pooling makes `SET LOCAL` load-bearing rather than stylistic.** A
plain `SET` persists on the connection. Returned to the pool and handed to
the next request, it carries the previous user's project scope with it —
a cross-project leak produced *by the isolation mechanism*. That failure
cannot be reproduced without a real pool, which is why there is one here
and why `psycopg_pool` is a dependency.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from Application.backend.errors import BackendUnavailable
from pipeline.config import Settings, get_settings

# The session variables the row-level security policies read. Named once so
# a policy rename is a single edit here, not a hunt through call sites.
PROJECT_KEY = "app.current_project"
USERNAME_KEY = "app.current_username"

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

def _assert_not_admin(settings: Settings) -> None:
    """Refuse to start if the application is configured with the owner role.

    `spm_admin` owns the tables and therefore **bypasses row-level
    security entirely**. Pointed at it by a copy-paste in `.env`, every
    query in this package would silently return all nine projects and every
    isolation test would still pass, because the tests scope sessions
    correctly — they verify the policies, not which role is connected.
    Failing loudly at startup is the only place this gets caught.
    """
    app_user = (settings.postgres_app.user or "").strip()
    admin_user = (settings.postgres.user or "").strip()
    if not app_user:
        raise BackendUnavailable(
            "POSTGRES_SPM_APP_USER is not set. The application must connect as "
            "the SELECT-only, RLS-constrained role, never as the pipeline's."
        )
    if app_user == admin_user:
        raise BackendUnavailable(
            f"the application is configured to connect as {app_user!r}, which is "
            f"also the pipeline's role. That role owns the tables and bypasses "
            f"row-level security, so every project would be visible. Set "
            f"POSTGRES_SPM_APP_USER to the restricted role."
        )


def _reset(conn: psycopg.Connection) -> None:
    """Scrub session state before a connection goes back to the pool.

    Defence in depth, not the primary mechanism: `SET LOCAL` already ends
    with its transaction. This exists so that a *future* mistake — someone
    reaching for a plain `SET`, or a stray `SET ROLE` — cannot outlive the
    request that made it. The primary mechanism is still tested directly,
    within a single borrowed connection, so this cannot mask its absence.

    ⚠️ **The commit is not optional.** `RESET ALL` on a non-autocommit
    connection opens a transaction, and psycopg_pool discards any
    connection handed back in `INTRANS` — so without this the pool throws
    away every connection it is given and silently reconnects on each
    request. Found by watching it happen: "connection left in status
    INTRANS by reset function: discarded", once per request. Committing
    also makes the reset stick, since `RESET ALL` is itself transactional
    and would otherwise be rolled back.
    """
    conn.execute("RESET ALL")
    conn.commit()


def pool(settings: Settings | None = None) -> ConnectionPool:
    """The process-wide pool, created on first use.

    Lazily built rather than created at import, so importing this module
    never opens sockets — which is what lets the test suite import it
    without a database, and what will let a FastAPI lifespan handler own
    the lifecycle explicitly later.
    """
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:                                # pragma: no cover
            return _pool
        settings = settings or get_settings()
        _assert_not_admin(settings)
        cfg = settings.application
        try:
            _pool = ConnectionPool(
                conninfo=settings.postgres_app.dsn,
                min_size=cfg.pool_min_size,
                max_size=cfg.pool_max_size,
                timeout=cfg.pool_timeout_seconds,
                reset=_reset,
                open=True,
                name="enjaz-app",
            )
        except Exception as exc:                             # noqa: BLE001
            raise BackendUnavailable(f"could not open the connection pool: {exc}") from exc
        return _pool


def close_pool() -> None:
    """Release every connection. Safe to call when no pool was ever built."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def apply_scope(cursor, *, project: str | None = None,
                username: str | None = None) -> None:
    """Set the session scope the RLS policies read, transaction-locally.

    Split out from `scoped()` so the transaction-local property can be
    tested on a connection the test controls, rather than only observed
    through the pool — where the pool's own reset would mask a regression
    from `true` to `false`.

    The third argument to `set_config` is `is_local`. It is the entire
    security property of this function.
    """
    if username is not None:
        cursor.execute("SELECT set_config(%s, %s, true)", (USERNAME_KEY, username))
    if project is not None:
        cursor.execute("SELECT set_config(%s, %s, true)", (PROJECT_KEY, project))


@contextmanager
def scoped(*, project: str | None = None, username: str | None = None,
           settings: Settings | None = None,
           connection: psycopg.Connection | None = None) -> Iterator[psycopg.Cursor]:
    """A transaction with the session scope applied, yielding a cursor.

    Passing neither `project` nor `username` is legitimate and yields a
    session that can see **nothing** — the fail-closed default. Callers
    that forget to scope get emptiness, not everything.

    `connection` is injectable so tests can supply one they will roll back.
    """
    if connection is not None:
        with connection.transaction():
            cursor = connection.cursor()
            apply_scope(cursor, project=project, username=username)
            yield cursor
        return

    try:
        with pool(settings).connection() as conn:
            with conn.transaction():
                cursor = conn.cursor()
                apply_scope(cursor, project=project, username=username)
                yield cursor
    except BackendUnavailable:
        raise
    except psycopg.OperationalError as exc:
        # Connection refused, dropped mid-query, authentication failure.
        raise BackendUnavailable(f"database unavailable: {exc}") from exc
    except PoolTimeout as exc:
        # Every connection busy for longer than the configured wait. This
        # must surface as unavailability and never as an empty result,
        # because an empty result is indistinguishable from "permitted but
        # nothing there".
        raise BackendUnavailable(f"no database connection available: {exc}") from exc
