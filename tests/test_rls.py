"""Tests for the SQL isolation boundary — migration 013's row-level security.

**Why this file exists.** RLS is one of the two layers that make AC09
("no user can access an unassigned project's documents") structural rather
than a promise, and until now it had no regression test at all. Every other
test in this suite connects as `spm_admin` — the table owner, which
**bypasses RLS entirely** — so not one of them could ever have noticed this
break. The seven checks recorded when `013` landed were run by hand, once.

Three properties are defended here, in rising order of how quietly they
would fail:

  1. **Writes are refused.** Loud and obvious if it regresses.
  2. **A scoped session sees one project.** Also obvious — the wrong data
     would show up in an answer.
  3. **An *unscoped* session sees nothing, and scope does not survive a
     transaction.** This is the silent one. A pooled connection that
     inherits the previous request's project would serve one project
     manager another's documents, with no error anywhere, and the leak
     would come from the isolation mechanism itself.

**These tests are read-only, deliberately.** RLS can only be observed
across two connections — the writer cannot be the reader, because the
writer bypasses the thing under test — so the usual trick of writing
inside a transaction that is always rolled back does not work here: the
`spm_app` connection would not see uncommitted rows. Rather than commit
fixture data into the live database and hope the cleanup runs, these tests
assert *relationships* over whatever corpus is present. Nothing is written,
so nothing can be left behind.

**Nothing here is hardcoded to this corpus.** Project codes, usernames,
row counts and even the role name are discovered at run time. The suite is
just as valid against a real SharePoint export with different projects, and
`test_every_table_the_app_can_read_is_scoped_or_exempt` is written so that
a *future* migration adding a table without a policy fails a test instead
of opening a hole nobody notices.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from pipeline.config import get_settings

# `users` carries no project dimension — it is names and emails, needed to
# render whoever is signed in. It is therefore granted without a policy, on
# purpose. Anything else appearing here should be a deliberate decision, and
# the coverage test below is what forces that decision to be made rather
# than defaulted into.
INTENTIONALLY_UNSCOPED = frozenset({"users"})

PROJECT_KEY = "app.current_project"
USERNAME_KEY = "app.current_username"


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def _connect(dsn: str, who: str):
    psycopg = pytest.importorskip("psycopg")
    try:
        return psycopg.connect(dsn, connect_timeout=3)
    except psycopg.OperationalError as exc:                  # pragma: no cover
        pytest.skip(f"cannot connect as {who} — is mof-postgres-spm up? ({exc})")


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def admin(settings):
    """The pipeline's role. Used only to read ground truth to compare against."""
    conn = _connect(settings.postgres.dsn, "spm_admin")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def app(settings):
    """The chat application's role — the one actually under test.

    A fresh connection per test, so a test that deliberately leaks session
    state (there is one) cannot contaminate its neighbours.
    """
    if not settings.postgres_app.password:
        pytest.skip("POSTGRES_SPM_APP_PASSWORD is not set in infra/.env")
    conn = _connect(settings.postgres_app.dsn, settings.postgres_app.user)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def corpus(admin):
    """Two distinct projects that actually hold documents, whatever they are.

    Skips rather than fails on a corpus too small to demonstrate isolation —
    with only one project there is nothing to leak *from*.
    """
    rows = admin.execute(
        "SELECT project_code, count(*) FROM document_links GROUP BY 1 "
        "ORDER BY count(*) DESC, project_code"
    ).fetchall()
    if len(rows) < 2:
        pytest.skip(f"needs 2+ projects holding documents, found {len(rows)}")
    return [(code, int(n)) for code, n in rows]


@contextmanager
def scoped(conn, *, project=None, username=None):
    """One transaction with the session scope applied, always rolled back.

    `set_config(name, value, true)` is the *parameterised* equivalent of
    `SET LOCAL name = value`. The literal form cannot take a bind parameter,
    so building it by string concatenation is the only alternative — with a
    project code arriving from a request, that is an injection into the very
    statement that establishes the security scope. Whatever consumes this
    boundary should use this form. `test_the_documented_set_local_form_behaves
    _identically` pins the two together.
    """
    cur = conn.cursor()
    if project is not None:
        cur.execute(f"SELECT set_config('{PROJECT_KEY}', %s, true)", (project,))
    if username is not None:
        cur.execute(f"SELECT set_config('{USERNAME_KEY}', %s, true)", (username,))
    try:
        yield cur
    finally:
        conn.rollback()


def count(cur, table: str, where: str = "", params: tuple = ()) -> int:
    return cur.execute(f"SELECT count(*) FROM {table} {where}", params).fetchone()[0]


