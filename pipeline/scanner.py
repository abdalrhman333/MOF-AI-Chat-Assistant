"""Stage 0 — discovery. Walk the document root, hash, upsert document_links.

This is the stage that makes the solution **dynamic**. Every other stage
consumes what this one finds, so if discovery is coupled to the demo
corpus, the whole pipeline is. Two rules follow from that and are not
negotiable:

  * **The manifest is never read.** `mock-data/spm_manifest.json` produced
    the seeded rows, and Stage 0's job is precisely to replace those
    seeded placeholders with real discovery. Dropping a new file into a
    project folder must make it appear with no code change — which is only
    true if the filesystem, not a manifest, is the source of truth.
  * **Everything comes from `get_settings()`.** The root, the ignore
    patterns, the default doc type. Repointing at a real SharePoint export
    is a config change.

What Stage 0 decides, per file:

    no row                  -> INSERT, status 'pending'
    row, file_hash IS NULL  -> BACKFILL (the seeded-placeholder case)
    hash matches, path same -> UNCHANGED, zero writes
    hash matches, path moved-> PATH_UPDATED (moved within the corpus, or a
                               row still holding a pre-fix absolute path)
    hash differs            -> MODIFIED: reset to 'pending' AND drop the
                               stale OCR cache

`file_path` is stored **relative to DOCUMENT_ROOT** (`PRJ-01/x.docx`). It
was absolute until containerising proved that wrong: the Windows host and
the Linux worker mount the same corpus at different roots, so every run
rewrote every row and Stage 1 could not open a file the other environment
had recorded. Relative is valid in both, and makes PATH_UPDATED mean
"moved" instead of "a different machine ran last".

That last deletion is the one with teeth. `document_content`'s *presence*
is the "OCR done" marker (INFRASTRUCTURE.md 8), so leaving a stale row
behind on a changed file makes Stage 1 skip re-OCR and change detection
silently does nothing at all. It is deleted only on MODIFIED — where a
differing hash is proof the cached OCR describes different bytes. A
BACKFILL keeps its cache: a NULL hash means "never scanned", not "content
changed", and OCR is billed per page.

**A row whose file is gone from disk is reported, never touched.**
`DOCUMENT_ROOT` is a setting, and a setting pointed one folder too high
makes every project look empty. Deleting rows on that signal would cascade
away the paid-for OCR cache — the one thing a re-run cannot rebuild for
free. Cleanup is a deliberate separate step, once real SharePoint shows us
what actually goes missing.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import pathlib
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

from pipeline.config import Settings, get_settings
from pipeline.db import admin_connection
from pipeline.locking import AlreadyRunning, optional_stage_lock

# Read in blocks rather than slurping the file: a SharePoint export can
# contain video and archives, and the scanner must not be the thing that
# decides how much RAM the pipeline needs.
_HASH_BLOCK_BYTES = 1024 * 1024


class Action(str, Enum):
    INSERT = "insert"
    BACKFILL = "backfill"
    UNCHANGED = "unchanged"
    PATH_UPDATED = "path_updated"
    MODIFIED = "modified"


@dataclass(frozen=True)
class DiscoveredFile:
    """A file found on disk, before the database has been consulted."""
    project_code: str
    filename: str          # path RELATIVE to the project folder, POSIX form
    path: pathlib.Path     # absolute, for reading the bytes right now
    size_bytes: int

    @property
    def label(self) -> str:
        return f"{self.project_code}/{self.filename}"

    @property
    def stored_path(self) -> str:
        """What goes into `document_links.file_path`.

        **Relative to DOCUMENT_ROOT, POSIX-separated** — `PRJ-01/x.docx`,
        never `D:\\MOF_DataSample\\PRJ-01\\x.docx`. An absolute path is
        only valid on the machine that wrote it: the Windows host and the
        Linux worker container mount the same corpus at different roots, so
        storing absolutes made every run rewrite every row and left Stage 1
        unable to open a file the other environment had recorded.
        POSIX separators keep the value identical on both.
        """
        return f"{self.project_code}/{self.filename}"


@dataclass(frozen=True)
class ExistingRow:
    """The subset of a document_links row that Stage 0 reasons about."""
    id: int
    project_code: str
    filename: str
    file_path: str
    file_hash: str | None
    file_size_bytes: int | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.project_code, self.filename)


@dataclass
class ScanReport:
    root: pathlib.Path
    dry_run: bool
    projects_scanned: list[str] = field(default_factory=list)
    actions: dict[Action, list[str]] = field(default_factory=dict)
    unknown_projects: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    missing_on_disk: list[str] = field(default_factory=list)
    ocr_cache_cleared: int = 0

    def of(self, action: Action) -> list[str]:
        return self.actions.setdefault(action, [])

    @property
    def files_seen(self) -> int:
        return sum(len(v) for v in self.actions.values())

    @property
    def writes(self) -> int:
        """Rows this scan would change. UNCHANGED is free, by design."""
        return sum(
            len(self.of(a))
            for a in (Action.INSERT, Action.BACKFILL, Action.PATH_UPDATED, Action.MODIFIED)
        )


# ---------------------------------------------------------------------------
# Pure logic — no filesystem, no database, so it can be tested exhaustively
# ---------------------------------------------------------------------------

def is_ignored(name: str, patterns: tuple[str, ...]) -> bool:
    """Match a single path component against the ignore globs.

    `fnmatch` (not `fnmatchcase`) is deliberate: it folds case on Windows,
    so `thumbs.db` and `Thumbs.db` are both caught.
    """
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def decide(
    disc: DiscoveredFile,
    file_hash: str,
    row: ExistingRow | None,
) -> Action:
    """Classify one discovered file against its database row, if any.

    Note what is NOT here: `file_size_bytes` is stored because the schema
    asks for it and it is a useful diagnostic, but it is not used to skip
    hashing. A differing size does prove the file changed — yet the new
    hash still has to be computed in order to be stored, so the "cheap
    pre-check" would save no I/O whatsoever. Claiming otherwise would be
    a comment that reads well and does nothing.
    """
    if row is None:
        return Action.INSERT
    if row.file_hash is None:
        # Never scanned. The seeded placeholder case — every one of the 58
        # rows at the time this was written.
        return Action.BACKFILL
    if row.file_hash != file_hash:
        return Action.MODIFIED
    if row.file_path != disc.stored_path:
        # Same bytes, a different place in the corpus — or a row still
        # holding a pre-fix absolute path. Either way the stored value is
        # stale and Stage 1 would resolve it wrongly.
        return Action.PATH_UPDATED
    return Action.UNCHANGED


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------

def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_HASH_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def discover(
    root: pathlib.Path,
    ignore_patterns: tuple[str, ...],
    project_filter: str | None = None,
    ignored_sink: list[str] | None = None,
) -> Iterator[DiscoveredFile]:
    """Yield every candidate document under `root`, recursively.

    Recursive by design. The demo corpus is flat, so `filename` comes out
    as the bare name and matches all 58 seeded rows exactly — but a real
    SharePoint export has folders, and a non-recursive walk would make
    those documents silently invisible: no error, just missing answers.
    Storing the project-relative path also keeps the
    UNIQUE (project_code, filename) key honest when two subfolders hold
    the same filename.
    """
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if project_filter is not None and project_dir.name != project_filter:
            continue
        if is_ignored(project_dir.name, ignore_patterns):
            continue
        for path in sorted(project_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(project_dir)
            # Check every component, so a file inside an ignored folder is
            # ignored too — not just files whose own name matches.
            if any(is_ignored(part, ignore_patterns) for part in relative.parts):
                if ignored_sink is not None:
                    ignored_sink.append(f"{project_dir.name}/{relative.as_posix()}")
                continue
            yield DiscoveredFile(
                project_code=project_dir.name,
                filename=relative.as_posix(),
                path=path,
                size_bytes=path.stat().st_size,
            )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _known_project_codes(cursor) -> set[str]:
    cursor.execute("SELECT code FROM projects")
    return {row[0] for row in cursor.fetchall()}


def _existing_rows(cursor, project_codes: list[str]) -> dict[tuple[str, str], ExistingRow]:
    if not project_codes:
        return {}
    cursor.execute(
        """
        SELECT id, project_code, filename, file_path, file_hash, file_size_bytes
        FROM document_links
        WHERE project_code = ANY(%s)
        """,
        (project_codes,),
    )
    rows = [ExistingRow(*record) for record in cursor.fetchall()]
    return {row.key: row for row in rows}


_RESET_COLUMNS = """
        file_path       = %s,
        file_hash       = %s,
        file_size_bytes = %s,
        indexing_status = 'pending',
        error_message   = NULL,
        indexed_at      = NULL,
        chunk_count     = NULL
