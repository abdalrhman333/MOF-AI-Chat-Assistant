# Next build — the ingestion pipeline

**A brief for whoever builds this next, assuming no memory of earlier
sessions.** Read `START_HERE.md` first, then this.

Everything the pipeline needs already exists and is verified: schema,
config, chunker, credentials, corpus, tests. Nothing here is speculative.

---

## 1. What it does

Turn 58 documents on disk into searchable, project-isolated vectors.

```
D:\MOF_DataSample\<PROJECT>\<file>
        │
   [0] scan      → hash each file, upsert document_links
        │
   [1] OCR       → Document Intelligence → document_content.markdown
        │
   [2] chunk     → pipeline/chunker.py → embed via Azure OpenAI
        │
   [3] index     → Qdrant collection project_<code>
        │
   mark document_links.indexing_status = 'indexed'
```

---

## 2. Stage contracts

### Stage 0 — scan ✅ **BUILT** (`pipeline/scanner.py`, 2026-08-06)

Done and run over all 58 documents. The seeded `file_path` placeholders are
gone; every row carries a real SHA-256, and all 58 sit at `pending` waiting
for Stage 1.

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.scanner --dry-run
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.scanner --project TEST-PROJECT --list
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.scanner
```

It decides **five** outcomes per file, not the three this brief originally
specified — `PATH_UPDATED` and the BACKFILL/MODIFIED split were both
needed. Full reasoning in `SOLUTION_ARCHITECTURE.md` §4.8; verification
record (mutation testing, live change-detection run) in
`STATUS_AND_ROADMAP.md` §9.

Two corrections to what this brief said:

- **`file_size_bytes` is not a cheap pre-check that avoids hashing.** A
  differing size does prove the file changed, but the new hash still has to
  be computed in order to be stored — so hashing is never skipped. It's
  kept as a diagnostic.
- **The three-row decision table was incomplete.** It has no answer for a
  file whose bytes are identical but whose path moved (which is what
  repointing `DOCUMENT_ROOT` produces), nor for a row that has never been
  scanned but already has cached OCR.

### Stage 1 — OCR (**start here**)

Only for documents with no `document_content` row (its presence **is** the
"OCR done" marker — so a failed embed never re-pays for OCR). Stage 0
already maintains that marker correctly: it drops the row when a file's
content changes and keeps it otherwise, so the skip condition can be
trusted.

⚠️ **This is where real money starts being spent** — OCR is billed per
page. Run one document and read the markdown before batching.

- Model: `settings.models.ocr_model` (`prebuilt-layout`)
- Must request `outputContentFormat=markdown`
- Store `markdown`, `raw_response`, `page_count`, `ocr_model`,
  `ocr_api_version`

### Stage 2 — chunk + embed

```python
from pipeline.chunker import chunk_markdown
from pipeline.config import get_settings

s = get_settings()
chunks = chunk_markdown(
    markdown,
    doc_title=f"{doc_type} — {project_name}",   # feeds the breadcrumb
    target_tokens=s.chunking.target_tokens,
    overlap_tokens=s.chunking.overlap_tokens,
)
```

Embed `chunk.text` (breadcrumb included — that's what makes the vector
meaningful), **not** `chunk.body`. Batch the embedding calls.

### Stage 3 — index

```python
collection = s.qdrant.collection_for(project_code)   # -> "project_PRJ-03"
# create if absent: size=s.qdrant.vector_size (1536), distance=Cosine
payload = chunk.to_payload(
    project_code=project_code,
    document_link_id=link_id,
    filename=filename,
    doc_type=doc_type,
)
```

Payload schema is fixed and documented in `SOLUTION_ARCHITECTURE.md` §4.7.
**Changing it later means re-indexing everything** — treat it as a
contract.

Then set `indexing_status='indexed'`, `indexed_at`, `chunk_count`, and
clear `error_message`.

---

## 3. Rules that are not negotiable

1. **The pipeline connects as `spm_admin`** (`settings.postgres`), not
   `spm_app`. It needs cross-project access; owners bypass row-level
   security. `spm_app` is the *chat backend's* role and is `SELECT`-only.
2. **Read config only through `pipeline.config.get_settings()`.** No module
   reads `os.environ` or hardcodes a path, deployment name, or collection
   prefix — otherwise repointing at real SharePoint becomes a code change.
3. **Never re-OCR** a document whose `document_content` row exists and
   whose hash is unchanged. OCR is billed per page.
4. **Record failures.** On error set `indexing_status='failed'` and write
   `error_message`. Debugging a 58-document batch without it means
   re-running blind.
5. **Idempotent.** Running twice must not duplicate rows or vectors.
   Stage 0 demonstrates the bar: a second run over an unchanged corpus
   reports `unchanged 58, rows written 0`.
6. **Never overwrite curated data with a derived default.** `doc_type`,
   `language` and `contains_table` were curated; a stage that can't know
   them must leave them alone. Stage 0's `'Unclassified'` applies to new
   rows only.
7. **Prove a safety feature by breaking it.** Every check added here should
   be mutation-tested — deliberately broken, with the intended test
   confirmed to catch it. Commit first: reverting a mutation with
   `git checkout` will take uncommitted work with it
   (`STATUS_AND_ROADMAP.md` §6).

---

## 4. Practical notes

- `PYTHONIOENCODING=utf-8` on every command that prints — most of the
  corpus is Arabic (`INFRASTRUCTURE.md` §4.5a).
- Use the venv: `.venv/Scripts/python`, never system Python.
- Full corpus run ≈ **a few minutes** of API time (~1 s/page) and single-
  digit dollars.
- Test on **one project first** before all 58 — `PRJ-05` is the cheapest
  (4 documents), `TEST-PROJECT` the most revealing.
- `TEST-PROJECT` holds 13 real-world files including `.pptx` and a
  scanned PDF — the untested shapes. Expect surprises there and treat
  them as findings, not failures.
- A Windows console defaults to cp1252 and will crash a working stage the
  moment it prints an Arabic filename. `pipeline/scanner.py` forces UTF-8
  on stdout in `main()` rather than relying on the operator remembering
  `PYTHONIOENCODING` — worth copying into every stage that has a CLI.

---

## 5. Verify with

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/ -q     # 61 pass

docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data -c \
  "SELECT indexing_status, count(*) FROM document_links GROUP BY 1;"
# today: pending | 58

docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data -c \
  "SELECT count(*) FROM document_content;"     # 0 until Stage 1 runs

curl -s -H "api-key: $QDRANT_API_KEY" http://localhost:6333/collections
```

**Then the real test** — the one that actually matters and cannot be
answered before now: ask a question a project manager would ask, and see
whether the right chunk comes back. Chunking quality is empirical, and
`document_content` stores the markdown precisely so re-chunking is free.

---

## 6. Known-unknowns, flagged honestly

- Only 4 of 58 documents have been through OCR. `.pptx` and the real
  English charters are untested shapes.
- Arabic paragraph order looked reversed in some *synthetically generated*
  `.docx` files; the genuinely scanned PDF was correct. Likely a
  test-file artifact — **confirm during the full run.**
- Text inside images/diagrams is **not** extracted (11 empty `<figure>`
  blocks in one sample). Graphics-heavy documents will have gaps.