# ---------------------------------------------------------------------------
# The role itself
# ---------------------------------------------------------------------------

def test_the_app_role_can_log_in(app, settings):
    """If this fails every other assertion below is vacuous."""
    assert app.execute("SELECT current_user").fetchone()[0] == settings.postgres_app.user


def test_the_app_role_cannot_bypass_row_level_security(admin, settings):
    """Superuser or BYPASSRLS would make every policy in migration 013 decorative."""
    row = admin.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
        (settings.postgres_app.user,),
    ).fetchone()
    assert row is not None, f"role {settings.postgres_app.user!r} does not exist"
    is_super, bypasses = row
    assert not is_super, "the application role must not be a superuser"
    assert not bypasses, "the application role must not have BYPASSRLS"


# ---------------------------------------------------------------------------
# Fail-closed — the property that decides which way a mistake goes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", ["projects", "document_links", "document_content"])
def test_an_unscoped_session_sees_nothing(app, corpus, table):
    """Forgetting to scope must yield zero rows, never every row.

    `current_setting(..., true)` returns NULL when unset and `code = NULL`
    is never true — so the failure mode of forgetting is emptiness. The
    corpus fixture guarantees there is real data to be denied, otherwise
    this would pass against an empty database and prove nothing.
    """
    with scoped(app) as cur:
        assert count(cur, table) == 0


def test_the_corpus_really_does_hold_the_rows_being_denied(admin, corpus):
    """Guards the test above from passing for the wrong reason."""
    total = sum(n for _, n in corpus)
    assert count(admin.cursor(), "document_links") == total
    assert total > 0


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------

def test_a_scoped_session_sees_exactly_its_own_project(app, admin, corpus):
    code, expected = corpus[0]
    assert expected > 0
    with scoped(app, project=code) as cur:
        assert count(cur, "document_links") == expected
        assert count(cur, "projects") == 1


def test_a_scoped_session_cannot_reach_another_project(app, corpus):
    """The careless query — an explicit filter for someone else's project."""
    mine, _ = corpus[0]
    theirs, theirs_n = corpus[1]
    assert theirs_n > 0, "the other project must hold rows for this to mean anything"
    with scoped(app, project=mine) as cur:
        assert count(cur, "document_links",
                     "WHERE project_code = %s", (theirs,)) == 0


def test_document_content_is_scoped_through_its_parent_link(app, admin, corpus):
    """`document_content` has no project column; it inherits scope by join."""
    code, _ = corpus[0]
    expected = admin.execute(
        "SELECT count(*) FROM document_content dc "
        "JOIN document_links dl ON dl.id = dc.document_link_id "
        "WHERE dl.project_code = %s", (code,),
    ).fetchone()[0]
    if not expected:
        pytest.skip(f"{code} has no OCR'd documents to scope")
    with scoped(app, project=code) as cur:
        assert count(cur, "document_content") == expected


def test_an_unknown_project_code_yields_nothing_rather_than_an_error(app):
    """A wrong scope must be empty, not a crash and not a fallback to all."""
    with scoped(app, project="NO-SUCH-PROJECT-ZZ") as cur:
        assert count(cur, "document_links") == 0


def test_assignments_are_scoped_by_user_not_by_project(app, admin):
    """The switcher needs a user's own assignments — and only their own."""
    row = admin.execute(
        "SELECT username, count(*) FROM project_assignments GROUP BY 1 "
        "ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no project assignments seeded")
    username, expected = row
    total = count(admin.cursor(), "project_assignments")

    with scoped(app, username=username) as cur:
        assert count(cur, "project_assignments") == expected
    if total > expected:
        with scoped(app, username="nobody.at.all") as cur:
            assert count(cur, "project_assignments") == 0


# ---------------------------------------------------------------------------
# SET LOCAL — the pooled-connection leak, and the reason it is worded that way
# ---------------------------------------------------------------------------

def test_scope_does_not_survive_the_transaction(app, corpus):
    """The whole reason the rule is `SET LOCAL` and not `SET`.

    A pooled connection is handed to the next request. If the scope came
    with it, the isolation mechanism would itself be the leak.
    """
    code, expected = corpus[0]
    cur = app.cursor()
    cur.execute(f"SELECT set_config('{PROJECT_KEY}', %s, true)", (code,))
    assert count(cur, "document_links") == expected
    app.commit()

    assert count(cur, "document_links") == 0, "scope survived the commit"
    app.rollback()


