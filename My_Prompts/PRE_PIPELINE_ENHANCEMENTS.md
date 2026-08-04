# Prompt — Pre-Ingestion-Pipeline Enhancements

> Copy everything below the line into a new session (or this one) to execute.

---

I'm working on the MOF Enjaz Chatbot at `D:\MOF Enjaz Chatbot`. Before I build
the ingestion pipeline, I want a set of groundwork enhancements applied.

**Orient yourself first**: read `Documentation\START_HERE.md`, then
`Documentation\SOLUTION_ARCHITECTURE.md` (§3 retrieval architecture, §4.1
providers, §4.5 tech stack) and `Documentation\INFRASTRUCTURE.md` (§7 migration
rules, §8 current schema). Then verify the live system rather than trusting the
docs — `docker compose ps` from `infra\`, and query the database.

**Project constraints that apply to everything below:**
- Additive-only schema changes. Never `DROP` or repurpose existing tables/columns.
- Raw SQL migrations, no ORM (decided — see `SOLUTION_ARCHITECTURE.md` §4.5).
- Nothing may be written to the `C:` drive. Tools/data live under `D:\`.
- Real secrets stay in `infra\.env` (gitignored). Never write secret values into
  docs, `.env.example`, or migration files.
- This environment mangles UTF-8 in inline shell args and in Python stdout on
  Windows. The corpus contains Arabic filenames and content. Always read/write
  files with explicit `encoding="utf-8"`, and set `PYTHONIOENCODING=utf-8` when
  printing non-ASCII.
- Verify against data, not logs — a silent success is not proof.

Work through the tasks **in the order given**; later ones depend on earlier ones.

---

## Task 1 — Migration runner + tracking table

**Problem**: rebuilding the database currently means running 9 migration files
manually, in the right order, by hand.

**Build**: a migration runner that applies every `.sql` file in
`infra\postgres-spm\migrations\` in filename-sorted order, skipping any already
applied.

**Best-practice specifics:**
- Create a `schema_migrations` table (`filename` TEXT PRIMARY KEY,
  `applied_at` TIMESTAMPTZ DEFAULT now()) as the tracking record.
- **Backfill it with `001`–`009`, which are already applied** — do not re-run
  them against the live database.
- Write the runner in **Python** (`infra\postgres-spm\apply_migrations.py`), not
  bash: the seed files contain Arabic text and need explicit UTF-8 handling.
  Shell out to `docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data`.
- Each migration should run in a transaction; on failure, stop and report which
  file failed rather than continuing.
- Print a clear summary: which files were applied, which were skipped as
  already-applied.

**Done when**: running it twice in a row is safe — the second run applies
nothing and says so.

---

## Task 2 — Lifecycle + change-detection columns on `document_links`

**Problem**: `indexing_status` can be `'failed'` but there's nowhere to record
*why*, when, or how much was produced. And there's no way to tell whether a file
has changed since it was last indexed — which matters because OCR costs money
per page, so re-running blindly is wasteful.

**Build**: migration `010_add_document_links_lifecycle_columns.sql` adding
(all `ADD COLUMN IF NOT EXISTS`):

| Column | Type | Purpose |
|---|---|---|
| `file_hash` | TEXT | SHA-256 of the file bytes — definitive change detection |
| `file_size_bytes` | BIGINT | Cheap pre-check before hashing |
| `file_modified_at` | TIMESTAMPTZ | Filesystem mtime |
| `indexed_at` | TIMESTAMPTZ | When indexing last succeeded |
| `last_attempt_at` | TIMESTAMPTZ | When indexing was last *attempted* (drives retry logic) |
| `chunk_count` | INT | How many chunks this document produced |
| `error_message` | TEXT | Failure reason, so a failed batch is debuggable without re-running |

**Why a hash and not just mtime**: mtime changes on copy/sync without the content
changing, which would trigger needless paid re-OCR. The hash is definitive.
This also resolves the open question flagged in `SOLUTION_ARCHITECTURE.md` §3.3
("what triggers re-ingestion when SharePoint files change") — record that.

---

## Task 3 — `document_content` table (store the OCR output)

**Problem — this is the highest-value item**: there is currently nowhere to put
extracted OCR text. If the pipeline only chunks→embeds and discards the markdown,
then *any* future change to chunking strategy forces a full paid re-OCR of the
entire corpus. Chunking is untuned and will change.

**Build**: migration `011_create_document_content_table.sql`:

```sql
CREATE TABLE IF NOT EXISTS document_content (
  document_id       INT PRIMARY KEY REFERENCES document_links(id) ON DELETE CASCADE,
  markdown          TEXT NOT NULL,
  raw_response      JSONB,
  ocr_model         TEXT NOT NULL DEFAULT 'prebuilt-layout',
  ocr_api_version   TEXT,
  page_count        INT,
  table_count       INT,
  ocr_completed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Best-practice notes:**
- `PRIMARY KEY` on `document_id` enforces exactly one content row per document.
- **Store `raw_response` too**, not just markdown. OCR is the expensive step;
  storage is cheap. The raw Document Intelligence JSON contains structured table
  cells, bounding boxes, and confidence scores that markdown flattens away —
  keeping it means we never need to pay for re-OCR for any reason.
- OCR-derived facts (`page_count`, `table_count`) belong here, not on
  `document_links` — keep `document_links` for lifecycle/status and this table
  for extraction output.

---

## Task 4 — Backfill script for the existing 58 documents

**Build**: a script that populates `file_hash`, `file_size_bytes`, and
`file_modified_at` for all existing `document_links` rows.

**Specifics:**
- Python, explicit UTF-8 — the corpus includes Arabic filenames.
- Read `file_path` from the database; do **not** re-scan the filesystem
  independently, so any mismatch between DB and disk surfaces as an error rather
  than being silently papered over.
- Report any row whose `file_path` doesn't resolve (expected: zero — all 58
  currently resolve).
