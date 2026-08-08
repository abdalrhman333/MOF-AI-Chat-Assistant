"""Tests for pipeline/scanner.py — Stage 0, discovery.

Two layers, mirroring test_chunker.py:

  * **Pure tests** pin the decision logic and the filesystem walk. They
    need no database and run in milliseconds.
  * **Integration tests** run the real SQL against the real schema — real
    constraints, real foreign keys, real cascade — inside a transaction
    that is always rolled back. Nothing survives the test. This matters
    more than a mock would: the bugs worth catching here are
    "NOT NULL violation", "foreign key violation", and "the UPDATE
    clobbered a curated column", none of which a mocked cursor can have
    an opinion about.

The lesson recorded in STATUS_AND_ROADMAP.md 6 applies throughout: assert
against literals, never against the constant under test, or mutating the
constant moves the assertion with it and the test passes against broken
code.
"""
from __future__ import annotations

import dataclasses
import hashlib
import pathlib

import pytest

from pipeline.config import get_settings
from pipeline.scanner import (
    Action,
    DiscoveredFile,
    ExistingRow,
    decide,
    discover,
    is_ignored,
    scan,
    sha256_file,
)

IGNORE = ("~$*", ".*", "Thumbs.db", "desktop.ini")


def disc(project="PRJ-05", filename="a.docx", path=r"D:\corpus\PRJ-05\a.docx", size=10):
    return DiscoveredFile(
        project_code=project,
        filename=filename,
        path=pathlib.Path(path),
        size_bytes=size,
    )


def row(file_hash="abc", path="PRJ-05/a.docx", size=10, row_id=1):
    """A correctly-scanned row. `file_path` is RELATIVE to DOCUMENT_ROOT,
    so it reads the same on the Windows host and in the Linux worker."""
    return ExistingRow(
        id=row_id,
        project_code="PRJ-05",
        filename="a.docx",
        file_path=path,
        file_hash=file_hash,
        file_size_bytes=size,
    )


# ---------------------------------------------------------------------------
# decide() — the whole change-detection contract
# ---------------------------------------------------------------------------

def test_no_row_is_insert():
    assert decide(disc(), "abc", None) is Action.INSERT


def test_null_hash_is_backfill():
    """The seeded-placeholder case: every one of the 58 rows before Stage 0."""
    assert decide(disc(), "abc", row(file_hash=None)) is Action.BACKFILL


def test_matching_hash_and_path_is_unchanged():
    assert decide(disc(), "abc", row(file_hash="abc")) is Action.UNCHANGED


def test_differing_hash_is_modified():
    assert decide(disc(), "NEW", row(file_hash="abc")) is Action.MODIFIED


def test_a_stale_absolute_path_is_repaired_without_reindexing():
    """The migration case — and now the ONLY way PATH_UPDATED can fire.

    `file_path` is stored relative and equals `<project>/<filename>`, which
    is the row's own unique key, so for a matched row it cannot differ
    except when the stored value predates the relative-path fix. Both
    shapes exist in the wild: the Windows host wrote `D:\\...` and the
    worker container wrote `/data/...`. Each must be repaired, and repaired
    WITHOUT re-OCR — the bytes are identical, only the record was wrong.
    """
    host_legacy = row(file_hash="abc", path=r"D:\MOF_DataSample\PRJ-05\a.docx")
    assert decide(disc(), "abc", host_legacy) is Action.PATH_UPDATED

    container_legacy = row(file_hash="abc", path="/data/PRJ-05/a.docx")
    assert decide(disc(), "abc", container_legacy) is Action.PATH_UPDATED


def test_content_change_outranks_a_stale_path():
    """Both wrong at once must still be MODIFIED, not PATH_UPDATED.

    PATH_UPDATED deliberately skips the re-OCR reset, so reaching it with
    changed bytes would leave the cached OCR describing the old file.
    """
    legacy = row(file_hash="abc", path=r"D:\MOF_DataSample\PRJ-05\a.docx")
    assert decide(disc(), "NEW", legacy) is Action.MODIFIED


def test_size_alone_never_decides():
    """REGRESSION-GUARD: a differing size with an identical hash is not a
    change. Size is stored as a diagnostic, not used as a decision input —
    if someone 'optimises' by trusting size, this fails."""
    assert decide(disc(size=999), "abc", row(file_hash="abc", size=10)) is Action.UNCHANGED


