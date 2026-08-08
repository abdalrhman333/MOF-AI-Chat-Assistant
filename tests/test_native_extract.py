"""Tests for pipeline/native_extract.py — the Office fallback.

Two properties matter here and nothing else does:

  * **Output speaks Document Intelligence's markdown dialect**, because
    the chunker was tuned against it. A fallback that emits a different
    shape would silently change chunking for exactly the documents that
    already had trouble.
  * **It survives the documents it exists for.** These are files produced
    by tools other than Word — that is *why* Document Intelligence
    rejected them — so the ordinary assumptions (paragraphs have styles,
    tables are rectangular) do not hold.
"""
from __future__ import annotations

import pathlib

import pytest

from pipeline import native_extract

docx = pytest.importorskip("docx")
pptx = pytest.importorskip("pptx")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("a.docx", True), ("a.DOCX", True), ("a.pptx", True),
    ("a.pdf", False), ("a.xlsx", False), ("noextension", False),
])
def test_can_handle_only_office_formats(name, expected):
    """PDF must be absent: native PDF text was measured as unusable, and a
    fallback that silently produces garbage beats nothing at all only in
    the sense that it is worse."""
    assert native_extract.can_handle(pathlib.Path(name)) is expected


def test_extract_refuses_a_pdf_loudly(tmp_path):
    target = tmp_path / "x.pdf"
    target.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="no native extractor"):
        native_extract.extract(target)


# ---------------------------------------------------------------------------
# .docx
# ---------------------------------------------------------------------------

def build_docx(path: pathlib.Path):
    document = docx.Document()
    document.add_heading("Top Level", level=1)
    document.add_paragraph("Intro paragraph.")
    document.add_heading("عنوان فرعي", level=2)
    document.add_paragraph("نص عربي.")
    table = document.add_table(rows=2, cols=3)
    for column, value in enumerate(["A", "B", "C"]):
        table.cell(0, column).text = value
    for column, value in enumerate(["1", "2", "3"]):
        table.cell(1, column).text = value
    document.save(str(path))
    return path


def test_headings_use_hash_markers(tmp_path):
    markdown, _, _ = native_extract.extract_docx(build_docx(tmp_path / "a.docx"))
    assert "# Top Level" in markdown
    assert "## عنوان فرعي" in markdown


def test_tables_use_di_html_not_pipe_syntax(tmp_path):
    """The chunker detects tables as HTML <table>; real DI output never
    contains markdown pipe tables (SOLUTION_ARCHITECTURE.md 4.6)."""
    markdown, _, _ = native_extract.extract_docx(build_docx(tmp_path / "a.docx"))
    assert "<table>" in markdown and "</table>" in markdown
    assert "<td>A</td>" in markdown
    assert "|---" not in markdown


def test_document_order_is_preserved(tmp_path):
    """Walking .paragraphs and .tables separately would detach a table from
    the heading that introduces it."""
    markdown, _, _ = native_extract.extract_docx(build_docx(tmp_path / "a.docx"))
    assert markdown.index("# Top Level") < markdown.index("Intro paragraph.")
    assert markdown.index("## عنوان فرعي") < markdown.index("<table>")


def test_arabic_survives_intact(tmp_path):
    markdown, _, _ = native_extract.extract_docx(build_docx(tmp_path / "a.docx"))
    assert "نص عربي." in markdown


def test_page_count_matches_what_di_reports_for_docx(tmp_path):
    """DI reports 1 page for a .docx of any length; matching it keeps
    page_count comparable across extractors."""
    _, _, pages = native_extract.extract_docx(build_docx(tmp_path / "a.docx"))
    assert pages == 1


def test_a_paragraph_with_no_style_does_not_crash(tmp_path):
    """REGRESSION: `paragraph.style` is None on documents not produced by
    Word — true of both our generated corpus and the repackaged SDD, i.e.
    precisely the documents this fallback exists to rescue. An unguarded
    `.style.name` raises AttributeError and loses the document twice."""
    path = build_docx(tmp_path / "a.docx")
    document = docx.Document(str(path))
    document.paragraphs[0].style = None
    document.save(str(path))

    markdown, _, _ = native_extract.extract_docx(path)
    assert "Top Level" in markdown


def test_horizontally_merged_cells_are_not_repeated(tmp_path):
    """python-docx returns one entry per grid column, so a header merged
    across three columns comes back three times — 48 spurious duplicates on
    one real document against Document Intelligence's 0."""
    document = docx.Document()
    table = document.add_table(rows=2, cols=3)
    merged = table.cell(0, 0).merge(table.cell(0, 2))
    merged.text = "Merged Header"
    for column, value in enumerate(["x", "y", "z"]):
        table.cell(1, column).text = value
    path = tmp_path / "merged.docx"
    document.save(str(path))

    markdown, _, _ = native_extract.extract_docx(path)

    assert markdown.count("Merged Header") == 1
    assert 'colSpan="3"' in markdown
    assert markdown.count("<td") == 4          # 1 merged + 3 in the second row


# ---------------------------------------------------------------------------
# .pptx
# ---------------------------------------------------------------------------

def build_pptx(path: pathlib.Path, notes: str | None = None):
    presentation = pptx.Presentation()
    for index, title in enumerate(["First slide", "شريحة ثانية"]):
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = title
        if notes and index == 0:
            slide.notes_slide.notes_text_frame.text = notes
    presentation.save(str(path))
    return path


def test_slides_are_separated_by_di_pagebreak_markers(tmp_path):
    markdown, _, pages = native_extract.extract_pptx(build_pptx(tmp_path / "a.pptx"))
    assert markdown.count("<!-- PageBreak -->") == 1     # 2 slides -> 1 break
    assert pages == 2


def test_slide_text_including_arabic_is_extracted(tmp_path):
    markdown, _, _ = native_extract.extract_pptx(build_pptx(tmp_path / "a.pptx"))
    assert "First slide" in markdown
    assert "شريحة ثانية" in markdown


def test_speaker_notes_are_captured(tmp_path):
    """The one thing the fallback gets that Document Intelligence cannot —
    it has no concept of notes. Untested against a real deck, since neither
    deck in this corpus has any."""
    markdown, _, _ = native_extract.extract_pptx(
        build_pptx(tmp_path / "a.pptx", notes="Remember the budget question"))
    assert "<!-- SpeakerNotes: Remember the budget question -->" in markdown


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_extract_labels_which_library_produced_the_markdown(tmp_path):
    _, model, version, _ = native_extract.extract(build_docx(tmp_path / "a.docx"))
    assert model == "native:python-docx"
    assert version and version != "unknown"

    _, model, version, _ = native_extract.extract(build_pptx(tmp_path / "a.pptx"))
    assert model == "native:python-pptx"
    assert version and version != "unknown"
