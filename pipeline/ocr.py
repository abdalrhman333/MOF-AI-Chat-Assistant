"""Stage 1 — OCR. Document Intelligence in, `document_content` out.

Every format goes to Azure Document Intelligence, including `.docx` and
`.pptx` whose text is technically readable straight from their XML. That
was **measured, not assumed** (2026-08-06, 11 files across every shape in
the corpus — full numbers in `STATUS_AND_ROADMAP.md` §10):

  * **PDF** — native extraction is unusable. Our own generated Arabic PDF
    yielded 36 Arabic characters against Document Intelligence's 2,623,
    because the text comes back as Arabic *presentation forms* in visual
    order — glyph codes, not words. A real 19-page Arabic BRD shared only
    80 tokens between the two methods. An English PDF came back with every
    letter doubled (`CChhaarrtteerr`). None of that raises an error; it
    would simply embed as garbage vectors.
  * **DOCX** — native works, but expands merged cells once per grid
    position: 48 duplicated cells on one real Arabic document where
    Document Intelligence produced 0. And it is billed as **one page per
    document** regardless of length, so the saving is about a cent each.
  * **PPTX** — genuinely a tie: content-identical, 928 of 928 tokens
    shared. Native is free and would also catch speaker notes, which
    Document Intelligence has no concept of. With two decks in the corpus
    that is ~$0.24, which does not justify a second code path and a second
    markdown dialect for the chunker to understand. Revisit if a real
    corpus turns out to be deck-heavy.

Two rules this stage lives by, both because **OCR is billed per page**:

  1. **Never re-OCR.** The presence of a `document_content` row is the
     "OCR done" marker (`INFRASTRUCTURE.md` §8). Stage 0 maintains it —
     dropping it when a file's content hash changes, keeping it otherwise
     — so this stage can trust it completely.
  2. **Commit per document, not per run.** Unlike Stage 0, whose work is
     cheap and repeatable, every row here cost real money. A batch that
     dies on document 40 must not roll back the 39 already paid for.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time
from dataclasses import dataclass, field

from pipeline.config import Settings, get_settings
from pipeline.db import admin_connection
from pipeline.locking import AlreadyRunning, optional_stage_lock


@dataclass(frozen=True)
class PendingDocument:
    id: int
    project_code: str
    filename: str
    file_path: str
    doc_type: str

    @property
    def label(self) -> str:
        return f"{self.project_code}/{self.filename}"


@dataclass(frozen=True)
class Extraction:
    markdown: str
    raw_response: dict
    page_count: int
    api_version: str
    model_id: str
    seconds: float
    # True when Document Intelligence refused the file and a native
    # extractor rescued it. Recorded so "how was this document read?" is a
    # query, never a guess — `ocr_model` carries the same fact in the row.
    via_fallback: bool = False


@dataclass
class OcrReport:
    dry_run: bool
    candidates: list[str] = field(default_factory=list)
    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    thin: list[tuple[str, int]] = field(default_factory=list)
    recovered: list[tuple[str, str]] = field(default_factory=list)
    oversized: list[tuple[str, int]] = field(default_factory=list)
    pages_billed: int = 0
    raw_bytes: int = 0
    seconds: float = 0.0


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

_SELECT = """
    SELECT dl.id, dl.project_code, dl.filename, dl.file_path, dl.doc_type
    FROM document_links dl
    LEFT JOIN document_content dc ON dc.document_link_id = dl.id
    WHERE dc.document_link_id IS NULL          -- the "OCR done" marker
      -- Casts are required, not cosmetic: a parameter that appears only in
      -- `IS NULL` gives Postgres nothing to infer a type from, and the
      -- query fails with AmbiguousParameter.
      AND (%(project)s::text IS NULL OR dl.project_code = %(project)s::text)
      AND (%(retry_failed)s::boolean OR dl.indexing_status <> 'failed')
    ORDER BY dl.project_code, dl.filename
