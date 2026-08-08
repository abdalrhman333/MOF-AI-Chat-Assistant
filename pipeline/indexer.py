"""Stages 2 and 3 — chunk, embed, and index into per-project collections.

**One module, not two.** `NEXT_BUILD_PIPELINE.md` describes chunk+embed and
index as separate stages, and that split does not survive contact with what
the work actually costs: chunking is free and deterministic, while embedding
is billed per token. Ending a stage holding 678 vectors that nothing has
stored yet means either carrying them across a process boundary in memory or
inventing a temporary table whose only purpose is to bridge two modules. An
embedding that isn't persisted is money spent and thrown away, so the paid
step and the durable write belong in the same per-document transaction.

Isolation is the whole point of the output shape. Each project gets its own
Qdrant collection (`project_<code>`), so a session scoped to one project
**cannot** query another's vectors — there is no shared index to misfilter
(`SOLUTION_ARCHITECTURE.md` §4.2). `project_code` is carried in every
payload as well, redundantly and on purpose: a wrong-collection query still
cannot pass as correct.

Three correctness rules, each of which is a silent-wrong-answer bug if
broken:

  1. **Point IDs are deterministic** — `uuid5` over
     `document_link_id:chunk_index`. Re-running upserts in place instead of
     duplicating, which is what makes "idempotent" true for vectors and not
     just for rows.
  2. **Old vectors are deleted before new ones are written.** If an edited
     document produces *fewer* chunks than before, the surplus points from
     the previous version would survive and keep being retrieved — stale
     content answering current questions, with nothing anywhere reporting
     an error.
  3. **`chunk.text` is embedded, not `chunk.body`.** The breadcrumb is what
     makes the vector mean anything: "SAR 4.2M was allocated" is nearly
     meaningless, "Budget Report > Financial Summary > SAR 4.2M was
     allocated" encodes what the text is about (§4.6). The payload still
     carries `body` for display, so citations read cleanly.
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass, field

from pipeline.chunker import Chunk, chunk_markdown
from pipeline.config import Settings, get_settings
from pipeline.db import admin_connection
from pipeline.locking import AlreadyRunning, optional_stage_lock

# Fixed namespace: point IDs must stay stable across runs and machines, so
# this must never be regenerated. Changing it orphans every existing vector.
POINT_NAMESPACE = uuid.UUID("6f3e7c02-2a4b-5d18-9c77-1b0e5a9f4d33")


@dataclass(frozen=True)
class PendingDocument:
    id: int
    project_code: str
    filename: str
    doc_type: str
    project_name: str
    markdown: str

    @property
    def label(self) -> str:
        return f"{self.project_code}/{self.filename}"

    @property
    def doc_title(self) -> str:
        """Feeds the breadcrumb prefixed to every chunk's embedded text."""
        return f"{self.doc_type} — {self.project_name}"


@dataclass
class IndexReport:
    dry_run: bool
    candidates: list[str] = field(default_factory=list)
    indexed: list[tuple[str, int]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    collections_created: list[str] = field(default_factory=list)
    stale_deleted: int = 0
    chunks: int = 0
    tokens: int = 0
    seconds: float = 0.0


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

_SELECT = """
    SELECT dl.id, dl.project_code, dl.filename, dl.doc_type,
           COALESCE(p.name_en, p.name_ar), dc.markdown
    FROM document_content dc
    JOIN document_links dl ON dl.id = dc.document_link_id
    JOIN projects p        ON p.code = dl.project_code
    WHERE dl.indexing_status <> 'indexed'
      -- Casts required: a parameter appearing only in IS NULL gives
      -- Postgres nothing to infer a type from (AmbiguousParameter).
      AND (%(project)s::text IS NULL OR dl.project_code = %(project)s::text)
      AND (%(retry_failed)s::boolean OR dl.indexing_status <> 'failed')
    ORDER BY dl.project_code, dl.filename
"""


def select_pending(cursor, project: str | None, retry_failed: bool,
                   limit: int | None = None) -> list[PendingDocument]:
    """Documents that have OCR but are not indexed yet.

    Stage 0 resets a changed document to 'pending' and drops its OCR cache,
    so re-indexing after an edit needs no special handling here.
    """
    cursor.execute(_SELECT, {"project": project, "retry_failed": retry_failed})
    rows = [PendingDocument(*record) for record in cursor.fetchall()]
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def build_embedder(settings: Settings):
    from openai import AzureOpenAI

    return AzureOpenAI(
        azure_endpoint=settings.models.embedding_endpoint,
        api_key=settings.models.embedding_key,
        api_version=settings.models.embedding_api_version,
    )


def plan_batches(chunks: list[Chunk], settings: Settings) -> list[list[Chunk]]:
    """Group chunks into requests under BOTH the count and token caps.

    Either limit can bite first — a handful of table-heavy chunks can blow
    the token budget long before the count cap, and a long tail of tiny
    chunks does the reverse.
    """
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_tokens = 0
    for chunk in chunks:
        too_many = len(current) >= settings.indexing.batch_size
        too_big = current and current_tokens + chunk.token_count > settings.indexing.max_batch_tokens
        if too_many or too_big:
            batches.append(current)
            current, current_tokens = [], 0
        current.append(chunk)
        current_tokens += chunk.token_count
    if current:
        batches.append(current)
    return batches


def embed(chunks: list[Chunk], settings: Settings, client) -> list[list[float]]:
    """Embed `chunk.text` — breadcrumb included. See module docstring rule 3."""
    vectors: list[list[float]] = []
    for batch in plan_batches(chunks, settings):
        response = client.embeddings.create(
            model=settings.models.embedding_deployment,
            input=[chunk.text for chunk in batch],
        )
        # The API returns results in request order, but it also returns an
        # explicit index. Trusting order silently misaligns every vector
        # with its payload if that ever stops being true, and a
        # misaligned vector store retrieves confidently wrong passages.
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(batch):
            raise RuntimeError(
                f"embedding count mismatch: sent {len(batch)}, got {len(ordered)}"
            )
        vectors.extend(item.embedding for item in ordered)
    return vectors


# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------

def build_qdrant(settings: Settings):
    from qdrant_client import QdrantClient

    return QdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key)


