"""The signed cookie that carries a signed-in identity between requests.

**A cookie is a claim the browser hands back to us, so it must be a claim we
made.** Without a signature, `session=someone.else` in devtools is a
complete impersonation of another user, and the AC09 gate would then
faithfully check *their* assignments. Signing is what makes the identity in
the cookie the same identity we put there.

`itsdangerous`-style, but hand-rolled against `hmac` and `hashlib` so this
adds no dependency:

    <base64url payload>.<base64url HMAC-SHA256 of the payload>

The payload carries the username and an expiry, so a stolen cookie stops
working, and both are inside the signature so neither can be edited.

⚠️ **The secret comes from configuration and has no default.** A hard-coded
fallback would mean every deployment of this code shares a signing key, and
anyone with the source could mint a valid session for any username. When it
is missing, sessions are refused rather than signed with something
convenient.

This is still *demo* sign-in. It does not replace AD SSO — it makes the
simulated identity survive a page reload without being forgeable from the
browser.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

COOKIE_NAME = "enjaz_session"


class CookieError(Exception):
    """The cookie was absent, malformed, tampered with, or expired."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(username: str, secret: str, *, max_age_seconds: int) -> str:
    """Mint a session cookie value for an authenticated username."""
    if not secret:
        raise CookieError("no session secret is configured")
    payload = json.dumps(
        {"u": username, "exp": int(time.time()) + max_age_seconds},
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    body = _b64e(payload)
    mac = hmac.new(secret.encode("utf-8"), body.encode("utf-8"),
                   hashlib.sha256).digest()
    return f"{body}.{_b64e(mac)}"


def unsign(value: str, secret: str) -> str:
    """Return the username, or raise. Never returns a partial result.

    The signature is checked **before** the payload is parsed. Parsing first
    would mean acting on attacker-controlled JSON — deciding an expiry, a
    length, a type — before knowing whether we wrote it.
    """
    if not secret:
        raise CookieError("no session secret is configured")
    if not value or value.count(".") != 1:
        raise CookieError("malformed session cookie")

    body, provided = value.split(".", 1)
    expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"),
                        hashlib.sha256).digest()
    try:
        given = _b64d(provided)
    except Exception as exc:                                  # noqa: BLE001
        raise CookieError("malformed session signature") from exc
    if not hmac.compare_digest(expected, given):
        raise CookieError("session signature does not match")

    try:
        payload = json.loads(_b64d(body))
        username = str(payload["u"])
        expires_at = int(payload["exp"])
    except Exception as exc:                                  # noqa: BLE001
        raise CookieError("unreadable session payload") from exc

    if expires_at < time.time():
        raise CookieError("session expired")
    if not username:
        raise CookieError("empty session subject")
    return username