"""


def select_pending(cursor, project: str | None, retry_failed: bool,
                   limit: int | None = None) -> list[PendingDocument]:
    """Documents with no OCR yet.

    Previously-failed documents are excluded by default. Including them
    would mean a genuinely broken file silently re-billing Document
    Intelligence on every single run — `--retry-failed` makes that a
    decision instead of a default.
    """
    cursor.execute(_SELECT, {"project": project, "retry_failed": retry_failed})
    rows = [PendingDocument(*record) for record in cursor.fetchall()]
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def build_client(settings: Settings):
    """Constructed here, and only from settings, so a provider swap is one
    file's worth of change (SOLUTION_ARCHITECTURE.md 4.1)."""
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    return DocumentIntelligenceClient(
        endpoint=settings.models.ocr_endpoint,
        credential=AzureKeyCredential(settings.models.ocr_key),
    )


def extract(path: pathlib.Path, settings: Settings, client) -> Extraction:
    """One document through Document Intelligence, as markdown.

    `output_content_format=MARKDOWN` is what makes the chunker's input
    contract hold — it is markdown-in by design, not "DI output"-in, so an
    on-prem extractor that emits markdown substitutes without a rewrite
    (SOLUTION_ARCHITECTURE.md 4.6).
    """
    from azure.ai.documentintelligence.models import DocumentContentFormat

    started = time.time()
    poller = client.begin_analyze_document(
        settings.models.ocr_model,
        body=path.read_bytes(),
        output_content_format=DocumentContentFormat.MARKDOWN,
        content_type="application/octet-stream",
    )
    result = poller.result()
    return Extraction(
        markdown=result.content or "",
        raw_response=result.as_dict(),
        page_count=len(result.pages or []),
        api_version=result.api_version,
        model_id=result.model_id,
        seconds=round(time.time() - started, 2),
    )


