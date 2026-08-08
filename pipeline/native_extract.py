"""Native Office extraction — the fallback for when Document Intelligence
refuses a file that is actually fine.

**This is not the primary path and must not become one.** Measurement
(2026-08-06, 11 files) settled that: Document Intelligence wins on tables
and decisively on Arabic, and bills a `.docx` as one page. This module
exists for one situation only — DI *rejecting* an Office file — where the
alternative is not "slightly worse extraction" but **no content at all**.

The case that motivated it: `SDD_AI_Project_Charter_Generator_OnPrem.docx`,
749 KB, rejected with "Content is not supported: The input content is
corrupted or format is invalid." The file is not corrupted — `testzip()`
is clean, `word/document.xml` is present, and `python-docx` reads 136
paragraphs. Its archive carries zero-byte directory entries and lacks
`word/theme/theme1.xml`, i.e. it was repackaged by something other than
Word, and Azure's OOXML parser refuses it. Without a fallback that
document contributes nothing to the knowledge base.

**Output must match Document Intelligence's markdown conventions** —
HTML `<table>`, `#` headings, `<!-- PageBreak -->` — because the chunker
was tuned against those (`SOLUTION_ARCHITECTURE.md` §4.6). One dialect,
whichever extractor produced it.

There is no PDF fallback, deliberately. Native PDF text was measured as
unusable: our own generated Arabic PDF yielded 36 Arabic characters
against DI's 2,623, because the text comes out as Arabic presentation
forms in visual order. A fallback that silently produces garbage is worse
than an honest failure.
"""
from __future__ import annotations

import pathlib

# Extensions this module can handle. `.pdf` is absent on purpose — see
# the module docstring.
SUPPORTED_SUFFIXES = frozenset({".docx", ".pptx"})


def _library_version(distribution: str) -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(distribution)
    except PackageNotFoundError:            # pragma: no cover
        return "unknown"


def can_handle(path: pathlib.Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


# ---------------------------------------------------------------------------
# Shared table rendering
# ---------------------------------------------------------------------------

def _render_row(cells: list[tuple[str, int]]) -> str:
    parts = []
    for text, span in cells:
        attr = f' colSpan="{span}"' if span > 1 else ""
        parts.append(f"<td{attr}>{text}</td>")
    return "<tr>" + "".join(parts) + "</tr>"


def _docx_row_cells(row) -> list[tuple[str, int]]:
    """Collapse horizontally-merged cells instead of repeating them.

    python-docx returns one entry per *grid column*, so a header merged
    across four columns comes back as four identical cells — 48 spurious
    duplicates on one real document, measured against Document
    Intelligence's 0. Consecutive entries backed by the same underlying
    `<w:tc>` element are the same merged cell, so identity is the correct
    test here; comparing text would also collapse two genuinely distinct
    cells that happen to say the same thing.

    Vertical merges still repeat down the rows — python-docx models them
    the same way, and unpicking that needs the whole grid rather than one
    row. Recorded as a known limit rather than silently half-solved.
    """
    collapsed: list[tuple[str, int]] = []
    previous = None
    for cell in row.cells:
        if previous is not None and cell._tc is previous:
            text, span = collapsed[-1]
            collapsed[-1] = (text, span + 1)
            continue
        collapsed.append((cell.text.strip(), 1))
        previous = cell._tc
    return collapsed


# ---------------------------------------------------------------------------
# .docx
# ---------------------------------------------------------------------------

def extract_docx(path: pathlib.Path) -> tuple[str, str, int]:
    """Return (markdown, extractor_version, page_count).

    Walks the body element rather than `.paragraphs` and `.tables`, which
    are separate collections and lose their interleaving — a table would
    end up detached from the heading that introduces it.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    blocks: list[str] = []

    for child in document.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            blocks.append(_heading_or_text(paragraph, text))
        elif tag == "tbl":
            table = Table(child, document)
            rows = [_render_row(_docx_row_cells(row)) for row in table.rows]
            blocks.append("<table>\n" + "\n".join(rows) + "\n</table>")

    # Word stores no reliable page count without rendering, and Document
    # Intelligence itself reports 1 for a .docx of any length. Matching
    # that keeps page_count comparable across extractors.
    return "\n\n".join(blocks), _library_version("python-docx"), 1


def _heading_or_text(paragraph, text: str) -> str:
    """`paragraph.style` is None on documents not produced by Word.

    Not hypothetical: it is None on both our generated corpus files and on
    the repackaged SDD — i.e. on exactly the documents this fallback
    exists to rescue. An unguarded `paragraph.style.name` raises
    AttributeError and loses the document a second time.
    """
    import re

    style = paragraph.style
    name = (getattr(style, "name", None) or "") if style is not None else ""
    match = re.search(r"heading\s*(\d)", name.lower())
    if match:
        return "#" * min(int(match.group(1)), 6) + " " + text
    return text


# ---------------------------------------------------------------------------
# .pptx
# ---------------------------------------------------------------------------

def _is_group(shape) -> bool:
    """True for a grouped shape.

    Uses python-pptx's own enum rather than the literal 6. The number is
    correct today and is exactly the kind of thing that silently stops
    being correct — and a wrong answer here does not raise, it just quietly
    drops every piece of text nested inside a group.
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    return shape.shape_type == MSO_SHAPE_TYPE.GROUP


def _walk_shapes(shapes):
    """Recurse into groups — text inside a grouped shape is still text."""
    for shape in shapes:
        if _is_group(shape) and hasattr(shape, "shapes"):
            yield from _walk_shapes(shape.shapes)
        else:
            yield shape


def extract_pptx(path: pathlib.Path) -> tuple[str, str, int]:
    """Return (markdown, extractor_version, page_count).

    Slides are separated with `<!-- PageBreak -->`, which is what Document
    Intelligence emits, so the chunker's page tracking works identically
    whichever extractor produced the markdown.

    Speaker notes are included. Document Intelligence has no concept of
    them, so on this one point the fallback recovers something the primary
    path cannot — untested against a deck that actually has notes, since
    neither deck in this corpus does.
    """
    from pptx import Presentation

    presentation = Presentation(str(path))
    slides: list[str] = []

    for slide in presentation.slides:
        blocks: list[str] = []
        for shape in _walk_shapes(slide.shapes):
            if getattr(shape, "has_table", False):
                rows = [
                    _render_row([(cell.text.strip(), 1) for cell in row.cells])
                    for row in shape.table.rows
                ]
                blocks.append("<table>\n" + "\n".join(rows) + "\n</table>")
            elif getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    blocks.append(text)
        if slide.has_notes_slide:
            note = slide.notes_slide.notes_text_frame.text.strip()
            if note:
                blocks.append(f"<!-- SpeakerNotes: {note} -->")
        slides.append("\n\n".join(blocks))

    markdown = "\n\n<!-- PageBreak -->\n\n".join(slides)
    return markdown, _library_version("python-pptx"), len(slides)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_EXTRACTORS = {".docx": (extract_docx, "python-docx"),
               ".pptx": (extract_pptx, "python-pptx")}


def extract(path: pathlib.Path) -> tuple[str, str, str, int]:
    """Return (markdown, model_label, extractor_version, page_count).

    `model_label` goes into `document_content.ocr_model`, so which
    extractor produced any given row is always one query away — the
    fallback never hides behind the primary path's name.
    """
    suffix = path.suffix.lower()
    if suffix not in _EXTRACTORS:
        raise ValueError(f"no native extractor for {suffix or 'a file with no suffix'}")
    extractor, library = _EXTRACTORS[suffix]
    markdown, version, pages = extractor(path)
    return markdown, f"native:{library}", version, pages