# ---------------------------------------------------------------------------
# is_ignored()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "~$MOZN Project Charter-V0.1.docx",   # Word lock file, real shape
    ".DS_Store",
    "Thumbs.db",
    "thumbs.db",                          # case folded on Windows
    "desktop.ini",
])
def test_junk_files_are_ignored(name):
    assert is_ignored(name, IGNORE) is True


@pytest.mark.parametrize("name", [
    "MOZN Project Charter-V0.1.docx",
    "_المنهجية التقنية للمشروع.pdf",
    "Tanmiah_GenAI_POC_Proposal_Technical 1.pptx",
    "PRJ-05_RiskRegister_20260701.docx",
])
def test_real_documents_are_not_ignored(name):
    assert is_ignored(name, IGNORE) is False


# ---------------------------------------------------------------------------
# sha256_file()
# ---------------------------------------------------------------------------

def test_hash_matches_an_independent_computation(tmp_path):
    payload = "مرحبا بالعالم".encode("utf-8") * 500
    target = tmp_path / "arabic.txt"
    target.write_bytes(payload)
    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_hash_detects_a_one_byte_change(tmp_path):
    target = tmp_path / "doc.bin"
    target.write_bytes(b"A" * 4096)
    before = sha256_file(target)
    target.write_bytes(b"A" * 4095 + b"B")
    assert sha256_file(target) != before


def test_hash_spans_block_boundaries(tmp_path):
    """The reader loops in 1 MiB blocks; a change past the first block must
    still register."""
    size = 1024 * 1024 * 2 + 17
    target = tmp_path / "big.bin"
    target.write_bytes(b"\x00" * size)
    before = sha256_file(target)
    target.write_bytes(b"\x00" * (size - 1) + b"\xff")
    assert sha256_file(target) != before


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------

def build_corpus(root: pathlib.Path) -> None:
    (root / "PRJ-05").mkdir()
    (root / "PRJ-05" / "charter.docx").write_bytes(b"charter")
    (root / "PRJ-05" / "~$charter.docx").write_bytes(b"lock")
    (root / "PRJ-05" / "Thumbs.db").write_bytes(b"junk")
    (root / "PRJ-05" / "archive").mkdir()
    (root / "PRJ-05" / "archive" / "old.pdf").write_bytes(b"old")
    (root / "PRJ-05" / ".sync").mkdir()
    (root / "PRJ-05" / ".sync" / "state.dat").write_bytes(b"x")
    (root / "TEST-PROJECT").mkdir()
    (root / "TEST-PROJECT" / "_المنهجية التقنية للمشروع.pdf").write_bytes(b"ar")


def test_flat_files_keep_their_bare_filename(tmp_path):
    """Guards the 58 seeded rows: a flat corpus must produce exactly the
    bare filenames already in document_links, or every row backfills as a
    spurious INSERT."""
    build_corpus(tmp_path)
    found = {f.filename for f in discover(tmp_path, IGNORE, project_filter="PRJ-05")}
    assert "charter.docx" in found


def test_subfolders_yield_a_relative_posix_path(tmp_path):
    build_corpus(tmp_path)
    found = {f.filename for f in discover(tmp_path, IGNORE, project_filter="PRJ-05")}
    assert found == {"charter.docx", "archive/old.pdf"}


def test_junk_files_and_ignored_folders_are_skipped(tmp_path):
    build_corpus(tmp_path)
    sink: list[str] = []
    found = {f.filename for f in discover(tmp_path, IGNORE, ignored_sink=sink)}
    assert "~$charter.docx" not in found
    assert "Thumbs.db" not in found
    assert ".sync/state.dat" not in found          # ignored via the FOLDER
    assert sorted(sink) == [
        "PRJ-05/.sync/state.dat", "PRJ-05/Thumbs.db", "PRJ-05/~$charter.docx",
    ]


def test_arabic_filenames_survive_discovery(tmp_path):
    build_corpus(tmp_path)
    found = list(discover(tmp_path, IGNORE, project_filter="TEST-PROJECT"))
    assert [f.filename for f in found] == ["_المنهجية التقنية للمشروع.pdf"]


def test_project_filter_excludes_everything_else(tmp_path):
    build_corpus(tmp_path)
    codes = {f.project_code for f in discover(tmp_path, IGNORE, project_filter="TEST-PROJECT")}
    assert codes == {"TEST-PROJECT"}


def test_project_code_comes_from_the_folder_name(tmp_path):
    build_corpus(tmp_path)
    found = {f.project_code for f in discover(tmp_path, IGNORE)}
    assert found == {"PRJ-05", "TEST-PROJECT"}


