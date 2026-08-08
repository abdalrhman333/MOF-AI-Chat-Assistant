"""Tests for pipeline/ocr.py — Stage 1.

Every test here runs against the **real** schema inside a transaction that
is always rolled back, with a **stub extractor** in place of Azure. That
split is the point: the expensive, slow, external part is faked, while the
part that actually holds the money-saving guarantees — "never re-OCR",
"a failure must not look like a success" — runs against the real
constraints, the real cascade and the real SQL.

The stub records every call, so the strongest assertions in this file are
about calls that must NOT happen: a document that already has OCR, a file
missing from disk, and anything at all during a dry run.
"""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from pipeline.config import get_settings
from pipeline.ocr import run, select_pending

TEST_CODE = "ZZ-OCR-TEST"


# ---------------------------------------------------------------------------
# Stub Document Intelligence
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, content: str, pages: int):
        self.content = content
        self.pages = [object()] * pages
        self.api_version = "2024-11-30"
        self.model_id = "prebuilt-layout"

    def as_dict(self) -> dict:
        return {"content": self.content, "pageCount": len(self.pages)}


class FakePoller:
    def __init__(self, result: FakeResult):
        self._result = result

    def result(self) -> FakeResult:
        return self._result


class StubClient:
    """Records calls; can be told to fail on a particular filename."""

    def __init__(self, content: str = "# Extracted\n\nBody text here.",
                 pages: int = 3, fail_on: str | None = None):
        self.content, self.pages, self.fail_on = content, pages, fail_on
        self.calls: list[int] = []

    def begin_analyze_document(self, model_id, body=None, **kwargs):
        self.calls.append(len(body or b""))
        if self.fail_on and self.fail_on.encode() in (body or b""):
            raise RuntimeError("InvalidRequest: the document could not be analyzed")
        return FakePoller(FakeResult(self.content, self.pages))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    psycopg = pytest.importorskip("psycopg")
    settings = get_settings()
    try:
        conn = psycopg.connect(settings.postgres.dsn, connect_timeout=3)
    except psycopg.OperationalError as exc:              # pragma: no cover
        pytest.skip(f"Postgres not reachable — is mof-postgres-spm up? ({exc})")
    try:
        conn.execute(
            "INSERT INTO projects (code, name_ar, name_en, status) VALUES (%s,%s,%s,%s)",
            (TEST_CODE, "اختبار المسح الضوئي", "OCR test", "testing"),
        )
        yield conn
    finally:
        conn.rollback()
        conn.close()


def add_document(db, tmp_path, name="charter.docx", body=b"binary content",
                 status="pending", error=None) -> tuple[int, pathlib.Path]:
    """Create a file and its row the way the scanner would.

    `file_path` is stored RELATIVE to DOCUMENT_ROOT (`<project>/<name>`),
    so the row is valid on the host and inside the worker container alike.
    Storing an absolute path here was the defect containerising exposed.
    """
    path = tmp_path / TEST_CODE / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    row = db.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type, "
        "indexing_status, error_message) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (TEST_CODE, name, f"{TEST_CODE}/{name}", "Project Charter", status, error),
    ).fetchone()
    return row[0], path


def rooted_at(tmp_path):
    return dataclasses.replace(get_settings(), document_root=tmp_path)


def go(db, client, tmp_path=None, **kwargs):
    settings = rooted_at(tmp_path) if tmp_path is not None else get_settings()
    return run(project=TEST_CODE, settings=settings,
               connection=db, client=client, **kwargs)


def one(db, sql, params=()):
    return db.execute(sql, params).fetchone()


# ---------------------------------------------------------------------------
# Selection — what gets sent, and more importantly what doesn't
# ---------------------------------------------------------------------------

def test_a_document_with_ocr_is_never_sent_again(db, tmp_path):
    """The money rule. document_content's presence IS the 'OCR done' marker."""
    link_id, _ = add_document(db, tmp_path)
    db.execute("INSERT INTO document_content (document_link_id, markdown) VALUES (%s,%s)",
               (link_id, "# already extracted"))

    client = StubClient()
    report = go(db, client, tmp_path)

    assert report.candidates == []
    assert client.calls == []            # nothing was sent, nothing was billed


def test_previously_failed_documents_are_skipped_by_default(db, tmp_path):
    """A genuinely broken file must not silently re-bill on every run."""
    add_document(db, tmp_path, name="broken.pdf", status="failed", error="boom")
    client = StubClient()

    report = go(db, client, tmp_path)

    assert report.candidates == []
    assert client.calls == []


def test_retry_failed_includes_them_again(db, tmp_path):
    add_document(db, tmp_path, name="broken.pdf", status="failed", error="boom")
    client = StubClient()

    report = go(db, client, tmp_path, retry_failed=True)

    assert report.succeeded == [f"{TEST_CODE}/broken.pdf"]
    assert len(client.calls) == 1


