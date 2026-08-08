# The ingestion pipeline — ✅ BUILT AND RUNNING (2026-08-09)

**This was the brief for building the ingestion pipeline. All four stages
now exist and have run over the full corpus**, so it is kept as the record
of what was specified, what was actually built, and where the two differ —
not as a to-do list.

If you are here to build the **next** thing, that is the chat backend:
`STATUS_AND_ROADMAP.md` §5.

```
58 documents on disk  →  678 vectors across 9 project collections
```

| Stage | Module | State |
|---|---|---|
| 0 — scan | `pipeline/scanner.py` | ✅ 58/58 discovered and hashed |
| 1 — OCR | `pipeline/ocr.py` + `pipeline/native_extract.py` | ✅ 58/58 extracted |
| 2+3 — chunk, embed, index | `pipeline/indexer.py` | ✅ 678 chunks indexed |
| — operate it | `pipeline/tasks.py`, `locking.py`, `status.py` | ✅ runs unattended, daily 13:00 |

Every stage is idempotent and free to re-run on an unchanged corpus. Total
spend: **~$2.51**.

**Five places the build deviated from this brief**, each for a reason
recorded at the point of deviation below:

1. Stage 0 has **five** outcomes per file, not three.
2. `file_size_bytes` is **not** a cheap pre-check that avoids hashing.
3. Stages 2 and 3 were built as **one** module, not two.
4. A **native fallback** exists for Office files Document Intelligence
   rejects — which the brief did not anticipate, because it assumed the
   API always succeeds or honestly fails.
5. The brief stopped at "the stages work". Making them *run* — a worker, a
   schedule, cross-process locks and a health check — was a whole further
   layer, and it exposed a defect the stages alone never could (§2b).

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

### Stage 0 — scan ✅ **BUILT** (`pipeline/scanner.py`, 2026-08-08)

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

### Stage 1 — OCR ✅ **BUILT** (`pipeline/ocr.py`, 2026-08-08)

Done and run over all 58 documents — 57 via `prebuilt-layout`, 1 via the
native fallback after Azure rejected it. 154 pages billed, ~$1.50.

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.ocr --dry-run     # free; candidates: 0
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.ocr --limit 1
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.ocr --retry-failed
```

**Every format goes to Document Intelligence**, including `.docx`/`.pptx`.
That was measured across 11 files and the measurement *reversed* the
initial plan to extract Office formats natively — see
`SOLUTION_ARCHITECTURE.md` §4.9 and `STATUS_AND_ROADMAP.md` §10. The short
version: DI bills a `.docx` as one page, so the saving was imaginary,
while native extraction costs merged-cell duplication and a second
markdown dialect.

`pipeline/native_extract.py` runs **only after DI fails**, and **never for
PDFs** — native PDF text was measured as garbage (Arabic presentation
forms in visual order), and silent garbage in the vector store is worse
than an honest failure.

Decisions worth not re-litigating:

- A **failure writes no `document_content` row** — its presence is the
  "OCR done" marker, so writing one would skip a paid step forever.
- **Thin results are stored and flagged, never failed** — a scan whose
  content lives inside images can never improve, and `failed` would
  re-bill it every run. Documents that genuinely failed are excluded from
  the next run by default (`--retry-failed` opts back in).
- **Commit per document, not per run** — a batch dying at document 40 must
  not roll back 39 already paid for.

### Stages 2+3 — chunk, embed, index ✅ **BUILT** (`pipeline/indexer.py`)

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.indexer --dry-run   # free; candidates: 0
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.indexer --project PRJ-05
```

**Built as ONE module, not the two below.** Chunking is free and
deterministic; embedding is billed per token. A boundary between them ends
holding 678 vectors nothing has stored — needing an in-memory hand-off or a
temp table that exists only to bridge two modules. Full reasoning in
`SOLUTION_ARCHITECTURE.md` §4.10.

Four rules, each a *silent wrong answer* if broken:

- **Deterministic point IDs** (`uuid5` over `link_id:chunk_index`) — this is
  what makes rule 5 below true for vectors, not just rows. The namespace
  UUID must never be regenerated.
