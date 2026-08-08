"""Tests for pipeline/indexer.py — Stages 2+3.

Real schema inside a rolled-back transaction, with the embedding API and
the vector store both stubbed. The stubs record every call, because the
sharpest assertions here are about calls that must happen in a particular
ORDER — specifically that stale vectors are deleted *before* new ones are
written. Upserting alone looks correct and quietly leaves a shrunken
document's surplus chunks in the index, where they go on being retrieved.
"""
from __future__ import annotations

import dataclasses

import pytest

from pipeline.chunker import Chunk
from pipeline.config import get_settings
from pipeline.indexer import (
    build_points,
    plan_batches,
    point_id,
    run,
    select_pending,
)

TEST_CODE = "ZZ-INDEX-TEST"
DIMS = 1536


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class StubEmbeddings:
    def __init__(self, owner):
        self.owner = owner

    def create(self, model=None, input=None):
        self.owner.batches.append(list(input))
        if self.owner.fail_on and any(self.owner.fail_on in text for text in input):
            raise RuntimeError("RateLimitError: embedding request rejected")

        class Item:
            def __init__(self, index, embedding):
                self.index, self.embedding = index, embedding

        class Response:
            pass

        response = Response()
        # Deliberately returned OUT of order: the indexer must sort by the
        # API's own index rather than trusting arrival order, or every
        # vector silently pairs with the wrong payload.
        items = [Item(i, [float(i)] * DIMS) for i in range(len(input))]
        response.data = list(reversed(items))
        return response


class StubEmbedder:
    def __init__(self, fail_on: str | None = None):
        self.batches: list[list[str]] = []
        self.fail_on = fail_on
        self.embeddings = StubEmbeddings(self)


class StubQdrant:
    def __init__(self, existing=()):
        self.collections = set(existing)
        self.calls: list[tuple] = []
        self.points: dict[str, dict] = {}

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, name, vectors_config=None):
        self.calls.append(("create", name, vectors_config.size, vectors_config.distance))
        self.collections.add(name)

    def delete(self, collection_name=None, points_selector=None):
        condition = points_selector.filter.must[0]
        link_id = condition.match.value
        self.calls.append(("delete", collection_name, link_id))
        for pid, point in list(self.points.items()):
            if point["payload"]["document_link_id"] == link_id:
                del self.points[pid]

    def upsert(self, collection_name=None, points=None):
        self.calls.append(("upsert", collection_name, len(points)))
        for p in points:
            self.points[p.id] = {"vector": p.vector, "payload": p.payload,
                                 "collection": collection_name}

    def count_for(self, link_id):
        return sum(1 for p in self.points.values()
                   if p["payload"]["document_link_id"] == link_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MARKDOWN = """# مقدمة

هذا نص عربي طويل بما يكفي لإنتاج جزء واحد على الأقل من المحتوى القابل للفهرسة.
""" + ("جملة إضافية لزيادة عدد الرموز في هذا المستند. " * 60) + """

## Financial Summary

SAR 4.2M was allocated to the integration workstream during the period.
""" + ("Additional narrative text to push this section past the minimum. " * 60)


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
            (TEST_CODE, "مشروع الفهرسة", "Indexer test", "testing"),
        )
        yield conn
    finally:
        conn.rollback()
        conn.close()


def add_document(db, name="charter.docx", markdown=MARKDOWN, status="pending"):
    link_id = db.execute(
        "INSERT INTO document_links (project_code, filename, file_path, doc_type, "
        "indexing_status) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (TEST_CODE, name, rf"D:\x\{name}", "Project Charter", status),
    ).fetchone()[0]
    db.execute("INSERT INTO document_content (document_link_id, markdown) VALUES (%s,%s)",
               (link_id, markdown))
    return link_id


def go(db, embedder=None, qdrant=None, **kwargs):
    return run(project=TEST_CODE, settings=get_settings(), connection=db,
               embedder=embedder or StubEmbedder(), qdrant=qdrant or StubQdrant(),
               **kwargs)


def one(db, sql, params=()):
    return db.execute(sql, params).fetchone()


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def test_point_ids_are_deterministic():
    """Re-running must upsert in place, not duplicate."""
    assert point_id(42, 3) == point_id(42, 3)


def test_point_ids_differ_per_document_and_per_chunk():
    assert point_id(42, 3) != point_id(42, 4)
    assert point_id(42, 3) != point_id(43, 3)


def chunk(index=0, tokens=100, text="t"):
    return Chunk(text=text, body=text, page=1, section="", chunk_index=index,
                 token_count=tokens, char_start=0, char_end=1)


