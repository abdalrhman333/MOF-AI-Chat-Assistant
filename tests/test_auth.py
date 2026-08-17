"""Tests for the demo sign-in: credential checking and the session cookie.

**This is a simulation of sign-in, and the tests are about the properties
that must hold anyway** — because getting them wrong here teaches the wrong
shape to whoever replaces this with real AD SSO.

  1. **No plaintext, ever.** The stored value is a PBKDF2 string; the
     comparison is constant-time; the parameters come out of the stored
     value rather than being assumed.
  2. **A wrong password and an unknown address are indistinguishable** — in
     the reason, and in roughly the work done, because a login form that
     answers faster for an unknown address is an account directory.
  3. **An unprovisioned account cannot be signed in to.** A NULL hash means
     "no password set", never "any password will do".
  4. **The cookie is signed, and the signature is checked before the payload
     is parsed.** An unsigned cookie is a username the browser can edit —
     and parsing first means acting on attacker-controlled JSON before
     knowing we wrote it.

Nothing here touches the database: `authenticate` takes an injected
connection, for the same reason every other paid or stateful path in this
repo does.
"""
from __future__ import annotations

import base64
import hashlib
import time

import pytest

from Application.backend.api import cookies, credentials
from Application.backend.errors import AccessDenied, BackendUnavailable

PASSWORD = "correct horse battery staple"
SECRET = "a-test-signing-secret"


def make_hash(password: str, iterations: int = 1000, salt: bytes = b"0123456789abcdef") -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return (f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}"
            f"${base64.b64encode(dk).decode()}")


class StubConn:
    """Returns one row for one address, like the real lookup would."""

    def __init__(self, row=None):
        self.row, self.queries = row, []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return self

    def fetchone(self):
        return self.row


def account_row(password_hash=None, username="noura.alharbi"):
    return (username, "noura.alharbi@enjaz.gov.sa",
            "نورة الحربي", "Noura Al-Harbi", password_hash)


# ---------------------------------------------------------------------------
# 1. Password verification
# ---------------------------------------------------------------------------

def test_the_right_password_verifies():
    assert credentials.verify_password(PASSWORD, make_hash(PASSWORD))


def test_the_wrong_password_does_not():
    assert not credentials.verify_password("wrong", make_hash(PASSWORD))


@pytest.mark.parametrize("stored", [None, "", "not-a-hash", "pbkdf2_sha256$x$y",
                                    "bcrypt$12$abc$def", "a$b$c$d"])
def test_a_malformed_or_missing_hash_never_verifies(stored):
    """An unprovisioned or corrupt account is not a free pass."""
    assert not credentials.verify_password(PASSWORD, stored)


def test_an_empty_password_never_verifies():
    assert not credentials.verify_password("", make_hash(""))


def test_the_iteration_count_is_read_from_the_stored_value():
    """So the cost can be raised by rewriting data, and old hashes keep
    working while new ones use the new cost."""
    cheap = make_hash(PASSWORD, iterations=1000)
    dear = make_hash(PASSWORD, iterations=5000)
    assert cheap != dear
    assert credentials.verify_password(PASSWORD, cheap)
    assert credentials.verify_password(PASSWORD, dear)


def test_the_hash_never_contains_the_password():
    assert PASSWORD not in make_hash(PASSWORD)


def test_both_secret_comparisons_are_constant_time():
    """Structural, because nothing behavioural can see this.

    `==` and `compare_digest` return the same answer for every input — the
    only difference is how long `==` takes to say no, which leaks how much
    of the secret was right. Two mutations swapping them survived every
    functional test, so the guarantee is pinned at the source level, the
    same way the response schema's shape is pinned rather than only its
    runtime effect.
    """
    import inspect

    for function in (credentials.verify_password, cookies.unsign):
        source = inspect.getsource(function)
        assert "compare_digest" in source, \
            f"{function.__qualname__} must not compare secrets with =="


# ---------------------------------------------------------------------------
# 2. Authentication refuses identically
# ---------------------------------------------------------------------------

def test_a_correct_sign_in_returns_the_account():
    conn = StubConn(account_row(make_hash(PASSWORD)))
    found = credentials.authenticate("noura.alharbi@enjaz.gov.sa", PASSWORD,
                                     connection=conn)
    assert found.username == "noura.alharbi"
    assert found.names == ("نورة الحربي", "Noura Al-Harbi")


def test_an_unknown_address_and_a_wrong_password_give_the_same_reason():
    """Different reasons are an account directory."""
    with pytest.raises(AccessDenied) as unknown:
        credentials.authenticate("nobody@enjaz.gov.sa", PASSWORD,
                                 connection=StubConn(None))
    with pytest.raises(AccessDenied) as wrong:
        credentials.authenticate("noura.alharbi@enjaz.gov.sa", "nope",
                                 connection=StubConn(account_row(make_hash(PASSWORD))))
    assert unknown.value.reason == wrong.value.reason == "bad_credentials"
    assert str(unknown.value) == str(wrong.value)


