# Status & Roadmap — MOF Enjaz Chatbot

**Last verified:** 2026-08-08

---

## 1. Done

- [x] **Requirements understood.** BRD section read and verified against the
  source PDF; scope doc at `..\My_Prompts\SOLUTION_SCOPE.md`.
- [x] **Retrieval architecture designed** (RAG + live SPM + CAG, three
  sources per query). See `SOLUTION_ARCHITECTURE.md` §3.
- [x] **Key design decisions made and confirmed**: cloud models over
  on-prem; per-project separate Qdrant collections over a shared collection
  + RBAC filter. See `SOLUTION_ARCHITECTURE.md` §4.
- [x] **Compared against a colleague's separate solution** (different MOF
  use case, same BRD) — validated our stack choices, corrected a factual
  assumption about his OCR (not actually built), reconfirmed our decisions.
  See `SOLUTION_ARCHITECTURE.md` §5.
- [x] **Infrastructure fully built and verified.** 11 containers at the
  time: Postgres (SPM), Qdrant, Redis, full Langfuse v3 stack, Grafana
  LGTM, Portainer. **Now 13** — the ingestion worker and its scheduler were
  added on 2026-08-08 and are the first application containers in the
  stack. See `INFRASTRUCTURE.md`.
- [x] **"Nothing on C:" constraint satisfied** — Docker Desktop's engine
  disk relocated to D:, all service data bind-mounted to D: (except
  ClickHouse, which needs a named volume — still on D:, see
  `INFRASTRUCTURE.md` §4.1).