def point_id(document_link_id: int, chunk_index: int) -> str:
    """Stable across runs and machines, so a re-run upserts in place."""
    return str(uuid.uuid5(POINT_NAMESPACE, f"{document_link_id}:{chunk_index}"))


def ensure_collection(qdrant, settings: Settings, project_code: str) -> tuple[str, bool]:
    """Return (collection_name, was_created).

    Vector size and distance come from settings, never literals — the size
    is fixed by the embedding model (1536), and changing the model means
    recreating every collection (`SOLUTION_ARCHITECTURE.md` §4.1).
    """
    from qdrant_client import models

    name = settings.qdrant.collection_for(project_code)
    if qdrant.collection_exists(name):
        return name, False
    qdrant.create_collection(
        name,
        vectors_config=models.VectorParams(
            size=settings.qdrant.vector_size,
            distance=models.Distance[settings.qdrant.distance.upper()],
        ),
    )
    return name, True


def delete_existing(qdrant, collection: str, document_link_id: int) -> None:
    """Drop this document's previous vectors before writing new ones.

    Upserting alone is not enough: identical chunk indices overwrite, but a
    document that now yields fewer chunks leaves its surplus points behind,
    and they keep being retrieved. See module docstring rule 2.
    """
    from qdrant_client import models

    qdrant.delete(
        collection_name=collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[models.FieldCondition(
                key="document_link_id",
                match=models.MatchValue(value=document_link_id),
            )])
        ),
    )


def build_points(doc: PendingDocument, chunks: list[Chunk], vectors: list[list[float]]):
    """Payload schema is a CONTRACT — SOLUTION_ARCHITECTURE.md §4.7.
    Changing it means re-indexing the entire corpus."""
    from qdrant_client import models

    return [
        models.PointStruct(
            id=point_id(doc.id, chunk.chunk_index),
            vector=vector,
            payload=chunk.to_payload(
                project_code=doc.project_code,   # redundant by design (§4.2)
                document_link_id=doc.id,
                filename=doc.filename,
                doc_type=doc.doc_type,
            ),
        )
        for chunk, vector in zip(chunks, vectors)
    ]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _mark_indexed(cursor, doc: PendingDocument, chunk_count: int) -> None:
    cursor.execute(
        "UPDATE document_links SET indexing_status='indexed', indexed_at=now(), "
        "chunk_count=%s, error_message=NULL WHERE id=%s",
        (chunk_count, doc.id),
    )