def test_batches_respect_the_count_cap():
    settings = dataclasses.replace(
        get_settings(),
        indexing=dataclasses.replace(get_settings().indexing, batch_size=4,
                                     max_batch_tokens=10_000))
    batches = plan_batches([chunk(i, 10) for i in range(9)], settings)
    assert [len(b) for b in batches] == [4, 4, 1]


def test_batches_respect_the_token_cap():
    """A few table-heavy chunks blow the token budget long before the count
    cap — either limit has to be able to close a batch."""
    settings = dataclasses.replace(
        get_settings(),
        indexing=dataclasses.replace(get_settings().indexing, batch_size=100,
                                     max_batch_tokens=250))
    batches = plan_batches([chunk(i, 100) for i in range(5)], settings)
    assert [len(b) for b in batches] == [2, 2, 1]


def test_an_oversized_single_chunk_still_gets_its_own_batch():
    """It must not be silently dropped for exceeding the budget alone."""
    settings = dataclasses.replace(
        get_settings(),
        indexing=dataclasses.replace(get_settings().indexing, batch_size=10,
                                     max_batch_tokens=50))
    batches = plan_batches([chunk(0, 500)], settings)
    assert [len(b) for b in batches] == [1]


def test_payload_matches_the_documented_contract():
    """SOLUTION_ARCHITECTURE.md §4.7 — changing this means re-indexing
    everything, so it is pinned here."""
    from pipeline.indexer import PendingDocument

    doc = PendingDocument(7, "PRJ-03", "a.docx", "Risk Register", "Platform", "x")
    points = build_points(doc, [chunk(0)], [[0.0] * DIMS])
    payload = points[0].payload
    assert set(payload) == {
        "project_code", "document_link_id", "filename", "doc_type",
        "text", "page", "section", "chunk_index", "char_start", "char_end", "has_table",
    }
    assert payload["project_code"] == "PRJ-03"      # redundant by design (§4.2)
    assert payload["document_link_id"] == 7


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def test_already_indexed_documents_are_skipped(db):
    add_document(db, status="indexed")
    report = go(db)
    assert report.candidates == []


def test_failed_documents_are_retried_by_default(db):
    """Opposite of the OCR stage, deliberately: re-embedding the whole
    corpus costs under a cent, so hiding failures serves nobody."""
    add_document(db, name="broken.docx", status="failed")
    report = go(db)
    assert report.candidates == [f"{TEST_CODE}/broken.docx"]


def test_skip_failed_opts_out(db):
    add_document(db, name="broken.docx", status="failed")
    report = go(db, retry_failed=False)
    assert report.candidates == []


def test_a_document_without_ocr_is_not_a_candidate(db):
    """No document_content row means Stage 1 hasn't run for it."""
    db.execute("INSERT INTO document_links (project_code, filename, file_path, doc_type) "
               "VALUES (%s,%s,%s,%s)", (TEST_CODE, "no-ocr.docx", r"D:\x", "Charter"))
    cursor = db.cursor()
    assert select_pending(cursor, TEST_CODE, retry_failed=True) == []


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_indexing_writes_vectors_and_marks_the_row(db):
    link_id = add_document(db)
    qdrant = StubQdrant()

    report = go(db, qdrant=qdrant)

    assert len(report.indexed) == 1
    assert report.chunks > 1                       # the fixture is long enough to split
    status, chunk_count, indexed_at, error = one(db,
        "SELECT indexing_status, chunk_count, indexed_at, error_message "
        "FROM document_links WHERE id=%s", (link_id,))
    assert status == "indexed"
    assert chunk_count == report.chunks
    assert indexed_at is not None and error is None
    assert qdrant.count_for(link_id) == report.chunks


def test_each_project_gets_its_own_collection(db):
    """Isolation is topological: a session scoped to one project cannot
    query another's vectors, because there is no shared index (§4.2)."""
    add_document(db)
    qdrant = StubQdrant()
    go(db, qdrant=qdrant)
    created = [c for c in qdrant.calls if c[0] == "create"]
    assert created[0][1] == f"project_{TEST_CODE}"
    assert created[0][2] == 1536                   # fixed by the embedding model


def test_an_existing_collection_is_not_recreated(db):
    add_document(db)
    qdrant = StubQdrant(existing={f"project_{TEST_CODE}"})
    report = go(db, qdrant=qdrant)
    assert report.collections_created == []
    assert not [c for c in qdrant.calls if c[0] == "create"]


def test_the_breadcrumb_is_embedded_not_the_bare_body(db):
    """"SAR 4.2M was allocated" is nearly meaningless as a vector; the
    breadcrumb is what encodes what the text is about (§4.6)."""
    add_document(db)
    embedder = StubEmbedder()
    go(db, embedder=embedder)
    sent = [text for batch in embedder.batches for text in batch]
    assert any("Project Charter" in text for text in sent)   # doc_title breadcrumb


