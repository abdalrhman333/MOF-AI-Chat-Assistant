"""Migrations may reference a secret; they may never contain one.

Migration 013 wrote the `spm_app` password literally into a tracked file,
so the live credential of the role the chat application authenticates with
sat in the repository — and in every clone — from the commit that added it.
It was found by auditing the tree before the first push to a remote, not by
anything in the test suite, which is why these tests exist.

`apply_migrations.py` now substitutes `${VAR}` from `infra/.env` as a quoted
SQL literal immediately before the bytes reach psql. Two things have to hold
for that to be worth anything:

* the substitution must actually be wired into the apply path — a
  `substitute()` that is defined, tested and never called is precisely the
  failure mode recorded in §25c, where four of fifteen surviving mutations
  were all wiring; and
* a missing or empty variable must be a hard failure. Expanding
  `PASSWORD ${MISSING}` to `PASSWORD ''` would set a blank password on the
  least-privileged role and report success.

Nothing here needs Docker or a database: `psql` is replaced with a recorder.
"""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "infra" / "postgres-spm" / "apply_migrations.py"
MIGRATIONS = ROOT / "infra" / "postgres-spm" / "migrations"


def _module():
    """A fresh import each time — the tests rebind module-level paths."""
    spec = importlib.util.spec_from_file_location("apply_migrations", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Quoting
# ---------------------------------------------------------------------------

def test_a_value_becomes_a_quoted_sql_literal():
    assert _module().quote_literal("abc123") == "'abc123'"


def test_a_single_quote_is_doubled():
    """Otherwise a password containing a quote ends the literal early and
    the rest of it is executed as SQL."""
    assert _module().quote_literal("a'b") == "'a''b'"


def test_a_backslash_is_left_alone():
    """standard_conforming_strings is on by default, so a backslash in a
    literal is a backslash. Escaping it would change the password."""
    assert _module().quote_literal("a\\b") == "'a\\b'"


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------

def test_a_placeholder_is_replaced_with_the_quoted_value():
    mod = _module()
    out = mod.substitute(b"ALTER ROLE r PASSWORD ${PW};", {"PW": "s3cret"}, "x.sql")
    assert out == b"ALTER ROLE r PASSWORD 's3cret';"


def test_sql_without_a_placeholder_is_untouched():
    mod = _module()
    sql = "SELECT 'مرحبا';".encode("utf-8")
    assert mod.substitute(sql, {}, "x.sql") == sql


def test_a_missing_variable_is_fatal():
    mod = _module()
    with pytest.raises(SystemExit):
        mod.substitute(b"PASSWORD ${NOPE};", {}, "x.sql")


def test_an_empty_variable_is_fatal_too():
    """⚠️ The dangerous case. An empty expansion would set a blank password
    and report success."""
    mod = _module()
    with pytest.raises(SystemExit):
        mod.substitute(b"PASSWORD ${PW};", {"PW": ""}, "x.sql")


# ---------------------------------------------------------------------------
# The wiring: it is actually used when a migration is applied
# ---------------------------------------------------------------------------

def test_the_apply_path_substitutes_before_sending(tmp_path, monkeypatch, capsys):
    """Runs the real `main()` with psql recorded instead of executed."""
    mod = _module()

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "900_example.sql").write_text(
        "ALTER ROLE spm_app PASSWORD ${POSTGRES_SPM_APP_PASSWORD};\n",
        encoding="utf-8")

    env = tmp_path / ".env"
    env.write_text("POSTGRES_SPM_USER=spm_admin\n"
                   "POSTGRES_SPM_DB=spm_data\n"
                   "POSTGRES_SPM_APP_PASSWORD=rotated-value\n", encoding="utf-8")

    sent: list[bytes] = []

    def fake_psql(user, database, sql=None, sql_bytes=None, **kwargs):
        if sql_bytes is not None:
            sent.append(sql_bytes)
        return (0, "", "")

    monkeypatch.setattr(mod, "MIGRATIONS_DIR", migrations)
    monkeypatch.setattr(mod, "ENV_PATH", env)
    monkeypatch.setattr(mod, "psql", fake_psql)
    monkeypatch.setattr(mod, "require_container", lambda: None)
    monkeypatch.setattr(mod, "get_applied", lambda user, db: {})
    monkeypatch.setattr("sys.argv", ["apply_migrations.py"])

    mod.main()
    capsys.readouterr()

    body = b"".join(sent)
    assert b"'rotated-value'" in body
    assert b"${POSTGRES_SPM_APP_PASSWORD}" not in body


def test_the_checksum_is_taken_from_the_file_not_the_substitution():
    """Otherwise rotating a secret would register as drift on every machine
    and the recorded hash would differ between them."""
    mod = _module()
    path = MIGRATIONS / "021_rotate_spm_app_password.sql"
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert mod.checksum(path) == hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# No migration carries a live credential
# ---------------------------------------------------------------------------

# 013 is the one that caused this. It is deliberately left untouched —
# editing an applied migration registers as drift, and 021 rotates the value
# it contains, so what remains in it is dead text.
RETIRED = {"013_enable_row_level_security.sql"}


def test_no_migration_sets_a_literal_password():
    offenders = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in RETIRED:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lstrip().startswith("--"):
                continue
            upper = line.upper()
            if "PASSWORD" in upper and "'" in line and "${" not in line:
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders


def test_the_retired_migrations_value_is_no_longer_live():
    """The exposed password must not be what `.env` currently holds. This is
    the test that proves the rotation actually happened, and it keeps
    proving it — re-introducing the old value would fail here.

    Skips where `.env` is absent, as on a fresh clone.
    """
    env_path = ROOT / "infra" / ".env"
    if not env_path.exists():
        pytest.skip("infra/.env not present")
    exposed = "5e5d79d8149db9624345f7cc"
    assert exposed in (MIGRATIONS / "013_enable_row_level_security.sql").read_text(
        encoding="utf-8"), "test is stale: 013 no longer contains the exposed value"
    assert exposed not in env_path.read_text(encoding="utf-8")
