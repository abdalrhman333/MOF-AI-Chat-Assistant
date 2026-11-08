"""The README is the repository's landing page, so it is read by people
who have not cloned it and cannot check anything it claims.

Two things actually go wrong with a file like this, and neither announces
itself: a link rots when a document is renamed, and a value gets pasted in
that should not leave the machine. Both are cheap to pin and expensive to
notice late — a dead link on a public landing page is embarrassing, and a
pasted secret is published the moment it is pushed.

What is deliberately NOT pinned here is prose. A test that asserted the
README's wording would fail on every honest edit and teach people to
delete it.

Nothing here needs Docker running.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TEXT = README.read_text(encoding="utf-8")

# [label](target) — skip http(s) and anchors, keep repository-relative paths
LINK = re.compile(r"\[[^\]]+\]\((?!https?://|#)([^)]+)\)")


def _targets() -> list[str]:
    return [m.group(1).split("#", 1)[0] for m in LINK.finditer(TEXT)]


def test_the_readme_exists_and_is_not_a_stub():
    assert len(TEXT) > 1000


def test_every_linked_file_exists():
    """Link rot: a document is renamed and the landing page keeps pointing
    at the old name."""
    missing = [t for t in _targets() if t and not (ROOT / t).exists()]
    assert not missing


# The documents a reader cannot navigate this project without. "at least
# five links exist" was the first version of this test and proved worthless
# — removing any single row still left five.
CORE_DOCS = (
    "Documentation/START_HERE.md",
    "Documentation/SOLUTION_ARCHITECTURE.md",
    "Documentation/INFRASTRUCTURE.md",
    "Documentation/STATUS_AND_ROADMAP.md",
    "Documentation/MIGRATION.md",
    "Documentation/INVENTORY.md",
)


def test_it_links_every_core_document():
    """START_HERE above all — everything else in the project assumes it was
    read first."""
    targets = set(_targets())
    assert not [doc for doc in CORE_DOCS if doc not in targets]


def test_every_command_it_tells_you_to_run_exists():
    """A landing page that opens with a command that is not there is worse
    than one with no commands."""
    for referenced in ("scripts\\bootstrap.ps1", "infra", "tests/"):
        assert referenced.replace("\\", "/") in TEXT.replace("\\", "/")
    assert (ROOT / "scripts" / "bootstrap.ps1").exists()
    assert (ROOT / "infra" / "docker-compose.yml").exists()


def test_the_readme_holds_no_secret():
    """It is published. Every credential-shaped value in the real .env is
    checked against it; the file itself is gitignored, so this skips on a
    fresh clone rather than failing."""
    env_path = ROOT / "infra" / ".env"
    if not env_path.exists():
        pytest.skip("infra/.env not present")
    leaked = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if len(value) >= 8 and name.endswith(("_PASSWORD", "_KEY", "_SECRET", "_SALT")):
            if value in TEXT:
                leaked.append(name)
    assert not leaked


def test_it_warns_that_the_corpus_holds_real_documents():
    """The one warning on this page that protects something outside the
    repository: 13 of the 58 documents are real client and ministry files,
    and a reader who misses that may commit them.

    Matched against whitespace-normalised text, because the sentence wraps,
    and on the specific claim rather than the stray words "real" and "13",
    which the first version of this test accepted anywhere on the page.
    """
    flat = " ".join(TEXT.split())
    assert re.search(r"13 of them are \*\*real\*\* client and ministry documents", flat)
    assert "must not be committed" in flat
