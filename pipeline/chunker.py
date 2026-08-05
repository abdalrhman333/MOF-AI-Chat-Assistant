"""Split Document Intelligence layout markdown into retrieval chunks.

Input contract is MARKDOWN, deliberately — not "Azure DI output". Any OCR
that emits markdown (Docling, marker, a PaddleOCR + layout post-processor)
drops in unchanged, so switching to on-prem OCR later doesn't require
rewriting this. See Documentation/SOLUTION_ARCHITECTURE.md.

What the real markdown actually looks like (verified against OCR'd samples
from the corpus, not assumed):

  * Tables are HTML <table>...</table>, NOT pipe tables.
  * Headings may be entirely absent. Two of four sampled documents had
    none, because the source .docx used visual formatting rather than real
    Word heading styles — extremely common in practice. A chunker that
    only splits on headings produces one useless mega-chunk for those.
  * <!-- PageBreak --> marks page boundaries.
  * <!-- PageHeader="..." --> / PageFooter repeat identical boilerplate on
    every page; embedding them adds noise and dilutes the vector.

So chunking uses a fallback ladder rather than a single rule:
    1. split at headings when the document has them
    2. otherwise accumulate structural blocks to the target size
    3. split oversized prose blocks on sentence boundaries
    4. oversized atomic blocks (a huge table) become their own chunk
       rather than being cut in half
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_TARGET_TOKENS = 600
DEFAULT_OVERLAP_TOKENS = 80

# A heading at or above this level starts a new chunk. Deeper headings are
# kept as breadcrumb context but don't force a split, or short subsections
# would each become their own tiny chunk.
SPLIT_HEADING_LEVEL = 2

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_PAGE_BREAK = re.compile(r"<!--\s*PageBreak\s*-->", re.I)
_PAGE_FURNITURE = re.compile(r"<!--\s*Page(?:Header|Footer|Number)=.*?-->", re.I | re.S)
_TABLE_OPEN, _TABLE_CLOSE = "<table>", "</table>"
_FIGURE_OPEN, _FIGURE_CLOSE = "<figure>", "</figure>"

_ENCODER = None


def count_tokens(text: str) -> int:
    """Token count for chunk sizing.

    Isolated behind one function on purpose: swapping to a HuggingFace
    tokenizer for an on-prem embedding model is a change here and nowhere
    else. Chunk boundaries stay valid across models — only the vector
    dimension changes, which means re-embedding but never re-chunking.
    """
    global _ENCODER
    if _ENCODER is None:
        import tiktoken
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return len(_ENCODER.encode(text, disallowed_special=()))


@dataclass
class Block:
    """An atomic unit of the document. Tables are never split across chunks."""
    kind: str          # 'heading' | 'table' | 'figure' | 'text'
    text: str
    page: int
    level: int = 0     # heading level, 0 for everything else
    atomic: bool = False
    # Character span in the ORIGINAL markdown. Tracked during parsing rather
    # than recovered afterwards with str.find(), which returns the first
    # match and is simply wrong for repeated text or synthesised content.
    start: int = -1
    end: int = -1


@dataclass
class Chunk:
    text: str          # breadcrumb + content, i.e. what actually gets embedded
    body: str          # content without the breadcrumb, for display/citation
    page: int
    section: str       # heading breadcrumb, '' if the document has no headings
    chunk_index: int
    token_count: int
    char_start: int
    char_end: int
    has_table: bool = False

    def to_payload(self, **extra) -> dict:
        payload = {
            "text": self.body,
            "page": self.page,
            "section": self.section,
            "chunk_index": self.chunk_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "has_table": self.has_table,
        }
        payload.update(extra)
        return payload


def _parse_blocks(markdown: str) -> list[Block]:
    """Walk the RAW markdown once, emitting blocks with page numbers and
    true character spans.

    Parses the raw text (skipping page furniture inline rather than
    stripping it first) so recorded offsets stay aligned with the markdown
    actually stored in document_content — which is what parent-document
    retrieval will index into later.
    """
    blocks: list[Block] = []
    page = 1
    buffer: list[str] = []
    buffer_start = -1

    lines = markdown.splitlines(keepends=True)
    offsets: list[int] = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line)

    def flush_text(end_offset: int):
        nonlocal buffer, buffer_start
        joined = "".join(buffer).strip()
        if joined:
            blocks.append(Block("text", joined, page, start=buffer_start, end=end_offset))
        buffer, buffer_start = [], -1

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.rstrip("\n")
        line_start = offsets[i]
        line_end = line_start + len(raw_line)

        # Repeated page headers/footers are identical on every page: no
        # retrievable meaning, and they dilute every vector they land in.
        if _PAGE_FURNITURE.search(line):
            i += 1
            continue

        if _PAGE_BREAK.search(line):
            flush_text(line_start)
            page += 1
            i += 1
            continue

        stripped = line.strip()
        matched_container = False
        for open_tag, close_tag, kind in (
            (_TABLE_OPEN, _TABLE_CLOSE, "table"),
            (_FIGURE_OPEN, _FIGURE_CLOSE, "figure"),
        ):
            if stripped.startswith(open_tag):
                flush_text(line_start)
                collected = [line]
                block_start = line_start
                i += 1
                while i < len(lines) and close_tag not in lines[i]:
                    collected.append(lines[i].rstrip("\n"))
                    i += 1
                block_end = line_end
                if i < len(lines):
                    collected.append(lines[i].rstrip("\n"))
                    block_end = offsets[i] + len(lines[i])
                    i += 1
                body = "\n".join(collected).strip()
                # An empty <figure></figure> carries no text — DI does not
                # OCR content inside images. Dropping it avoids empty chunks.
                if not (kind == "figure" and not re.sub(r"</?figure>", "", body).strip()):
                    blocks.append(Block(kind, body, page, atomic=True,
                                        start=block_start, end=block_end))
                matched_container = True
                break
        if matched_container:
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_text(line_start)
            blocks.append(Block("heading", heading.group(2).strip(), page,
                                level=len(heading.group(1)),
                                start=line_start, end=line_end))
        else:
            if buffer_start < 0 and stripped:
                buffer_start = line_start
            buffer.append(raw_line)
        i += 1

    flush_text(running)
    return blocks


def _split_table_by_rows(table_html: str, target: int) -> list[str]:
    """Split an oversized table into row groups, REPEATING THE HEADER in each.

    Keeping a huge table whole produces chunks so large that the one row you
    asked about is diluted by every other row (a risk register with 13 risks
    was a single 3,331-token chunk before this). Splitting blindly is worse
    though — the header row is what gives every cell beneath it meaning, so
    each part carries the header with it.
    """
    rows = re.findall(r"<tr>.*?</tr>", table_html, re.S)
    if len(rows) <= 2:
        return [table_html]

    header = rows[0] if ("<th" in rows[0] or len(rows) > 1) else ""
    body_rows = rows[1:] if header else rows
    header_tokens = count_tokens(header)

    parts, current = [], []
    for row in body_rows:
        row_tokens = count_tokens(row)
        current_tokens = sum(count_tokens(r) for r in current)
        if current and header_tokens + current_tokens + row_tokens > target:
            parts.append("<table>\n" + header + "\n" + "\n".join(current) + "\n</table>")
            current = [row]
        else:
            current.append(row)
    if current:
        parts.append("<table>\n" + header + "\n" + "\n".join(current) + "\n</table>")
    return parts


def _pack(pieces: list[str], target: int) -> list[str]:
    """Greedily group pieces into runs that stay under the target size."""
    out: list[str] = []
    current: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        candidate = " ".join(current + [piece])
        if current and count_tokens(candidate) > target:
            out.append(" ".join(current))
            current = [piece]
        else:
            current.append(piece)
    if current:
        out.append(" ".join(current))
    return out


def _breadcrumb(doc_title: str | None, heading_path: list[str]) -> str:
    parts = ([doc_title] if doc_title else []) + heading_path
    return " > ".join(p for p in parts if p)


def chunk_markdown(
    markdown: str,
    *,
    doc_title: str | None = None,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split layout markdown into retrieval chunks.

    doc_title is prepended to every chunk's embedded text. This matters more
    than it looks: a chunk reading "SAR 4.2M was allocated" is nearly
    meaningless as a vector, while "Budget Report > Financial Summary >
    SAR 4.2M was allocated" actually encodes what the text is about.
    """
    blocks = _parse_blocks(markdown)
    if not blocks:
        return []

    has_headings = any(b.kind == "heading" for b in blocks)

    chunks: list[Chunk] = []
    heading_path: list[str] = []
    pending: list[Block] = []
    pending_tokens = 0
    carry: str = ""          # overlap text carried from the previous chunk

    def emit():
        nonlocal pending, pending_tokens, carry
        if not pending:
            return
        body = "\n\n".join(b.text for b in pending).strip()
        if not body:
            pending, pending_tokens = [], 0
            return
        section = _breadcrumb(None, heading_path)
        prefix = _breadcrumb(doc_title, heading_path)
        # Breadcrumb first, then overlap, then body — the breadcrumb is what
        # tells the embedding what this text is about, so it leads.
        parts = [p for p in (prefix, carry, body) if p]
        embedded = "\n\n".join(parts)
        start = min((b.start for b in pending if b.start >= 0), default=-1)
        end = max((b.end for b in pending if b.end >= 0), default=-1)
        chunks.append(Chunk(
            text=embedded,
            body=body,
            page=pending[0].page,
            section=section,
            chunk_index=len(chunks),
            token_count=count_tokens(embedded),
            char_start=start,
            char_end=end,
            has_table=any(b.kind == "table" for b in pending),
        ))
        # Overlap: carry the tail of this chunk so a fact spanning a
        # boundary is still findable from the next one.
        if overlap_tokens > 0:
            words = body.split()
            tail: list[str] = []
            for word in reversed(words):
                tail.insert(0, word)
                if count_tokens(" ".join(tail)) >= overlap_tokens:
                    break
            carry = " ".join(tail)
        pending, pending_tokens = [], 0

    for block in blocks:
        if block.kind == "heading":
            # Rule 1: headings drive the split, when the document has them.
            if has_headings and block.level <= SPLIT_HEADING_LEVEL and pending:
                emit()
            heading_path = heading_path[: block.level - 1]
            while len(heading_path) < block.level - 1:
                heading_path.append("")
            heading_path.append(block.text)
            continue

        block_tokens = count_tokens(block.text)

        # Rule 4: an oversized table is split BY ROWS with the header
        # repeated, never cut arbitrarily. Figures and anything else atomic
        # get their own chunk whole.
        if block.atomic and block_tokens > target_tokens:
            emit()
            if block.kind == "table":
                # Every part inherits the WHOLE table's span: the parts are
                # synthesised (header + a row subset), so they have no
                # contiguous span of their own. Pointing at the full table
                # is both honest and what parent-document retrieval wants.
                for part in _split_table_by_rows(block.text, target_tokens):
                    pending = [Block("table", part, block.page, atomic=True,
                                     start=block.start, end=block.end)]
                    pending_tokens = count_tokens(part)
                    emit()
            else:
                pending = [block]
                pending_tokens = block_tokens
                emit()
            continue

        # Rule 3: oversized prose is split on sentence boundaries (Arabic
        # '؟' included). Pieces inherit the parent block's span — they're
        # re-joined text, so they have no exact span of their own.
        if not block.atomic and block_tokens > target_tokens:
            for piece in _pack(re.split(r"(?<=[.!?؟])\s+|\n\n+", block.text), target_tokens):
                if pending_tokens + count_tokens(piece) > target_tokens:
                    emit()
                pending.append(Block("text", piece, block.page,
                                     start=block.start, end=block.end))
                pending_tokens += count_tokens(piece)
            continue

        # Rule 2: otherwise accumulate to the target size. This is the path
        # that carries documents with no headings at all.
        if pending and pending_tokens + block_tokens > target_tokens:
            emit()
        pending.append(block)
        pending_tokens += block_tokens

    emit()
    return _merge_small(chunks, target_tokens, doc_title)


