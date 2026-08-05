-- Pipeline diagnostics for document_links.
--
-- Without these, a failed batch can only be debugged by re-running it and
-- watching logs. With them, one query explains what happened and when.
ALTER TABLE document_links
  -- Why indexing failed. Written when indexing_status -> 'failed',
  -- cleared by the pipeline on a subsequent success so a stale error can
  -- never sit next to a healthy row.
  ADD COLUMN IF NOT EXISTS error_message TEXT,

  -- When this document was last SUCCESSFULLY indexed (NULL until then).
  -- Also what the knowledge-base page (AC10) needs to show a meaningful
  -- status — "indexed 3 days ago" rather than just "indexed".
  ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ,

  -- How many chunks this document produced in Qdrant. Cheap sanity check:
  -- a 20-page document that yields 1 chunk means the chunker broke. Also
  -- lets you size the corpus without querying the vector DB.
  ADD COLUMN IF NOT EXISTS chunk_count INTEGER;
