-- Structured relationship linking each project to its documents (BRD AC11),
-- and the source for the knowledge-base page's document type + indexing
-- status columns (AC10). doc_type is deliberately free text, not a fixed
-- enum — real-world documents (see TEST-PROJECT) don't fit a closed set of
-- categories, and the whole point of this table is to stay dynamic rather
-- than assume every project looks like the fictional demo set.
CREATE TABLE IF NOT EXISTS document_links (
  id                SERIAL PRIMARY KEY,
  project_code      TEXT NOT NULL REFERENCES projects(code),
  filename          TEXT NOT NULL,
  file_path         TEXT NOT NULL,   -- absolute path under D:\MOF_DataSample
  doc_type          TEXT NOT NULL,   -- free text; not constrained to a fixed set
  language          TEXT,            -- 'Arabic' | 'English' | 'mixed' | NULL if unknown
  contains_table    BOOLEAN,         -- NULL if not yet determined (e.g. unOCR'd scans)
  indexing_status   TEXT NOT NULL DEFAULT 'pending'
                    CHECK (indexing_status IN ('pending', 'indexing', 'indexed', 'failed')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_code, filename)
);
