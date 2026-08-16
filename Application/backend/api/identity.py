"""Where the HTTP layer gets a username — and where it must never get one.

`session.open_session()` answers *"may this user open this project?"* and
never *"is this really that user?"*. This module is the other half, and
it is the boundary `Application/README.md` warns about: **whatever supplies
the identity becomes part of the AC09 guarantee.**

**The rule that shapes everything here: a username never comes from the
request body, the query string, or a client-settable field.** If it did,
AC09 would be one `curl` away from meaningless — the gate would faithfully
check an assignment for whoever the caller claimed to be.

Two modes, and the default is the one that cannot be spoofed.

| Mode | Identity from | Safe when |
|---|---|---|
| `dev_fixed` *(default)* | `AUTH_DEV_USERNAME` in config | always — a browser cannot change it |
| `trusted_header` | a request header | **only** behind a proxy that strips client copies |

`dev_fixed` is the honest default for this build. Real AD SSO and OTP are
external dependencies that do not exist here, and the BRD's own note says
to simulate what can be simulated honestly and flag the rest. A fixed
identity does exactly that: every request is the same signed-in user, the
project switcher still works because that user has several projects, and
nothing pretends to be authentication.

⚠️ **`trusted_header` is production-*shaped*, not production-*safe*.** It
is correct only if a reverse proxy authenticates the user and **overwrites**
the header on every inbound request. Behind nothing, any client can send
`X-Remote-User: someone.else` and become them. The mode exists so the
eventual AD integration has an obvious place to land, and it refuses to
start silently — see `describe()`, which the app logs at startup.
"""
from __future__ import annotations

from dataclasses import dataclass

from Application.backend.errors import AccessDenied
from pipeline.config import ApiSettings

MODE_DEV_FIXED = "dev_fixed"
MODE_TRUSTED_HEADER = "trusted_header"
MODES = (MODE_DEV_FIXED, MODE_TRUSTED_HEADER)


@dataclass(frozen=True)
class Identity:
    """Who the request is from, and how we decided that.

    `source` is carried so a log line can say *how* the identity was
    established, not just what it was. When something goes wrong with
    authentication, that is the field that explains it.
    """
    username: str
    source: str


def resolve(headers, config: ApiSettings) -> Identity:
    """Establish the caller's identity, or refuse.

    Raises `AccessDenied` rather than returning an anonymous identity,
    because "we could not tell who you are" must never continue as a
    request that the gate then evaluates against *somebody*.
    """
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