def test_limit_caps_the_run(db, tmp_path):
    for i in range(4):
        add_document(db, tmp_path, name=f"doc{i}.pdf")
    client = StubClient()

    report = go(db, client, tmp_path, limit=2)

    assert len(report.succeeded) == 2
    assert len(client.calls) == 2


def test_project_filter_excludes_other_projects(db, tmp_path):
    add_document(db, tmp_path)
    cursor = db.cursor()
    scoped = select_pending(cursor, TEST_CODE, retry_failed=False)
    everything = select_pending(cursor, None, retry_failed=False)
    assert [d.project_code for d in scoped] == [TEST_CODE]
    assert len(everything) >= len(scoped)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_success_stores_markdown_and_provenance(db, tmp_path):
    link_id, _ = add_document(db, tmp_path)
    client = StubClient(content="# العنوان\n\nنص عربي.", pages=7)

    report = go(db, client, tmp_path)

    assert report.succeeded == [f"{TEST_CODE}/charter.docx"]
    assert report.pages_billed == 7
    markdown, page_count, model, api_version, raw = one(db,
        "SELECT markdown, page_count, ocr_model, ocr_api_version, raw_response "
        "FROM document_content WHERE document_link_id=%s", (link_id,))
    assert markdown == "# العنوان\n\nنص عربي."
    assert (page_count, model, api_version) == (7, "prebuilt-layout", "2024-11-30")
    assert raw["pageCount"] == 7          # raw_response really is JSONB


def test_success_after_a_previous_failure_clears_the_error(db, tmp_path):
    link_id, _ = add_document(db, tmp_path, status="failed", error="previous boom")
    client = StubClient()

    go(db, client, tmp_path, retry_failed=True)

    status, error = one(db,
        "SELECT indexing_status, error_message FROM document_links WHERE id=%s", (link_id,))
    assert (status, error) == ("pending", None)


def test_status_stays_pending_because_stage_3_owns_indexed(db, tmp_path):
    link_id, _ = add_document(db, tmp_path)
    go(db, StubClient(), tmp_path)
    status, = one(db, "SELECT indexing_status FROM document_links WHERE id=%s", (link_id,))
    assert status == "pending"


# ---------------------------------------------------------------------------
# Failure path — the part that must never look like success
# ---------------------------------------------------------------------------

def test_failure_records_the_error_and_writes_no_document_content(db, tmp_path):
    """If a failure wrote document_content, its presence would permanently
    convince Stage 1 the document was done — a paid step skipped forever."""
    link_id, _ = add_document(db, tmp_path, name="bad.pdf", body=b"POISON bytes")
    client = StubClient(fail_on="POISON")

    report = go(db, client, tmp_path)

    assert report.succeeded == []
    assert len(report.failed) == 1
    status, error = one(db,
        "SELECT indexing_status, error_message FROM document_links WHERE id=%s", (link_id,))
    assert status == "failed"
    assert "InvalidRequest" in error
    rows, = one(db, "SELECT count(*) FROM document_content WHERE document_link_id=%s", (link_id,))
    assert rows == 0


def test_a_missing_file_fails_without_calling_the_api(db, tmp_path):
    """Paying Document Intelligence to discover a file isn't there is silly.

    The message assertion is load-bearing, not decoration. Removing the
    explicit `is_file()` guard does NOT change whether the API is called —
    `path.read_bytes()` raises first either way — so `client.calls == []`
    alone cannot tell a working guard from a deleted one (proven: that
    mutation survived until this assertion was added). The guard's real
    value is the diagnostic, so the diagnostic is what gets pinned.
    """
    link_id, path = add_document(db, tmp_path, name="gone.pdf")
    path.unlink()
    client = StubClient()

    report = go(db, client, tmp_path)

    assert client.calls == []                     # never sent
    assert len(report.failed) == 1
    status, error = one(db,
        "SELECT indexing_status, error_message FROM document_links WHERE id=%s", (link_id,))
    assert status == "failed"
    assert error.startswith("FileNotFoundError: file not on disk:")
    assert str(path) in error


def test_one_failure_does_not_stop_the_batch(db, tmp_path):
    add_document(db, tmp_path, name="good1.pdf", body=b"fine")
    add_document(db, tmp_path, name="bad.pdf", body=b"POISON")
    add_document(db, tmp_path, name="good2.pdf", body=b"fine too")

    report = go(db, StubClient(fail_on="POISON"), tmp_path)

    assert sorted(report.succeeded) == [f"{TEST_CODE}/good1.pdf", f"{TEST_CODE}/good2.pdf"]
    assert len(report.failed) == 1
    stored, = one(db, "SELECT count(*) FROM document_content dc JOIN document_links dl "
                      "ON dl.id=dc.document_link_id WHERE dl.project_code=%s", (TEST_CODE,))
    assert stored == 2


