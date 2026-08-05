#!/usr/bin/env python3
"""Apply pending SPM schema migrations, in order, exactly once each.

Usage (from anywhere):
    python infra/postgres-spm/apply_migrations.py            # apply pending
    python infra/postgres-spm/apply_migrations.py --status   # preview, change nothing
    python infra/postgres-spm/apply_migrations.py --allow-drift   # see below

Applies every `NNN_*.sql` in `migrations/` not already recorded in the
`schema_migrations` table, in filename order.

Design notes:
  * Each migration runs in a SINGLE TRANSACTION, so a file that fails
    partway leaves the database exactly as it was — never half-applied.
  * A migration is recorded only AFTER it succeeds, so a crash can't leave
    a false "applied" marker.
  * Each file's SHA-256 is stored. If an already-applied file is later
    edited, that drift is detected and the run stops: the database no
    longer matches the code that supposedly produced it. Override with
    --allow-drift once you've confirmed the edit was cosmetic.
  * SQL is piped to psql as raw BYTES. Several migrations contain Arabic,
    and this machine mangles UTF-8 through inline shell arguments (see
    Documentation/STATUS_AND_ROADMAP.md) — bytes sidestep the shell
    codepage entirely.
"""
import argparse
import hashlib
import pathlib
import subprocess
import sys
import time

CONTAINER = "mof-postgres-spm"
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
MIGRATIONS_DIR = SCRIPT_DIR / "migrations"
ENV_PATH = SCRIPT_DIR.parent / ".env"

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename     TEXT PRIMARY KEY,
  checksum     TEXT NOT NULL,
  applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  duration_ms  INTEGER
);
"""


def die(message):
    print(f"\nERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_env():
    """Read DB credentials from infra/.env — never hardcoded in this script."""
    if not ENV_PATH.exists():
        die(f"{ENV_PATH} not found. Copy .env.example to .env and fill it in.")
    values = {}
    with open(ENV_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    missing = [k for k in ("POSTGRES_SPM_USER", "POSTGRES_SPM_DB") if k not in values]
    if missing:
        die(f"{ENV_PATH} is missing: {', '.join(missing)}")
    return values["POSTGRES_SPM_USER"], values["POSTGRES_SPM_DB"]


def require_container():
    """Fail with a useful message rather than a cryptic docker error."""
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{CONTAINER}$", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        die("could not reach Docker. Is Docker Desktop running?")
    if CONTAINER not in result.stdout:
        die(f"container '{CONTAINER}' is not running. Start it with:\n"
            f'  cd "infra" && docker compose up -d postgres-spm')


def psql(user, database, sql=None, sql_bytes=None, tuples_only=False, single_transaction=False):
    """Run SQL in the container. Returns (returncode, stdout, stderr).

    tuples_only    -> -t -A: bare values, no header/separator/"(N rows)"
                      footer. Required whenever output is parsed.
    single_transaction -> wrap the whole file in BEGIN/COMMIT so a failure
                      rolls the entire migration back. (Safe for every
                      migration here; would need care only for statements
                      that cannot run inside a transaction, e.g.
                      CREATE INDEX CONCURRENTLY.)
    """
    cmd = ["docker", "exec", "-i", CONTAINER, "psql", "-U", user, "-d", database,
           "-v", "ON_ERROR_STOP=1"]
    if tuples_only:
        cmd += ["-t", "-A"]
    if single_transaction:
        cmd += ["--single-transaction"]
    if sql is not None:
        sql_bytes = sql.encode("utf-8")
    if sql_bytes is None:
        raise ValueError("need sql or sql_bytes")
    result = subprocess.run(cmd, input=sql_bytes, capture_output=True)
    return (
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def checksum(path):
    """SHA-256 of the file's CONTENT, with line endings normalised to LF.

    Hashing raw bytes looks correct but isn't: git's core.autocrlf rewrites
    line endings on checkout, so the same logical file has different bytes
    on Windows than on Linux. That would raise false drift alarms on every
    fresh clone. Normalising first makes the checksum depend on content
    only. (.gitattributes also pins *.sql to LF, so the two defences agree.)
    """
    raw = path.read_bytes()
    normalised = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalised).hexdigest()


def get_applied(user, database):
    """Map of filename -> recorded checksum."""
    code, out, err = psql(
        user, database,
        sql="SELECT filename || '\t' || checksum FROM schema_migrations;",
        tuples_only=True,
    )
    if code != 0:
        die(f"could not read schema_migrations:\n{err}")
    applied = {}
    for line in out.splitlines():
        line = line.strip()
        if line and "\t" in line:
            name, digest = line.split("\t", 1)
            applied[name] = digest
    return applied


def main():
    parser = argparse.ArgumentParser(description="Apply pending SPM schema migrations.")
    parser.add_argument("--status", action="store_true",
                        help="show applied/pending/drift and exit without changing anything")
    parser.add_argument("--allow-drift", action="store_true",
                        help="proceed even if an already-applied migration file was edited")
    args = parser.parse_args()

    user, database = load_env()
    require_container()

    if not MIGRATIONS_DIR.is_dir():
        die(f"{MIGRATIONS_DIR} not found")

    code, _, err = psql(user, database, sql=TRACKING_TABLE, single_transaction=True)
    if code != 0:
        die(f"could not create schema_migrations table:\n{err}")

    applied = get_applied(user, database)
    files = sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)
    if not files:
        die(f"no .sql files found in {MIGRATIONS_DIR}")

    # Detect files that changed after they were applied.
    drift = [f for f in files if f.name in applied and checksum(f) != applied[f.name]]
    pending = [f for f in files if f.name not in applied]

    if args.status:
        for f in files:
            if f.name in applied:
                state = "DRIFT" if f in drift else "applied"
            else:
                state = "PENDING"
            print(f"  {state:>7}  {f.name}")
        print(f"\n{len(applied)} applied, {len(pending)} pending, {len(drift)} drifted")
        return

    if drift:
        names = "\n".join(f"    {f.name}" for f in drift)
        if not args.allow_drift:
            die("these migrations were edited after being applied, so the database\n"
                f"no longer matches the files that produced it:\n{names}\n\n"
                "Review the edits. If they were cosmetic (comments/whitespace), re-run\n"
                "with --allow-drift to refresh the recorded checksums.")
        for f in drift:
            code, _, err = psql(
                user, database,
                sql=f"UPDATE schema_migrations SET checksum = '{checksum(f)}' "
                    f"WHERE filename = '{f.name}';",
            )
            if code != 0:
                die(f"could not refresh checksum for {f.name}:\n{err}")
            print(f"  refreshed checksum  {f.name}")

    if not pending:
        print(f"nothing to do — all {len(files)} migrations already applied")
        return

    for f in pending:
        started = time.monotonic()
        code, _, err = psql(user, database, sql_bytes=f.read_bytes(), single_transaction=True)
        if code != 0:
            die(f"FAILED on {f.name} (rolled back, database unchanged):\n{err}")
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # Recorded only after the migration itself succeeded.
        code, _, err = psql(
            user, database,
            sql="INSERT INTO schema_migrations (filename, checksum, duration_ms) "
                f"VALUES ('{f.name}', '{checksum(f)}', {elapsed_ms}) "
                "ON CONFLICT (filename) DO NOTHING;",
        )
        if code != 0:
            die(f"applied {f.name} but could not record it:\n{err}")
        print(f"  applied  {f.name}  ({elapsed_ms} ms)")

    print(f"\ndone — {len(pending)} migration(s) applied")


if __name__ == "__main__":
    main()
