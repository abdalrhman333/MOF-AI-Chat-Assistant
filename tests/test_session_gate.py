"""Tests for the AC09 session gate and the scoped data-access layer.

**What makes this file different from `test_rls.py`.** That one proves the
database refuses to serve rows outside the declared scope. This one proves
the application refuses to *declare* a scope the user was never granted —
the question row-level security cannot answer, because scoping is an input
to RLS rather than something it validates. Demonstrated live on 2026-08-09:
a user assigned to three projects, scoping a session to a fourth, was
handed that fourth project's row without complaint. That is correct RLS
behaviour and exactly why this gate exists.

Four properties are defended, in rising order of how quietly they fail:

  1. **Writes are refused.** Loud if it regresses.
  2. **A denied pairing raises.** Obvious — the wrong data would appear.
  3. **Denial is non-enumerable.** "Not yours" and "does not exist" must be
     indistinguishable, or the error message becomes a project directory.
  4. **Scope does not survive a pooled connection.** Silent, and the worst:
     the next request to borrow that connection inherits the previous
     user's project, and nothing anywhere reports it.

**Read-only, like `test_rls.py`, and for the same reason** — the writer
would have to be `spm_admin`, which bypasses the thing under test. These
assert relationships over whatever corpus is present.

**Nothing is hardcoded.** Usernames, project codes and counts are all
discovered at run time, so the file is equally valid against a real
directory with different people and projects.
"""
from __future__ import annotations

import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from Application.backend import db
from Application.backend.errors import AccessDenied, AccessError, BackendUnavailable
from Application.backend.session import Session, list_assigned_projects, open_session
from pipeline.config import get_settings


# ---------------------------------------------------------------------------
# Fixtures — everything discovered, nothing assumed
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _pool_teardown():
    yield
    db.close_pool()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def admin(settings):
    """Ground truth. Read-only; never the connection under test."""
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(settings.postgres.dsn, connect_timeout=3)
    except psycopg.OperationalError as exc:                  # pragma: no cover
        pytest.skip(f"Postgres not reachable ({exc})")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def app_ready(settings):
    """Skip cleanly when the restricted role is not configured."""
    if not settings.postgres_app.password:
        pytest.skip("POSTGRES_SPM_APP_PASSWORD is not set in infra/.env")
    try:
        with db.scoped() as cursor:
            cursor.execute("SELECT 1")
    except BackendUnavailable as exc:                        # pragma: no cover
        pytest.skip(f"application role cannot connect ({exc})")


@pytest.fixture
def world(admin, app_ready):
    """The access-control facts, read from the database at run time."""
    rows = admin.execute(
        "SELECT username, project_code FROM project_assignments ORDER BY 1, 2"
    ).fetchall()
    if not rows:
        pytest.skip("no project assignments seeded")
    by_user: dict[str, list[str]] = {}
    for username, code in rows:
        by_user.setdefault(username, []).append(code)

    all_projects = [r[0] for r in
                    admin.execute("SELECT code FROM projects ORDER BY code").fetchall()]
    assigned_anywhere = {code for codes in by_user.values() for code in codes}
    unassigned = [c for c in all_projects if c not in assigned_anywhere]

    multi = sorted((u for u, c in by_user.items() if len(c) > 1),
                   key=lambda u: -len(by_user[u]))
    user = multi[0] if multi else sorted(by_user)[0]
    theirs = by_user[user]
    foreign = [c for c in all_projects if c not in theirs]
    if not foreign:
        pytest.skip("every project is assigned to the test user — no isolation to prove")

    return {
        "by_user": by_user, "user": user, "theirs": theirs,
        "foreign": foreign,          # exist, but not this user's
        "unassigned": unassigned,    # exist, assigned to nobody
        "all_projects": all_projects,
    }


