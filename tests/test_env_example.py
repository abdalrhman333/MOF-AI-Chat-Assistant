"""`infra/.env.example` is the only setup instructions a new machine gets.

`infra/.env` is gitignored, and deliberately so — it holds every password,
every Azure key and the cookie-signing secret. That makes the *example* the
whole of what a clone carries: someone bringing this up on another device
reads that file and nothing else. A setting added to `config.py` without a
line there is a setting the next person cannot know exists, and nothing
fails loudly — the default quietly applies, which is exactly the kind of
drift that is only noticed on the machine where it matters.

So these tests read the real files and pin the contract:

* every variable the code reads is documented,
* every variable Compose interpolates is documented,
* the one variable with no default is presented as required,
* and no real secret has been pasted into the tracked file.

⚠️ A commented line (`# API_PORT=8000`) counts as documented. That is the
intended form for anything optional: it shows the name *and* the default
without overriding it. Only a setting that must be supplied is left
uncommented with an empty value.

Nothing here needs Docker running.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE = (ROOT / "infra" / ".env.example").read_text(encoding="utf-8")
CONFIG = (ROOT / "pipeline" / "config.py").read_text(encoding="utf-8")
COMPOSE_TEXT = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")

# `_optional("NAME", default)`, `_flag("NAME", default)`, `_required("NAME")`
_READ_BY_CODE = re.compile(r"_(?:optional|required|flag)\(\s*\"([A-Z][A-Z0-9_]*)\"")
# `${NAME}` / `${NAME:-default}` in the Compose file
_READ_BY_COMPOSE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
# A documented line, whether live or commented out as a default
_DOCUMENTED = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)
# A documented line that is live — no leading `#`
_LIVE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$", re.MULTILINE)


def _names(pattern: re.Pattern[str], text: str) -> set[str]:
    return {m.group(1) for m in pattern.finditer(text)}


DOCUMENTED = _names(_DOCUMENTED, EXAMPLE)
LIVE = {m.group(1): m.group(2).strip() for m in _LIVE.finditer(EXAMPLE)}


# ---------------------------------------------------------------------------
# Nothing the system reads is undocumented
# ---------------------------------------------------------------------------

def test_every_setting_the_code_reads_is_documented():
    """A new setting without an example line is invisible to the next machine."""
    read = _names(_READ_BY_CODE, CONFIG)
    assert read, "the regex stopped matching config.py — fix the regex, not this"
    assert not (read - DOCUMENTED)


def test_every_setting_compose_interpolates_is_documented():
    """An undocumented `${VAR}` makes Compose substitute an empty string —
    a container that starts and is wrong, rather than one that refuses."""
    used = _names(_READ_BY_COMPOSE, COMPOSE_TEXT)
    assert used, "the regex stopped matching docker-compose.yml"
    assert not (used - DOCUMENTED)


# ---------------------------------------------------------------------------
# What must be supplied is presented as such
# ---------------------------------------------------------------------------

def test_the_session_secret_is_live_and_empty():
    """`API_SESSION_SECRET` has no default anywhere on purpose: a hard-coded
    fallback would let anyone with the source mint a session for any
    username. Commenting it out here would hide the one line a new machine
    must fill in, and sign-in would refuse with nothing pointing at why."""
    assert LIVE.get("API_SESSION_SECRET") == ""


def test_the_paths_are_live_so_a_new_machine_sees_them():
    """These three are absolute and machine-specific — a device without a
    D: drive must edit them. Shown as live values, not commented defaults,
    because they are the likeliest thing to be wrong after a move."""
    for name in ("DATA_ROOT", "DOCUMENT_ROOT", "DOCUMENT_ROOT_HOST"):
        assert LIVE.get(name), name


# ---------------------------------------------------------------------------
# No real secret is in the tracked file
# ---------------------------------------------------------------------------

SECRET_SUFFIXES = ("_PASSWORD", "_KEY", "_SECRET", "_SALT")


def test_no_secret_carries_a_value():
    """This file is tracked in git. A password pasted here is published, and
    rewriting history is the only way back. Every secret stays empty — the
    real values live in `infra/.env`, which is gitignored."""
    leaked = {
        name: value
        for name, value in LIVE.items()
        if name.endswith(SECRET_SUFFIXES) and value
    }
    assert not leaked


def test_the_example_says_it_must_not_be_filled_in_and_committed():
    """The warning is the reason the file above stays empty."""
    assert "never commit" in EXAMPLE.lower()
