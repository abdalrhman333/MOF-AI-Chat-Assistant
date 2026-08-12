"""The AC09 session gate — the most security-sensitive code in the repo.

AC09 is worded as an absolute in the source requirements: **لا يستطيع أي
مستخدم الاطلاع على وثائق مشروع غير مخصص له** — no user can access an
unassigned project's documents under any circumstance. Three layers stand
behind that, and only one of them is here:

  | Layer                         | Enforced by            | Can it be forgotten? |
  |-------------------------------|------------------------|----------------------|
  | One Qdrant collection/project | infrastructure         | no — no shared index |
  | Row-level security            | the database           | no — fail-closed     |
  | **Is this project theirs?**   | **this module**        | **yes. Hence tests** |

**Row-level security does not answer the third question and cannot.** It
serves whatever project the session declares. Demonstrated live on
2026-08-09: a user assigned to PRJ-02/05/06, with a session scoped to
PRJ-01, was handed PRJ-01's row without complaint. That is RLS working
correctly — scoping is an input to it, not something it validates. Deciding
whether a scope is *permitted* happens exactly once, here, before a session
exists.

**Trust boundary.** This module trusts the username it is handed. Real AD
SSO and OTP are external dependencies that do not exist in this
environment, and the BRD's own note for a demo build says to simulate what
can be simulated honestly and flag the rest rather than build something
that merely resembles authentication. So the gate answers *"may this user
open this project?"* and never *"is this really that user?"*. Whatever
supplies the username later becomes part of this boundary.

**Nothing here is hardcoded.** No usernames, no project codes, no counts.
Authorisation is whatever `project_assignments` says at the moment it is
asked, which is what makes the same code correct against a real directory.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from Application.backend import db
from Application.backend.errors import AccessDenied
from pipeline.config import Settings


@dataclass(frozen=True)
class AssignedProject:
    """One entry for the project switcher."""
    code: str
    name_ar: str | None
    name_en: str | None
    status: str | None

    @property
    def display_name(self) -> str:
        return self.name_en or self.name_ar or self.code


@dataclass(frozen=True)
class Session:
    """Proof that the gate allowed this user onto this project.

    A value object, deliberately: it holds no connection and owns no
    resources, so it is cheap to pass around and impossible to leave open.
    What it *is* is evidence — the only way to obtain one is through
    `open_session`, so a function that requires a `Session` argument cannot
    be called with an unvalidated project code.

    Use `.scoped()` rather than reaching for `db.scoped()` directly. Both
    work; only this one cannot be called with the wrong project, because it
    does not accept a project to be wrong about.
    """
    username: str
    project_code: str

    @contextmanager
    def scoped(self, *, settings: Settings | None = None,
               connection=None) -> Iterator:
        """A transaction scoped to this session, yielding a cursor."""
        with db.scoped(project=self.project_code, username=self.username,
                       settings=settings, connection=connection) as cursor:
            yield cursor


def _clean(value: str | None) -> str:
    """Normalise to a bare string. Never alters case.

    Project codes come from SPM and are matched exactly. Folding case would
    invent a rule the database does not have, and a lookup that succeeds
    where the source system would fail is a bug in the direction that
    grants access.
    """
    return value.strip() if isinstance(value, str) else ""


def _is_storable(value: str) -> bool:
    """Reject only what Postgres itself cannot represent: a NUL byte.

    Found by adversarial testing. A project code containing `\\x00` reached
    the driver and raised `psycopg.DataError`, which is **not** an
    `AccessError` — so it escaped this module's contract, and a caller that
    catches `AccessError` in order to fail closed would not have handled
    it. No session opened, so it was never a breach, but an input that
    produces a different exception type is both a broken contract and a
    weak signal an attacker can probe.

    Deliberately minimal. Imposing a character set or a regex on project
    codes would invent a rule the source system does not have — the same
    mistake as folding case — and would refuse a legitimate SPM code that
    happens to look unusual. NUL is not a judgement call: the database
    cannot store it in a text column at all.
    """
    return "\x00" not in value


def open_session(username: str, project_code: str, *,
                 settings: Settings | None = None,
                 connection=None) -> Session:
    """Authorise a user onto exactly one project, or refuse.

    Raises `AccessDenied` when the pairing is not permitted, and
    `BackendUnavailable` when no decision could be reached. Both inherit
    `AccessError`, so a caller that only knows how to fail closed still
    does the right thing — an unreachable database is never permission.

    The check runs as `spm_app` under the same row-level security it is
    enforcing: the query below carries **no `WHERE username = ...` clause**,
    because the `assignments_own_only` policy has already constrained the
    rows to the scoped user. Two independent mechanisms therefore have to
    fail before a wrong answer is possible.
    """
    user = _clean(username)
    project = _clean(project_code)

    # Checked before any query. An empty scope would make RLS return zero
    # rows and deny anyway, so this guard is about *diagnosis*, not
    # behaviour — which is exactly the kind of guard that a purely
    # behavioural test cannot see (STATUS_AND_ROADMAP.md 6). The tests
    # therefore assert on `reason`, so removing this is caught.
    if not user:
        raise AccessDenied("blank_username", user, project)
    if not project:
        raise AccessDenied("blank_project", user, project)
    if not _is_storable(user) or not _is_storable(project):
        # Refused here so every rejection leaves this module as the same
        # exception type. See `_is_storable`.
        raise AccessDenied("malformed_input", "", "")

    with db.scoped(username=user, settings=settings, connection=connection) as cursor:
        row = cursor.execute(
            "SELECT 1 FROM project_assignments WHERE project_code = %s",
            (project,),
        ).fetchone()

    if row is None:
        # One reason for both "not yours" and "does not exist". Telling them
        # apart here is what turns an error message into a project
        # directory: submit codes, read the difference, enumerate the
        # ministry's portfolio.
        raise AccessDenied("not_assigned", user, project)

    return Session(username=user, project_code=project)


def list_assigned_projects(username: str, *,
                           settings: Settings | None = None,
                           connection=None) -> list[AssignedProject]:
    """Every project this user may open, with names, for the switcher.

    Reads `projects` with a username scope and **no project scope**, which
    migration `014` exists to make possible: before a session there is no
    current project, and `013` alone returned nothing. The rows come back
    filtered by the database rather than by a filter written here.

    Returns an empty list for an unknown user — no error, because "you have
    no projects" and "you are not a user" are the same answer to a
    switcher, and distinguishing them would leak directory membership.
    """
    identity = _clean(username)
    if not _is_storable(identity):
        return []
    with db.scoped(username=identity, settings=settings,
                   connection=connection) as cursor:
        rows = cursor.execute(
            "SELECT code, name_ar, name_en, status FROM projects ORDER BY code"
        ).fetchall()
    return [AssignedProject(*row) for row in rows]