def test_a_plain_set_really_does_leak_and_is_why_the_rule_exists(app, corpus):
    """Characterises the danger rather than assuming it.

    Asserting the *unsafe* behaviour is deliberate: it is the evidence
    behind a rule stated in three documents, and it fails loudly if the
    semantics ever change and the rule stops being necessary.
    """
    code, expected = corpus[0]
    cur = app.cursor()
    cur.execute(f"SELECT set_config('{PROJECT_KEY}', %s, false)", (code,))  # NOT local
    app.commit()

    assert count(cur, "document_links") == expected, (
        "a plain SET no longer persists across transactions — if this fails, "
        "the SET LOCAL rule may no longer be load-bearing; re-read "
        "INFRASTRUCTURE.md 7c before relaxing anything"
    )
    app.rollback()


def test_the_documented_set_local_form_behaves_identically(app, corpus):
    """`SET LOCAL x = 'y'` is what the docs prescribe; `set_config(..., true)`
    is what code must use, because only the latter takes a bind parameter."""
    code, expected = corpus[0]
    cur = app.cursor()
    cur.execute(f"SET LOCAL {PROJECT_KEY} = '{code}'")
    assert count(cur, "document_links") == expected
    app.commit()
    assert count(cur, "document_links") == 0
    app.rollback()


# ---------------------------------------------------------------------------
# Read-only, enforced by the database rather than by convention
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("statement", [
    "UPDATE document_links SET doc_type = 'tampered'",
    "DELETE FROM document_links",
    "INSERT INTO document_links (project_code, filename, file_path, doc_type) "
    "VALUES ('X', 'y', 'x/y', 'z')",
    "UPDATE projects SET name_en = 'tampered'",
    "DELETE FROM document_content",
    "UPDATE project_assignments SET username = 'tampered'",
])
def test_the_app_role_cannot_write(app, corpus, statement):
    """The BRD's "performs no write operations" as an enforced fact.

    Scoped, so the attempt is the most favourable one possible: rows are
    visible and the only thing standing in the way is the missing grant.
    """
    psycopg = pytest.importorskip("psycopg")
    code, _ = corpus[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with scoped(app, project=code) as cur:
            cur.execute(statement)


# ---------------------------------------------------------------------------
# Coverage — the test that survives a schema nobody has written yet
# ---------------------------------------------------------------------------

def test_every_table_the_app_can_read_is_scoped_or_exempt(admin, settings):
    """A future migration must not be able to open a hole silently.

    Adding a table and granting the app role SELECT is a one-line change
    that is easy to make and impossible to notice. This asks the catalog
    directly, so it covers tables that do not exist yet.
    """
    role = settings.postgres_app.user
    granted = {r[0] for r in admin.execute(
        "SELECT table_name FROM information_schema.role_table_grants "
        "WHERE grantee = %s AND privilege_type = 'SELECT'", (role,)).fetchall()}
    assert granted, f"{role} can read nothing at all — is migration 013 applied?"

    protected = {r[0] for r in admin.execute(
        "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
        "AND relkind = 'r' AND relrowsecurity").fetchall()}

    unguarded = granted - protected - INTENTIONALLY_UNSCOPED
    assert not unguarded, (
        f"{sorted(unguarded)} are readable by {role} with row-level security "
        f"switched off. Either add a policy, or add the table to "
        f"INTENTIONALLY_UNSCOPED with the reason written down."
    )


def test_every_scoped_table_actually_has_a_policy_for_the_app_role(admin, settings):
    """RLS enabled with no policy denies everything; enabled with a policy for
    the wrong role denies everything too. Both are outages, not leaks — but
    both would be found in production rather than here."""
    role = settings.postgres_app.user
    granted = {r[0] for r in admin.execute(
        "SELECT table_name FROM information_schema.role_table_grants "
        "WHERE grantee = %s AND privilege_type = 'SELECT'", (role,)).fetchall()}
    protected = {r[0] for r in admin.execute(
        "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
        "AND relkind = 'r' AND relrowsecurity").fetchall()}
    with_policy = {r[0] for r in admin.execute(
        "SELECT tablename FROM pg_policies WHERE %s = ANY(roles)", (role,)).fetchall()}

    missing = (granted & protected) - with_policy
    assert not missing, (
        f"{sorted(missing)} have row-level security enabled but no policy "
        f"granting {role} anything — every read returns empty"
    )


def test_no_policy_grants_the_app_role_more_than_select(admin, settings):
    """A policy written FOR ALL would quietly widen the boundary the grants
    were carefully narrowed to."""
    role = settings.postgres_app.user
    wide = admin.execute(
        "SELECT tablename, policyname, cmd FROM pg_policies "
        "WHERE %s = ANY(roles) AND cmd <> 'SELECT'", (role,)).fetchall()
    assert not wide, f"non-SELECT policies for {role}: {wide}"