- Do **not** populate `page_count`/`table_count` — those come from OCR, which
  hasn't run yet. Leave them NULL.

---

## Task 5 — `.gitignore` for Python artifacts

Application code is about to land. Add (if not already present):
`__pycache__/`, `*.py[cod]`, `.venv/`, `venv/`, `.pytest_cache/`, `*.egg-info/`,
`.mypy_cache/`, `.ruff_cache/`.

Leave the existing entries (`infra/.env`, `Tools ACC/`,
`.claude/settings.local.json`) untouched.

---

## Task 6 — Record the chunking + Qdrant payload decisions

**Do not build the pipeline** — just document these decisions in
`Documentation\SOLUTION_ARCHITECTURE.md` (new subsection under §4) so the
pipeline task has an unambiguous spec to follow. Add matching entries to the
decisions log in `STATUS_AND_ROADMAP.md` §4.

### 6a. Chunking strategy — structure-aware

Grounded in a real OCR test run on `_المنهجية التقنية للمشروع.pdf` (9-page
scanned Arabic PDF): output came back as markdown with `#`/`##` headings and
`<!-- PageBreak -->` markers, correct RTL Arabic, in ~9 seconds.

Spec to record:
- Split on markdown heading boundaries (`#`, `##`, `###`).
- **Never split inside a markdown table** — keeping tables intact is precisely
  why `prebuilt-layout` was chosen over `prebuilt-read`. If a single table
  exceeds the size cap, keep it whole anyway; one oversized chunk beats a
  meaningless half-table.
- Target ~800 tokens per chunk, hard maximum ~1500. (`text-embedding-3-small`
  accepts up to 8191, but smaller chunks retrieve more precisely.)
- ~100 token overlap between consecutive chunks *within the same section*.
- If a section exceeds the cap, split on paragraph boundaries.
- **Prepend the heading path to each chunk** (e.g. `# Charter > ## Budget`) so an
  isolated chunk still carries its context — this materially improves retrieval
  quality and answer grounding.
- Track page number by counting `<!-- PageBreak -->` markers.
- **Drop empty `<figure></figure>` blocks** — verified as common noise in the
  real OCR output.

### 6b. Known OCR limitation — record this honestly

The same test produced **11 empty `<figure>` blocks**: text embedded inside
images/diagrams is *not* extracted. For graphics-heavy documents (slide decks
especially), some content will be missing from the knowledge base. Record this
as a known limitation so it isn't rediscovered later as "why can't the assistant
answer this?". Note it as a candidate for future work, not something to solve now.

### 6c. Qdrant collection + payload schema

- **One collection per project**, named `project_{code}` (e.g.
  `project_PRJ-01`). Hyphens verified as accepted by Qdrant.
- Vector size **1536**, distance **Cosine** (fixed by
  `text-embedding-3-small` — see `SOLUTION_ARCHITECTURE.md` §4.1).
- Per-chunk payload — decide this now, because changing it later means
  re-indexing everything:

| Field | Purpose |
|---|---|
| `document_id` | FK back to `document_links.id` |
| `project_code` | Redundant with the collection, but makes results self-describing |
| `filename` | Source citation (BRD AC06 requires citing the source) |
| `doc_type` | Lets answers say *which kind* of document backs a claim |
| `language` | Useful for response-language decisions |
| `heading_path` | Section context for the chunk |
| `page_number` | Precise citation |
| `chunk_index` | Ordering within the document |
| `text` | The chunk content itself — needed so retrieval can return text |

---

## Definition of done

1. All migrations applied via the new runner; running it a second time is a
   clean no-op.
2. Verify against the database, not logs: all 58 `document_links` rows have
   `file_hash` and `file_size_bytes` populated; `document_content` exists and is
   empty (correct — OCR hasn't run).
3. `Documentation\SOLUTION_ARCHITECTURE.md`, `INFRASTRUCTURE.md` §8 (schema
   reference), and `STATUS_AND_ROADMAP.md` all updated to reflect the new schema
   and the recorded decisions.
4. Everything committed to git with a clear message. Do not commit secrets.
5. Report back concisely: what changed, what was verified, and anything you hit
   that the prompt didn't anticipate.