# ---------------------------------------------------------------------------
# Thin results — flagged, never failed
# ---------------------------------------------------------------------------

def test_a_thin_result_is_stored_and_flagged_not_failed(db, tmp_path):
    """A scan whose content lives inside images can never improve. Marking
    it failed would re-bill it on every future run, forever."""
    link_id, _ = add_document(db, tmp_path, name="scanned.pdf")
    settings = dataclasses.replace(
        rooted_at(tmp_path),
        ocr=dataclasses.replace(get_settings().ocr, thin_result_chars=200))

    report = run(project=TEST_CODE, settings=settings, connection=db,
                 client=StubClient(content="<figure></figure>", pages=9))

    assert report.thin == [(f"{TEST_CODE}/scanned.pdf", len("<figure></figure>"))]
    assert report.failed == []
    status, = one(db, "SELECT indexing_status FROM document_links WHERE id=%s", (link_id,))
    assert status == "pending"                    # NOT failed
    rows, = one(db, "SELECT count(*) FROM document_content WHERE document_link_id=%s", (link_id,))
    assert rows == 1                              # stored, so never re-billed


def test_a_normal_result_is_not_flagged_thin(db, tmp_path):
    add_document(db, tmp_path)
    report = go(db, StubClient(content="x" * 500), tmp_path)
    assert report.thin == []


# ---------------------------------------------------------------------------
# Native fallback — only when Document Intelligence refuses an Office file
# ---------------------------------------------------------------------------

def project_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """DOCUMENT_ROOT/<project>/ — where a real corpus keeps its files."""
    d = tmp_path / TEST_CODE
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_docx(path: pathlib.Path, paragraphs=("Hello",), heading: str | None = None):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    if heading:
        document.add_heading(heading, level=1)
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))
    return path


def test_a_rejected_docx_is_rescued_natively(db, tmp_path):
    """The case this exists for: DI calls a perfectly readable .docx
    'corrupted', and without a fallback the document contributes nothing."""
    path = make_docx(project_dir(tmp_path) / "rescued.docx",
                     paragraphs=["نص عربي للاختبار", "Second paragraph"],
                     heading="Design Document")
    link_id = db.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (TEST_CODE, "rescued.docx", f"{TEST_CODE}/rescued.docx", "Software Design Document"),
    ).fetchone()[0]

    report = go(db, StubClient(fail_on="PK"), tmp_path)     # every .docx starts with PK

    assert report.succeeded == [f"{TEST_CODE}/rescued.docx"]
    assert [label for label, _ in report.recovered] == [f"{TEST_CODE}/rescued.docx"]
    assert report.failed == []
    markdown, model = one(db,
        "SELECT markdown, ocr_model FROM document_content WHERE document_link_id=%s",
        (link_id,))
    assert model == "native:python-docx"          # provenance is never ambiguous
    assert "# Design Document" in markdown        # DI's heading convention
    assert "نص عربي للاختبار" in markdown


def test_a_rescued_document_bills_zero_pages(db, tmp_path):
    path = make_docx(project_dir(tmp_path) / "free.docx")
    db.execute("INSERT INTO document_links (project_code, filename, file_path, doc_type) "
               "VALUES (%s,%s,%s,%s)", (TEST_CODE, "free.docx", f"{TEST_CODE}/free.docx", "Charter"))

    report = go(db, StubClient(fail_on="PK"), tmp_path)

    assert report.pages_billed == 0


def test_a_rejected_pdf_is_NOT_rescued(db, tmp_path):
    """Native PDF text was measured as garbage — 36 Arabic characters
    against DI's 2,623. A fallback that silently produces nonsense is worse
    than an honest failure, so PDFs must fail."""
    link_id, _ = add_document(db, tmp_path, name="scan.pdf", body=b"POISON pdf bytes")

    report = go(db, StubClient(fail_on="POISON"), tmp_path)

    assert report.recovered == []
    assert len(report.failed) == 1
    rows, = one(db, "SELECT count(*) FROM document_content WHERE document_link_id=%s",
                (link_id,))
    assert rows == 0

    # The recorded error must be Document Intelligence's own, NOT a
    # "both extractors failed" message. Without the format guard the PDF
    # still fails — native_extract refuses it too — but the diagnostic
    # would wrongly imply a fallback was appropriate and attempted. That
    # difference is the only observable effect of the guard, so it is what
    # gets pinned (a mutation removing the guard survived until this).
    error, = one(db, "SELECT error_message FROM document_links WHERE id=%s", (link_id,))
    assert "InvalidRequest" in error
    assert "native fallback" not in error
    assert "no native extractor" not in error