def test_size_is_read_from_disk(tmp_path):
    build_corpus(tmp_path)
    charter = next(f for f in discover(tmp_path, IGNORE) if f.filename == "charter.docx")
    assert charter.size_bytes == len(b"charter")


# ---------------------------------------------------------------------------
# Integration — real schema, real SQL, always rolled back
# ---------------------------------------------------------------------------

TEST_CODE = "ZZ-SCANNER-TEST"


@pytest.fixture
def txn():
    """A real connection with a throwaway project, rolled back afterwards."""
    psycopg = pytest.importorskip("psycopg")
    settings = get_settings()
    try:
        conn = psycopg.connect(settings.postgres.dsn, connect_timeout=3)
    except psycopg.OperationalError as exc:            # pragma: no cover
        pytest.skip(f"Postgres not reachable — is mof-postgres-spm up? ({exc})")
    try:
        conn.execute(
            "INSERT INTO projects (code, name_ar, name_en, status) "
            "VALUES (%s, %s, %s, %s)",
            (TEST_CODE, "مشروع اختبار", "Scanner test", "testing"),
        )
        yield conn
    finally:
        conn.rollback()      # nothing this test did is kept
        conn.close()


def settings_rooted_at(root: pathlib.Path):
    return dataclasses.replace(get_settings(), document_root=root)


def corpus(root: pathlib.Path, **files: bytes) -> pathlib.Path:
    project = root / TEST_CODE
    project.mkdir()
    for name, payload in files.items():
        (project / name).write_bytes(payload)
    return project


def fetch(txn, sql, params=()):
    return txn.execute(sql, params).fetchall()


def test_new_file_is_inserted_as_pending(txn, tmp_path):
    corpus(tmp_path, charter_docx=b"hello")
    report = scan(project=TEST_CODE, settings=settings_rooted_at(tmp_path), connection=txn)

    assert report.of(Action.INSERT) == [f"{TEST_CODE}/charter_docx"]
    rows = fetch(txn,
        "SELECT filename, file_path, doc_type, indexing_status, file_hash, file_size_bytes "
        "FROM document_links WHERE project_code = %s", (TEST_CODE,))
    assert len(rows) == 1
    filename, file_path, doc_type, status, file_hash, size = rows[0]
    assert filename == "charter_docx"
    assert file_path == f"{TEST_CODE}/charter_docx"    # relative to DOCUMENT_ROOT
    assert doc_type == "Unclassified"
    assert status == "pending"
    assert file_hash == hashlib.sha256(b"hello").hexdigest()
    assert size == 5


def test_running_twice_writes_nothing_the_second_time(txn, tmp_path):
    """Idempotence — NEXT_BUILD_PIPELINE.md 3.5."""
    corpus(tmp_path, a=b"one", b=b"two")
    settings = settings_rooted_at(tmp_path)

    first = scan(project=TEST_CODE, settings=settings, connection=txn)
    second = scan(project=TEST_CODE, settings=settings, connection=txn)

    assert len(first.of(Action.INSERT)) == 2
    assert len(second.of(Action.UNCHANGED)) == 2
    assert second.writes == 0
    count = fetch(txn, "SELECT count(*) FROM document_links WHERE project_code = %s",
                  (TEST_CODE,))[0][0]
    assert count == 2


def test_backfill_fills_the_hash_without_clobbering_curated_columns(txn, tmp_path):
    """The seeded rows carry doc_type/language/contains_table curated from
    the manifest. The filesystem cannot know them; overwriting them with
    defaults would destroy real data."""
    corpus(tmp_path, seeded=b"content")
    txn.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type, "
        "language, contains_table) VALUES (%s, %s, %s, %s, %s, %s)",
        (TEST_CODE, "seeded", r"D:\placeholder\seeded", "Risk Register", "Arabic", True),
    )

    report = scan(project=TEST_CODE, settings=settings_rooted_at(tmp_path), connection=txn)

    assert report.of(Action.BACKFILL) == [f"{TEST_CODE}/seeded"]
    doc_type, language, contains_table, file_path, file_hash = fetch(txn,
        "SELECT doc_type, language, contains_table, file_path, file_hash "
        "FROM document_links WHERE project_code = %s", (TEST_CODE,))[0]
    assert (doc_type, language, contains_table) == ("Risk Register", "Arabic", True)
    assert file_path == f"{TEST_CODE}/seeded"          # placeholder replaced, relative
    assert file_hash == hashlib.sha256(b"content").hexdigest()


