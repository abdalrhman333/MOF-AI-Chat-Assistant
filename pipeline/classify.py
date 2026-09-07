"""Stage 1b — decide what kind of document each file actually is.

`document_links.doc_type` is what the knowledge-base page shows and what a
citation is labelled with ("Risk Register — page 3"), and until now nothing
ever set it for a newly discovered file. Stage 0 writes `'Unclassified'`
deliberately — **the filesystem does not know what a document is**, and
guessing from the filename would only work because we generated the
fictional filenames ourselves, which is exactly the demo-corpus coupling the
pipeline must not have.

So classification waits until there is text to read, which is here: after
OCR, before indexing.

**The model classifies; the label set is data.** `doc_type_labels_path`
points at a JSON list, so a different corpus brings its own vocabulary
without a code change — the same reasoning as the evaluation question set
and the document root. The model is constrained to that list plus
`Unclassified`, in a strict schema, so it cannot invent a category the
knowledge-base page has no idea how to show.

**It refuses rather than guesses.** A document whose type is genuinely
unclear stays `Unclassified`, because the KB page saying "Unclassified" is
true, and saying "Project Charter" about a spreadsheet is not.

⚠️ **It costs one completion per unclassified document, and never
re-classifies.** A row that already has a type is left alone, so this is
free on an unchanged corpus and cannot silently re-bill — the same contract
every other stage holds.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.classify --dry-run
    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.classify [--limit N]
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from dataclasses import dataclass, field

from pipeline.config import Settings, get_settings
from pipeline.db import admin_connection
from pipeline.locking import AlreadyRunning, optional_stage_lock

log = logging.getLogger(__name__)

UNCLASSIFIED = "Unclassified"

# How much of the document the model sees. The type is announced in the
# first page of every real document — a title, a header, a breadcrumb — so
# sending more would cost tokens to re-read the body it already decided from.
SAMPLE_CHARS = 2500

SYSTEM_PROMPT = """\
You classify one project document into exactly one type, for a government \
project-management platform. You are given the file name and the opening of \
the extracted text.