def extract_natively(path: pathlib.Path) -> Extraction:
    """Fallback for an Office file Document Intelligence refused.

    Only reached after DI has already failed, and only for the formats
    `native_extract` supports — a PDF has no honest fallback, because
    native PDF text was measured as garbage rather than merely worse.
    """
    from pipeline import native_extract

    started = time.time()
    markdown, model_label, version, pages = native_extract.extract(path)
    return Extraction(
        markdown=markdown,
        # No provider JSON exists here. Recording why the fallback ran
        # keeps `raw_response` meaningful rather than an empty object that
        # looks like a truncated API result.
        raw_response={"extractor": model_label, "extractor_version": version,
                      "note": "native fallback — Document Intelligence rejected this file"},
        page_count=pages,
        api_version=version,
        model_id=model_label,
        seconds=round(time.time() - started, 2),
        via_fallback=True,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _store(cursor, doc: PendingDocument, extraction: Extraction) -> None:
    import json

    cursor.execute(
        """
        INSERT INTO document_content
            (document_link_id, markdown, raw_response, page_count,
             ocr_model, ocr_api_version)
        VALUES (%s, %s, %s::jsonb, %s, %s, %s)
        ON CONFLICT (document_link_id) DO UPDATE SET
            markdown        = EXCLUDED.markdown,
            raw_response    = EXCLUDED.raw_response,
            page_count      = EXCLUDED.page_count,
            ocr_model       = EXCLUDED.ocr_model,
            ocr_api_version = EXCLUDED.ocr_api_version,
            extracted_at    = now()
        """,
        (doc.id, extraction.markdown, json.dumps(extraction.raw_response, ensure_ascii=False),
         extraction.page_count, extraction.model_id, extraction.api_version),
    )
    # A previous failure is now resolved. Leave indexing_status at
    # 'pending' — Stage 3 owns the transition to 'indexed'.
    cursor.execute(
        "UPDATE document_links SET indexing_status='pending', error_message=NULL "
        "WHERE id=%s", (doc.id,),
    )


def _try_fallback(path: pathlib.Path, primary_error: Exception) -> Extraction:
    """Rescue an Office file, or re-raise so the failure stays honest.

    The re-raised error mentions both attempts. Debugging a batch through
    `error_message` is much harder if the record only says the fallback
    failed and never mentions that Document Intelligence was tried first.
    """
    from pipeline import native_extract

    if not native_extract.can_handle(path):
        raise primary_error
    try:
        return extract_natively(path)
    except Exception as fallback_error:               # noqa: BLE001
        raise RuntimeError(
            f"Document Intelligence failed ({type(primary_error).__name__}: "
            f"{primary_error}); native fallback also failed "
            f"({type(fallback_error).__name__}: {fallback_error})"
        ) from fallback_error


def _record_failure(cursor, doc: PendingDocument, message: str) -> None:
    """Mark the failure WITHOUT writing document_content.

    Deliberate: the row's presence is the "OCR done" marker, so writing one
    for a failed extraction would permanently convince Stage 1 that this
    document is finished.
    """
    cursor.execute(
        "UPDATE document_links SET indexing_status='failed', error_message=%s WHERE id=%s",
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
    client=None,
    progress=None,
) -> OcrReport:
    """Run Stage 1.

    `connection` and `client` are injectable: tests supply a transaction
    they roll back and a stub extractor, so the whole write path — including
    the failure path — is exercised against the real schema without calling
    a paid API.
    """
    settings = settings or get_settings()
    if retry_failed is None:
        retry_failed = settings.ocr.retry_failed_by_default

    # Fail fast on a misconfigured root. Without this, every document fails
    # individually with FileNotFoundError and gets marked 'failed' — and
    # because failed documents are excluded by default, a single wrong
    # DOCUMENT_ROOT would turn the whole corpus into rows that need
    # --retry-failed to recover. One clear error beats 58 quiet ones.
    if not settings.document_root.is_dir():
        raise RuntimeError(
            f"DOCUMENT_ROOT does not exist or is not a directory: "
            f"{settings.document_root}. Set DOCUMENT_ROOT in infra/.env."
        )

    if connection is not None:
        # Caller owns the transaction — usually a test that will roll back.
        return _run_with(connection, settings, project, dry_run, limit,
                         retry_failed, client, progress, owns=False)
    with admin_connection() as conn:
        return _run_with(conn, settings, project, dry_run, limit,
                         retry_failed, client, progress, owns=True)


def _run_with(conn, settings: Settings, project: str | None, dry_run: bool,
              limit: int | None, retry_failed: bool, client, progress,
              owns: bool) -> OcrReport:
    report = OcrReport(dry_run=dry_run)
    started = time.time()

    cursor = conn.cursor()
    pending = select_pending(cursor, project, retry_failed, limit)
    report.candidates = [d.label for d in pending]

    if dry_run or not pending:
        report.seconds = round(time.time() - started, 2)
        return report

    client = client or build_client(settings)

    for doc in pending:
        try:
            # `file_path` is relative to DOCUMENT_ROOT, so the absolute path
            # is derived per environment rather than stored. resolve_document
            # refuses a stale absolute value with an actionable message
            # instead of silently building a nonsense path.
            path = settings.resolve_document(doc.file_path)
            if not path.is_file():
                # Caught before the API call — a missing file is free to
                # fail, and paying to discover it would be silly.
                raise FileNotFoundError(f"file not on disk: {path}")
            try:
                extraction = extract(path, settings, client)
            except Exception as primary_error:       # noqa: BLE001
                # Document Intelligence refused the file. For an Office
                # format that is recoverable — a valid .docx repackaged by
                # something other than Word is rejected as "corrupted"
                # while being perfectly readable. Anything else, including
                # every PDF, fails honestly.
                extraction = _try_fallback(path, primary_error)
                report.recovered.append((doc.label, f"{type(primary_error).__name__}"))
            _store(cursor, doc, extraction)
            report.succeeded.append(doc.label)
            report.pages_billed += 0 if extraction.via_fallback else extraction.page_count
            raw_size = len(str(extraction.raw_response))
            report.raw_bytes += raw_size
            if raw_size > settings.ocr.raw_response_warn_bytes:
                # Not truncated — the geometry is exactly what markdown
                # discards and what migration 010 exists to keep. Flagged so
                # unusual growth is seen here rather than as a disk surprise.
                report.oversized.append((doc.label, raw_size))
            if len(extraction.markdown) < settings.ocr.thin_result_chars:
                report.thin.append((doc.label, len(extraction.markdown)))
            if progress:
                progress(doc, extraction, None)
        except Exception as exc:                     # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            _record_failure(cursor, doc, message)
            report.failed.append((doc.label, message))
            if progress:
                progress(doc, None, message)
        finally:
            # Per document, not per run: every success above cost money and
            # must survive whatever the next document does. Only when we own
            # the connection — an injected one belongs to the caller, who is
            # usually a test that will roll back.
            if owns:
                conn.commit()

    report.seconds = round(time.time() - started, 2)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_report(report: OcrReport, list_files: bool = False) -> str:
    mode = "DRY RUN — nothing sent, nothing written" if report.dry_run else "APPLIED"
    lines = [
        f"Stage 1 OCR — {mode}",
        f"  candidates : {len(report.candidates)}",
    ]
    if report.dry_run or list_files:
        lines.extend(f"      {c}" for c in report.candidates)
    if not report.dry_run:
        lines += [
            f"  succeeded  : {len(report.succeeded)}",
            f"  failed     : {len(report.failed)}",
            f"  pages billed : {report.pages_billed}",
            f"  raw_response : {report.raw_bytes / 1_048_576:.1f} MB",
            f"  elapsed    : {report.seconds}s",
        ]
        if report.oversized:
            lines.append("")
            lines.append(
                f"  NOTE — {len(report.oversized)} document(s) stored an unusually large "
                f"provider response (over OCR_RAW_RESPONSE_WARN_BYTES). Nothing was "
                f"truncated — the geometry is what markdown discards and what the OCR "
                f"cache exists to keep:"
            )
            lines.extend(f"      {label} — {n / 1_048_576:.1f} MB"
                         for label, n in report.oversized)
        if report.recovered:
            lines.append("")
            lines.append(
                f"  RECOVERED — {len(report.recovered)} document(s) were rejected by "
                f"Document Intelligence and extracted natively instead. Billed 0 pages. "
                f"Check `ocr_model` to see which extractor produced them:"
            )
            lines.extend(f"      {label} — DI raised {err}" for label, err in report.recovered)
        if report.thin:
            lines.append("")
            lines.append(
                f"  NOTE — {len(report.thin)} document(s) returned very little text. "
                f"Stored and usable; flagged for a human to judge, NOT marked failed "
                f"(a scan whose content lives inside images can never improve, and "
                f"re-OCRing it every run would just re-bill):"
            )
            lines.extend(f"      {label} — {n} chars" for label, n in report.thin)
        if report.failed:
            lines.append("")
            lines.append("  FAILED (no document_content written, so a retry re-runs them):")
            lines.extend(f"      {label}\n          {msg}" for label, msg in report.failed)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Stage 1 — OCR pending documents via Azure Document Intelligence."
    )
    parser.add_argument("--project", help="Only this project code.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be sent. Costs nothing.")
    parser.add_argument("--limit", type=int,
                        help="Process at most N documents this run.")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Also retry documents previously marked failed.")
    parser.add_argument("--list", action="store_true", dest="list_files")
    args = parser.parse_args(argv)

    def progress(doc, extraction, error):
        if error:
            print(f"  [fail] {doc.label} — {error}")
        else:
            print(f"  [ ok ] {doc.label} — {extraction.page_count}p, "
                  f"{len(extraction.markdown):,} chars, {extraction.seconds}s")

    try:
        with optional_stage_lock("ocr", enabled=not args.dry_run):
            report = run(
                project=args.project, dry_run=args.dry_run, limit=args.limit,
                retry_failed=args.retry_failed or None, progress=progress,
            )
    except AlreadyRunning as exc:
        print(f"Stage 1 OCR — SKIPPED: {exc}", file=sys.stderr)
        return 2
    print(format_report(report, list_files=args.list_files))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
