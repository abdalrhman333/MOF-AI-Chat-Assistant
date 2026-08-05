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

### Stage 0 — scan (**start here**)

Walk `settings.document_root`, and for every file: derive `project_code`
from the folder name, compute SHA-256, and **upsert** into
`document_links`.

⚠️ **The `file_path` values currently in the database are seeded
placeholders** generated from `mock-data/spm_manifest.json`. Stage 0 is
what replaces them with real discovery. This is the step that makes the
solution *dynamic* — dropping a new file into a project folder must make
it appear, with no code change. The pipeline must never read the manifest.

Decide per file using the stored hash:

| Condition | Action |
|---|---|
| No row exists | insert, status `pending` |
| `file_hash` matches | **skip entirely** — already processed, costs nothing |
| `file_hash` differs | re-OCR and re-index |

`file_size_bytes` is a cheap pre-check: a different size proves a change
without hashing.

### Stage 1 — OCR

Only for documents with no `document_content` row (its presence **is** the
"OCR done" marker — so a failed embed never re-pays for OCR).

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

---

## 4. Practical notes

- `PYTHONIOENCODING=utf-8` on every command that prints — most of the
  corpus is Arabic (`INFRASTRUCTURE.md` §4.5a).
- Use the venv: `.venv/Scripts/python`, never system Python.
- Full corpus run ≈ **a few minutes** of API time (~1 s/page) and single-
  digit dollars.
- Test on **one project first** (`PRJ-05`, only 4 documents) before all 58.
- `TEST-PROJECT` holds 13 real-world files including `.pptx` and a
  scanned PDF — the untested shapes. Expect surprises there and treat
  them as findings, not failures.

---

## 5. Verify with

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/ -q     # 26 pass

docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data -c \
  "SELECT indexing_status, count(*) FROM document_links GROUP BY 1;"

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