- **Delete this document's vectors before writing new ones.** Upserting
  alone leaves orphans when an edited document yields fewer chunks, and
  they stay retrievable.
- **Embed `chunk.text`, not `chunk.body`** (as the brief already said).
- **Re-sort embedding results by the API's own `index`** — arrival order
  pairs every vector with the wrong payload.

Verified: 678 chunks, 9 collections, Qdrant matching Postgres per project,
**0 cross-project contamination across all 678 payloads**, re-run reports 0
candidates. Record in `STATUS_AND_ROADMAP.md` §11.

⚠️ **Do not "fix" retrieval by changing chunking.** Arabic queries rank the
correct chunk 2nd–3rd rather than 1st, and the obvious theory — that HTML
table markup dilutes the vectors — was tested and **refuted** (§11c). That
is a ranking problem for the chat backend.

The original stage contracts, kept for reference:


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
It is now **live on 678 points** — changing it means re-indexing the whole
corpus. Treat it as a contract.

Then set `indexing_status='indexed'`, `indexed_at`, `chunk_count`, and
clear `error_message`.

---

## 2b. Operating it — the layer the brief did not describe

`pipeline/tasks.py`, `pipeline/locking.py`, `pipeline/status.py`. The brief
ended at "the stages work", which is not the same as a pipeline anyone can
run.

```bash
docker compose exec pipeline-worker python -m pipeline.tasks ingest --wait
docker compose exec pipeline-worker python -m pipeline.tasks ingest --project PRJ-05 --wait
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.status        # exit 0 healthy / 1 not
```

**It runs itself.** `mof-pipeline-beat` fires a full ingest daily at
**13:00 `Asia/Amman`**; dropping a file into a project folder is the entire
procedure. Verified by doing exactly that — a new Arabic `.docx` was picked
up unaided ~60s later, OCR'd and indexed, then removed again.

**Containerised, not a host process.** Beyond restart-safety: Celery's
prefork pool does not work on Windows, so a host worker is stuck on
`--pool=solo` with no concurrency.

**A wall-clock schedule, and the timezone is part of it.** Containers run
on UTC, so `hour=13` alone fires at 16:00 in a UTC+3 deployment. An
interval would also drift against the clock on every beat restart.

**Concurrency is not idempotence.** Every stage was already idempotent —
that stops a *repeat*, not an *overlap*. Two runs starting together both
see the same document as un-OCR'd and both get billed. `locking.py` takes a
Redis lock per stage, **from the CLI as well as the worker**, since the
likeliest collision is someone typing `python -m pipeline.ocr` while the
schedule fires. A busy lock skips rather than queues; the lock has a TTL so
a killed worker cannot wedge the pipeline, and an owner token so a run that
overran its TTL cannot delete its successor's lock.

### 2b-i. The defect containerising exposed

`document_links.file_path` was **absolute**. Invisible while one machine
ran everything; the moment the Linux worker mounted the same corpus at
`/data`, host and container each rewrote all 58 rows every run and Stage 1
could not open a file the other had recorded. It is now stored **relative
to `DOCUMENT_ROOT`**.

That migration was free only because of a decision made three stages
earlier: `PATH_UPDATED` deliberately does not reset `indexing_status`. Had
a relocated file been treated as `MODIFIED` — which this brief's original
three-outcome table implied — the fix would have silently re-OCR'd the
entire corpus.

⚠️ `PATH_UPDATED` is now reachable **only** for rows written before that
fix, since `file_path` equals `<project>/<filename>` — the row's own unique
key. It is a migration path, not a live case.

### 2b-ii. What `status` checks that nothing else can

Postgres and Qdrant are written in one per-document transaction but are
**separate systems**. A worker killed between the Qdrant upsert and the
Postgres commit leaves them disagreeing — the row says `indexed`, every
stage agrees there is no work left, and nothing notices. `pipeline.status`
compares them per project. Validated by deleting 5 vectors: it reported
`NEEDS ATTENTION`, named the mismatch, exited 1.

