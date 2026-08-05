"""Tests for pipeline/chunker.py.

Two layers, deliberately:

  * Synthetic cases pin each rule precisely and read as documentation of
    the intended behaviour.
  * Real Document Intelligence output (tests/fixtures/) guards the lesson
    that produced this chunker in the first place — real markdown did not
    match what the design assumed. Fixtures cover the three shapes that
    actually broke things: a table-heavy document with NO headings, a
    mixed-language one with headings, and a scanned document full of
    empty <figure> blocks.

Several tests are marked REGRESSION: they cover bugs that were found by
exercising the code rather than reading it, and that inspection had
already missed once.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from pipeline.chunker import (
    DEFAULT_TARGET_TOKENS,
    MIN_CHUNK_TOKENS,
    chunk_markdown,
    count_tokens,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def table_with_rows(n: int) -> str:
    """A table with a distinctive header and n body rows."""
    rows = "\n".join(
        f"<tr><td>R-{i:02d}</td><td>description number {i} " + ("padding " * 12) + "</td></tr>"
        for i in range(n)
    )
    return ("<table>\n<tr><th>RiskID</th><th>Detail</th></tr>\n" + rows + "\n</table>")


# ---------------------------------------------------------------------
# Degenerate input
# ---------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "\n\n\n"])
def test_empty_input_produces_no_chunks(text):
    assert chunk_markdown(text) == []


def test_short_document_is_one_chunk():
    chunks = chunk_markdown("Just a single short paragraph.")
    assert len(chunks) == 1
    assert "single short paragraph" in chunks[0].body


# ---------------------------------------------------------------------
# Rule 1 — headings drive the split when present
# ---------------------------------------------------------------------

def test_level_two_headings_start_new_chunks():
    # Sections must clear MIN_CHUNK_TOKENS, or the merge rule will
    # (correctly) fold them back together and this would test nothing.
    md = ("## Section A\n\n" + "alpha content here. " * 60 +
          "\n\n## Section B\n\n" + "bravo content here. " * 60)
    chunks = chunk_markdown(md)
    assert len(chunks) == 2
    assert "alpha" in chunks[0].body and "bravo" not in chunks[0].body


def test_deep_headings_become_breadcrumb_not_a_split():
    """### is context, not a boundary — otherwise every short subsection
    becomes its own undersized chunk."""
    md = "## Budget\n\n### Q3\n\nSome content here about the third quarter."
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert "Budget" in chunks[0].section and "Q3" in chunks[0].section


def test_doc_title_and_section_prefix_embedded_text_only():
    """The breadcrumb is what makes a vector meaningful, but it must not
    pollute the text shown in a citation."""
    md = "## Financial Summary\n\nSAR 4.2M was allocated."
    chunk = chunk_markdown(md, doc_title="Budget Report")[0]
    assert "Budget Report" in chunk.text and "Financial Summary" in chunk.text
    assert "Budget Report" not in chunk.body
    assert chunk.body.startswith("SAR 4.2M")


# ---------------------------------------------------------------------
# Rule 2 — REGRESSION: documents with no headings at all
# ---------------------------------------------------------------------

def test_document_without_headings_still_splits_by_size():
    """REGRESSION. Two of four real documents had no headings, because the
    source .docx used visual formatting rather than heading styles. A
    heading-only splitter produced one unusable mega-chunk for them."""
    paragraph = "This sentence carries some weight. " * 40
    md = "\n\n".join([paragraph] * 6)
    chunks = chunk_markdown(md)
    assert len(chunks) > 1
    assert all(c.section == "" for c in chunks)
    assert max(c.token_count for c in chunks) < DEFAULT_TARGET_TOKENS * 2


# ---------------------------------------------------------------------
# Rules 3 & 4 — tables
# ---------------------------------------------------------------------

def test_small_table_is_kept_whole():
    md = "## Risks\n\n" + table_with_rows(2)
    chunks = chunk_markdown(md)
    assert sum(c.body.count("<table>") for c in chunks) == 1
    assert any(c.has_table for c in chunks)


def test_oversized_table_is_split_by_rows():
    md = table_with_rows(60)
    chunks = chunk_markdown(md)
    table_chunks = [c for c in chunks if c.has_table]
    assert len(table_chunks) > 1, "a 60-row table must not stay one chunk"


def test_every_split_table_part_repeats_the_header():
    """REGRESSION. The header row defines what every cell beneath it means;
    a part without it is worse than useless for retrieval."""
    chunks = chunk_markdown(table_with_rows(60))
    table_chunks = [c for c in chunks if c.has_table]
    assert len(table_chunks) > 1
    for chunk in table_chunks:
        assert "RiskID" in chunk.body, f"chunk {chunk.chunk_index} lost the header"


def test_table_rows_are_not_duplicated_across_parts():
    chunks = chunk_markdown(table_with_rows(30))
    seen = [rid for c in chunks for rid in re.findall(r"R-\d\d", c.body)]
    body_ids = [i for i in seen if i != "RiskID"]
    assert len(body_ids) == len(set(body_ids)), "a row appears in two chunks"


# ---------------------------------------------------------------------
# Noise handling
# ---------------------------------------------------------------------

def test_empty_figures_are_dropped():
    """Document Intelligence does not OCR text inside images, so these come
    back empty. Keeping them would create contentless chunks."""
    md = "## Overview\n\n<figure>\n</figure>\n\nReal content follows here."
    chunks = chunk_markdown(md)
    assert all(c.body.strip() for c in chunks)
    assert not any("<figure>" in c.body for c in chunks)


def test_page_furniture_is_stripped():
    """Headers/footers repeat identically on every page; embedding them
    dilutes every vector they land in."""
    md = ('<!-- PageHeader="Ministry of Finance Confidential" -->\n\n'
          "## Section\n\nActual content.")
    chunk = chunk_markdown(md)[0]
    assert "PageHeader" not in chunk.text
    assert "Ministry of Finance Confidential" not in chunk.text


def test_page_numbers_track_page_breaks():
    md = ("## One\n\n" + "First page content. " * 30 +
          "\n\n<!-- PageBreak -->\n\n## Two\n\n" + "Second page content. " * 30)
    chunks = chunk_markdown(md)
    assert chunks[0].page == 1
    assert any(c.page == 2 for c in chunks), "page counter did not advance"


# ---------------------------------------------------------------------
# REGRESSION — character offsets
# ---------------------------------------------------------------------

def test_offsets_are_valid_and_index_the_original_markdown():
    """REGRESSION. Offsets were wrong on 8 of 10 chunks: str.find() returns
    the FIRST match (wrong for repeated text) and finds nothing at all for
    synthesised content such as split table parts. They index into
    document_content.markdown, so wrong values would silently break
    parent-document retrieval later."""
    md = load("risk_register_ar.md")
    for chunk in chunk_markdown(md, doc_title="Risk Register"):
        assert chunk.char_start >= 0, f"chunk {chunk.chunk_index} has no start"
        assert chunk.char_end <= len(md), "offset runs past end of document"
        assert chunk.char_start < chunk.char_end, "empty or inverted span"


def test_offsets_survive_repeated_text():
    """REGRESSION, the specific shape str.find() got wrong: identical text
    appearing twice resolved both chunks to the FIRST occurrence."""
    body = "Identical paragraph text. " * 60
    repeated = f"## A\n\n{body}\n\n## B\n\n{body}"
    chunks = chunk_markdown(repeated)
    assert len(chunks) == 2
    assert chunks[0].char_start != chunks[1].char_start
    assert chunks[0].char_start < chunks[1].char_start


# ---------------------------------------------------------------------
# Merging and overlap
# ---------------------------------------------------------------------

def test_undersized_chunks_are_merged_forward():
    """A heading plus one line carries too little signal to retrieve well.

    Asserts against a LITERAL, not the imported MIN_CHUNK_TOKENS: importing
    the constant made the assertion move with the code, so setting
    MIN_CHUNK_TOKENS = 0 still passed. A test that mutates along with what
    it tests cannot fail.
    """
    md = "\n\n".join(f"## S{i}\n\nShort line {i}." for i in range(6))
    chunks = chunk_markdown(md)
    # Six trivial sections must collapse — not survive as six tiny chunks.
    assert len(chunks) < 6, "undersized chunks were not merged"
    assert all(c.token_count >= 100 for c in chunks) or len(chunks) == 1


def test_chunk_indices_are_sequential_after_merging():
    md = "\n\n".join(f"## S{i}\n\nShort line {i}." for i in range(6))
    chunks = chunk_markdown(md)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_overlap_carries_context_between_chunks():
    md = "## A\n\n" + ("alpha " * 400) + "\n\n## B\n\n" + ("bravo " * 400)
    with_overlap = chunk_markdown(md, overlap_tokens=80)
    without = chunk_markdown(md, overlap_tokens=0)
    assert sum(len(c.text) for c in with_overlap) > sum(len(c.text) for c in without)


# ---------------------------------------------------------------------
# Payload contract — changing this means re-indexing the whole corpus
# ---------------------------------------------------------------------

def test_payload_contains_every_documented_field():
    chunk = chunk_markdown("## S\n\nSome content.", doc_title="Doc")[0]
    payload = chunk.to_payload(project_code="PRJ-01", document_link_id=7,
                               filename="f.docx", doc_type="Charter")
    for field in ("text", "page", "section", "chunk_index", "char_start",
                  "char_end", "has_table", "project_code",
                  "document_link_id", "filename", "doc_type"):
        assert field in payload, f"payload lost documented field: {field}"


# ---------------------------------------------------------------------
# Real Document Intelligence output
# ---------------------------------------------------------------------

@pytest.mark.parametrize("fixture,title", [
    ("risk_register_ar.md", "Risk Register"),          # no headings, big table
    ("budget_report_mixed.md", "Budget Report"),       # headings + tables + AR/EN
    ("scanned_arabic_figures.md", "Methodology"),      # scanned, empty figures
])
def test_real_documents_chunk_cleanly(fixture, title):
    md = load(fixture)
    chunks = chunk_markdown(md, doc_title=title)

    assert chunks, f"{fixture} produced no chunks"
    assert all(c.body.strip() for c in chunks), "empty chunk"
    assert all(0 <= c.char_start < c.char_end <= len(md) for c in chunks), "bad offset"
    # Generous ceiling: one oversized atomic block may still exceed target,
    # but nothing should approach the 8191-token embedding limit.
    assert max(c.token_count for c in chunks) < 2000, "chunk far too large"
    assert all(c.token_count == count_tokens(c.text) for c in chunks), \
        "token_count disagrees with the text it describes"


def test_arabic_content_is_preserved_not_mangled():
    chunks = chunk_markdown(load("risk_register_ar.md"), doc_title="Risk Register")
    combined = " ".join(c.body for c in chunks)
    assert "المخاطر" in combined or "مخاطرة" in combined


def test_real_table_heavy_document_splits_its_table():
    """The risk register was a single 3,331-token chunk before row-splitting."""
    chunks = chunk_markdown(load("risk_register_ar.md"), doc_title="Risk Register")
    assert len([c for c in chunks if c.has_table]) > 1
    assert max(c.token_count for c in chunks) < 1000
