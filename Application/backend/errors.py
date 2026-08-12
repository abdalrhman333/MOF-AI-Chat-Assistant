"""Failure types for the access boundary.

Types rather than strings, for one reason that matters: **a caller must be
able to distinguish "denied" from "broken" without parsing a message**, and
must never accidentally treat "broken" as "allowed". Everything here
inherits from a single base so a route can catch one thing and fail closed.
"""
from __future__ import annotations


class AccessError(Exception):
    """Base for anything that must prevent a session from opening."""


class AccessDenied(AccessError):
    """The user may not open a session on the requested project.

    **The message is deliberately uniform.** "You are not assigned to that
    project" and "that project does not exist" must be indistinguishable to
    the caller, or the error response becomes a project directory: an
    attacker submits codes and reads the difference. The distinction is not
    hidden from *us* — `reason` carries it for logging — but it never
    reaches a rendered message.

    `reason` is a short stable slug, never prose, so logs stay greppable:

      ``blank_username``   the identity was empty or whitespace
      ``blank_project``    the requested project code was empty or whitespace
      ``malformed_input``  contained a NUL byte, which Postgres cannot store
                           in a text column. Carries no echo of the input.
      ``not_assigned``     no assignment exists — INCLUDING when the project
                           does not exist at all. One slug for both, on
                           purpose.
    """

    def __init__(self, reason: str, username: str = "", project_code: str = ""):
        self.reason = reason
        self.username = username
        self.project_code = project_code
        super().__init__("access denied: the requested project is not available "
                         "to this user")

    def __repr__(self) -> str:                                # for logs only
        return (f"AccessDenied(reason={self.reason!r}, "
                f"username={self.username!r}, project_code={self.project_code!r})")


class BackendUnavailable(AccessError):
    """The database could not be reached, so no decision could be made.

    Separate from `AccessDenied` because they mean opposite things
    operationally — one is a user problem, the other is an outage — but it
    inherits `AccessError` so that a caller which only knows how to fail
    closed still does the right thing. An unreachable database must never
    be read as permission.
    """
