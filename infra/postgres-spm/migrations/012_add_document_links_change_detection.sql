-- Change detection for document_links.
--
-- Answers the question left open in Documentation/SOLUTION_ARCHITECTURE.md
-- §3.3: "what triggers re-ingestion when SharePoint files change".
--
-- Without this the scanner can only choose between reprocessing everything
-- (paying for OCR on unchanged files every run) or skipping anything
-- already marked 'indexed' (so an edited file is never re-indexed and the
-- assistant silently answers from stale content). The hash makes
-- new/unchanged/modified distinguishable, so the pipeline is idempotent
-- and only pays for work that actually needs doing.
ALTER TABLE document_links
  -- SHA-256 of the file contents, computed by the scanner (stage 0 of the
  -- ingestion pipeline). NULL = never scanned, which the scanner treats
  -- the same as "new". Kept as TEXT rather than a fixed-width type so a
  -- future algorithm change doesn't need a migration.
  ADD COLUMN IF NOT EXISTS file_hash TEXT,

  -- Size in bytes, from filesystem metadata (a stat call — no need to read
  -- the file). Used as a cheap pre-check: a differing size proves the file
  -- changed without hashing it at all. Negligible at 58 files, but this is
  -- the mechanism that keeps a full SharePoint corpus scan affordable.
  -- Also useful on its own (a 0-byte file is a broken file).
  ADD COLUMN IF NOT EXISTS file_size_bytes BIGINT;