def _mark_failed(cursor, doc: PendingDocument, message: str) -> None:
    cursor.execute(
        "UPDATE document_links SET indexing_status='failed', error_message=%s, "
        "indexed_at=NULL, chunk_count=NULL WHERE id=%s",
        (message[:2000], doc.id),
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(
    project: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    retry_failed: bool | None = None,
    settings: Settings | None = None,
    connection=None,
    embedder=None,
    qdrant=None,
    progress=None,
) -> IndexReport:
    """Run Stages 2+3.

    `connection`, `embedder` and `qdrant` are injectable so tests can use a
    rolled-back transaction and stubs — the whole write path is exercised
    against the real schema without a paid call or a live vector store.
    """
    settings = settings or get_settings()
    if retry_failed is None:
        retry_failed = settings.indexing.retry_failed_by_default

    if connection is not None:
        return _run_with(connection, settings, project, dry_run, limit,
                         retry_failed, embedder, qdrant, progress, owns=False)
    with admin_connection() as conn:
        return _run_with(conn, settings, project, dry_run, limit,
                         retry_failed, embedder, qdrant, progress, owns=True)


def _run_with(conn, settings, project, dry_run, limit, retry_failed,
              embedder, qdrant, progress, owns: bool) -> IndexReport:
    report = IndexReport(dry_run=dry_run)
    started = time.time()

    cursor = conn.cursor()
    pending = select_pending(cursor, project, retry_failed, limit)
    report.candidates = [d.label for d in pending]
    if not pending:
        report.seconds = round(time.time() - started, 2)
        return report

    # Chunking is free, so a dry run reports real chunk and token counts
    # rather than an estimate — the numbers that decide what embedding costs.
    if dry_run:
        for doc in pending:
            chunks = _chunk(doc, settings)
            report.chunks += len(chunks)
            report.tokens += sum(c.token_count for c in chunks)
        report.seconds = round(time.time() - started, 2)
        return report

    embedder = embedder or build_embedder(settings)
    qdrant = qdrant or build_qdrant(settings)

    for doc in pending:
        try:
            chunks = _chunk(doc, settings)
            if not chunks:
                raise RuntimeError("chunker produced no chunks from stored markdown")

            collection, created = ensure_collection(qdrant, settings, doc.project_code)
            if created:
                report.collections_created.append(collection)

            vectors = embed(chunks, settings, embedder)
            if len(vectors) != len(chunks):
                raise RuntimeError(
                    f"vector/chunk mismatch: {len(vectors)} vs {len(chunks)}"
                )

            delete_existing(qdrant, collection, doc.id)
            report.stale_deleted += 1
            qdrant.upsert(collection_name=collection,
                          points=build_points(doc, chunks, vectors))

            _mark_indexed(cursor, doc, len(chunks))
            report.indexed.append((doc.label, len(chunks)))
            report.chunks += len(chunks)
            report.tokens += sum(c.token_count for c in chunks)
            if progress:
                progress(doc, len(chunks), None)
        except Exception as exc:                     # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            _mark_failed(cursor, doc, message)
            report.failed.append((doc.label, message))
            if progress:
                progress(doc, 0, message)
        finally:
            # Per document: every vector above cost money to produce.
            if owns:
                conn.commit()

    report.seconds = round(time.time() - started, 2)
    return report


def _chunk(doc: PendingDocument, settings: Settings) -> list[Chunk]:
    return chunk_markdown(
        doc.markdown,
        doc_title=doc.doc_title,
        target_tokens=settings.chunking.target_tokens,
        overlap_tokens=settings.chunking.overlap_tokens,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_report(report: IndexReport, list_files: bool = False) -> str:
    mode = "DRY RUN — nothing embedded, nothing written" if report.dry_run else "APPLIED"
    lines = [f"Stage 2+3 index — {mode}", f"  candidates : {len(report.candidates)}"]
    if report.dry_run or list_files:
        lines.extend(f"      {c}" for c in report.candidates)
    lines.append(f"  chunks     : {report.chunks}")
    lines.append(f"  tokens     : {report.tokens:,}")
    if not report.dry_run:
        lines += [
            f"  indexed    : {len(report.indexed)}",
            f"  failed     : {len(report.failed)}",
            f"  elapsed    : {report.seconds}s",
        ]
        if report.collections_created:
            lines.append(f"  collections created: {', '.join(report.collections_created)}")
        if report.failed:
            lines.append("")
            lines.append("  FAILED (status='failed', error recorded; re-run retries them):")
            lines.extend(f"      {label}\n          {msg}" for label, msg in report.failed)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Stages 2+3 — chunk, embed, and index into per-project collections."
    )
    parser.add_argument("--project", help="Only this project code.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Chunk and count only. Embeds nothing, writes nothing.")
    parser.add_argument("--limit", type=int, help="Process at most N documents.")
    parser.add_argument("--skip-failed", action="store_true",
                        help="Skip documents already marked failed (they are retried "
                             "by default, because re-embedding is nearly free).")
    parser.add_argument("--list", action="store_true", dest="list_files")
    args = parser.parse_args(argv)

    def progress(doc, chunk_count, error):
        if error:
            print(f"  [fail] {doc.label} — {error}")
        else:
            print(f"  [ ok ] {doc.label} — {chunk_count} chunks")

    try:
        with optional_stage_lock("index", enabled=not args.dry_run):
            report = run(project=args.project, dry_run=args.dry_run, limit=args.limit,
                         retry_failed=False if args.skip_failed else None,
                         progress=progress)
    except AlreadyRunning as exc:
        print(f"Stage 2+3 index — SKIPPED: {exc}", file=sys.stderr)
        return 2
    print(format_report(report, list_files=args.list_files))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