def test_modified_file_resets_status_and_drops_the_stale_ocr_cache(txn, tmp_path):
    """The load-bearing one. document_content's presence IS the 'OCR done'
    marker, so a stale row left behind makes Stage 1 skip re-OCR and change
    detection silently does nothing."""
    project = corpus(tmp_path, doc=b"version one")
    settings = settings_rooted_at(tmp_path)
    scan(project=TEST_CODE, settings=settings, connection=txn)

    link_id = fetch(txn, "SELECT id FROM document_links WHERE project_code = %s",
                    (TEST_CODE,))[0][0]
    txn.execute(
        "INSERT INTO document_content (document_link_id, markdown, page_count) "
        "VALUES (%s, %s, %s)", (link_id, "# cached OCR of version one", 3))
    txn.execute("UPDATE document_links SET indexing_status='indexed', indexed_at=now(), "
                "chunk_count=7, error_message='old failure' WHERE id=%s", (link_id,))

    edited = "version two — النسخة الثانية".encode("utf-8")
    (project / "doc").write_bytes(edited)
    report = scan(project=TEST_CODE, settings=settings, connection=txn)

    assert report.of(Action.MODIFIED) == [f"{TEST_CODE}/doc"]
    assert report.ocr_cache_cleared == 1
    status, indexed_at, chunk_count, error, file_hash = fetch(txn,
        "SELECT indexing_status, indexed_at, chunk_count, error_message, file_hash "
        "FROM document_links WHERE id = %s", (link_id,))[0]
    assert status == "pending"
    assert (indexed_at, chunk_count, error) == (None, None, None)
    assert file_hash == hashlib.sha256(edited).hexdigest()
    cached = fetch(txn, "SELECT count(*) FROM document_content WHERE document_link_id = %s",
                   (link_id,))[0][0]
    assert cached == 0


def test_backfill_keeps_the_ocr_cache(txn, tmp_path):
    """The other half of the same rule. A NULL hash means 'never scanned',
    not 'content changed' — there is no evidence the cache is stale, and
    OCR is billed per page."""
    corpus(tmp_path, doc=b"content")
    txn.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (TEST_CODE, "doc", r"D:\placeholder\doc", "Project Charter"))
    link_id = txn.execute("SELECT id FROM document_links WHERE project_code = %s",
                          (TEST_CODE,)).fetchone()[0]
    txn.execute("INSERT INTO document_content (document_link_id, markdown) VALUES (%s, %s)",
                (link_id, "# cached"))

    report = scan(project=TEST_CODE, settings=settings_rooted_at(tmp_path), connection=txn)

    assert report.of(Action.BACKFILL) == [f"{TEST_CODE}/doc"]
    assert report.ocr_cache_cleared == 0
    cached = fetch(txn, "SELECT count(*) FROM document_content WHERE document_link_id = %s",
                   (link_id,))[0][0]
    assert cached == 1


def test_a_row_whose_file_vanished_is_reported_never_touched(txn, tmp_path):
    corpus(tmp_path, present=b"here")
    txn.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type, "
        "indexing_status, chunk_count) VALUES (%s, %s, %s, %s, %s, %s)",
        (TEST_CODE, "deleted-from-sharepoint.docx", r"D:\gone.docx", "Project Charter",
         "indexed", 12))

    report = scan(project=TEST_CODE, settings=settings_rooted_at(tmp_path), connection=txn)

    assert report.missing_on_disk == [f"{TEST_CODE}/deleted-from-sharepoint.docx"]
    status, chunk_count = fetch(txn,
        "SELECT indexing_status, chunk_count FROM document_links "
        "WHERE project_code = %s AND filename = %s",
        (TEST_CODE, "deleted-from-sharepoint.docx"))[0]
    assert (status, chunk_count) == ("indexed", 12)      # untouched, not deleted


def test_a_folder_with_no_project_row_is_skipped_not_a_crash(txn, tmp_path):
    """A stray folder must not abort the scan with a foreign-key violation."""
    corpus(tmp_path, real=b"x")
    stray = tmp_path / "NOT-A-PROJECT"
    stray.mkdir()
    (stray / "orphan.docx").write_bytes(b"y")

    report = scan(settings=settings_rooted_at(tmp_path), connection=txn)

    assert report.unknown_projects == ["NOT-A-PROJECT"]
    assert report.of(Action.INSERT) == [f"{TEST_CODE}/real"]