MIN_CHUNK_TOKENS = 120


def _merge_small(chunks: list[Chunk], target: int, doc_title: str | None) -> list[Chunk]:
    """Fold undersized chunks into the next one where it fits.

    Heading-driven splitting can leave a chunk that is just a heading and one
    line (58 tokens was the smallest observed). Chunks that thin retrieve
    poorly — there isn't enough signal in the vector — so they're merged
    forward when the combined size still fits.
    """
    if len(chunks) < 2:
        return chunks

    merged: list[Chunk] = []
    i = 0
    while i < len(chunks):
        current = chunks[i]
        while (
            current.token_count < MIN_CHUNK_TOKENS
            and i + 1 < len(chunks)
            and current.token_count + chunks[i + 1].token_count <= target
        ):
            nxt = chunks[i + 1]
            body = f"{current.body}\n\n{nxt.body}".strip()
            section = current.section or nxt.section
            prefix = _breadcrumb(doc_title, [section] if section else [])
            text = f"{prefix}\n\n{body}" if prefix else body
            current = Chunk(
                text=text,
                body=body,
                page=current.page,
                section=section,
                chunk_index=current.chunk_index,
                # Counted on `text`, matching emit(): token_count must
                # describe what is actually embedded, not just the body.
                token_count=count_tokens(text),
                char_start=current.char_start,
                char_end=nxt.char_end,
                has_table=current.has_table or nxt.has_table,
            )
            i += 1
        merged.append(current)
        i += 1

    for index, chunk in enumerate(merged):
        chunk.chunk_index = index
    return merged
