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
    path = tmp_path / name
    path.write_bytes(body)
    row = db.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type, "
        "indexing_status, error_message) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (TEST_CODE, name, str(path), "Project Charter", status, error),
    ).fetchone()
    return row[0], path


def go(db, client, **kwargs):
    return run(project=TEST_CODE, settings=get_settings(),
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
    report = go(db, client)

    assert report.candidates == []
    assert client.calls == []            # nothing was sent, nothing was billed


def test_previously_failed_documents_are_skipped_by_default(db, tmp_path):
    """A genuinely broken file must not silently re-bill on every run."""
    add_document(db, tmp_path, name="broken.pdf", status="failed", error="boom")
    client = StubClient()

    report = go(db, client)

    assert report.candidates == []
    assert client.calls == []


def test_retry_failed_includes_them_again(db, tmp_path):
    add_document(db, tmp_path, name="broken.pdf", status="failed", error="boom")
    client = StubClient()

    report = go(db, client, retry_failed=True)

    assert report.succeeded == [f"{TEST_CODE}/broken.pdf"]
    assert len(client.calls) == 1


def test_limit_caps_the_run(db, tmp_path):
    for i in range(4):
        add_document(db, tmp_path, name=f"doc{i}.pdf")
    client = StubClient()

    report = go(db, client, limit=2)

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

    report = go(db, client)

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

    go(db, client, retry_failed=True)

    status, error = one(db,
        "SELECT indexing_status, error_message FROM document_links WHERE id=%s", (link_id,))
    assert (status, error) == ("pending", None)


def test_status_stays_pending_because_stage_3_owns_indexed(db, tmp_path):
    link_id, _ = add_document(db, tmp_path)
    go(db, StubClient())
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

    report = go(db, client)

    assert report.succeeded == []
    assert len(report.failed) == 1
    status, error = one(db,
        "SELECT indexing_status, error_message FROM document_links WHERE id=%s", (link_id,))
    assert status == "failed"
    assert "InvalidRequest" in error
    rows, = one(db, "SELECT count(*) FROM document_content WHERE document_link_id=%s", (link_id,))
    assert rows == 0


def test_a_missing_file_fails_without_calling_the_api(db, tmp_path):
    """Paying Document Intelligence to discover a file isn't there is silly."""
    link_id, path = add_document(db, tmp_path, name="gone.pdf")
    path.unlink()
    client = StubClient()

    report = go(db, client)

    assert client.calls == []                     # never sent
    assert len(report.failed) == 1
    status, error = one(db,
        "SELECT indexing_status, error_message FROM document_links WHERE id=%s", (link_id,))
    assert status == "failed"
    assert "FileNotFoundError" in error


def test_one_failure_does_not_stop_the_batch(db, tmp_path):
    add_document(db, tmp_path, name="good1.pdf", body=b"fine")
    add_document(db, tmp_path, name="bad.pdf", body=b"POISON")
    add_document(db, tmp_path, name="good2.pdf", body=b"fine too")

    report = go(db, StubClient(fail_on="POISON"))

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
        get_settings(), ocr=dataclasses.replace(get_settings().ocr, thin_result_chars=200))

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
    report = go(db, StubClient(content="x" * 500))
    assert report.thin == []


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_sends_nothing_and_writes_nothing(db, tmp_path):
    link_id, _ = add_document(db, tmp_path)
    client = StubClient()

    report = go(db, client, dry_run=True)

    assert report.candidates == [f"{TEST_CODE}/charter.docx"]
    assert client.calls == []
    assert report.pages_billed == 0
    rows, = one(db, "SELECT count(*) FROM document_content WHERE document_link_id=%s", (link_id,))
    assert rows == 0