def test_vectors_are_realigned_by_the_api_index_not_arrival_order(db):
    """The stub returns results reversed on purpose. Trusting arrival order
    pairs every vector with the wrong payload — which no error would ever
    report, and which makes retrieval confidently wrong."""
    link_id = add_document(db)
    qdrant = StubQdrant()
    go(db, qdrant=qdrant)
    for point in qdrant.points.values():
        expected = float(point["payload"]["chunk_index"] % get_settings().indexing.batch_size)
        assert point["vector"][0] == expected


# ---------------------------------------------------------------------------
# Re-indexing — where stale data hides
# ---------------------------------------------------------------------------

def test_stale_vectors_are_deleted_before_new_ones_are_written(db):
    link_id = add_document(db)
    qdrant = StubQdrant()
    go(db, qdrant=qdrant)

    order = [c[0] for c in qdrant.calls]
    assert order.index("delete") < order.index("upsert")


def test_reindexing_a_shrunken_document_leaves_no_orphans(db):
    """The bug this prevents: an edited document yielding FEWER chunks
    leaves its surplus points in the index, still retrievable, with nothing
    reporting an error. Upserting alone would not catch it."""
    link_id = add_document(db)
    qdrant = StubQdrant()
    go(db, qdrant=qdrant)
    before = qdrant.count_for(link_id)
    assert before > 1

    db.execute("UPDATE document_content SET markdown=%s WHERE document_link_id=%s",
               ("# Short\n\n" + ("tiny. " * 40), link_id))
    db.execute("UPDATE document_links SET indexing_status='pending' WHERE id=%s", (link_id,))

    go(db, qdrant=qdrant)
    after = qdrant.count_for(link_id)
    assert after < before
    assert qdrant.count_for(link_id) == after      # no leftovers from the longer version


def test_running_twice_does_not_duplicate_vectors(db):
    link_id = add_document(db)
    qdrant = StubQdrant()
    go(db, qdrant=qdrant)
    first = qdrant.count_for(link_id)

    db.execute("UPDATE document_links SET indexing_status='pending' WHERE id=%s", (link_id,))
    go(db, qdrant=qdrant)

    assert qdrant.count_for(link_id) == first


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------

def test_a_failure_records_the_error_and_clears_stale_success_fields(db):
    link_id = add_document(db)
    db.execute("UPDATE document_links SET indexed_at=now(), chunk_count=99 WHERE id=%s",
               (link_id,))

    report = go(db, embedder=StubEmbedder(fail_on="Project Charter"))

    assert report.indexed == []
    assert len(report.failed) == 1
    status, indexed_at, chunk_count, error = one(db,
        "SELECT indexing_status, indexed_at, chunk_count, error_message "
        "FROM document_links WHERE id=%s", (link_id,))
    assert status == "failed"
    assert (indexed_at, chunk_count) == (None, None)
    assert "RateLimitError" in error


def test_one_failure_does_not_stop_the_batch(db):
    add_document(db, name="good.docx")
    add_document(db, name="bad.docx", markdown="# POISONED\n\n" + ("x " * 400))
    qdrant = StubQdrant()

    report = go(db, embedder=StubEmbedder(fail_on="POISONED"), qdrant=qdrant)

    assert [label for label, _ in report.indexed] == [f"{TEST_CODE}/good.docx"]
    assert len(report.failed) == 1


def test_a_failure_writes_no_vectors(db):
    """Embedding is what fails, so nothing should reach the store at all."""
    link_id = add_document(db)
    qdrant = StubQdrant()
    go(db, embedder=StubEmbedder(fail_on="Project Charter"), qdrant=qdrant)
    assert qdrant.count_for(link_id) == 0


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_counts_chunks_without_embedding_or_writing(db):
    """Chunking is free, so the dry run reports the REAL token count — the
    number that decides what embedding will cost."""
    link_id = add_document(db)
    embedder, qdrant = StubEmbedder(), StubQdrant()

    report = go(db, embedder=embedder, qdrant=qdrant, dry_run=True)

    assert report.chunks > 0
    assert report.tokens > 0
    assert embedder.batches == []
    assert qdrant.calls == []
    status, = one(db, "SELECT indexing_status FROM document_links WHERE id=%s", (link_id,))
    assert status == "pending"


def test_limit_caps_the_run(db):
    for i in range(3):
        add_document(db, name=f"doc{i}.docx")
    report = go(db, limit=2)
    assert len(report.indexed) == 2