- [x] **Cloud model provider selection — all Azure, all provisioned and
  verified live**, across **two separate Azure accounts**:
  - **LLM** (account #1): Azure OpenAI `gpt-5.4-mini` via an Azure AI
    Foundry unified endpoint (`*.services.ai.azure.com` — not the classic
    `*.openai.azure.com` resource format). Verified live
    (`gpt-5.4-mini-2026-03-17`, confirmed working in Arabic).
  - **Embeddings** (account #2): Azure OpenAI `text-embedding-3-small`
    (deployment name saved truncated as `text-embedding-3-S` — cosmetic,
    harmless). Verified live — **1536-dim vectors**, now a fixed parameter
    for every per-project Qdrant collection.
  - **OCR** (account #2): Azure AI Document Intelligence, resource
    `DI-Enjaz` (East US, Standard tier). Verified live — model choice is
    **`prebuilt-layout`** (not `prebuilt-read`) with
    `outputContentFormat=markdown`, chosen for table/structure extraction
    and reading-order handling on Arabic/English/mixed content.

  See `SOLUTION_ARCHITECTURE.md` §4.1 for full reasoning.
- [x] **Application tech stack decided**: Python + FastAPI, raw SQL
  migrations (no ORM), Celery (task queue over Redis), LangChain (RAG
  orchestration). See `SOLUTION_ARCHITECTURE.md` §4.5 for the full
  reasoning and tradeoffs weighed for each.
- [x] **Git repository initialized** — baseline commit with infra + all
  documentation + the scope doc. `infra/.env`, `.claude/settings.local.json`,
  and `Tools ACC/` (app attachment storage) correctly excluded via
  `.gitignore`.

---

## 2. Not started yet

- [x] **SPM mock data manifest delivered and saved** —
  `mock-data/spm_manifest.json`: 8 projects (status mix: 2 on-track, 2
  at-risk, 2 delayed, 2 completed), 4 mock AD users (3 single-project, 1
  multi-project — tests the switcher), 2 projects deliberately unassigned
  to any test user (tests isolation), and a 45-document manifest across 7
  document types (Project Charter, Weekly Status Report, Meeting Minutes,
  Risk Register, Budget/Financial Report, Handover Document, Lessons
  Learned) with realistic language distribution (24 Arabic / 18 mixed / 3
  English) and table coverage (40/45 docs) for OCR testing. User-authored
  via a Claude chat prompt, reviewed and committed as-is.
- [x] **All 45 fictional document files generated** at
  `D:\MOF_DataSample\<ProjectCode>\<filename>` — verified byte-for-byte
  against the manifest (45 expected, 45 present, filenames match exactly).
  Generated via 8 parallel background agents (one per project), each
  producing real `.docx` (python-docx, explicit RTL/bidi XML, Arabic-
  capable fonts) and `.pdf` (reportlab + arabic_reshaper/python-bidi,
  embedded Arabic-capable TTF fonts) files with genuine formatted tables
  and section-by-section Arabic/English interleaving where specified —
  not stubs. Content is internally consistent per project (budget/dates/
  status match across a project's own charter, status reports, risk
  register, budget report, etc.).
- [x] **Real sample documents placed** — user overrode the earlier
  per-project triage: instead of splitting into 7 separate real projects,
  all 13 non-excluded real files (13 of 15 — excluded the 2 duplicate
  copies of our own BRD, which aren't project content at all) are grouped
  as **one single project, `TEST-PROJECT`**, at
  `D:\MOF_DataSample\TEST-PROJECT\` (original filenames, untouched —
  includes the 3 generic/off-topic templates and the 1 scanned/OCR-only
  PDF that were previously flagged as candidates for exclusion; user's
  "deal with them as a group" instruction superseded that finer-grained
  proposal). See §7 below for what's in it.
- [x] **SPM Postgres `projects` table created and seeded.**
  `infra/postgres-spm/migrations/001_create_projects_table.sql` (schema:
  `code` PK, `name_ar`/`name_en`, `description_ar`/`description_en`,
  `status`, `start_date`, `planned_end_date`, `budget_sar`,
  `sponsoring_department_ar`/`_en`, `created_at`) and
  `002_seed_projects.sql` (9 rows: `PRJ-01`–`PRJ-08` from the manifest +
  `TEST-PROJECT`), both applied directly to the running `mof-postgres-spm`
  container and verified (row count, Arabic text integrity). **This is a
  manually-applied migration, not an init-script** — `init/` only runs on
  a fresh/empty volume; `migrations/` is the new home for ongoing
  additive schema changes, applied via
  `docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data < file.sql`.
  Every project folder under `D:\MOF_DataSample\` now has a matching SPM
  row — the gap flagged earlier (real project folders with no SPM entry)
  is closed.
- [x] **Full SPM schema created and seeded**: `users` (4 mock AD users),
  `project_assignments` (6 rows — 3 single-project, Noura on 3 projects
  to exercise the switcher; `PRJ-07`/`PRJ-08`/`TEST-PROJECT` correctly
  have zero assignments), `document_links` (58 rows — 45 fictional + 13
  real, `doc_type` deliberately free text not a fixed enum, 14 distinct
  types across the seeded data, `indexing_status` defaults to `pending`
  with a check constraint, `file_path` holds the path **relative to
  `DOCUMENT_ROOT`** — e.g. `PRJ-01/charter.docx`. It held an absolute path
  until 2026-08-08; see §12 for why that was wrong). Migrations `003`–`009` in
  `infra/postgres-spm/migrations/`, all applied and verified (row counts,
  isolation query, per-user assignment counts). Informed by BRD user
  stories 4–5 (AC08–AC11) — `project_assignments` **is** the AC09 access
  boundary: a project with no row there is invisible to every user by
  construction, not by application logic.
- [x] **Pre-pipeline hardening (points 1–4), each explained before applying:**
  - **`document_content` table** (migration `010`) — caches OCR markdown +
    the full DI JSON response, so a chunking change never forces a re-OCR.
    Row presence doubles as the "OCR done" marker, so a failed embed step
    never re-pays for OCR.
  - **Diagnostics columns** (`011`) — `error_message`, `indexed_at`,
    `chunk_count`. A failed batch becomes debuggable by query instead of
    by re-running and watching logs.
  - **Change detection** (`012`) — `file_hash` (SHA-256) +
    `file_size_bytes`, letting the scanner tell new / unchanged / modified
    apart. Closed a documented open question in
    `SOLUTION_ARCHITECTURE.md` §3.3.
  - **Migration runner** (`infra/postgres-spm/apply_migrations.py`) —
    single-transaction application, records only after success, stores and
    re-verifies checksums to catch edited migrations, pre-flight container
    check. Plus `.gitattributes` and Python `.gitignore` entries.
    See `INFRASTRUCTURE.md` §7b.
- [x] **Row-level security** (migration `013`) — SQL isolation is now
  structural rather than filter-dependent. A `SELECT`-only `spm_app` role
  scoped by RLS policies; the pipeline keeps `spm_admin`. Fail-closed when
  unscoped. See `INFRASTRUCTURE.md` §7c and `SOLUTION_ARCHITECTURE.md`
  §4.2b. **Verified with 7 tests** including the pooled-connection
  `SET LOCAL` reset.
- [x] **Chunker** (`pipeline/chunker.py`) — structure-aware, markdown-in
  contract so on-prem OCR can substitute later. Design corrected by
  testing against 4 real OCR'd documents. See `SOLUTION_ARCHITECTURE.md`
  §4.6. *(Not the app skeleton — one standalone module.)*
- [x] **Ingestion pipeline — Stage 0 (discovery)** (`pipeline/scanner.py`,
  `pipeline/db.py`). Walks `DOCUMENT_ROOT`, hashes every file, upserts
  `document_links`. **Run over all 58 documents**; the seeded `file_path`
  placeholders are gone and every row carries a real SHA-256. Verified by
  hashing all 58 files independently and comparing to the database
  (58/58 hash + size + path match, 58 distinct hashes), by `certutil`
  agreeing with one stored hash, and by a second run reporting
  `unchanged 58, rows written 0`. **35 tests, all 8 mutations caught** —
  see §9. The manifest is never read: discovery is the filesystem.
- [x] **Ingestion pipeline — Stage 1 (OCR)** (`pipeline/ocr.py`,
  `pipeline/native_extract.py`). **All 58 documents extracted** — 57 via
  Azure Document Intelligence `prebuilt-layout`, 1 via the native fallback
  after Azure rejected it. 154 pages billed, 3.3 MB stored in
  `document_content`. Re-running finds **0 candidates**, so an unchanged
  corpus is never re-billed. Every format goes to Document Intelligence —
  a decision **measured across 11 files, which reversed the initial
  recommendation** (§10 below, reasoning in `SOLUTION_ARCHITECTURE.md`
  §4.9). 55 tests; all 21 mutations across the two modules caught.
- [x] **Ingestion pipeline — Stages 2+3 (chunk → embed → index)**
  (`pipeline/indexer.py`, built as **one** module — reasoning in
  `SOLUTION_ARCHITECTURE.md` §4.10). **All 58 documents indexed**: 678
  chunks, 295,232 tokens, 9 per-project Qdrant collections, Qdrant point
  counts matching `chunk_count` on every project, **0 cross-project
  contamination across all 678 payloads**, re-run reports 0 candidates.
  23 tests; **all 15 mutations caught first pass**. Retrieval verified
  with real questions in Arabic and English (§11).
- [x] **The ingestion pipeline is complete end to end** — scan → OCR →
  chunk → embed → index. Every stage is free to re-run on an unchanged
  corpus. Total spend across the whole project: **~$2.51**.
- [x] **Operational health check** (`pipeline/status.py`) — one command
  answering when ingestion last succeeded, whether anything failed, and
  **whether Qdrant still agrees with Postgres**. That last check is the one
  nothing else performs: the two are written in a single per-document
  transaction but are separate systems, so a worker killed between the
  Qdrant upsert and the Postgres commit leaves them disagreeing while every
  stage still reports no work left. Exit 0 healthy / 1 needs attention, so
  it works as a cron or container check. **Verified by deliberately
  deleting 5 vectors** — it went red, named the mismatch, and exited 1.
  Full observability (OTLP into Grafana) is deliberately deferred to the
  chat backend, which needs Langfuse alongside it; designing that layer for
  the pipeline alone would mean designing it blind.
- [x] **The pipeline is operational, not just working** (2026-08-08).
  It runs as a **containerised Celery worker** (`mof-pipeline-worker`) with
  a scheduler (`mof-pipeline-beat`), both healthchecked and
  `restart: unless-stopped`. Cross-process stage locks stop a scheduled run
  and a hand-typed CLI run colliding — the failure idempotence cannot cover.
  `document_links.file_path` became **relative to `DOCUMENT_ROOT`**, which
  is what made the host and the container stop fighting over every row.
  Verified end-to-end from a clean slate. See §12.
- [x] **Chunker test suite** — 26 tests in `tests/test_chunker.py`
  (the suite is now 102 across four modules), synthetic cases plus
  three real Document Intelligence fixtures. **Validated by mutation
  testing**, not by the suite passing: removing table-header repetition,
  disabling the small-chunk merge, and reverting offsets to `str.find()`
  are each caught by the intended test. Found one real bug (`token_count`
  computed from `.body` in the merge path but `.text` in `emit()`).
- [ ] **Chunker follow-ups, deferred deliberately** until the pipeline can
  measure real retrieval: the 54 untested documents (`.pptx` and real
  English charters especially), and confirming whether the Arabic
  line-order oddity is a synthetic-file artifact.
- [ ] **Workflow engine / chat backend.** Needs: language detection +
  rejection of unsupported languages (AC01), intent routing (special vs.
  general/SPM-retrieval, AC02), the three-source retrieval composition
  (§3 of `SOLUTION_ARCHITECTURE.md`), grounded responses with citations
  (AC06), safe fallback for out-of-scope/missing-knowledge queries (AC07),
  5-second response budget (AC08). **Must validate the requested project
  against `project_assignments` before opening a session** — RLS cannot
  cover that gate.
- [ ] **Auth**: AD SSO + OTP + RBAC. Real AD isn't available in dev — needs
  a mock per the BRD's own practical note (`SOLUTION_SCOPE.md`, "Practical
  note for a demo/development build"). True OTP is a hard external
  dependency — must be flagged as deferred, not faked to look
  production-ready.
- [ ] **Frontend**: chat interface, project switcher, knowledge-base
  view-only page, language switcher + RTL support, document upload UI.
- [ ] **OCR approach.** Unresolved for this project; also unresolved in the
  colleague's reference build — nothing to copy there.

---

## 3. Waiting on user action

*(none currently — Docker Desktop autostart was enabled 2026-08-04, see
`INFRASTRUCTURE.md` §4.4)*

---

## 4. Decisions log (chronological, don't re-litigate without reading the reasoning)

| Date | Decision | Reasoning (full detail in) |
|---|---|---|
| 2026-08-04 | Relocate Docker Desktop's engine disk to D: via GUI, not raw `wsl` commands | `INFRASTRUCTURE.md` §2 |
| 2026-08-04 | Langfuse v3 (full stack) over v2 (single-Postgres) | Chosen explicitly among presented options |
| 2026-08-04 | Grafana LGTM all-in-one image, separate role from Langfuse (app timings/logs/traces vs. LLM content) | `SOLUTION_ARCHITECTURE.md` (implicit in stack), `INFRASTRUCTURE.md` §1 |
| 2026-08-04 | Two separate Postgres containers (SPM data vs. Langfuse's own) | Chosen explicitly among presented options |
| 2026-08-04 | ClickHouse on a named volume, not bind mount | `INFRASTRUCTURE.md` §4.1 (root-caused, not arbitrary) |
| 2026-08-04 | Keep cloud models for LLM/OCR/embeddings, not on-prem | `SOLUTION_ARCHITECTURE.md` §4.1 and §5 — confirmed twice |
| 2026-08-04 | Per-project separate Qdrant collections, not shared + RBAC filter | `SOLUTION_ARCHITECTURE.md` §4.2 — full reasoning, not a default |
| 2026-08-04 | Add Redis to infra proactively for the future ingestion pipeline | `SOLUTION_ARCHITECTURE.md` §4.3 |
| 2026-08-04 | All-Azure cloud providers: Azure OpenAI (LLM + embeddings) + Azure AI Document Intelligence (OCR) | Chosen explicitly among presented options — fits the existing Microsoft/SharePoint/AD ecosystem |
| 2026-08-04 | OCR model: `prebuilt-layout` (Markdown output), not `prebuilt-read` | `SOLUTION_ARCHITECTURE.md` §4.1 — tables + structure + reading order matter for chunking, user's own suggestion, confirmed |
| 2026-08-04 | Application tech stack: Python + FastAPI, raw SQL migrations (no ORM), Celery (task queue), LangChain (RAG orchestration) | `SOLUTION_ARCHITECTURE.md` §4.5 — each weighed against an alternative (Arq, hand-rolled) with full tradeoffs; Celery and LangChain both went against the initial recommendation, deliberately, after seeing the tradeoffs |
| 2026-08-04 | Mock corpus/AD shape decided (6-8 projects, broader document-type set, real files through real OCR, stored at `D:\MOF_DataSample`) | §2 above |
| 2026-08-04 | SPM mock data manifest delivered by user (8 projects/4 users/45-doc manifest) and committed to `mock-data/spm_manifest.json` | §2 above |
| 2026-08-04 | All 45 fictional document files generated (8 background agents, one per project) and verified against the manifest | §2 above, §7 below |
| 2026-08-04 | 15 real sample documents supplied by user; grouped as one `TEST-PROJECT` (13 files) instead of 7 separate real projects — user's explicit override of the initial per-project triage; 2 copies of our own BRD excluded (not sample content) | §7 below |
| 2026-08-04 | SPM Postgres `projects` table created (`infra/postgres-spm/migrations/`, applied manually, not via init script) and seeded with all 9 project rows (8 fictional + TEST-PROJECT) | §2 above |
| 2026-08-04 | Downloaded DBeaver CE (portable, no installer) to `D:\MOF_Installations\tools\dbeaver-ce-portable\` for visual Postgres browsing | User request — everything installed for this project stays under D: |
| 2026-08-04 | Full SPM schema completed: `users`, `project_assignments`, `document_links` tables created and seeded (migrations 003–009) | §2 above — `document_links.doc_type` is free text by design, not a closed enum |
| 2026-08-05 | Cache OCR output in `document_content` rather than discarding it (migration `010`) | OCR is paid per page and chunking is untuned; without the cache every chunking change re-OCRs the corpus |
| 2026-08-05 | Change detection by **content hash**, not filesystem mtime (`012`) | mtime is only a hint — copying resets it, SharePoint sync may not preserve it. Stale content producing confident wrong answers beats a slower scan |
| 2026-08-05 | Migration runner with per-migration transactions + checksum drift detection | `INFRASTRUCTURE.md` §7b — psql autocommits per statement, so a multi-statement migration could otherwise half-apply |
| 2026-08-05 | Checksums normalise line endings before hashing | Raw-byte hashing broke under `core.autocrlf=true`, flagging every file as drifted on a fresh clone. `.gitattributes` added as a second defence |
| 2026-08-05 | **Row-level security** with a separate SELECT-only `spm_app` role (`013`) | `SOLUTION_ARCHITECTURE.md` §4.2b — SQL was still filter-based, the same weakness rejected for Qdrant in §4.2 |
| 2026-08-05 | Chunker contract is **markdown in**, not "Azure DI output" | `SOLUTION_ARCHITECTURE.md` §4.6 — concentrates on-prem migration risk in OCR alone, where it's substitutable |
| 2026-08-05 | Chunking uses a **fallback ladder**, not heading-splitting alone | Testing showed 2 of 4 real documents have no headings at all; heading-only splitting would produce one unusable mega-chunk for half the corpus |
| 2026-08-08 | Stage 0 decides **five** outcomes per file, not the three in the brief — `PATH_UPDATED` added | `SOLUTION_ARCHITECTURE.md` §4.8. `DOCUMENT_ROOT` is a setting, so repointing it leaves identical bytes at new paths. Treating that as UNCHANGED strands every stored `file_path`; treating it as MODIFIED re-OCRs the whole corpus for a move |
| 2026-08-08 | A `document_links` row whose file is gone from disk is **reported, never touched** | `SOLUTION_ARCHITECTURE.md` §4.8. One mis-set `DOCUMENT_ROOT` makes every project look empty, and deleting on that signal cascades away the paid-for OCR cache. Chosen over marking it `failed` (conflates two different facts, and the AC10 page would show a red badge for a merely-deleted file) and over adding a `missing` status (a migration for a case with zero observed instances) |
| 2026-08-08 | `MODIFIED` deletes the `document_content` row; `BACKFILL` keeps it | That row's presence **is** the "OCR done" marker, so a stale one left behind makes Stage 1 skip re-OCR and change detection silently does nothing. A NULL hash means "never scanned", not "content changed" — no evidence the cache is stale, and OCR is billed per page |
| 2026-08-08 | New files get `doc_type = 'Unclassified'`; existing `doc_type`/`language`/`contains_table` are **never overwritten** | The filesystem cannot know a document's type — classification belongs after OCR, where the text can be read. Guessing from the filename would work only because we generated the fictional filenames ourselves, which is exactly the demo-corpus coupling Stage 0 exists to avoid |
| 2026-08-08 | Discovery is **recursive**; `filename` stores the project-relative POSIX path | Identical to the bare name for today's flat corpus (verified: 0 spurious inserts across all 58 rows), but a real SharePoint export has folders, and a non-recursive walk would make those documents silently invisible — no error, just missing answers |
| 2026-08-08 | **Every format goes to Document Intelligence**, including `.docx`/`.pptx` whose text is free to read from XML | `SOLUTION_ARCHITECTURE.md` §4.9. Measured across 11 files; **the measurement reversed the recommendation**. The saving native extraction promised evaporates because DI bills a `.docx` as one page — the whole corpus is ~$3 — while native costs merged-cell duplication, a crash, and a second markdown dialect for the chunker |
| 2026-08-08 | **No native PDF fallback**, ever | Native PDF text is unusable, not merely worse: 36 Arabic characters against DI's 2,623, because text arrives as Arabic presentation forms in visual order. A fallback here would push confident garbage into the vector store with no error anywhere |
| 2026-08-08 | **Native Office extraction as a fallback only**, after DI fails | DI rejected a valid 749 KB `.docx` as "corrupted" — it was repackaged by a non-Word tool. Without a fallback that document contributes nothing at all. `ocr_model` records which extractor ran, and a rescued document bills 0 pages |
| 2026-08-08 | A failed OCR writes **no** `document_content` row; a thin result **does** | The row's presence is the "OCR done" marker, so writing one on failure skips a paid step forever. Conversely a scan whose content lives in images can never improve, so marking it `failed` would re-bill it on every future run — it is stored, flagged, and left for a human |
| 2026-08-08 | Stage 1 commits **per document**, not per run | Unlike Stage 0, whose work is cheap and repeatable, every row here cost money. A batch dying on document 40 must not roll back the 39 already paid for |
| 2026-08-08 | Stages 2 and 3 built as **one module**, not two as the brief describes | `SOLUTION_ARCHITECTURE.md` §4.10. Chunking is free, embedding is paid; a boundary between them ends holding 678 vectors nothing has stored, needing either an in-memory hand-off or a temp table that exists only to bridge two modules. An embedding that isn't persisted is money thrown away |
| 2026-08-08 | Vectors are **deleted before upsert**, not upserted alone | An edited document yielding fewer chunks leaves surplus points behind. They stay retrievable and answer current questions with deleted content, with nothing reporting an error |
| 2026-08-08 | Embedding results re-sorted by the API's own `index` | Trusting arrival order pairs every vector with the wrong payload — invisible, and makes retrieval confidently wrong |
| 2026-08-08 | Indexing **retries failed documents by default** — opposite of Stage 1 | Cost asymmetry, not inconsistency: re-OCR re-bills per page, re-embedding the whole corpus costs under a cent. When retrying is nearly free, hiding failures serves nobody |
| 2026-08-09 | **Source arbitration: present BOTH sources, attribute each, let the PM decide** — never silently pick a winner | `SOLUTION_ARCHITECTURE.md` §3.3. User's decision, and the right one: the assistant is purely informational and has no authority to adjudicate a project judgement. Ranking the sources would settle the dispute invisibly, so the PM would never learn the two disagree — often the most useful thing in the answer. Two grounded, cited claims is a fuller answer than one, not a hedge |
| 2026-08-08 | Ingest runs on a **daily wall-clock schedule** (13:00 Asia/Amman), not an interval | An interval drifts against the clock every time beat restarts, so "1 PM" slowly stops being 1 PM. The **timezone is part of the schedule**: containers run on UTC, so `hour=13` alone fires at 16:00 in a UTC+3 deployment |
| 2026-08-08 | Operational monitoring is a **status command**, not OTLP — for now | `pipeline/status.py`. Full observability belongs with the chat backend, which needs Langfuse for LLM traces alongside Grafana for timing; building it for the pipeline alone means designing it blind to its main consumer. The status command has no design decisions that get thrown away when that arrives |
| 2026-08-08 | `document_links.file_path` stored **relative to DOCUMENT_ROOT**, not absolute | §12. Found by containerising: the host wrote `D:\...` and the worker wrote `/data/...`, so each rewrote all 58 rows every run and Stage 1 could not open a file the other had recorded. A relative path is valid in both |
| 2026-08-08 | Pipeline runs as a **containerised** Celery worker, not a host process | `SOLUTION_ARCHITECTURE.md` §4.11. Matches every other service (restart-safe, data on D:) and sidesteps Celery's prefork pool not working on Windows, which would have forced `--pool=solo` with no concurrency |
| 2026-08-08 | **One Celery task per stage**, not per document | Coarse retries are safe *because* the stages are idempotent — a retried OCR task over a finished corpus calls Azure zero times. Per-document fan-out buys parallelism 58 documents don't need |
| 2026-08-08 | Stage locks are taken by **entry points**, and by the **CLI** as well as the worker | A lock only the worker respected would miss the most likely collision: someone running `python -m pipeline.ocr` while the schedule fires. Taking it inside `run()` instead would deadlock the worker against itself |
| 2026-08-08 | `ingest` runs stages **inline**, not as a Celery chain | A chain returns a graph of task ids immediately, so a scheduled ingest could not report success or failure |
| 2026-08-08 | **Rejected**: stripping HTML markup from embedded text | Tested and refuted (§11c). The premise measured true (26% of table-chunk tokens are markup) but the mechanism did not: high-markup and low-markup chunks lose the same similarity when stripped. Changing it would have forced a full re-index on the strength of one query |

---

## 5. Immediate next step

**The ingestion pipeline is finished and running unattended.** Next is the
chat backend — the first component a user actually touches.

📄 **Its brief is `NEXT_BUILD_CHAT_BACKEND.md`** — acceptance criteria,
the AC09 session gate, suggested build order, non-negotiable rules and
known-unknowns. Written in the same shape as the pipeline brief, which
caught five places that build had to deviate from what was specified.

| Stage | Does | Writes | State |
|---|---|---|---|
| **0 — Scan** | Walk `DOCUMENT_ROOT`, hash each file, upsert rows | `document_links` (seeded `file_path` placeholders **replaced with real discovery**) | ✅ done, 58/58 |
| **1 — OCR** | Document Intelligence `prebuilt-layout` → markdown, native fallback for rejected Office files | `document_content` | ✅ done, 58/58 |
| **2+3 — Chunk, embed, index** | `pipeline/chunker.py` → Azure embeddings → `project_<code>` collections | Qdrant; `indexing_status`, `chunk_count`, `indexed_at` | ✅ done, 678 vectors |

What the chat backend walks into, flagged in advance:

- **Ranking, not retrieval, is the open quality problem.** The right chunk
  comes back for Arabic questions, but often 2nd or 3rd rather than 1st,
  and Arabic scores ~0.40 against English's ~0.60. Reranking, hybrid
  search, or simply retrieving more candidates and letting the LLM choose
  are all untried. Do **not** reach for a chunking change first — that
  hypothesis was already tested and rejected (§11c).
- **The session gate is application code and cannot be delegated.**
  Validating that the requested project is genuinely one of the user's,
  against `project_assignments`, before the session opens. RLS and
  per-project collections both assume the session is scoped *correctly*;
  neither can tell a wrong-but-valid project from a right one
  (`INFRASTRUCTURE.md` §7c).
- **`SET LOCAL`, never `SET`** — a plain `SET` persists on a pooled
  connection and leaks the previous request's project scope into the next.
- **Source arbitration is decided** (2026-08-09, §3.3 of
  `SOLUTION_ARCHITECTURE.md`): when a weekly report and SPM's status field
  disagree, present **both**, attribute each with its date, and let the
  project manager decide. Never silently pick a winner.
- **Nothing emits traces yet.** Langfuse and Grafana are both running and
  both unwired.

## 6. Gotchas hit during this phase

- **Git Bash mangles UTF-8 in inline shell arguments.** Testing the Azure
  OpenAI endpoint with Arabic text via `curl -d '{"...عربي..."}'` produced
  garbled input the model couldn't read; writing the same JSON to a file
  first and using `curl --data-binary @file` worked correctly. Same class
  of issue as the colleague's "PowerShell mangles UTF-8" gotcha — different
  shell, same root cause (don't trust inline args with non-ASCII on
  Windows). Always test Arabic payloads via a file, not an inline `-d`.
- **Python's text mode writes CRLF on Windows.** Seed migrations generated
  by a Python script were born with CRLF while Write-tool files had LF —
  which surfaced later as spurious checksum drift. Related: `git`'s
  `core.autocrlf=true` rewrites line endings on checkout, so **hashing raw
  file bytes is unreliable on this machine**; normalise to LF first.
- **`str.find()` is the wrong way to recover a substring's position.** It
  returns the *first* match, which is simply wrong for repeated text — and
  returns nothing at all for synthesised content. Track positions while
  parsing instead. This produced wrong character offsets on 8 of 10 chunks
  before being caught.
- **Verify safety features by exercising them, not by reading them.** Both
  the checksum-drift bug and the chunker offset bug were invisible on
  inspection and obvious the moment the feature was actually tested.
- **A passing test suite proves nothing until a test has failed.** Mutation
  testing (deliberately breaking the code and confirming the right test
  catches it) exposed a **self-referential test**: it imported
  `MIN_CHUNK_TOKENS`, so mutating that constant moved the assertion along
  with it and the test passed against broken code. Assert against literals,
  not against the thing under test.
- **Commit working changes before mutating code.** Reverting a mutation
  with `git checkout` also discarded an uncommitted bug fix, which then
  showed up as a mysterious failure after "restoring".
- **Postgres `LIKE` eats Windows path separators.** `\` is `LIKE`'s default
  escape character, so
  `file_path LIKE 'D:\MOF_DataSample\TEST-PROJECT\%'` silently matched
  **zero** of 13 correct rows — it reads as escaped `M`, `T` and a literal
  `%`. This looks exactly like a data bug and isn't one. Use
  `starts_with(file_path, '...')` when verifying Windows paths, and treat a
  surprising `0` in a verification query as suspect before believing it.
- **Python byte literals are ASCII-only.** `b"version two — different"` is
  a `SyntaxError`, not a runtime surprise: `bytes can only contain ASCII
  literal characters`. Write `"...".encode("utf-8")` in tests that need
  non-ASCII bytes — which, in this corpus, is most of them.
- **A setting nobody wrote down is a setting nobody knows exists.**
  `config.py` read `DOCUMENT_ROOT`, `CHUNK_*` and the OCR model name via
  `_optional()` defaults, so everything worked — while none of them
  appeared in `.env` or `.env.example`. The whole "repointing at real
  SharePoint is a config change" claim rested on a variable that was
  literally undocumented. `.env.example` had also grown a duplicated
  `AZURE_DOCUMENT_INTELLIGENCE` block. Anything `config.py` reads belongs
  in `.env.example`, including the optional ones.
- **A documented optimisation that optimises nothing.** The schema notes
  called `file_size_bytes` a "cheap pre-check" that avoids hashing. It
  doesn't: a differing size proves the file changed, but the new hash still
  has to be computed in order to be *stored*, so hashing is never actually
  skipped. The column is worth keeping as a diagnostic — the claim about it
  was not.
- **Injecting a connection into a test also excludes the commit path.** The
  scanner's integration tests pass in a transaction they roll back, which
  is what keeps them clean — and it means the real
  `admin_connection()` commit was never exercised by any test. Closed by
  running the stage live against the corpus; worth remembering for every
  stage that follows. **It bit immediately**: Stage 1 shipped with
  `admin_connection().__enter__()`, which closed the connection the moment
  the un-held context manager was garbage-collected. All 14 tests passed,
  because not one of them used the owned-connection branch. Every real
  invocation died on the first document.
- **A guard whose value is diagnostic is invisible to a behavioural test.**
  Twice now, mutation testing deleted a guard and every test still passed:
  the missing-file check (`read_bytes()` raises anyway) and the
  Office-format check in the OCR fallback (`native_extract` refuses PDFs
  anyway). Both guards are worth keeping — they produce the error message
  a 58-document batch is actually debugged through — but that means the
  *message* is the thing to assert, not just the behaviour. Assume this
  pattern repeats.
- **`AmbiguousParameter`: a parameter used only in `IS NULL` has no
  inferable type.** `WHERE (%(project)s IS NULL OR code = %(project)s)`
  fails outright in psycopg. Cast it: `%(project)s::text`.
- **A test that saves and reloads a file may not reproduce the condition
  it is testing.** Setting `paragraph.style = None`, saving, and re-opening
  does not give a `None` style back — python-docx resolves it to a default
  on load. The guard against `None` was therefore never executed, and a
  mutation removing it survived. A stub object was the only way to
  reproduce what real non-Word documents do.
- **Azure's error text can be wrong about the cause.** Document
  Intelligence rejected a valid `.docx` with "the input content is
  corrupted or format is invalid". `testzip()` was clean and `python-docx`
  read 136 paragraphs. The real cause was an archive repackaged by a
  non-Word tool (zero-byte directory entries, no `theme1.xml`). Don't
  accept a provider's diagnosis of a local file without checking it.
- **Test the mechanism, not just the outcome — a plausible theory with a
  true premise can still be wrong.** Arabic queries returned raw `<table>`
  markup at the top, so the theory was that markup dilutes the vectors.
  The premise checked out (26% of table-chunk tokens are markup) and a
  single-query A/B showed the correct chunk moving from rank 3 to rank 2.
  Every signal pointed one way. Testing the *mechanism* across 5 queries
  and 75 samples refuted it outright: high-markup and low-markup chunks
  lose the same similarity when stripped (0.029 vs 0.027). The one-query
  result was noise. **Had it been acted on, it would have changed the
  payload contract and forced a full re-index for nothing.** A measurement
  that confirms your premise is not a measurement that confirms your
  explanation.
- **Containerising is a correctness test, not just a packaging step.** The
  absolute-`file_path` defect had existed since Stage 0 and was invisible
  while only one machine ever ran the pipeline. The moment a second
  environment mounted the same corpus at a different root, it showed up
  immediately — all 58 rows rewritten on every run, and Stage 1 unable to
  open a file the other environment had recorded. Anything storing an
  absolute path in shared state has this bug and does not know yet.
- **Idempotence and concurrency-safety are different properties.** Every
  stage was idempotent and had been verified as such; none of that stops
  two runs starting at the same time and both paying for the same OCR.
  Adding a schedule is what turned a theoretical race into a likely one.
- **Distinguish "the answer isn't in the results" from "the answer isn't
  first".** The Arabic retrieval looked broken until the results were read
  properly: the correct chunk was present every time, just ranked 2nd or
  3rd. Those are different problems with different owners — the first
  belongs to ingestion, the second to the chat backend.

---

## 8. Running the tests

```bash
.venv/Scripts/python -m pytest tests/ -q      # Windows
```

**261 tests, ~12s** — `test_retrieval_eval.py` 39 + `test_scanner.py` 38 +
`test_tasks.py` 27 + `test_chunker.py` 26 + `test_ocr.py` 23 +
`test_indexer.py` 23 + `test_rls.py` 23 + `test_native_extract.py` 22 +
`test_status.py` 15 + `test_config.py` 13 + `test_locking.py` 12.
*(Counted from `--collect-only`, not estimated — this number has needed
correcting three times when it was inferred instead.)*

`test_rls.py` is the only file that connects as **`spm_app`**. Every other
file uses `spm_admin`, which bypasses row-level security, which is exactly
why RLS went untested for so long (§13a). `test_retrieval_eval.py` stubs
the embedding API and Qdrant, so the evaluation harness is fully covered
without a paid call.

`test_locking.py` and `test_tasks.py` need **Redis** as well as Postgres —
they exercise the real lock, because atomic set-if-absent, expiry and
compare-and-delete are exactly the properties a fake dictionary would not
have.

**Most of the 261 tests need `mof-postgres-spm` running**, and
`test_locking.py` / `test_tasks.py` / `test_status.py` need **Redis** as
well. They create a throwaway
project, exercise the **real** schema and constraints, and always roll
back; nothing survives the run (verified: no stray rows, `document_content`
untouched). If the container is down they skip with a clear message rather
than failing obscurely, and each skip costs its connection timeout, so the
suite slows noticeably.

`test_ocr.py` stubs Azure; `test_indexer.py` stubs both the embedding API
and Qdrant. So the full write path of both paid stages — including their
failure paths, the native fallback, and the delete-before-upsert
ordering — is tested against the real schema **without a single paid call
and without touching the live vector store**.

**The degradation was observed for real**, not designed and assumed:
Docker Desktop restarted mid-session on 2026-08-08 and the suite reported
`52 passed, 9 skipped` exactly as intended. The same restart also
confirmed `INFRASTRUCTURE.md` §4.4 — all 11 containers came back
unprompted.

⚠️ Counting note: an earlier draft said "11 integration tests". That came
from a `pytest -k` run whose filter also matched pure unit tests with
similar names. The Docker outage gave the honest count. **Don't infer a
subset's size from a filtered run** — count the fixture, or read the skip
line.

Fixtures in `tests/fixtures/` are real Document
Intelligence output, kept deliberately: the chunker's design was corrected
three times by real markdown contradicting assumptions, so the suite
guards against regressions on real structure rather than only on tidy
synthetic input.

---

## 7. Real sample documents — final resolution (2026-08-04)

User supplied 15 real files (Desktop + Downloads) to use *alongside* the
fictional 45-document set — explicitly to keep the ingestion pipeline
"dynamic, not static to any specific case." Read all 15 (docx/pptx via
python-docx/python-pptx, PDFs via pdfplumber). Initial proposal was to
split them into 7 separate real projects with individual codes — **user
overrode this**: group all non-excluded real files as **one single
project, `TEST-PROJECT`**, not split by original source project.

**Excluded (2 of 15)**: both copies of
`وثيقة المتطلبات التفصيلية الموحدة - الوكيل الذكي 01-07-2026.pdf`
(Desktop + Downloads) — **this is our own project's BRD**, not sample
project content. This exclusion was kept even after the "treat as one
group" instruction, since including our own requirements doc as fake
project content would be actively wrong, not just a style choice.

**Included as `TEST-PROJECT` (13 of 15)** — placed at
`D:\MOF_DataSample\TEST-PROJECT\`, original filenames unchanged:
- `SDD_AI_Project_Charter_Generator_OnPrem.docx` + `__وثيقة المتطلبات التفصيلية - ميثاق المشروع.docx` (AI Project Charter Generator — English SDD + Arabic BRD)
- `Tanmiah_GenAI_POC_Proposal_Technical 1.pptx` (Tanmiah social-impact POC proposal)
- `_نموذج الأثر الاستباقي  2026  -  v2.pdf` (منصة همة status/dashboard export)
- `TMO001001_...Project Charter...docx` (Data Warehouse/BI charter)
- `MOZN Project Charter-V0.1.docx`
- `Emdad - Project Charter - V1 1.docx`
- `JEDCO002001 - Project Charter...docx`
- `وثيقة المتطلبات التفصيلية PowerBI وإعادة التصميم 01-07-2026.pdf` (formal NCGR-style Arabic BRD)
- `client_template.pptx` + `IC-Project-Risk-Management-Plan-Example-11690_WORD.docx` (generic "Athletic Center Renovation" template — previously flagged for exclusion, now included per the "group them" instruction)
- `sample-project-charter.pdf` (generic "XYZ Computer System" template — same)
- `_المنهجية التقنية للمشروع.pdf` (scanned/image-only PDF, near-zero extractable text via pdfplumber — a genuine real-world OCR test case for Document Intelligence)

**SPM row**: `TEST-PROJECT` exists in the `projects` table (§2 above,
status `'testing'`, no budget/dates — doesn't map to a real PMO project
record, it's explicitly a test/QA grouping).

---

## 9. Stage 0 — how it was actually verified (2026-08-08)

Recorded because "the tests pass" is not evidence, and §6 already contains
two bugs that were invisible on inspection.

**Run on `TEST-PROJECT` first** (13 real files — Arabic filenames, spaces,
a `.pptx`), then the full 58.

| Property | How it was proven |
|---|---|
| Discovery matches the seeded rows | 58 files → `insert 0`, `missing 0`. Any drift in how `filename` is derived would have surfaced as spurious inserts **plus** an equal number of rows reported missing — the two failures are linked, which is what makes the zero meaningful |
| Hashes are correct | `certutil -hashfile` (an independent tool) on `MOZN Project Charter-V0.1.docx` produced the exact string stored in Postgres |
| Every row matches disk | All 58 files re-hashed independently and compared: 58/58 match on hash **and** size **and** path; 58 distinct hashes |
| Idempotent | Second run: `unchanged 58`, `rows written 0` |
| Curated columns survive | `doc_type`/`language` unchanged; `0` rows became `'Unclassified'` |
| `--dry-run` is honest | Before/after both `58 rows, 0 hashed` |
| Tests leave nothing behind | After the suite: no stray project rows, `document_content` still empty |

**The live change-detection proof**, run end-to-end because the
integration tests inject a rolled-back connection and therefore never
exercise the real commit path:

1. Copied `TEST-PROJECT` to a scratch folder, repointed `DOCUMENT_ROOT` at
   it with **no code change** → `path_updated 13`, no re-OCR triggered.
2. Gave one document a cached `document_content` row and marked it
   `indexed`, then genuinely edited its bytes → `modified 1`,
   `unchanged 12`, `OCR cache rows dropped: 1`. Row went
   `indexed → pending`, hash changed, `indexed_at`/`chunk_count`/
   `error_message` all cleared.
3. Repointed back to the real corpus → `path_updated 12` + `modified 1`,
   ending in a state verified identical to disk.

**Mutation testing — all 8 caught**, each by the test written for it:
dropping the OCR-cache delete on MODIFIED; dropping it on BACKFILL too;
treating a moved file as UNCHANGED; clobbering the curated `doc_type`;
making discovery non-recursive; storing the bare filename instead of the
relative path; trusting size as a change signal; and letting `--dry-run`
write. Per §6, the code was committed *before* mutating.

---

## 10. Stage 1 — the extraction measurement, and how it was verified (2026-08-08)

### 10a. Native extraction vs Document Intelligence, 11 files

Run because paying for OCR on `.docx`/`.pptx` — formats whose text is
already in XML — deserved a challenge rather than an assumption. **The
decisive metric is the token-set diff**: character counts differ for
formatting reasons that don't matter, but a word one extractor found and
the other didn't is content genuinely lost.

| File (type) | Native | Azure DI | Verdict |
|---|---|---|---|
| Real deck, Arabic/mixed (`.pptx`) | 11,216 ch · 7,282 ar | 11,535 ch · 7,282 ar | **928/928 tokens shared, 0 unique either way** |
| Generic template (`.pptx`) | 8,642 ch | 9,042 ch | 7 tokens only DI found — slide-master copyright |
| Real Arabic BRD, 28 tables (`.docx`) | 501 cells, **48 duplicated** | 421 cells, **0 duplicated** | DI — merged cells |
| Real charter, English (`.docx`) | 23,098 ch | 24,553 ch | DI — recovers ToC |
| Generic risk plan (`.docx`) | 10,719 ch | 11,160 ch | DI, marginally |
| **Our generated Arabic (`.docx`)** | **crashed** (`style` is None) | 3,938 ch | DI |
| Real Arabic BRD, 19pp (`.pdf`) | 35,559 ch | 38,422 ch | **only 80 tokens shared** — native mangles RTL |
| Generic charter (`.pdf`) | 27,947 ch | 20,792 ch | native emits `CChhaarrtteerr` — every letter doubled |
| Arabic dashboard (`.pdf`) | 13,936 ch | 14,258 ch | **6 tokens shared.** Doubled digits `1155` |
| **Our generated Arabic (`.pdf`)** | **36** Arabic ch | **2,623** Arabic ch | native returns presentation forms in visual order |
| **Scanned, image-only (`.pdf`)** | **0 ch** | 3,625 ch | OCR mandatory |

**Billing, the finding that decided it:** DI bills a `.docx` as **1 page**
regardless of length (a 44,610-character charter = 1 page); `.pptx` bills
per slide; PDFs per page. The hypothesis that native extraction would
avoid paying for 64% of the corpus was therefore **wrong** — that 64% costs
about a cent per document.

### 10b. Stage 1 verification

| Property | How it was proven |
|---|---|
| The API is called correctly | Our DI call reproduced `tests/fixtures/scanned_arabic_figures.md` **byte-identically** — and again after the full pipeline stored it in Postgres |
| Never re-bills | Re-run reports **0 candidates** |
| Failure is honest | The one rejected file got `indexing_status='failed'`, an `error_message`, and **no `document_content` row** |
| Recovery works | `--retry-failed` rescued it natively in 0.13 s, **0 pages billed**, `ocr_model = 'native:python-docx'` |
| Corpus complete | **58/58** have markdown; 154 pages billed; 3.3 MB stored (from ~9.3 MB raw JSON, TOAST-compressed) |
| Arabic order | Resolved — see `SOLUTION_ARCHITECTURE.md` §4.1 |

**Mutation testing — 21 across the two modules, all caught**, but only
after three survivors exposed real test gaps (§6): a merged-cell fix that
could equally have been keyed off text instead of element identity; a
`style is None` guard that a save-and-reload test could never reach; and a
format guard whose only observable effect was the error message.

**Total spend: ~241 pages ≈ $2.41**, including the 87-page measurement.

---

## 11. Stages 2+3 — verification record (2026-08-08)

### 11a. The free dry run that came first

The chunker had only ever seen 4 documents, and embedding is billed per
token — so all 58 were chunked **without embedding** before any money was
spent. Chunking costs nothing, which makes this pure profit as a safety
check.

| Check | Result |
|---|---|
| Documents producing **zero** chunks | **0** |
| **Mega-chunks** (long document → 1 chunk) | **0** |
| **Broken `char_start`/`char_end`** | **0** of 678 |
| Missing breadcrumbs | **0** |
| Out-of-order chunk indices | **0** |
| Oversize (>960 tokens) | 3 (0.4%) |
| Undersize (<120 tokens) | 2 (0.3%) |

678 chunks · median 418 tokens · 295,232 total. All five anomalies were
inspected individually and are benign: the 1,057-token outlier is a
two-row table (a header plus one enormous row — row-splitting genuinely
cannot help), and both undersize chunks are `chunk_index 0` document cover
blocks, which are short but useful for "what is this document" questions.
**No chunker change was needed.**

Two things the run proved that only real data could:

- **The fallback ladder holds.** 16 of 58 documents have no headings at
  all, and not one collapsed into a single unusable chunk — the failure
  §4.6 was written to prevent.
- **Table row-splitting is load-bearing, not theoretical.** 16 documents
  had a table split, creating 116 extra parts. 347 of 678 chunks (51%)
  contain a table, which also retroactively justifies choosing
  `prebuilt-layout` over `prebuilt-read`.

### 11b. The indexed result

| Property | How it was proven |
|---|---|
| Vectors match the database | Qdrant point count equals `sum(chunk_count)` **per project**, 678 = 678 across 9 collections |
| **AC09 isolation** | Every one of the **678 payloads** scrolled and checked: `project_code` matches its collection. **0 contamination.** There is no shared index to misfilter |
| Idempotent | Re-run reports `candidates : 0` |
| Retrieval works | *"ما هي أعلى المخاطر المسجلة في المشروع؟"* → `project_PRJ-03` returns that project's risk register as **both** top hits (0.548, 0.509). *"What are the project deliverables and scope?"* → "Project Scope > Deliverables" (0.600) |

**Mutation testing — 15 mutations, all caught first pass**, including the
two that produce silent wrong answers rather than errors: deleting stale
vectors *after* the upsert (wiping what was just written), and trusting
embedding results in arrival order instead of by the API's own index
(pairing every vector with the wrong payload).

### 11c. A hypothesis tested and rejected

Recorded in full because the temptation to retry it will be strong: Arabic
queries return raw `<table>` markup at the top of the results.

**The theory:** HTML markup dilutes the vectors. **The premise measured
true** — 51% of chunks contain tables, median **26%** of their tokens are
pure markup (max 67%) — and a single-query A/B moved the correct chunk from
rank 3 to rank 2.

**The mechanism test refuted it.** Across 5 queries and 75 samples:

| Chunks | Similarity lost when markup stripped |
|---|---|
| <5% markup | **0.027** |
| ≥25% markup | **0.029** |

If markup were inflating table-heavy chunks, the second number would be far
larger. It isn't — stripping lowers everything slightly, and the rank
improvement was noise. **No change was made**; acting on it would have
altered the payload contract and forced a full re-index for nothing.

What is real, and belongs to the chat backend: Arabic scores ~0.40 against
English's ~0.60, and the correct chunk often ranks 2nd–3rd rather than 1st.
That is **ranking**, not retrieval — the answer is in the results every
time.

---

## 13. Closing two gaps before the chat backend (2026-08-09)

### 13a. RLS had no regression test — now 23

Migration `013` made SQL isolation structural, and then nothing tested it.
**Every other test connects as `spm_admin`, which bypasses row-level
security entirely**, so no existing test could have noticed it break. The
seven checks recorded when `013` landed were run by hand, once.

`tests/test_rls.py` is read-only by necessity: RLS can only be observed
across two connections, and the writer bypasses the thing under test, so
the usual write-inside-a-rolled-back-transaction trick does not work — the
`spm_app` connection cannot see uncommitted rows. The tests assert
*relationships* over whatever corpus is present instead, and nothing is
written, so nothing can be left behind. Project codes, usernames, counts
and the role name are all discovered at run time.

**Mutation testing — 6 mutations, all caught:**

| Mutation | Tests that failed |
|---|---|
| Policy widened to `USING (true)` | 7 |
| RLS switched off on `document_links` | 8 |
| App role granted `UPDATE` | 1 |
| App role given `BYPASSRLS` | 12 |
| A new table granted SELECT with no policy | 1 |
| `document_content` policy dropped | 2 |

The fifth is the one worth naming: adding a table and granting the app role
SELECT is a one-line change that is easy to make and impossible to notice.
`test_every_table_the_app_can_read_is_scoped_or_exempt` asks the catalog
rather than a list, so it covers tables nobody has written yet.

Two findings recorded rather than fixed:

- **`SET LOCAL` cannot take a bind parameter.** The chat backend must use
  `SELECT set_config('app.current_project', %s, true)`; the literal form
  would require concatenating a request-supplied project code into the
  statement that establishes the security scope.
- **`users` is granted SELECT with no policy**, deliberately — it has no
  project dimension. It does mean the app role can enumerate all four
  users. Not an AC09 breach (that is about project documents) but worth a
  decision rather than a default; it is named in the test file so the
  decision stays visible.

### 13b. Retrieval quality is now measured, and the anecdote was wrong

`evaluation/` — 29 questions across Arabic, English and mixed, all 9
projects, each tagged with the document(s) that genuinely answer it. 39
tests, 8 mutations, all caught. Baseline in `evaluation/baseline.json`.

| | n | MRR | r@1 | r@3 | r@5 | mean top score |
|---|---|---|---|---|---|---|
| overall | 29 | 0.829 | 0.76 | 0.90 | 0.93 | 0.507 |
| Arabic | 16 | 0.825 | 0.75 | 0.88 | **0.94** | 0.483 |
| English | 10 | 0.850 | 0.80 | 0.90 | 0.90 | 0.551 |

**The "Arabic ranking gap" is largely not a ranking gap.** The anecdote —
Arabic ~0.40 against English ~0.60 — measured the *similarity score*, and
that difference is real though smaller than remembered (0.483 vs 0.551).
But ranking quality is essentially equal, and Arabic is *better* at
recall@5. **A low similarity score is not a bad rank**, and conflating the
two pointed at reranking as the obvious fix when the data does not support
it. This is the same lesson as §11c in a different disguise: a true
premise does not make the explanation true.

**The one systematic failure is missing signal, not weak ranking.** Of two
questions that missed entirely, the interesting one is the Emdad charter in
`TEST-PROJECT`. That project groups six real charters from six *different*
real projects, and the word "Emdad" appears **nowhere in the extracted
text** — only in the filename, which is never embedded. All four charters
carry the byte-identical breadcrumb `Project Charter — Test Project - Real
Document Samples`. Retrieval cannot distinguish them because nothing
distinguishing was ever indexed.

Putting the filename in the breadcrumb is the obvious hypothesis. It is an
ingestion change, it forces a full re-index, and — the point of building
this — it can now be *tested* rather than argued about. No change was made.

⚠️ **The baseline is only meaningful against this question set and this
corpus.** Both are named in `baseline.json`. The harness validates every
tagged filename against `document_links` before embedding anything, because
a renamed document scores zero forever and reads exactly like a regression.

---

## 12. Hardening the pipeline into an operational stage (2026-08-08)

The pipeline worked; it was not *operated*. Three stages a human typed, no
schedule, no worker, and — as containerising immediately proved — a latent
defect that only a second environment could expose.

### 12a. The defect containerising found

`document_links.file_path` held an **absolute** path. Nothing was wrong
while one machine ran the pipeline. The moment the Linux worker mounted the
same corpus at `/data`:

| | Before the fix | After |
|---|---|---|
| Host scan after a container run | 58 rows rewritten | `rows written: 0` |
| Container scan after a host run | 58 rows rewritten | `written: 0` |
| Host Stage 1 opening a container-recorded file | `exists on host: False` | resolves correctly |

Storing the path relative to `DOCUMENT_ROOT` fixes it in both directions.
Migrated in place: 58 rows rewritten once, **0 OCR cache rows dropped**,
all 58 still `indexed` — no re-OCR, no re-embed.

**That migration was free only because of a decision made three stages
earlier.** `PATH_UPDATED` deliberately does not reset `indexing_status`.
Had a relocated file been treated as `MODIFIED` — which the original
three-outcome brief would have implied — shipping this fix would have
silently re-OCR'd the entire corpus.

Two honest consequences:

- `PATH_UPDATED` is now reachable **only** for rows written before the fix,
  because `file_path` equals `<project>/<filename>`, which is the row's own
  unique key. It is a migration path, not a live case, and is documented as
  such rather than left looking load-bearing.
- `resolve_document()` **refuses** an absolute stored path rather than
  joining it, because `Path("D:/root") / "/data/x"` yields a real path
  pointing at the wrong place — a silent wrong answer instead of an error.

### 12b. What the worker added, and the new failure mode

Running as a container was not only about restart-safety: **Celery's
prefork pool does not work on Windows**, so a host worker is stuck on
`--pool=solo`. The container reports `concurrency: 2 (prefork)`.

The genuinely new risk is **concurrency, which idempotence does not
cover**. Every stage was already idempotent and verified as such — none of
that stops two runs starting together and both paying for the same OCR.
`pipeline/locking.py` adds a Redis lock per stage, taken by the CLI as well
as the worker, because the most likely collision is a developer running
`python -m pipeline.ocr` while the schedule fires.

Verified live, not just unit-tested:

```
$ (lock held by another process)
$ python -m pipeline.ocr
Stage 1 OCR — SKIPPED: stage 'ocr' is already running (held by worker-pretend:123)
exit code: 2
$ python -m pipeline.ocr --dry-run      # dry runs are exempt — they write nothing
  candidates : 0
exit: 0
```

### 12c. End-to-end from a clean slate

PRJ-05 was wiped completely — OCR cache deleted, hashes cleared, status
reset, Qdrant collection dropped — and rebuilt through the worker:

| Stage | Result |
|---|---|
| scan | 4 files, 4 written |
| ocr | 4 succeeded, **5 pages billed**, 0 failed |
| index | 4 indexed, **29 chunks**, 12,893 tokens, collection recreated |

**Identical to the pre-wipe state** (29 chunks, 29 points), and the
immediate re-run reported `written: 0`, `pages_billed: 0`. Corpus back to
9 collections / 678 points.

### 12d. Also closed

- `ingest` returned a nested graph of Celery task ids and did not wait, so
  a scheduled run could not report success or failure. It now runs the
  stages inline and returns `{ok, stages:[…]}`.
- `ocr.run()` had no pre-flight check on `DOCUMENT_ROOT`. A wrong root
  marked all 58 documents `failed` — and since failed documents are skipped
  by default, one typo needed `--retry-failed` to recover. It now fails
  once, loudly.
- An empty `DOCUMENT_ROOT` is refused **when the database already holds
  documents** (almost certainly a misconfigured root) but allowed when it
  does not (a legitimate first run).
- The Redis client is pooled once per process rather than rebuilt per lock
  acquisition, which leaked a connection pool per task.
- Healthchecks on both containers; beat's schedule persisted to D: instead
  of `/tmp`; `raw_response` growth flagged (never truncated — measured 177 kB
  average, 1.3 MB largest, ~65 kB/page).