Full observability (OTLP → Grafana) is deliberately deferred to the chat
backend, which needs Langfuse alongside it.

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
5. **Idempotent.** Running twice must not duplicate rows or vectors. All
   three stages meet this: a second run reports `rows written 0`,
   `candidates : 0`, and `candidates : 0` respectively. For vectors it took
   deterministic point IDs — upserting random IDs would duplicate the whole
   index on every run.
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
- Full corpus run, measured: OCR ~5 minutes and ~$1.50; indexing ~100
  seconds and under a cent. Whole project to date: **~$2.42**.
- Test on **one project first** before all 58 — `PRJ-05` is the cheapest
  (4 documents), `TEST-PROJECT` the most revealing.
- **Chunk before you embed.** Chunking is free, so running it over the
  whole corpus with no embedding is a complete safety check that costs
  nothing — it caught nothing this time, which is exactly what made it
  worth the two minutes (`STATUS_AND_ROADMAP.md` §11a).
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
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/ -q     # 125 pass

docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data -c \
  "SELECT indexing_status, count(*) FROM document_links GROUP BY 1;"
# indexed | 58

docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data -c \
  "SELECT count(*) FROM document_content;"     # 58

# All three stages should report nothing to do:
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.scanner --dry-run  # rows written : 0
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.ocr     --dry-run  # candidates : 0
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.indexer --dry-run  # candidates : 0
```

**Seeing the vectors** — the Qdrant dashboard at
http://localhost:6333/dashboard loads without auth, but every API call
needs the key, so paste `QDRANT_API_KEY` into its **API Key** box first.
Without pasting anything:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -c "from qdrant_client import QdrantClient; from pipeline.config import get_settings; s=get_settings(); q=QdrantClient(url=s.qdrant.url, api_key=s.qdrant.api_key); [print(f'{c.name:<26} {q.count(c.name).count:>4}') for c in q.get_collections().collections]"
# 9 collections, 678 points total
```

**The real test has now been run.** A project manager's question does
return the right passage — *"ما هي أعلى المخاطر المسجلة في المشروع؟"*
against `project_PRJ-03` returns that project's risk register as both top
hits. The remaining quality gap is **ranking** on Arabic, not retrieval:
see §11c of `STATUS_AND_ROADMAP.md` before attempting to fix it, because
the obvious fix was already tested and rejected.

---

## 6. Known-unknowns, flagged honestly

**Closed by the Stage 1 run (2026-08-08):**

- ~~Only 4 of 58 documents have been through OCR~~ — **all 58 now have
  markdown.** `.pptx` and the real English charters are no longer untested
  shapes for OCR (they remain untested for *chunking*).
- ~~Arabic paragraph order looked reversed~~ — **does not reproduce.**
  Verified structurally (risk IDs ascend in document order) and as prose.
  `SOLUTION_ARCHITECTURE.md` §4.1.

**Still open:**

- **Text inside images/diagrams is mostly not extracted.** Counted
  precisely on the scanned sample: of 11 `<figure>` blocks, 7 were empty,
  3 held a single stray character, and 1 produced a real caption. So it is
  ~1-in-11 recovery, not zero — the earlier "11 empty" claim was an
  overstatement. Graphics-heavy documents will still have gaps. Two PDFs
  in the corpus returned 26 and 36 figure blocks respectively.
- ~~Chunking quality across the corpus is unmeasured~~ — **the chunker
  coped.** All 58 chunked with 0 mega-chunks, 0 zero-chunk documents, 0
  broken offsets, and 5 benign anomalies out of 678 (§11a). 16 documents
  have no headings at all and none collapsed.
- **Ranking on Arabic is the open quality problem.** ~0.40 against
  English's ~0.60, correct chunk often 2nd–3rd. Belongs to the chat
  backend — and the obvious chunking "fix" was tested and refuted (§11c).
- **Vertical (row-spanning) merges repeat** in native fallback output.
  Affects only documents DI already rejected — 1 so far.
- **Speaker-note capture is untested against a real deck** — neither deck
  in this corpus has notes.
- **1 of 58 needed the fallback.** That rate says little about a real
  SharePoint holding documents from many more tools.