def test_dry_run_reports_but_writes_nothing(txn, tmp_path):
    corpus(tmp_path, a=b"one", b=b"two")

    report = scan(project=TEST_CODE, dry_run=True,
                  settings=settings_rooted_at(tmp_path), connection=txn)

    assert len(report.of(Action.INSERT)) == 2
    count = fetch(txn, "SELECT count(*) FROM document_links WHERE project_code = %s",
                  (TEST_CODE,))[0][0]
    assert count == 0


def test_repointing_document_root_is_a_no_op(txn, tmp_path):
    """The whole point of storing paths relative.

    The Windows host mounts the corpus at `D:\\MOF_DataSample` and the
    worker container mounts the same files at `/data`. Before this fix each
    run rewrote all 58 rows to its own absolute paths, so the two
    environments fought over every row and Stage 1 could not open a file the
    other had recorded. Now a different root produces **zero writes**.
    """
    first_root = tmp_path / "first"
    first_root.mkdir()
    corpus(first_root, doc=b"same bytes")
    scan(project=TEST_CODE, settings=settings_rooted_at(first_root), connection=txn)

    stored, = fetch(txn, "SELECT file_path FROM document_links WHERE project_code = %s",
                    (TEST_CODE,))[0]
    assert stored == f"{TEST_CODE}/doc"          # relative, not first_root

    second_root = tmp_path / "second"
    second_root.mkdir()
    corpus(second_root, doc=b"same bytes")
    report = scan(project=TEST_CODE, settings=settings_rooted_at(second_root), connection=txn)

    assert report.of(Action.UNCHANGED) == [f"{TEST_CODE}/doc"]
    assert report.writes == 0
    assert report.of(Action.PATH_UPDATED) == []


def test_a_legacy_absolute_path_is_repaired_without_re_ocr(txn, tmp_path):
    """Migrating a row written before the relative-path fix.

    It must be corrected — Stage 1 refuses an absolute `file_path` outright
    — but corrected WITHOUT resetting indexing_status or dropping the OCR
    cache, because the bytes never changed. Getting this wrong would
    re-OCR the entire corpus the first time the fix shipped.
    """
    corpus(tmp_path, doc=b"same bytes")
    link_id = txn.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type, "
        "file_hash, file_size_bytes, indexing_status, chunk_count) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (TEST_CODE, "doc", r"D:\MOF_DataSample\ZZ-SCANNER-TEST\doc", "Charter",
         hashlib.sha256(b"same bytes").hexdigest(), len(b"same bytes"), "indexed", 5),
    ).fetchone()[0]
    txn.execute("INSERT INTO document_content (document_link_id, markdown) VALUES (%s,%s)",
                (link_id, "# cached"))

    report = scan(project=TEST_CODE, settings=settings_rooted_at(tmp_path), connection=txn)

    assert report.of(Action.PATH_UPDATED) == [f"{TEST_CODE}/doc"]
    file_path, status, chunk_count = fetch(txn,
        "SELECT file_path, indexing_status, chunk_count FROM document_links WHERE id = %s",
        (link_id,))[0]
    assert file_path == f"{TEST_CODE}/doc"               # now relative
    assert (status, chunk_count) == ("indexed", 5)       # not reset
    cached = fetch(txn, "SELECT count(*) FROM document_content WHERE document_link_id = %s",
                   (link_id,))[0][0]
    assert cached == 1                                   # OCR not re-paid


def test_an_empty_root_with_known_documents_is_refused(txn, tmp_path):
    """The wrong-DOCUMENT_ROOT trap.

    An empty corpus is legitimate on a fresh install, so it cannot be a
    hard error in general. But an empty corpus *when the database already
    holds documents* almost always means DOCUMENT_ROOT points somewhere
    wrong — and continuing would report every existing document as missing.
    Refusing protects those rows and the paid-for OCR cache hanging off
    them.
    """
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(RuntimeError, match="wrong directory"):
        scan(settings=settings_rooted_at(empty), connection=txn)


def test_an_empty_root_on_a_fresh_database_is_allowed(txn, tmp_path):
    """The opposite case: nothing on disk AND nothing recorded is a valid
    first run, and must not be blocked."""
    empty = tmp_path / "empty"
    empty.mkdir()
    txn.execute("DELETE FROM document_content")
    txn.execute("DELETE FROM document_links")

    report = scan(settings=settings_rooted_at(empty), connection=txn)

    assert report.files_seen == 0
    assert report.writes == 0
