"""Where the HTTP layer gets a username — and where it must never get one.

`session.open_session()` answers *"may this user open this project?"* and
never *"is this really that user?"*. This module is the other half, and
it is the boundary `Application/README.md` warns about: **whatever supplies
the identity becomes part of the AC09 guarantee.**

**The rule that shapes everything here: a username never comes from the
request body, the query string, or a client-settable field.** If it did,
AC09 would be one `curl` away from meaningless — the gate would faithfully
check an assignment for whoever the caller claimed to be.

Three modes, none of which reads a name the client can choose.

| Mode | Identity from | Safe when |
|---|---|---|
| `session_login` *(default)* | a **signed** session cookie, minted after a password check | the signing secret is set and private |
| `dev_fixed` | `API_DEV_USERNAME` in config | always — a browser cannot change it |
| `trusted_header` | a request header | **only** behind a proxy that overwrites client copies |

`session_login` is what the demo runs: the user types their own address and
password, we verify against the seeded accounts, and the reply sets a cookie
signed with HMAC-SHA256. **The signature is the whole point** — an unsigned
cookie is a text field the user controls, and editing it would hand them
somebody else's projects through a gate that would check them faithfully.

It is still a *simulation* of sign-in. Real AD SSO and OTP replace
`credentials.py` and this branch; nothing above them changes.

`dev_fixed` remains for development and for the tests, where standing up a
login flow to assert something unrelated is noise.

⚠️ **`trusted_header` is production-*shaped*, not production-*safe*.** It
is correct only if a reverse proxy authenticates the user and **overwrites**
the header on every inbound request. Behind nothing, any client can send
`X-Remote-User: someone.else` and become them. The mode exists so the
eventual AD integration has an obvious place to land, and it refuses to
start silently — see `describe()`, which the app logs at startup.
"""
from __future__ import annotations

from dataclasses import dataclass

from Application.backend.api import cookies as cookie_module
from Application.backend.errors import AccessDenied
from pipeline.config import ApiSettings

MODE_SESSION_LOGIN = "session_login"
MODE_DEV_FIXED = "dev_fixed"
MODE_TRUSTED_HEADER = "trusted_header"
MODES = (MODE_SESSION_LOGIN, MODE_DEV_FIXED, MODE_TRUSTED_HEADER)


@dataclass(frozen=True)
class Identity:
    """Who the request is from, and how we decided that.

    `source` is carried so a log line can say *how* the identity was
    established, not just what it was. When something goes wrong with
    authentication, that is the field that explains it.
    """
    username: str
    source: str


def resolve(headers, config: ApiSettings, *, cookies=None) -> Identity:
    """Establish the caller's identity, or refuse.

    Raises `AccessDenied` rather than returning an anonymous identity,
    because "we could not tell who you are" must never continue as a
    request that the gate then evaluates against *somebody*.
    """
    if config.auth_mode == MODE_SESSION_LOGIN:
        raw = (cookies or {}).get(cookie_module.COOKIE_NAME)
        if not raw:
            raise AccessDenied("no_session")
        try:
            username = cookie_module.unsign(raw, config.session_secret)
        except cookie_module.CookieError as exc:
            # Forged, edited, expired or unsigned — all the same answer. A
            # client that can tell "expired" from "bad signature" learns
            # whether it guessed a real username.
            raise AccessDenied("invalid_session") from exc
        return Identity(username=username, source=MODE_SESSION_LOGIN)

    if config.auth_mode == MODE_DEV_FIXED:
        username = (config.dev_username or "").strip()
        if not username:
            # Fail closed. A blank dev username would otherwise reach the
            # gate as "", which denies — but for the wrong reason, and the
            # operator would debug an access problem rather than a config
            # one.
            raise AccessDenied("no_identity_configured")
        return Identity(username=username, source=MODE_DEV_FIXED)

    if config.auth_mode == MODE_TRUSTED_HEADER:
        raw = headers.get(config.auth_header) if headers else None
        username = (raw or "").strip()
        if not username:
            raise AccessDenied("no_identity_header")
        return Identity(username=username,
                        source=f"{MODE_TRUSTED_HEADER}:{config.auth_header}")

    # An unrecognised mode is a configuration error, and the safe reading of
    # a configuration error in an auth path is "nobody is authenticated".
    raise AccessDenied("unknown_auth_mode")


def describe(config: ApiSettings) -> str:
    """A startup line that makes the deferral impossible to miss."""
    if config.auth_mode == MODE_SESSION_LOGIN:
        state = "SET" if config.session_secret else "MISSING — sign-in will fail"
        return (f"AUTH: session_login — email + password against the seeded "
                f"demo accounts, then a signed cookie. Signing secret: "
                f"{state}. This SIMULATES sign-in; real AD SSO/OTP is "
                f"deferred and replaces credentials.py.")
    if config.auth_mode == MODE_DEV_FIXED:
        return (f"AUTH: dev_fixed — every request is '{config.dev_username}'. "
                f"Real AD SSO/OTP is deferred; this is a simulated identity, "
                f"not authentication.")
    if config.auth_mode == MODE_TRUSTED_HEADER:
        return (f"AUTH: trusted_header — identity read from "
                f"'{config.auth_header}'. THIS IS ONLY SAFE behind a proxy "
                f"that authenticates the user and overwrites that header on "
                f"every inbound request. Exposed directly, any client can "
                f"claim any identity.")
    return f"AUTH: unknown mode {config.auth_mode!r} — all requests refused."