"""


def _apply(cursor, action: Action, disc: DiscoveredFile, file_hash: str,
           row: ExistingRow | None, settings: Settings) -> int:
    """Write one decision. Returns how many OCR cache rows were dropped."""
    if action is Action.INSERT:
        cursor.execute(
            """
            INSERT INTO document_links
                (project_code, filename, file_path, doc_type,
                 file_hash, file_size_bytes, indexing_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            """,
            (disc.project_code, disc.filename, disc.stored_path,
             settings.scanner.default_doc_type, file_hash, disc.size_bytes),
        )
        return 0

    assert row is not None, "non-INSERT actions require an existing row"

    if action is Action.PATH_UPDATED:
        # Content is provably identical, so nothing downstream is stale —
        # only the location changed. Deliberately does NOT reset
        # indexing_status: re-OCRing identical bytes is paying twice.
        cursor.execute(
            "UPDATE document_links SET file_path = %s, file_size_bytes = %s WHERE id = %s",
            (disc.stored_path, disc.size_bytes, row.id),
        )
        return 0

    if action in (Action.BACKFILL, Action.MODIFIED):
        cursor.execute(
            f"UPDATE document_links SET {_RESET_COLUMNS} WHERE id = %s",
            (disc.stored_path, file_hash, disc.size_bytes, row.id),
        )
        if action is Action.MODIFIED:
            # The hash differs, so the cached OCR describes bytes that are
            # no longer on disk. Leaving it would make Stage 1 skip re-OCR.
            cursor.execute(
                "DELETE FROM document_content WHERE document_link_id = %s", (row.id,)
            )
            return cursor.rowcount
        return 0

    return 0   # UNCHANGED — no write at all


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def scan(
    project: str | None = None,
    dry_run: bool = False,
    settings: Settings | None = None,
    connection=None,
) -> ScanReport:
    """Run Stage 0.

    `connection` is injectable so tests can hand in a transaction they
    intend to roll back — the integration tests exercise the real schema,
    real constraints and real SQL without leaving anything behind.
    """
    settings = settings or get_settings()
    root = settings.document_root
    if not root.is_dir():
        raise RuntimeError(
            f"DOCUMENT_ROOT does not exist or is not a directory: {root}. "
            f"Set DOCUMENT_ROOT in infra/.env."
        )

    report = ScanReport(root=root, dry_run=dry_run)

    if connection is not None:
        return _scan_with(connection, settings, project, dry_run, report)
    with admin_connection() as conn:
        result = _scan_with(conn, settings, project, dry_run, report)
        if dry_run:
            conn.rollback()
        return result


def _scan_with(conn, settings: Settings, project: str | None,
               dry_run: bool, report: ScanReport) -> ScanReport:
    root = report.root
    cursor = conn.cursor()
    known = _known_project_codes(cursor)

    discovered = list(discover(
        root,
        settings.scanner.ignore_patterns,
        project_filter=project,
        ignored_sink=report.ignored,
    ))

    if project is not None and not discovered:
        raise RuntimeError(
            f"No files found for project {project!r} under {root}. "
            f"Folders present: {sorted(p.name for p in root.iterdir() if p.is_dir())}"
        )

    if not discovered:
        # An empty corpus is legitimate on a fresh install, and a hard error
        # would block the very first run. It is NOT legitimate when the
        # database already knows about documents: that combination means
        # DOCUMENT_ROOT is pointing somewhere wrong, and continuing would
        # report every existing document as missing. Refusing protects the
        # rows — and the paid-for OCR cache hanging off them.
        cursor.execute("SELECT count(*) FROM document_links")
        known_documents = cursor.fetchone()[0]
        if known_documents:
            raise RuntimeError(
                f"No files found anywhere under DOCUMENT_ROOT ({root}), but the "
                f"database already holds {known_documents} document(s). This almost "
                f"always means DOCUMENT_ROOT is set to the wrong directory. "
                f"Refusing to continue, so that every existing document is not "
                f"reported as missing. Check DOCUMENT_ROOT in infra/.env."
            )

    # A folder with no projects row would violate the foreign key. Skip and
    # report it rather than aborting the whole scan for one stray folder.
    in_scope, seen_projects = [], []
    for disc in discovered:
        if disc.project_code not in known:
            if disc.project_code not in report.unknown_projects:
                report.unknown_projects.append(disc.project_code)
            continue
        if disc.project_code not in seen_projects:
            seen_projects.append(disc.project_code)
        in_scope.append(disc)

    report.projects_scanned = sorted(seen_projects)
    existing = _existing_rows(cursor, report.projects_scanned)

    for disc in sorted(in_scope, key=lambda d: (d.project_code, d.filename)):
        file_hash = sha256_file(disc.path)
        row = existing.get((disc.project_code, disc.filename))
        action = decide(disc, file_hash, row)
        report.of(action).append(disc.label)
        if not dry_run:
            report.ocr_cache_cleared += _apply(
                cursor, action, disc, file_hash, row, settings
            )

    found_keys = {(d.project_code, d.filename) for d in in_scope}
    report.missing_on_disk = sorted(
        f"{row.project_code}/{row.filename}"
        for key, row in existing.items()
        if key not in found_keys
    )
    return report


# Every stage exposes `run()`. Stage 0's own name for it is `scan()`, which
# reads better at its call sites and is what the tests use; this alias keeps
# the three stages uniform for anything that drives them generically.
run = scan


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_report(report: ScanReport, list_files: bool = False) -> str:
    mode = "DRY RUN — nothing written" if report.dry_run else "APPLIED"
    lines = [
        f"Stage 0 scan — {mode}",
        f"  root      : {report.root}",
        f"  projects  : {', '.join(report.projects_scanned) or '(none)'}",
        f"  files seen: {report.files_seen}",
        "",
    ]
    for action in Action:
        entries = report.of(action)
        lines.append(f"  {action.value:<13} {len(entries)}")
        if list_files and entries:
            lines.extend(f"      {e}" for e in entries)

    lines.append("")
    lines.append(f"  rows written : {report.writes}")
    lines.append(f"  OCR cache rows dropped : {report.ocr_cache_cleared}")

    if report.ignored:
        lines.append(f"  ignored (junk/lock files): {len(report.ignored)}")
        lines.extend(f"      {e}" for e in report.ignored)
    if report.unknown_projects:
        lines.append("")
        lines.append("  WARNING — folders with no row in `projects`, skipped:")
        lines.extend(f"      {code}" for code in report.unknown_projects)
    if report.missing_on_disk:
        lines.append("")
        lines.append(
            f"  NOTE — {len(report.missing_on_disk)} row(s) have no file on disk. "
            f"Reported only; nothing was changed:"
        )
        lines.extend(f"      {e}" for e in report.missing_on_disk)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Most of this corpus is Arabic and a Windows console defaults to
    # cp1252, which turns a working scan into a UnicodeEncodeError at the
    # moment it prints (INFRASTRUCTURE.md 4.5a). PYTHONIOENCODING=utf-8
    # fixes it, but a stage should not crash because the operator forgot.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Stage 0 — discover documents on disk and upsert document_links."
    )
    parser.add_argument("--project", help="Scan only this project code (folder name).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change; write nothing.")
    parser.add_argument("--list", action="store_true", dest="list_files",
                        help="List every file under its action.")
    args = parser.parse_args(argv)

    # The lock is taken here, at the entry point, not inside scan() —
    # scan() is also called by the Celery task, which takes it too, and a
    # non-reentrant lock in both places would deadlock the worker against
    # itself. A dry run needs no lock: it writes nothing.
    try:
        with optional_stage_lock("scan", enabled=not args.dry_run):
            report = scan(project=args.project, dry_run=args.dry_run)
    except AlreadyRunning as exc:
        print(f"Stage 0 scan — SKIPPED: {exc}", file=sys.stderr)
        return 2
    print(format_report(report, list_files=args.list_files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