def test_when_both_extractors_fail_the_error_names_both(db, tmp_path):
    """A batch is debugged through error_message. If it only mentioned the
    fallback, nobody would know DI was tried first."""
    link_id, _ = add_document(db, tmp_path, name="broken.docx", body=b"POISON not-a-zip")

    report = go(db, StubClient(fail_on="POISON"), tmp_path)

    assert report.succeeded == []
    error, = one(db, "SELECT error_message FROM document_links WHERE id=%s", (link_id,))
    assert "Document Intelligence failed" in error
    assert "native fallback also failed" in error


def test_the_fallback_never_runs_when_azure_succeeds(db, tmp_path):
    """It is a fallback, not a router. DI stays primary for every format."""
    path = make_docx(project_dir(tmp_path) / "normal.docx")
    link_id = db.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (TEST_CODE, "normal.docx", f"{TEST_CODE}/normal.docx", "Charter")).fetchone()[0]

    report = go(db, StubClient(content="# From Azure", pages=3), tmp_path)

    assert report.recovered == []
    assert report.pages_billed == 3
    model, = one(db, "SELECT ocr_model FROM document_content WHERE document_link_id=%s",
                 (link_id,))
    assert model == "prebuilt-layout"


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_sends_nothing_and_writes_nothing(db, tmp_path):
    link_id, _ = add_document(db, tmp_path)
    client = StubClient()

    report = go(db, client, tmp_path, dry_run=True)

    assert report.candidates == [f"{TEST_CODE}/charter.docx"]
    assert client.calls == []
    assert report.pages_billed == 0
    rows, = one(db, "SELECT count(*) FROM document_content WHERE document_link_id=%s", (link_id,))
    assert rows == 0


# ---------------------------------------------------------------------------
# Configuration failure — loud, not 58 quiet ones
# ---------------------------------------------------------------------------

def test_a_missing_document_root_fails_the_run_not_every_document(db, tmp_path):
    """A wrong DOCUMENT_ROOT must stop the run, not mark the corpus failed.

    Without the pre-flight check every document fails individually with
    FileNotFoundError and is marked 'failed' — and because failed documents
    are excluded by default, one typo would turn the whole corpus into rows
    that need --retry-failed to recover. One clear error beats 58 quiet
    ones that also cost a recovery step.
    """
    add_document(db, tmp_path)
    settings = dataclasses.replace(get_settings(), document_root=tmp_path / "nope")

    with pytest.raises(RuntimeError, match="DOCUMENT_ROOT"):
        run(project=TEST_CODE, settings=settings, connection=db, client=StubClient())

    status, = one(db, "SELECT indexing_status FROM document_links WHERE project_code=%s",
                  (TEST_CODE,))
    assert status == "pending"        # untouched — nothing was marked failed


def test_a_stale_absolute_file_path_is_refused_with_instructions(db, tmp_path):
    """Rows written before the relative-path fix must not be guessed at."""
    link_id, _ = add_document(db, tmp_path)
    db.execute("UPDATE document_links SET file_path=%s WHERE id=%s",
               (r"D:\MOF_DataSample\ZZ-OCR-TEST\charter.docx", link_id))

    report = go(db, StubClient(), tmp_path)

    assert len(report.failed) == 1
    error, = one(db, "SELECT error_message FROM document_links WHERE id=%s", (link_id,))
    assert "must be relative" in error
    assert "pipeline.scanner" in error


def test_an_oversized_provider_response_is_flagged_not_truncated(db, tmp_path):
    """`raw_response` has no cap, and that is deliberate — the word-level
    geometry is exactly what markdown discards and what the OCR cache
    exists to preserve. Measured on this corpus: 177 kB average, 1.3 MB
    largest. Truncating would silently destroy the thing migration 010 was
    built to keep, so growth is surfaced instead."""
    link_id, _ = add_document(db, tmp_path)
    settings = dataclasses.replace(
        rooted_at(tmp_path),
        ocr=dataclasses.replace(get_settings().ocr, raw_response_warn_bytes=100))

    report = run(project=TEST_CODE, settings=settings, connection=db,
                 client=StubClient(content="x" * 500))

    assert len(report.oversized) == 1
    assert report.oversized[0][0] == f"{TEST_CODE}/charter.docx"
    stored, = one(db, "SELECT length(raw_response::text) FROM document_content "
                      "WHERE document_link_id=%s", (link_id,))
    assert stored > 100                      # kept whole, not trimmed
    assert report.failed == []               # a warning, never a failure


def test_a_normal_response_is_not_flagged_oversized(db, tmp_path):
    add_document(db, tmp_path)
    report = go(db, StubClient(), tmp_path)
    assert report.oversized == []