def test_an_unknown_address_still_does_the_hashing_work():
    """Timing must not answer the question the message refuses to.

    Asserted by observing that the dummy verification actually runs, rather
    than by timing — a wall-clock assertion would be flaky on a busy CI box
    and would not say *why* it passed.
    """
    calls = []
    real = credentials.verify_password

    def spy(password, stored):
        calls.append(stored)
        return real(password, stored)

    credentials.verify_password = spy
    try:
        with pytest.raises(AccessDenied):
            credentials.authenticate("nobody@enjaz.gov.sa", PASSWORD,
                                     connection=StubConn(None))
    finally:
        credentials.verify_password = real
    assert calls, "no verification work was done for an unknown address"
    assert calls[0].startswith("pbkdf2_sha256$")


def test_an_account_with_no_password_cannot_sign_in():
    with pytest.raises(AccessDenied):
        credentials.authenticate("noura.alharbi@enjaz.gov.sa", PASSWORD,
                                 connection=StubConn(account_row(None)))


@pytest.mark.parametrize("email,password", [
    ("", PASSWORD), ("   ", PASSWORD), ("a@b.c", ""),
    ("no\x00ll@enjaz.gov.sa", PASSWORD),
])
def test_empty_or_unstorable_credentials_are_refused_before_any_query(email, password):
    conn = StubConn(account_row(make_hash(PASSWORD)))
    with pytest.raises(AccessDenied):
        credentials.authenticate(email, password, connection=conn)
    assert conn.queries == [], "a refusal should not have hit the database"


def test_the_address_is_matched_case_insensitively():
    conn = StubConn(account_row(make_hash(PASSWORD)))
    credentials.authenticate("NOURA.ALHARBI@Enjaz.GOV.SA", PASSWORD,
                             connection=conn)
    assert conn.queries[0][1] == ("noura.alharbi@enjaz.gov.sa",)


def test_a_broken_directory_is_an_outage_not_a_failed_sign_in():
    """An unreachable database must never read as "wrong password"."""
    class Broken:
        def execute(self, *_a, **_kw):
            raise RuntimeError("connection reset")

    with pytest.raises(BackendUnavailable):
        credentials.authenticate("a@b.c", PASSWORD, connection=Broken())


# ---------------------------------------------------------------------------
# 3. The session cookie is signed
# ---------------------------------------------------------------------------

def test_a_signed_cookie_round_trips():
    token = cookies.sign("noura.alharbi", SECRET, max_age_seconds=3600)
    assert cookies.unsign(token, SECRET) == "noura.alharbi"


def test_an_edited_payload_is_rejected():
    """The whole point: without the signature, the cookie is a text field
    the user controls, and editing it hands them someone else's projects."""
    token = cookies.sign("noura.alharbi", SECRET, max_age_seconds=3600)
    body, mac = token.split(".")
    forged = cookies._b64e(cookies._b64d(body).replace(
        b"noura.alharbi", b"fahad.alqahtani")) + "." + mac
    with pytest.raises(cookies.CookieError):
        cookies.unsign(forged, SECRET)


def test_a_cookie_signed_with_another_secret_is_rejected():
    token = cookies.sign("noura.alharbi", "someone-elses-secret",
                         max_age_seconds=3600)
    with pytest.raises(cookies.CookieError):
        cookies.unsign(token, SECRET)


def test_an_expired_cookie_is_rejected():
    token = cookies.sign("noura.alharbi", SECRET, max_age_seconds=-1)
    with pytest.raises(cookies.CookieError):
        cookies.unsign(token, SECRET)


def test_a_cookie_expiring_in_the_future_is_accepted():
    token = cookies.sign("noura.alharbi", SECRET, max_age_seconds=60)
    assert cookies.unsign(token, SECRET) == "noura.alharbi"


@pytest.mark.parametrize("value", [
    "", "no-dot", "a.b.c", "....", "!!!.???",
    "eyJ1IjoiZmFoYWQifQ",                      # payload with no signature
    "eyJ1IjoiZmFoYWQifQ.deadbeef",             # plausible but unsigned
])
def test_a_malformed_cookie_is_rejected(value):
    with pytest.raises(cookies.CookieError):
        cookies.unsign(value, SECRET)


def test_signing_without_a_secret_refuses_rather_than_using_a_default():
    """A hard-coded fallback would mean every deployment of this source
    shares a key, and anyone with the code could mint any session."""
    with pytest.raises(cookies.CookieError):
        cookies.sign("noura.alharbi", "", max_age_seconds=60)
    with pytest.raises(cookies.CookieError):
        cookies.unsign("anything.at-all", "")


def test_the_signature_is_checked_before_the_payload_is_parsed():
    """Parsing first means acting on attacker-controlled JSON — a type, a
    length, an expiry — before knowing whether we wrote it."""
    bomb = cookies._b64e(b'{"u": {"nested": "object"}, "exp": "not-a-number"}')
    with pytest.raises(cookies.CookieError, match="signature"):
        cookies.unsign(f"{bomb}.{cookies._b64e(b'wrong-mac')}", SECRET)


def test_an_empty_subject_is_rejected_even_when_correctly_signed():
    token = cookies.sign("", SECRET, max_age_seconds=60)
    with pytest.raises(cookies.CookieError):
        cookies.unsign(token, SECRET)


def test_the_cookie_carries_no_secret_material():
    token = cookies.sign("noura.alharbi", SECRET, max_age_seconds=60)
    assert SECRET not in token
    assert base64.b64encode(SECRET.encode()).decode().rstrip("=") not in token
