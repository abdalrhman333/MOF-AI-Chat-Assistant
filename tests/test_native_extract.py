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


class StubParagraph:
    """A paragraph whose style python-docx could not resolve.

    Written as a stub rather than a saved-and-reloaded file on purpose:
    setting `.style = None` and re-opening does NOT reproduce the
    condition, because python-docx resolves the style back to the default
    on load. A mutation removing the guard survived until this test
    existed — the file-based version could never have caught it.
    """

    def __init__(self, style):
        self.style = style


class StubStyle:
    def __init__(self, name):
        self.name = name


def test_a_paragraph_whose_style_is_none_does_not_crash():
    """REGRESSION: `paragraph.style` is None on documents not produced by
    Word — true of both our generated corpus and the repackaged SDD, i.e.
    precisely the documents this fallback exists to rescue. An unguarded
    `.style.name` raises AttributeError and loses the document twice."""
    assert native_extract._heading_or_text(StubParagraph(None), "Body") == "Body"


def test_a_style_whose_name_is_none_does_not_crash():
    assert native_extract._heading_or_text(StubParagraph(StubStyle(None)), "Body") == "Body"


def test_a_resolvable_heading_style_still_becomes_a_heading():
    """The guard must not swallow the normal case along with the odd one."""
    para = StubParagraph(StubStyle("Heading 2"))
    assert native_extract._heading_or_text(para, "Title") == "## Title"


def test_heading_depth_is_clamped_to_six():
    para = StubParagraph(StubStyle("Heading 9"))
    assert native_extract._heading_or_text(para, "Deep") == "###### Deep"


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


def test_distinct_cells_with_identical_text_are_not_merged(tmp_path):
    """Merging must key off the underlying <w:tc> element, not the text.

    A risk register with three separate columns all reading 'مرتفع' has
    three real cells, not one spanning three. Collapsing by text would
    silently destroy the row's shape — and a mutation that did exactly
    that survived until this test existed, because the other table test
    happens to use distinct text everywhere.
    """
    document = docx.Document()
    table = document.add_table(rows=1, cols=3)
    for column in range(3):
        table.cell(0, column).text = "مرتفع"
    path = tmp_path / "same_text.docx"
    document.save(str(path))

    markdown, _, _ = native_extract.extract_docx(path)

    assert markdown.count("<td") == 3          # three cells, not one
    assert "colSpan" not in markdown           # nothing was merged


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
