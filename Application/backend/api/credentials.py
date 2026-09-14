"""Verifying a demo sign-in against the seeded accounts.

⚠️ **This is a simulation of sign-in, not authentication.** Real AD SSO and
OTP are external dependencies that do not exist in this environment. What
this gives the demo is a real "who am I" flow — a user types their own
address, gets their own projects, and is greeted by their own name — without
pretending to be a security control. Migration 015 says the same thing next
to the data.

Three properties it does hold, because getting them wrong teaches the wrong
shape to whoever replaces this with the real thing:

  1. **Passwords are never stored or compared in plaintext.** PBKDF2-
     HMAC-SHA256, per-user salt, parameters read from the stored string
     rather than assumed, and `compare_digest` for the comparison.
  2. **A wrong password and an unknown address are indistinguishable.** One
     message, one timing profile — a login form that answers "no such user"
     faster than "wrong password" is an account directory.
  3. **An account with no password cannot be signed in to.** A NULL hash
     means "not provisioned", never "any password will do".

The lookup runs as the pipeline's admin role, because the application role
is deliberately scoped to one project per transaction and sign-in happens
before any project exists. That is the one read in the whole application
that legitimately precedes the session gate.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass

from Application.backend.errors import AccessDenied, BackendUnavailable

log = logging.getLogger(__name__)

# Burned on an unknown address so that "no such user" costs roughly what a
# real verification costs. Without it, response time answers the question the
# error message refuses to.
_DUMMY_HASH = ("pbkdf2_sha256$240000$AAAAAAAAAAAAAAAAAAAAAA=="
               "$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")


@dataclass(frozen=True)
class Account:
    """A signed-in demo user."""
    username: str
    email: str
    full_name_ar: str
    full_name_en: str

    @property
    def names(self) -> tuple[str, str]:
        """(Arabic, English) full names — what the account screen shows."""
        return self.full_name_ar, self.full_name_en

    @property
    def first_names(self) -> tuple[str, str]:
        """(Arabic, English) first names — how the assistant addresses the user.

        "أهلاً بك يا نورة", not "أهلاً بك يا نورة بنت عبدالعزيز الحربي": the
        full name is the formal record, the first name is how a colleague
        speaks. Each language is shortened on its own, because the two are
        not transliterations of each other token by token.
        """
        return first_name(self.full_name_ar), first_name(self.full_name_en)


# Titles written before a name. Kept out of the first name: "Eng. Sultan"
# is addressed as "Sultan", and "م." alone is not a name at all.
_TITLES = {"م", "د", "أ", "أ.د", "eng", "dr", "mr", "mrs", "ms", "prof"}

# "Abd" forms a single first name with the word after it. Written with a
# space — "عبد الله", "Abdul Rahman" — the first word alone would address
# the user as "عبد", which means "servant". Written joined ("عبدالله")
# there is nothing to do.
_COMPOUND_PREFIXES = {"عبد", "abd", "abdul", "abdel", "abdal", "abdol"}


def first_name(full_name: str) -> str:
    """The name a person is addressed by: the first word, minus titles,
    with "Abd"-compounds kept whole. The full name if nothing is left."""
    words = (full_name or "").split()
    while words and words[0].rstrip(".").lower() in _TITLES:
        words = words[1:]
    if not words:
        return (full_name or "").strip()
    if len(words) > 1 and words[0].lower() in _COMPOUND_PREFIXES:
        return f"{words[0]} {words[1]}"
    return words[0]


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check against a self-describing PBKDF2 string.

    The iteration count and salt come out of the stored value, so rotating
    them is a data change rather than a code change — and an old hash keeps
    verifying while new ones use the new cost.
    """
    if not stored or not password:
        return False
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(hash_b64)
        computed = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            base64.b64decode(salt_b64), int(iterations), dklen=len(expected),
        )
    except (ValueError, TypeError):
        # A malformed hash is a provisioning fault, not a valid password.
        return False
    return hmac.compare_digest(computed, expected)


def authenticate(email: str, password: str, *, connection=None) -> Account:
    """Return the account, or raise `AccessDenied`. Never returns None.

    Raising rather than returning a falsy value is the same reasoning as the
    session gate: a caller that forgets to check a return value fails open,
    and a caller that forgets to catch an exception fails closed.
    """
    address = (email or "").strip().lower()
    if not address or "\x00" in address or not password:
        raise AccessDenied("bad_credentials")

    try:
        if connection is not None:
            row = _lookup(connection, address)
        else:
            from pipeline.db import admin_connection
            with admin_connection() as conn:
                row = _lookup(conn, address)
    except BackendUnavailable:
        raise
    except Exception as exc:                                  # noqa: BLE001
        # An unreachable directory is an outage, never a failed sign-in.
        raise BackendUnavailable(f"could not verify the sign-in: {exc}") from exc

    if row is None:
        # Spend the same work as a real check before refusing, so timing
        # does not distinguish an unknown address from a wrong password.
        verify_password(password, _DUMMY_HASH)
        raise AccessDenied("bad_credentials")

    username, stored_email, name_ar, name_en, password_hash = row
    if not verify_password(password, password_hash):
        raise AccessDenied("bad_credentials")

    return Account(username=username, email=stored_email,
                   full_name_ar=name_ar, full_name_en=name_en)


def _lookup(connection, address: str):
    return connection.execute(
        "SELECT username, email, full_name_ar, full_name_en, password_hash "
        "FROM users WHERE lower(email) = %s",
        (address,),
    ).fetchone()


def load_account(username: str, *, connection=None) -> Account | None:
    """Re-read an account for an already-authenticated session cookie."""
    identity = (username or "").strip()
    if not identity or "\x00" in identity:
        return None
    try:
        if connection is not None:
            row = _account_row(connection, identity)
        else:
            from pipeline.db import admin_connection
            with admin_connection() as conn:
                row = _account_row(conn, identity)
    except Exception as exc:                                  # noqa: BLE001
        raise BackendUnavailable(f"could not read the account: {exc}") from exc
    if row is None:
        return None
    return Account(username=row[0], email=row[1],
                   full_name_ar=row[2], full_name_en=row[3])


def _account_row(connection, username: str):
    return connection.execute(
        "SELECT username, email, full_name_ar, full_name_en "
        "FROM users WHERE username = %s",
        (username,),
    ).fetchone()