Rules:
1. Choose ONE label from the list supplied in the user message. Copy it \
exactly — do not invent, translate, pluralise or reword a label.
2. If the document does not clearly match any label, answer \
"{unclassified}". A wrong confident label is worse than an honest unknown: \
this value is shown to project managers and used to describe citations.
3. Judge by what the document IS, not what it mentions. A weekly report that \
discusses risks is a weekly report, not a risk register.
4. The text may be Arabic, English or both. The label you return is always \
from the supplied list, whatever the document's language.
"""

RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "document_type",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "doc_type": {"type": "string"},
                "confident": {"type": "boolean"},
            },
            "required": ["doc_type", "confident"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class ClassifyReport:
    dry_run: bool
    candidates: list[str] = field(default_factory=list)
    classified: list[tuple[str, str]] = field(default_factory=list)
    left_unclassified: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"stage": "classify", "dry_run": self.dry_run,
                "candidates": len(self.candidates),
                "classified": len(self.classified),
                "unclassified": len(self.left_unclassified),
                "failed": len(self.failed)}


def load_labels(settings: Settings | None = None) -> list[str]:
    """The allowed types. **Data, not code** — a different corpus brings its
    own list without touching this module."""
    settings = settings or get_settings()
    path = pathlib.Path(settings.classification.labels_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    labels = [str(x).strip() for x in raw.get("labels", []) if str(x).strip()]
    if not labels:
        raise RuntimeError(f"{path} lists no labels")
    return labels


def select_pending(cursor, limit: int | None = None) -> list[tuple]:
    """Documents that have text but no type yet.

    The `doc_type = 'Unclassified'` filter is the "already done" marker, the
    same shape every other stage uses: a row that has been classified is
    never revisited, so a re-run costs nothing.
    """
    rows = cursor.execute(
        "SELECT dl.id, dl.project_code, dl.filename, dc.markdown "
        "FROM document_links dl JOIN document_content dc "
        "  ON dc.document_link_id = dl.id "
        "WHERE dl.doc_type = %s "
        "ORDER BY dl.project_code, dl.filename",
        (UNCLASSIFIED,),
    ).fetchall()
    return rows[:limit] if limit else rows


def build_messages(filename: str, text: str, labels: list[str]) -> list[dict]:
    numbered = "\n".join(f"- {label}" for label in labels)
    return [
        {"role": "system",
         "content": SYSTEM_PROMPT.format(unclassified=UNCLASSIFIED)},
        {"role": "user",
         "content": (f"Allowed labels:\n{numbered}\n- {UNCLASSIFIED}\n\n"
                     f"File name: {filename}\n\n"
                     f"Opening of the document:\n{text[:SAMPLE_CHARS]}")},
    ]


def classify_one(filename: str, text: str, labels: list[str], llm,
                 settings: Settings) -> tuple[str, bool]:
    """Return `(label, confident)`. Never raises for an unusable answer."""
    response = llm.chat.completions.create(
        model=settings.models.llm_deployment,
        messages=build_messages(filename, text, labels),
        max_completion_tokens=64,
        temperature=0,
        response_format=RESPONSE_SCHEMA,
    )
    try:
        payload = json.loads((response.choices[0].message.content or "").strip())
    except (ValueError, TypeError):
        return UNCLASSIFIED, False

    label = str(payload.get("doc_type", "")).strip()
    confident = bool(payload.get("confident"))
    # The schema constrains the shape, not the vocabulary. A label outside
    # the list would put a category into the KB page that nothing else knows
    # about, so it is refused rather than stored.
    if label not in labels:
        return UNCLASSIFIED, False
    return label, confident


def run(settings: Settings | None = None, *, dry_run: bool = False,
        limit: int | None = None, llm=None, connection=None) -> ClassifyReport:
    settings = settings or get_settings()
    labels = load_labels(settings)
    report = ClassifyReport(dry_run=dry_run)

    def _work(cursor):
        nonlocal llm
        pending = select_pending(cursor, limit)
        report.candidates = [f"{p}/{f}" for _, p, f, _ in pending]
        if dry_run or not pending:
            return
        if llm is None:
            from Application.backend.answer import build_llm
            llm = build_llm(settings)
        for link_id, project, filename, markdown in pending:
            label = f"{project}/{filename}"
            try:
                doc_type, confident = classify_one(filename, markdown or "",
                                                   labels, llm, settings)
            except Exception as exc:                          # noqa: BLE001
                report.failed.append((label, f"{type(exc).__name__}: {exc}"))
                continue
            if doc_type == UNCLASSIFIED or not confident:
                report.left_unclassified.append(label)
                continue
            cursor.execute(
                "UPDATE document_links SET doc_type = %s WHERE id = %s",
                (doc_type, link_id))
            report.classified.append((label, doc_type))

    if connection is not None:
        _work(connection)
    else:
        with admin_connection() as conn:
            _work(conn)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        with optional_stage_lock("classify"):
            report = run(dry_run=args.dry_run, limit=args.limit)
    except AlreadyRunning as exc:
        print(f"Stage 1b classify — SKIPPED ({exc})")
        return 2

    if args.json:
        print(json.dumps(report.as_dict()))
        return 1 if report.failed else 0

    head = "Stage 1b classify — DRY RUN — nothing written" if args.dry_run \
        else "Stage 1b classify"
    print(f"{head}\n\n  candidates : {len(report.candidates)}")
    if not args.dry_run:
        print(f"  classified : {len(report.classified)}")
        for label, doc_type in report.classified[:20]:
            print(f"      {label} -> {doc_type}")
        if report.left_unclassified:
            print(f"  left unclassified : {len(report.left_unclassified)}"
                  f"   (unclear, and an honest unknown beats a wrong label)")
        if report.failed:
            print(f"  failed : {len(report.failed)}")
            for label, why in report.failed[:5]:
                print(f"      {label}: {why}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