def docs_for(admin, code: str) -> int:
    return admin.execute(
        "SELECT count(*) FROM document_links WHERE project_code = %s", (code,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# The gate allows what it should
# ---------------------------------------------------------------------------

def test_a_user_can_open_each_project_they_are_assigned_to(world):
    for code in world["theirs"]:
        session = open_session(world["user"], code)
        assert isinstance(session, Session)
        assert session.project_code == code
        assert session.username == world["user"]


def test_a_session_reads_its_own_project(world, admin):
    code = world["theirs"][0]
    expected = docs_for(admin, code)
    session = open_session(world["user"], code)
    with session.scoped() as cursor:
        assert cursor.execute("SELECT count(*) FROM document_links").fetchone()[0] == expected


def test_a_session_cannot_reach_another_project(world, admin):
    """Belt and braces: even holding a valid session, an explicit filter for
    someone else's project returns nothing."""
    code = world["theirs"][0]
    other = next((c for c in world["foreign"] if docs_for(admin, c) > 0), None)
    if other is None:
        pytest.skip("no other project holds documents")
    with open_session(world["user"], code).scoped() as cursor:
        assert cursor.execute(
            "SELECT count(*) FROM document_links WHERE project_code = %s", (other,)
        ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# The gate refuses what it must — the reason this module exists
# ---------------------------------------------------------------------------

def test_a_real_project_the_user_is_not_assigned_to_is_refused(world):
    """**The hole RLS leaves open.** Scoping a session here would have
    returned that project's data quite happily."""
    for code in world["foreign"]:
        with pytest.raises(AccessDenied) as exc:
            open_session(world["user"], code)
        assert exc.value.reason == "not_assigned"


def test_a_project_assigned_to_nobody_is_refused_for_every_user(world):
    if not world["unassigned"]:
        pytest.skip("every project has at least one assignment")
    for username in world["by_user"]:
        for code in world["unassigned"]:
            with pytest.raises(AccessDenied):
                open_session(username, code)


def test_an_unknown_user_is_refused(world):
    with pytest.raises(AccessDenied) as exc:
        open_session("no.such.person", world["theirs"][0])
    assert exc.value.reason == "not_assigned"


def test_a_nonexistent_project_is_refused(world):
    with pytest.raises(AccessDenied):
        open_session(world["user"], "NO-SUCH-PROJECT-ZZ")


@pytest.mark.parametrize("username, project, reason", [
    ("", "PRJ-01", "blank_username"),
    ("   ", "PRJ-01", "blank_username"),
    (None, "PRJ-01", "blank_username"),
    ("someone", "", "blank_project"),
    ("someone", "   ", "blank_project"),
    ("someone", None, "blank_project"),
])
def test_blank_input_is_refused_before_any_query(app_ready, username, project, reason):
    """Asserting `reason`, not just the raise.

    An empty scope makes RLS return zero rows and deny anyway, so these
    guards change diagnosis rather than behaviour — and a purely
    behavioural test cannot see them (STATUS_AND_ROADMAP.md 6, where this
    exact blind spot cost real time twice).
    """
    with pytest.raises(AccessDenied) as exc:
        open_session(username, project)
    assert exc.value.reason == reason


def test_denial_does_not_reveal_whether_the_project_exists(world):
    """Otherwise the error message is a project directory: submit codes,
    read the difference, enumerate the ministry's portfolio."""
    real_but_forbidden = world["foreign"][0]
    with pytest.raises(AccessDenied) as a:
        open_session(world["user"], real_but_forbidden)
    with pytest.raises(AccessDenied) as b:
        open_session(world["user"], "DEFINITELY-NOT-A-PROJECT")
    assert str(a.value) == str(b.value)
    assert a.value.reason == b.value.reason
    assert real_but_forbidden not in str(a.value)


@pytest.mark.parametrize("hostile", [
    "' OR '1'='1",
    "'; DROP TABLE document_links; --",
    "PRJ-01' OR project_code LIKE '%",
    "\\'; SELECT 1; --",
    "PRJ-01\x00",
])
def test_injection_shaped_project_codes_are_refused_and_harmless(world, admin, hostile):
    """The scope is applied with a bind parameter via set_config, so a
    project code is data. SET LOCAL cannot take one, which is why the
    literal form is not used anywhere."""
    with pytest.raises(AccessError):
        open_session(world["user"], hostile)
    assert admin.execute("SELECT count(*) FROM document_links").fetchone()[0] > 0


def test_project_codes_are_matched_case_sensitively(world):
    """Codes come from SPM. Folding case would invent a rule the database
    does not have, in the direction that grants access."""
    code = world["theirs"][0]
    swapped = code.swapcase()
    if swapped == code:
        pytest.skip("project code has no case to swap")
    with pytest.raises(AccessDenied):
        open_session(world["user"], swapped)


# ---------------------------------------------------------------------------
# Read-only, enforced by the database
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("statement", [
    "UPDATE document_links SET doc_type = 'tampered'",
    "DELETE FROM document_links",
    "UPDATE projects SET name_en = 'tampered'",
])
def test_a_valid_session_still_cannot_write(world, statement):
    """These are live destructive statements. They must be unable to commit
    even when they unexpectedly SUCCEED.

    ⚠️ This test previously relied on the `spm_app` role to refuse them,
    and ran them through the pool — which commits on clean exit. During
    mutation testing, the mutation that points the pool at the owner role
    made all three statements succeed, and they were committed before
    `pytest.raises` reported the missing exception. It destroyed the corpus:
    58 document rows, the cascaded OCR cache, and every project name.

    The lesson generalises well beyond this file: **a destructive test must
    not depend on the system under test for its own safety.** So the work
    now happens inside a transaction this test owns and unconditionally
    rolls back. `session.scoped(connection=...)` opens a nested transaction
    (a savepoint), so a refusal still rolls back cleanly to it, while the
    outer block guarantees nothing reaches disk on either path.
    """
    psycopg = pytest.importorskip("psycopg")
    settings = get_settings()
    session = open_session(world["user"], world["theirs"][0])

    with psycopg.connect(settings.postgres_app.dsn, connect_timeout=3) as conn:
        with conn.transaction():
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with session.scoped(connection=conn) as cursor:
                    cursor.execute(statement)
            # Reached only when the statement was correctly refused. Rolling
            # back explicitly means the passing path leaves nothing behind
            # either. If the statement instead succeeded, pytest.raises
            # raises Failed here, which also unwinds this transaction.
            raise psycopg.Rollback()


def test_the_write_denial_tests_cannot_commit(world, admin):
    """Guards the guard: proves the harness above actually rolls back.

    Runs the same shape of statement, asserts it was refused, and then
    confirms from an independent connection that the corpus is untouched.
    Without this, a future refactor could quietly reintroduce the commit
    path that destroyed the corpus once already.
    """
    psycopg = pytest.importorskip("psycopg")
    before = admin.execute("SELECT count(*) FROM document_links").fetchone()[0]
    assert before > 0, "nothing to protect — is the corpus loaded?"

    settings = get_settings()
    session = open_session(world["user"], world["theirs"][0])
    with psycopg.connect(settings.postgres_app.dsn, connect_timeout=3) as conn:
        with conn.transaction():
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with session.scoped(connection=conn) as cursor:
                    cursor.execute("DELETE FROM document_links")
            raise psycopg.Rollback()

    admin.rollback()          # fresh snapshot, not this connection's view
    after = admin.execute("SELECT count(*) FROM document_links").fetchone()[0]
    assert after == before


# ---------------------------------------------------------------------------
# The switcher (migration 014)
# ---------------------------------------------------------------------------

def test_the_switcher_lists_exactly_the_users_own_projects_with_names(world):
    projects = list_assigned_projects(world["user"])
    assert sorted(p.code for p in projects) == sorted(world["theirs"])
    assert all(p.display_name for p in projects), "a switcher needs names, not codes"


def test_the_switcher_shows_nothing_to_an_unknown_user(app_ready):
    assert list_assigned_projects("no.such.person") == []


def test_the_switcher_is_fail_closed_without_an_identity(app_ready):
    assert list_assigned_projects("") == []


def test_the_switcher_does_not_widen_document_access(world):
    """Migration 014 opens `projects` to assigned users. It must not have
    opened anything else — documents stay invisible until a project is
    scoped."""
    with db.scoped(username=world["user"]) as cursor:
        assert cursor.execute("SELECT count(*) FROM document_links").fetchone()[0] == 0
        assert cursor.execute("SELECT count(*) FROM document_content").fetchone()[0] == 0


def test_every_switcher_entry_can_actually_be_opened(world):
    """The switcher and the gate must agree, or a user is offered a project
    that then refuses them."""
    for project in list_assigned_projects(world["user"]):
        assert open_session(world["user"], project.code).project_code == project.code


# ---------------------------------------------------------------------------
# The pool — where SET LOCAL stops being stylistic
# ---------------------------------------------------------------------------

def test_the_pool_reuses_connections(world):
    """Not a performance test. A pool that discards every connection hides
    the leak this file exists to catch, because a fresh connection is
    always clean — and it silently reconnects on every request.
    (`RESET ALL` without a commit did exactly that.)"""
    pids = []
    for _ in range(6):
        with db.scoped(username=world["user"], project=world["theirs"][0]) as cursor:
            pids.append(cursor.execute("SELECT pg_backend_pid()").fetchone()[0])
    assert len(set(pids)) < len(pids), f"no connection was reused: {pids}"


def test_scope_does_not_survive_a_borrow(world):
    """The pooled-connection leak, end to end: scope, return the connection,
    borrow again, and see nothing."""
    code = world["theirs"][0]
    with db.scoped(username=world["user"], project=code) as cursor:
        assert cursor.execute("SELECT count(*) FROM document_links").fetchone()[0] >= 0
    for _ in range(4):
        with db.scoped() as cursor:
            assert cursor.execute("SELECT count(*) FROM document_links").fetchone()[0] == 0
            assert cursor.execute("SELECT count(*) FROM projects").fetchone()[0] == 0


def test_the_pool_scrubs_session_state_a_future_mistake_might_leave(world):
    """Covers the *defence in depth*, not the primary mechanism.

    `SET LOCAL` already ends with its transaction, so removing the pool's
    `RESET ALL` changes nothing while every caller behaves — which is why
    that mutation survived: a redundant layer is invisible when the layer
    it backs up is intact. This exercises what the reset actually exists
    for: someone, someday, reaching for a plain `SET`.

    A dedicated pool with `max_size=1` guarantees the same connection comes
    back, so the assertion cannot pass by getting a fresh one.
    """
    pytest.importorskip("psycopg")
    from psycopg_pool import ConnectionPool

    settings = get_settings()
    code = world["theirs"][0]
    single = ConnectionPool(conninfo=settings.postgres_app.dsn, min_size=1,
                            max_size=1, timeout=5.0, reset=db._reset, open=True,
                            name="reset-probe")
    try:
        with single.connection() as conn:
            first = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            # A plain SET: the mistake the reset is insurance against.
            conn.execute("SELECT set_config(%s, %s, false)", (db.PROJECT_KEY, code))
            assert conn.execute("SELECT count(*) FROM document_links").fetchone()[0] > 0
        with single.connection() as conn:
            assert conn.execute("SELECT pg_backend_pid()").fetchone()[0] == first, \
                "different connection — the test proved nothing"
            assert conn.execute("SELECT count(*) FROM document_links").fetchone()[0] == 0, \
                "a plain SET survived the pool; RESET ALL is not doing its job"
    finally:
        single.close()


def test_the_scope_is_transaction_local_on_one_connection(world, admin):
    """Proves `set_config(..., true)` directly, on a single connection,
    where the pool's own RESET ALL cannot mask a regression to `false`.

    Uses the app role but a connection this test owns, so two sequential
    transactions are observed on the same backend.
    """
    psycopg = pytest.importorskip("psycopg")
    settings = get_settings()
    code = world["theirs"][0]
    expected = docs_for(admin, code)
    with psycopg.connect(settings.postgres_app.dsn, connect_timeout=3) as conn:
        cursor = conn.cursor()
        db.apply_scope(cursor, project=code, username=world["user"])
        assert cursor.execute("SELECT count(*) FROM document_links").fetchone()[0] == expected
        conn.commit()
        # Same connection, next transaction. Nothing re-applied.
        assert cursor.execute("SELECT count(*) FROM document_links").fetchone()[0] == 0
        conn.rollback()


def test_the_username_scope_is_transaction_local_too(world, admin):
    """The project scope is not the only one that can leak.

    ⚠️ Added because a mutation SURVIVED: switching only the *username*
    scope to connection-level was caught by nothing. The test above applies
    both scopes but asserts on `document_links`, which is scoped by
    project — so a leaking username was invisible to it, and the pool's own
    RESET ALL hid it from the pooled tests.

    `projects` is the observable that depends on username alone, via
    migration 014's policy. If the username survives the transaction, a
    user's assigned projects stay visible into the next one.
    """
    psycopg = pytest.importorskip("psycopg")
    settings = get_settings()
    expected = len(world["theirs"])
    with psycopg.connect(settings.postgres_app.dsn, connect_timeout=3) as conn:
        cursor = conn.cursor()
        db.apply_scope(cursor, username=world["user"])
        assert cursor.execute("SELECT count(*) FROM projects").fetchone()[0] == expected
        conn.commit()
        assert cursor.execute("SELECT count(*) FROM projects").fetchone()[0] == 0, \
            "the username scope survived its transaction"
        conn.rollback()


def test_concurrent_sessions_on_different_projects_do_not_cross(world, admin):
    """Two requests in flight on one pool. If scope were connection-level
    rather than transaction-level, these would contaminate each other."""
    codes = [c for c in world["theirs"] if docs_for(admin, c) > 0][:2]
    if len(codes) < 2:
        pytest.skip("need two of the user's projects to hold documents")
    expected = {c: docs_for(admin, c) for c in codes}
    barrier = threading.Barrier(len(codes))

    def read(code: str) -> tuple[str, int, int]:
        session = open_session(world["user"], code)
        with session.scoped() as cursor:
            barrier.wait(timeout=10)      # force genuine overlap
            mine = cursor.execute("SELECT count(*) FROM document_links").fetchone()[0]
            others = cursor.execute(
                "SELECT count(*) FROM document_links WHERE project_code <> %s", (code,)
            ).fetchone()[0]
        return code, mine, others

    with ThreadPoolExecutor(max_workers=len(codes)) as executor:
        for code, mine, others in executor.map(read, codes):
            assert mine == expected[code], f"{code} saw {mine}, expected {expected[code]}"
            assert others == 0, f"{code} leaked {others} rows from another project"


# ---------------------------------------------------------------------------
# Failure is not permission
# ---------------------------------------------------------------------------

def test_an_unreachable_database_denies_rather_than_opens(settings):
    """Fail-closed on an outage. An empty result set from a broken
    connection is indistinguishable from 'permitted but empty', so this
    must raise rather than return."""
    broken = dataclasses.replace(
        settings,
        postgres_app=dataclasses.replace(settings.postgres_app, port=59999),
        application=dataclasses.replace(settings.application,
                                        pool_min_size=0, pool_timeout_seconds=1.0),
    )
    db.close_pool()
    try:
        with pytest.raises(AccessError):
            open_session("someone", "PRJ-01", settings=broken)
    finally:
        db.close_pool()


def test_backend_unavailable_is_an_access_error(settings):
    """A caller that only knows how to fail closed must catch both."""
    assert issubclass(BackendUnavailable, AccessError)
    assert issubclass(AccessDenied, AccessError)


def test_the_application_refuses_to_run_as_the_pipeline_role(settings):
    """`spm_admin` owns the tables and bypasses RLS. Configured with it,
    every query here would return all projects and every isolation test
    would still pass, because the tests scope correctly — they verify the
    policies, not the role. This is the only place that catches it."""
    misconfigured = dataclasses.replace(
        settings, postgres_app=dataclasses.replace(
            settings.postgres_app, user=settings.postgres.user)
    )
    with pytest.raises(BackendUnavailable, match="bypasses row-level security"):
        db._assert_not_admin(misconfigured)


def test_a_blank_application_role_is_refused(settings):
    blank = dataclasses.replace(
        settings, postgres_app=dataclasses.replace(settings.postgres_app, user="  ")
    )
    with pytest.raises(BackendUnavailable, match="POSTGRES_SPM_APP_USER"):
        db._assert_not_admin(blank)


# ---------------------------------------------------------------------------
# The default is nothing
# ---------------------------------------------------------------------------

def test_an_unscoped_read_sees_nothing(app_ready):
    """`db.scoped()` with no arguments is legitimate and must be empty —
    callers who forget to scope get emptiness, not everything."""
    with db.scoped() as cursor:
        for table in ("projects", "document_links", "document_content",
                      "project_assignments"):
            assert cursor.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# The SPM record read — against the real database, where 014 actually bites
# ---------------------------------------------------------------------------

def test_the_spm_record_is_this_session_s_project_not_another_of_the_users(world):
    """⚠️ The regression a stub could never reproduce.

    Migration 014 lets a user read every project they are assigned to, so
    inside a scoped session `projects` yields more than one row. An
    unfiltered `fetchone()` therefore returned a DIFFERENT project of the
    same user's — found live, where a session on one project reported
    another's budget as its own.

    This test needs the real database and a user with several projects,
    which is exactly why the stubbed suite missed it.
    """
    from Application.backend import spm

    if len(world["theirs"]) < 2:
        pytest.skip("needs a user with two or more projects")

    for code in world["theirs"]:
        session = open_session(world["user"], code)
        record = spm.fetch(session)
        assert record is not None, f"no project record readable for {code}"
        assert record.code == code, (
            f"session on {code} read {record.code} — another of this user's "
            f"projects leaked into the record read"
        )


def test_the_raw_projects_read_really_does_return_several_rows(world):
    """Proves the hazard above is real rather than theoretical, so the
    filter is not mistaken for redundant belt-and-braces later."""
    if len(world["theirs"]) < 2:
        pytest.skip("needs a user with two or more projects")
    code = world["theirs"][0]
    with db.scoped(project=code, username=world["user"]) as cursor:
        visible = [r[0] for r in
                   cursor.execute("SELECT code FROM projects ORDER BY code").fetchall()]
    assert len(visible) > 1, "014 no longer widens `projects`; re-check spm.fetch"
    assert code in visible
