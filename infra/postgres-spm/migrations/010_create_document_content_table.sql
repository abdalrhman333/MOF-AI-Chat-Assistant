-- OCR output cache, 1:1 with document_links.
--
-- Why this table exists: OCR (Azure Document Intelligence) costs money per
-- page. Chunking strategy is untuned and will change; without this table,
-- every chunking change would force a full re-OCR of the whole corpus.
-- With it, re-chunking is free.
--
-- The PRESENCE of a row here is also the "OCR is done" marker for a
-- document. That deliberately avoids adding a separate ocr_status column:
-- if embedding or indexing fails later, document_links.indexing_status goes
-- to 'failed' but this row survives, so a retry never re-pays for OCR.
CREATE TABLE IF NOT EXISTS document_content (
  document_link_id  INTEGER PRIMARY KEY REFERENCES document_links(id) ON DELETE CASCADE,

  -- The primary artifact: prebuilt-layout markdown. This is what feeds
  -- chunking/embedding.
  markdown          TEXT NOT NULL,

  -- Full Document Intelligence JSON response. Markdown is lossy — it drops
  -- structured table cells, per-page geometry, and confidence scores. Since
  -- the OCR call is already paid for, keeping the raw response is cheap
  -- insurance against later needing richer table handling or page-accurate
  -- citations without re-processing.
  raw_response      JSONB,

  page_count        INTEGER,      -- from the OCR result, not the file itself

  -- Which model/API version produced this content, so a future model change
  -- is detectable rather than silently mixed in with older extractions.
  ocr_model         TEXT,         -- e.g. 'prebuilt-layout'
  ocr_api_version   TEXT,         -- e.g. '2024-11-30'

  extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
