# Status & Roadmap — MOF Enjaz Chatbot

**Last verified:** 2026-09-14

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
  - **LLM** (account #2 since 2026-09-07, §22): Azure OpenAI `gpt-5.4-mini` via an Azure AI
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
- [x] **Session gate + scoped data access (AC09)** — `Application/`,
  built 2026-08-12. Validates the requested project against
  `project_assignments` before a session opens, which RLS cannot do. 40
  tests, 16 mutations. See §14.
- [x] **Retrieval evaluation set** — `evaluation/`, built 2026-08-09.
  29 tagged questions; MRR and recall@k; a recorded baseline. See §13b.
- [ ] **Workflow engine / chat backend — remaining.** Still needs:
  language detection + rejection of unsupported languages (AC01), intent
  routing (special vs. general/SPM-retrieval, AC02), the three-source
  retrieval composition (§3 of `SOLUTION_ARCHITECTURE.md`), grounded
  responses with citations (AC06), safe fallback for out-of-scope queries
  (AC07), and the 5-second response budget (AC08). The AC09 gate it sits
  on is done.
- [ ] **HTTP layer (FastAPI)** — `Application/backend/api/`. Deliberately
  not built with the gate: keeping a web framework out of the security
  boundary is what let it be tested without one.
- [ ] **Auth**: AD SSO + OTP + RBAC. Real AD isn't available in dev — needs
  a mock per the BRD's own practical note (`SOLUTION_SCOPE.md`, "Practical
  note for a demo/development build"). True OTP is a hard external
  dependency — must be flagged as deferred, not faked to look
  production-ready.
- [ ] **Frontend**: chat interface, project switcher, knowledge-base
  view-only page, language switcher + RTL support, document upload UI.
- [x] **OCR approach — resolved 2026-08-08 and built.** Azure Document
  Intelligence `prebuilt-layout`, markdown output, for every format, with a
  native fallback only for Office files Azure rejects. Measured across 11
  files (§10a), and the measurement reversed the initial recommendation.
  All 58 documents extracted. This line sat unticked for a day after the
  work was finished — it stayed on the "not started" list while §10
  described it in detail two screens below.

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

## 5. Where the build has reached, and what is next

📄 **The brief is `NEXT_BUILD_CHAT_BACKEND.md`.** Its five-step order is
being followed; steps 1 and 2 are done.

| # | Component | State |
|---|---|---|
| — | **Infrastructure** — 13 containers, all data on D: | ✅ |
| — | **SPM schema** — 17 migrations, RLS, 23 regression tests | ✅ |
| — | **Ingestion pipeline** — scan → OCR → chunk → embed → index | ✅ 58 docs, 678 vectors |
| — | **Operations** — worker, daily schedule, stage locks, health check | ✅ runs unattended |
| **1** | **Retrieval evaluation** — 29 tagged questions, MRR + recall | ✅ 2026-08-09, §13b |
| **2** | **Session gate + scoped access** (AC09) — `Application/` | ✅ 2026-08-12, §14 |
| **3a** | **Grounded answering, RAG only** (AC06) — cited, in-language | ✅ 2026-08-12, §15 |
| **3b** | **Live SPM source** — structured fields alongside the passages | ✅ 2026-08-12, §16 |
| **3c** | **Both-sides presentation** when SPM and a document disagree | ✅ as prompting, §16 — over-triggering unobserved |
| **3d** | **Mid-chat upload** (CAG) — session-scoped, never persisted | ⬜ open design question |
| **4a** | **Language check** (AC01) + **intent routing** (AC02) | ✅ 2026-08-16, §17 |
| **4b** | **Reply-in-kind** (AC05) — now told, not inferred | ✅ 2026-08-16, §17c |
| **4c** | **Out-of-scope fallback** (AC07) — a message, not a marker | ✅ 2026-08-16, §17c |
| **4d** | **Session continuity** (AC03) + **bilingual welcome** (AC04) | ✅ 2026-08-16, §18 |
| **5** | **Latency (AC08) + observability** — Langfuse, OTLP → Grafana | ✅ 2026-08-16, §19 |
| — | **HTTP layer** (FastAPI) — `Application/backend/api/` | ✅ 2026-08-16, §20 |
| — | **Frontend** — chat, switcher, KB page (AC10), RTL | ✅ 2026-08-16, §20 |
| — | **Auth** — AD SSO + OTP | ⬜ deferred, external dependency |

**The build order is finished, and the system runs in a browser.** Every
acceptance criterion the BRD assigns to this build is implemented,
measured, and exercised end to end:

```bash
.venv/Scripts/python -m Application.backend.api    # then open http://127.0.0.1:8000
```

| AC | State |
|---|---|
| AC01 language · AC02 intent · AC03 continuity · AC04 welcome | ✅ §17, §18 |
| AC05 reply-in-kind · AC06 grounded + cited · AC07 fallback | ✅ §15, §16, §17 |
| AC08 response time | ✅ measured, p50 2333 / p95 2994 ms, §19, §22 |
| AC09 no cross-project access | ✅ four layers, §14, §20a–b |
| AC10 knowledge-base page · AC11 document links | ✅ §20, §11 |
| AC08 *(dup)* SSO/OTP sign-in | ⬜ **deferred — external dependency** |

**What is genuinely left**, in the order it matters:

1. **Auth is the only unbuilt acceptance criterion.** `dev_fixed` is an
   honest simulation, not authentication. Real AD SSO + OTP replaces
   `api/identity.py` and nothing else — that seam exists for it.
2. **CAG — mid-chat upload** (§3.3 source 3, step 3d). Still an open design
   question: OCR into session context, never persisted.
3. **The conversation store is in-memory and per-process**, so the API runs
   single-process. Redis is already in the stack for it.
4. **`doc_type` classification was deferred and never built** — new files
   get `'Unclassified'`. Zero rows affected today, but the KB page now
   surfaces it.
5. **A real SPM integration must carry its own last-updated field** (§16b);
   until it does, dated comparison between sources is weaker than §3.3
   assumes.
6. **The native-extraction fallback does not distinguish a transient error
   from a rejection** (§14e) and silently downgrades quality when it fires.

What it walks into, flagged in advance:

- ~~**Ranking is the open quality problem.**~~ **Measured 2026-08-09 and
  largely refuted.** Arabic *similarity scores* are lower (0.483 vs 0.551)
  but ranking is essentially equal (MRR 0.813 vs 0.900 on the current
  index, and Arabic has matched or beaten English at recall@5). Reranking
  is a much weaker bet than it looked. Do **not** reach for a chunking
  change — tested and rejected twice (§11c, §13b).
- **Every retrieval must go through a `Session`.** It is obtainable only
  from the gate, so a function that requires one cannot be handed an
  unvalidated project code. Do not call `db.scoped()` with a raw project.
- **`SET LOCAL` cannot take a bind parameter** — use
  `set_config(name, value, true)`. `Application/backend/db.py` already does.
- **Recall@k is not stable across index rebuilds** (§14e). Treat a
  one-question change as noise; MRR and mean top score are steadier.
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

**629 Python tests, ~30s**, across 23 files — the largest being
`test_answering.py` 53, `test_conversation.py` 46, `test_session_gate.py`
42, `test_ocr.py` 40, `test_retrieval_eval.py` 39, `test_observability.py`
39, `test_scanner.py` 38, `test_auth.py` 38, `test_continuity.py` 33.
*(Counted from `--collect-only`, not estimated — this number has needed
correcting three times when it was inferred instead.)*

That one command covers the **frontend** too: `test_frontend.py` shells out
to `node --test` for the 40 JavaScript tests in `tests/frontend/`. They
stay JavaScript because the thing under test is — testing the real page
script through a Python approximation would be testing the approximation —
and they **skip rather than fail** where node is absent, since nothing else
in the project needs it.

`test_rls.py` and `test_session_gate.py` are the only files that connect as
**`spm_app`**. Every other file uses `spm_admin`, which bypasses row-level
security — exactly why RLS went untested for so long (§13a). `test_retrieval_eval.py` stubs
the embedding API and Qdrant, so the evaluation harness is fully covered
without a paid call.

`test_locking.py` and `test_tasks.py` need **Redis** as well as Postgres —
they exercise the real lock, because atomic set-if-absent, expiry and
compare-and-delete are exactly the properties a fake dictionary would not
have.

**Most of these tests need `mof-postgres-spm` running**, and
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

## 24. The MOF redesign, content in every trace, and a Docker that kept dying (2026-09-14)

**Tests: 629 Python, plus 40 JavaScript.**

### 24a. The UI now follows the MOF design screens

Rebuilt to the three design screens the user supplied (chat, knowledge base,
project picker): DIN Next LT Arabic, the ministry teal `#005459`, the brand
logo, the sidebar with the project card and agents, source chips under each
answer, a paginated knowledge-base table, and a picker with search and
"recently used".

Every element with no data behind it was put to the user as a decision
rather than faked, and the answers were:

| Design shows | Built as | Why |
|---|---|---|
| Counts on the other agents; a notification dot | No numbers, agents marked "قريباً", bell without a dot | Nothing produces those numbers. "What are these 4 risks?" has no answer in a demo |
| Follow-up chips tailored to the last answer | Three fixed starters | Real follow-ups change the system prompt and need the router eval re-run — its own step |
| "إعادة المحاولة" on a failed document | Status only | The app's database role is read-only by design, and a retry re-runs paid OCR |
| "المستخدمة مؤخرًا" | Remembered in the browser | Orders the list only; what may be opened is still the server's decision on every request |
| No sign-in or landing screen | Sign-in restyled; no dashboard | Sign-in lands straight in the chat, as the screens imply |

⚠️ **The font is licensed, so it is not in git.** `Application/frontend/
assets/fonts/` is gitignored apart from a README naming the three files. A
fresh clone serves 404 for them and falls back to a system font, which is
why the static mount uses `check_dir=False` rather than refusing to start.
Windows has no MIME entry for `.ttf`, so fonts went out as `text/plain`
until the type was declared explicitly.

Two real defects surfaced only in the browser, not in the tests:

- **A bilingual welcome put the English full stop at the start of its line**
  (".each time"), because one direction was applied to both paragraphs.
  `unicode-bidi: plaintext` lets each paragraph find its own.
- **Five passages from page 1 of one risk register produced five identical
  source chips.** The reader needs each source once; chips that read the
  same are now shown once, with a test.

The API gained `as_of` on citations so the SPM chip can show its date in
the reader's language instead of the English "record updated …" sentence.
All ten mutations of the new logic were caught.

### 24b. Every traced step now carries its input and output

Only the model call had recorded content, so in Langfuse the trace itself,
`retrieval.search`, `retrieval.embed` and `spm.fetch` all read `null` —
including the step you look at first when an answer is wrong. They now
record the question and answer, the passages found with their scores, and
the project record, through one helper that takes `capture_content` as a
required argument. Verified in Langfuse on a live question.

⚠️ **Correction on the record: Grafana receives the same content.** It was
described as holding only timings. It never was — the one instrumentation
exports identical attributes to both backends, and the model prompt with its
passages had been reaching Tempo all along. With `LANGFUSE_CAPTURE_CONTENT`
on, anyone who can sign in to *either* tool can read every project's
passages. Stripping content from the Grafana exporter was proposed and not
yet decided.

### 24c. Docker

- **A Docker Desktop update reset its disk location to C:**, presenting an
  empty engine — 0 images, 0 containers — while the real 43 GB disk sat
  untouched on D:. Restored by editing `settings-store.json` with Docker
  quit. ⚠️ Never through the Settings GUI, which *moves* the current (empty)
  disk to the new location and would overwrite the real one.
- **Force-quitting Docker left Unix-socket files** in
  `%LOCALAPPDATA%\Docker\run` that crashed the next start, and left the
  vhdx attached to Windows so WSL could not mount it. Use `docker desktop
  stop` / `start`.
- **The VM restarted itself every few minutes on 4 GB.** Containers showed
  "Up" while Postgres, Qdrant and Grafana were unreachable from Windows —
  the symptom was `server closed the connection unexpectedly`, not a crash
  report. The stack uses ~2.9 GB a minute after start. Raised to 8 GB in
  `.wslconfig`; stable since.

---

## 23. Finalisation: the permanent gaps, the frontend, and tracing end to end (2026-09-07)

The instruction was to finish the backend and frontend "from all aspects",
resolve the permanent gaps, and connect Grafana and Langfuse to the whole
process — with AD, real SharePoint and mid-chat upload explicitly out of
scope. What follows is what was actually gapped, not a summary of what was
already working.

**Test count: 353 → 620 Python, plus 25 JavaScript. 17 migrations.**

### 23a. The gaps that were real

| Gap | Why it mattered | Closed by |
|---|---|---|
| Conversations lived in process memory | A restart lost every live chat, and a second worker could not see the first's | `api/redis_store.py`, durable and shared |
| Nothing capped question rate | A signed-in loop was a bill, not a breach — but an obvious hole | `api/ratelimit.py`, keyed on the authenticated username |
| `projects` had no `updated_at` | §3.3 requires each source to be cited **with its date**, and the record could only be attributed "created" | migration 017, backfilled honestly |
| Document types were never classified | Everything ingested as `Unclassified` | `pipeline/classify.py`, Stage 1b |
| A throttled OCR call failed the document | A transient error is not a bad file | transient-only retries in `pipeline/ocr.py` |
| The router had no evaluation | Routing had moved into the model and nothing measured it | `evaluation/router_eval.py`, 59 probes |
| The frontend had no tests at all | The only untested component in the system | `tests/frontend/`, 25 tests |
| 27 settings were undocumented | Invisible: not findable by reading the template, defaults discoverable only from source | `.env.example`, plus a test in both directions |

### 23b. Dead configuration, deleted rather than left switched off

`LanguageSettings` and `IntentSettings` — some 5.5k of dataclass and env
loader — survived §21's redesign as vestiges of the deterministic router.
Nothing outside `config.py` had referenced them since; `INTENT_PHRASES_PATH`
defaulted to a file that no longer exists. `LANGFUSE_BASE_URL` was the same
story, superseded by the OTLP endpoint.

⚠️ **Documented settings that change nothing are worse than undocumented
ones.** An undocumented setting is merely hard to find. A documented one
that no code reads tells a deployer they can steer behaviour they cannot,
and the belief survives until something depends on it. Both directions are
now tested — the stale-side test counts what `docker-compose.yml`
interpolates as "read", scanned from compose rather than from a hand-kept
prefix list that would drift and quietly excuse itself.

### 23c. The frontend

Three things a user would actually hit were unhandled:

- **A 429 showed the server's English detail string to an Arabic reader.**
  The server answers a rate limit with a status code, not prose, so the
  wording is now chosen in the page in the UI language.
- **A 403 after sign-in left a dead chat** that refused every message. It
  now returns to sign-in, says why, and clears the conversation with it —
  those turns belong to a session that no longer exists.
- **The mockups' starter chips and indexed-document count were unbuilt.**
  The chips are ordinary messages, not a menu: each takes the same path as
  typed text, so the model still classifies it rather than being handed a
  pre-decided intent.

`renderTopbar` became the single writer of the header meta, so a slow
document lookup cannot be clobbered by a project switch, and an absent
count leaves the project code standing rather than showing a "0 documents"
that reads as an answer about the project.

**The tests run the real script from the real file** against a small DOM,
no bundler and no dependencies. Two choices carry their weight:
`getElementById` throws for an id the markup does not contain, so a `$()`
typo fails there instead of on a user's click; and `innerHTML` is checked
across the whole file to only ever receive a constant, which is the
prompt-fencing argument applied to the DOM.

A shipped Arabic typo surfaced while verifying in the browser — the
dashboard read **لوحة المتحة**, which is not a word. It had sat in two
places because nothing checked the strings. Two guards now cover the class
rather than the instance: both dictionaries must hold the same keys, and no
value may equal its own key (a missing key renders as the bare key —
`try1` in the middle of an Arabic screen, with no warning).

### 23d. Tracing, verified in both backends

One instrumentation, two backends, and the same turn was read back out of
each rather than assumed:

| Span | Tempo | Meaning |
|---|---|---|
| `POST /api/conversations/{token}/messages` | 2808 ms | The route **template**, not the raw path — a thousand conversations group into one line |
| `chat.turn` | 2790 ms | The answering work |
| `chat gpt-5.4-mini` | 2045 ms | Three quarters of the total |
| `retrieval.embed` | 590 ms | The other network call |
| `retrieval.search` | 17 ms | Never the bottleneck |
| `spm.fetch` | 2 ms | |

Langfuse returned the same trace with 6 observations, from the same
instrumentation. Tempo's query port is not published, so it is reached
through Grafana's datasource proxy — recorded in `INVENTORY.md` §7,
because the obvious `localhost:3200` simply refuses to connect.

**The shape of that trace is the answer to "why three seconds?"** Searching
is free; the two Azure calls are the entire cost. No amount of tuning the
vector search will move it.

### 23e. Verified live, not stubbed

- Sign-in, then a chip answered through the model: `project_overview`, ten
  named fields, cited and dated.
- **Session expiry** could not be tested by deleting the cookie from the
  page — it is `HttpOnly`, which is itself the confirmation. The signing
  secret was rotated under a live browser session instead: the app returned
  to sign-in with the expiry message and an emptied chat, and signing back
  in restored everything.
- All ten frontend mutations caught, then two more for the i18n guards.

⚠️ **One mutation initially survived, and the fault was in my test.** It
asserted that the model's chosen language beats guessing from the alphabet
— using a plain English sentence, which guessing also gets right, so the
test passed with the logic deleted. Rewritten to use an English answer
naming an Arabic project, where the two genuinely disagree, it caught the
break. A test that cannot fail is not a test, and only breaking it on
purpose reveals which kind you have.

### 23f. Still deliberately not built

Unchanged, and unchanged on purpose: **real AD/SSO/OTP**, **a real
SharePoint connection**, and **mid-chat document upload / CAG**. The
knowledge-base screen shows the upload control disabled with a reason
rather than hiding it, so the gap stays visible instead of silently
missing.

The one thing worth doing next that is not on this list is **streaming the
answer**. §23d says why: the model is 2 s of a 2.8 s turn, and that is a
number no amount of tuning elsewhere will move — but first tokens on screen
in 400 ms would change how long the wait *feels* without changing what it
*is*.

---

## 22. The LLM account moved, and a key that looked right was not (2026-09-07)

After three weeks idle the assistant could sign a user in, open a
conversation and render the welcome — but could not answer. Account #1's
subscription key had stopped authenticating: `401 — Access denied due to
invalid subscription key`. Embeddings and retrieval were unaffected, so the
failure was one credential, not the stack.

### 22a. The screenshot key was not the fix

`gpt-5.4-mini` had been redeployed onto **account #2's** resource
(`sce-foundry1`) — the one the embeddings already used — and the Azure
portal showed an endpoint and a key for it.

**That key returned 401 on every surface tried**: the v1 route with
`api-key`, the v1 route with `Authorization: Bearer`, and the classic
`/openai/deployments/{name}/chat/completions` route. Meanwhile the key
**already in `.env` for embeddings returned 200 on both surfaces** for the
same deployment.

So the deployment was reachable all along and needed no new secret: it lives
on a resource we were already authenticated against. The fix was two lines
of `.env` — repoint `AZURE_OPENAI_LLM_ENDPOINT` at `sce-foundry1` and reuse
the account-#2 key.

⚠️ **The general lesson: a credential shown next to a resource is not
evidence that it authenticates that resource.** Probing four
endpoint/key/route combinations took under a minute and turned a guess into
a fact — and avoided persisting a secret that does not work, which would
have produced exactly the same 401 with a much more confusing cause.

### 22b. The two-account split is gone, and that is a real trade

All three roles — LLM, embeddings, OCR — now sit on one resource and one
key. **A single expired or rotated key now takes down generation *and*
retrieval *and* ingestion together**, where previously an LLM outage still
left the corpus searchable. Recorded rather than glossed; a production
deployment should decide whether that blast radius is acceptable.

Nothing in the code assumed the split. `config.py` groups model settings by
**role**, not by account, which is exactly why moving one role was a
configuration change and not a code change (§4.1's original reasoning,
paying off in the direction it was written for).

### 22c. AC08, re-measured on the new deployment

| | before (§19) | now |
|---|---|---|
| p50 / p95 | 1845 / 2782 ms | **2333 / 2994 ms** |
| within budget | 18/18 | **18/18** |
| mean LLM phase | 2020 ms | 2041 ms |

Slightly slower at the median and still comfortably inside the five-second
budget. Warm-up also dropped from ~20 s to 4.8 s, which says the earlier
figures were taken during a degraded network rather than that the new
deployment is faster.

### 22d. A secret nearly became a tracked file

Backing up `infra/.env` before editing produced `infra/.env.bak.<date>` —
and **`.gitignore` only ignored `infra/.env` exactly**, so the backup, which
contained the old key, was a tracked file waiting to be committed.

Deleted, and the rule widened to `infra/.env.*` with `!infra/.env.example`
so the template stays tracked. Verified in both directions rather than
assumed: a probe backup is ignored, and `.env.example` still is not.

⚠️ **A dead credential is still a credential.** The old key had stopped
working, which is exactly the reasoning that makes it easy to leave lying
around in a repository.

---

## 21. The LLM becomes the router, and sign-in arrives (2026-08-17)

A requirements change, not a refinement: **every input goes to the model,
which decides the language and the intent, and nothing is hard-coded.**
That reverses §17's design, so the reversal is recorded rather than
quietly applied.

### 21a. What was traded, explicitly

| | Before (§17) | Now |
|---|---|---|
| Language | script counting + confidence gate — **0 ms, free** | the model |
| Intent | whole-utterance phrase table — **0 ms, free** | the model |
| `مرحبا` | 0 embeddings, 0 completions | 1 embedding, 1 completion, ~2 s |
| Where the rules live | Python | one system prompt |

The cost is real and was stated before the work started: a greeting now
costs a model call, and language rejection stops being structural. What is
bought is that **exactly one thing decides how a message is treated, and it
is legible to someone who does not read Python** — which is what the
requirement asked for.

**One call, not two.** Retrieval runs *first*, for every message, so the
model receives the passages at the same moment it is asked what the message
is. Classifying first and answering second would have been two round trips
and roughly doubled the latency against AC08. The cost of retrieving on a
greeting is one embedding (~330 ms, a fraction of a cent) against the
second completion it saves on every real question. Measured live: **1.5–2.9
s** per turn.

**Deleted:** `intent.py`, `intent_phrases.json`, `language.py` and their
tests. `messages.py` keeps two strings — the pre-input welcome, which has no
message to detect a language from, and the outage message, for when the
model is precisely what cannot be called.

**AC06 survived untouched**, because it never depended on routing: the
response schema still has no field for a filename, citations are still
rebuilt from retrieved payloads, and the relevance floor still decides what
is *sent*. What it no longer does is decide whether to call at all — it
cannot, now that a greeting must also be answered.

⚠️ **`found` is no longer a marker string.** `NOT_IN_DOCUMENTS` could not
survive a design where the model also writes greetings and apologies: those
are legitimate replies with no citations, and a text match would have to
guess which was which. It is now derived from the returned intent.

⚠️ **A refusal that arrives carrying citations has them dropped.** Saying
the sources did not cover it and then naming sources is a contradiction.

### 21b. Migrations 015 and 016

**015 — demo credentials.** PBKDF2-HMAC-SHA256, 240,000 iterations,
per-user salt, self-describing so the verifier assumes no parameters. The
demo password is shared across the four mock users and recorded in
`INFRASTRUCTURE.md`, deliberately: these are fictional users in a mock
dataset on one machine, and a demo nobody can sign in to is not a demo.

**016 — the five Project Overview fields** QA1.1 asks for that SPM did not
carry: owner, manager, completion %, phase, stage.

⚠️ **Seeded to agree with the corpus, not invented alongside it.** Manager
and owner were read out of each project's charter *first*, then verified
back against the whole corpus — every seeded name is corroborated. That
matters because the assistant is instructed to surface disagreement between
SPM and a document (§3.3): a name typed carelessly here would make it
report a conflict that only this file created.

`TEST-PROJECT` is left NULL throughout. It is thirteen unrelated real
documents filed as one "project" (§13b), not a project with a manager and a
completion percentage — giving it any would make the overview confidently
wrong. PRJ-03, PRJ-07 and PRJ-08 keep NULL managers for the same reason:
their charters do not name one, and *"not recorded"* is a true answer where
a plausible one is not. Verified live — the overview reports
`المالك: غير مسجل` rather than omitting the line.

### 21c. Sign-in, simulated honestly

`api/credentials.py` verifies an email and password; `api/cookies.py` mints
an HMAC-SHA256-signed session cookie; `identity.py` gains a
`session_login` mode and now defaults to it.

Four properties, each mutation-tested, because getting them wrong teaches
the wrong shape to whoever replaces this with real AD SSO:

- **No plaintext anywhere**, and the iteration count and salt are read from
  the stored value, so raising the cost is a data change.
- **A wrong password and an unknown address are indistinguishable** — same
  reason, same message, and the same hashing work is done for both, because
  timing must not answer what the message refuses to.
- **An account with no password cannot be signed in to.** NULL means "not
  provisioned", never "any password will do".
- **The cookie signature is verified before the payload is parsed.**
  Parsing first means acting on attacker-controlled JSON — a type, a
  length, an expiry — before knowing we wrote it.

⚠️ **The signing secret has no default.** A hard-coded fallback would mean
every deployment of this source shares a key, and anyone with the code
could mint a session for any username. Missing means sign-in refuses.

Real AD SSO and OTP replace `credentials.py` and one branch of
`identity.py`. **Nothing above them changes** — that seam is why the AC09
gate never had to know how identity was established.

### 21d. The frontend, rebuilt to the supplied screens

Sign-in, dashboard, project picker, assistant chat, knowledge base. Indigo
platform chrome, MOF green footer, EN/AR toggle, RTL by default with
per-message direction.

The other two agents (رادار المخاطر, محرك الدروس) appear in the nav and are
inert. The knowledge base's upload button is rendered **disabled**: CAG is
not built, and a button that looks live but does nothing is worse than one
that says so.

**Switching project asks first**, then opens a *new* conversation — a
`Conversation` is bound to one session for its lifetime, so a switch cannot
be a mutation of the old one.

### 21e. Verified live, and two defects found by running it

Seven probes through the real UI and the real API on PRJ-06: a greeting
answered by name; the ten-field overview with `غير مسجل` for the unrecorded
owner; a bare `لماذا؟` correctly resolved against the previous risks
question; German refused with a trilingual apology; a PRJ-03 question
refused with "switch project first"; and *"what is the manager's phone
number?"* answered with **case 3 exactly** — not present in the indexed
documents, check the knowledge base.

Two defects the browser found that the tests could not:

- **A heredoc silently ate an escape** and updated the *caller* of a prompt
  template without updating the template, producing a 500 on every message.
  Third occurrence of that tooling trap (§15d); the fix is to edit files
  directly rather than through shell-quoted Python.
- **The bilingual welcome used the Arabic name in its English half.** Each
  half now uses its own language's name.

### 21f. Mutation testing — 20 mutations, and two that no test could catch

18 caught immediately. The two survivors were both **`hmac.compare_digest`
replaced with `==`** — and they survived correctly: both return the same
answer for every input, and the only difference is how long `==` takes to
say no, which leaks how much of the secret was right.

Nothing behavioural can see that, and a timing assertion would be flaky
without proving anything. So the guarantee is pinned **structurally**, by
asserting the source uses `compare_digest` — the same move as pinning the
response schema's shape rather than only its runtime effect (§15b). 20/20
after that.

⚠️ **~115 tests were deleted**, because they asserted the behaviour of code
that no longer exists. Replacing them with tests that still passed would
have left a suite asserting nothing. What replaced them tests what is still
testable without paying for a completion — including that the rules AC06
and AC09 depend on are **still present in the prompt text**, since moving
them out of code made them easy to delete by accident.

---

## 20. The HTTP layer and the frontend (2026-08-16)

`Application/backend/api/` (FastAPI) and `Application/frontend/index.html`.
**The system is now usable in a browser**, and every acceptance criterion
the BRD assigns to this build is implemented, measured and exercised end to
end.

**Neither layer adds behaviour.** That framing is the point: a route that
reached past `Conversation` into `answer_question` would silently skip
AC01–AC04, and a frontend that decided anything would make the acceptance
criteria true of one client rather than of the system. Both are
deliberately thin.

### 20a. Identity — the boundary the whole of AC09 rests on

`Application/README.md` has always said the gate answers *"may this user
open this project?"* and never *"is this really that user?"*. The HTTP
layer is where that second question finally had to be answered, so
`api/identity.py` is the new most-security-sensitive file after
`session.py`.

**A username never comes from the request body or the query string**, and
that is enforced structurally — there is no field to put one in. Same
argument as the response schema that gives the model no field for a
filename: a rule you cannot express is a rule that cannot be broken by
accident. Two mutations add such a field, and both are caught.

| Mode | Identity from | Safe when |
|---|---|---|
| `dev_fixed` *(default)* | configuration | always — a client cannot change it |
| `trusted_header` | a request header | **only** behind a proxy that overwrites it |

`dev_fixed` is the honest default: every request is the same signed-in
user, which cannot be spoofed and does not pretend to be authentication.
`trusted_header` is production-*shaped*, not production-*safe*, and the
startup log says so in those words — a mutation that softens that wording
is caught.

### 20b. A conversation id is a handle, not a credential

Conversations are held server-side under an opaque token. **Every lookup is
owner-checked**, because the gate validated the *owner's* assignment and
not the caller's — so without that check an id that was guessed, logged,
shared or left in a browser history is a complete AC09 bypass.

Demonstrated rather than asserted: removing the check fails three tests.

"No such conversation" and "not yours" return **byte-identical** 403s, for
the same reason `AccessDenied` is non-enumerable — a caller who can tell
them apart can probe for other people's live conversations. A mutation that
returns 404 for one and 403 for the other is caught.

⚠️ **The store is per-process and in memory**, so the API must run
single-process until it moves to Redis — which is already in the stack for
exactly this. Bounded twice, by idle time and by count with LRU eviction,
because an unbounded store is a slow leak.

### 20c. What the frontend deliberately is not

One static file, no build step, no framework, served by the API process on
the same origin. A `node_modules` tree and a bundler would be three more
things to install, version and explain for a page with four screens, and
this is a demo build whose job is to show the backend working.

**RTL is the default, not an option** — the platform is Arabic-first — and
direction is then set *per message* from the language the backend detected,
so a bilingual transcript reads correctly in both directions.

Everything from the API is inserted as **text, never markup**. Document
content is not trusted input; that is the passage-fencing reasoning from
§15b applied to the DOM.

### 20d. Verified in a real browser

Switcher populated from `/api/projects` (three assigned projects, and only
those). AC04's bilingual welcome before anything was typed. A question
answered in **2564 ms** with four citations. The knowledge-base page
showing **6/6 indexed** with type, chunk count and date.

**The answer returned PRJ-02's budget — 28,750,000 — not PRJ-06's
22,900,000.** Project scoping holding through session gate, RLS, the
per-project collection, the HTTP layer and the UI, verified by the number
being the right one rather than by reading the code.

### 20e. Mutation testing — 17 mutations, all caught

Owner checks removed from `get()` and `close()`; expiry and eviction
disabled; predictable ids; `dev_fixed` falling back to a header; blank,
missing and unknown identities allowed through; the trusted-header warning
softened; a 404/403 oracle; the reason slug rendered to the client; an
outage reported as a permission decision; the knowledge base read without
opening a session; the question-length bound removed; and a username field
added to each request model.

**One was a badly designed mutation** — it edited the first line of a
multi-line warning string, leaving the actual warning text intact, so it
never implemented the threat it claimed. Re-run against the whole string,
it is caught. Same species as §15d's `min()` no-op.

⚠️ **And a documented command did not work.** `python -m
Application.backend.api` needs a `__main__.py`, which the package did not
have — the module docstring said one thing and the package did another.
Found by running it, not by reading it. §16a's lesson in miniature.

---

## 19. AC08 latency and observability — step 5 (2026-08-16)

Langfuse and Grafana had been running unused since the infrastructure was
built. They are now wired, and AC08 is **measured rather than asserted**.

New: `observability/` (a fourth top-level package — it instruments the
other three and belongs to none of them), `Application/backend/clients.py`,
and `evaluation/latency_bench.py`.

### 19a. The AC08 fix was setup, not the model

The cold-start story recorded in §15c and §18f was only half the problem.
Measured directly:

| | first call | steady state |
|---|---|---|
| construct the embedding client | 4196 ms | **515 ms** |
| construct the Qdrant client | 4064 ms | **1035 ms** |
| construct the LLM client | 847 ms | **800 ms** |
| embed a question, fresh client each time | 3734 ms | **~1000 ms** |
| embed a question, reusing one client | 628 ms | **~290 ms** |

**Every question was rebuilding all three clients** — roughly 2.3 seconds
of pure setup, plus ~700 ms more because a brand-new HTTPS client cannot
reuse a connection and repeats the TLS handshake. None of it was retrieval
or generation.

Caching them on the `Settings` object (not globally — a global cache would
make `dataclasses.replace` a no-op in exactly the tests that exist to prove
settings are honoured) took a warm turn from 2.5–3.3 s to **1.8–2.45 s**.
`warmup()` additionally *opens* the connections at startup, because
constructing a client is not connecting one.

### 19b. AC08, measured

`evaluation/latency_bench.py`, over the real question set:

| | result |
|---|---|
| turns measured | 18 (11 skipped — no assignment, so AC09 forbids a session) |
| p50 / p95 | **1845 / 2782 ms** |
| mean per phase | embed 328 · search 23 · SPM 8 · **LLM 2020** |

**The LLM call is ~80% of a turn and is the irreducible part.** Embedding,
search and SPM together are under 400 ms.

**The verdict is p95, not the mean and not the max.** A mean hides exactly
the turns that break the budget; AC08 says *under normal conditions*, which
is a percentile statement, so one slow turn in twenty is not a failure.
`max_ms` is reported alongside so the outlier stays visible rather than
being averaged away — and on the final run that mattered: one turn hit
10.9 s because Azure itself took 10.3 s to respond. p95 held at 2782 ms and
the run passed, which is the intended reading rather than a lucky one.

⚠️ **Azure connectivity was degraded throughout this session** — 15–18 s
connects at times, and a 17 s warmup probe. The numbers above are therefore
pessimistic, not optimistic.

⚠️ **Cold is still over budget and that is expected.** `--cold` puts the
first turn at ~19 s, because client construction happens *inside* it and is
not attributed to any phase — the phase timings start after the client
exists, so a cold turn's total legitimately exceeds the sum of its parts.
A long-lived HTTP process calls `warmup()` once and never sees this. Worth
knowing before anyone reads a cold number as a regression.

### 19c. One instrumentation, two backends

**Langfuse v3 exposes an OTLP endpoint.** Checked before anything was built
on it — `POST /api/public/otel/v1/traces` with the project's Basic auth
returns 200 — which settled the architecture: OpenTelemetry is the only
instrumentation API, and the same spans go to both backends. Two SDKs would
mean two sets of instrumentation calls that drift apart, and the first to
drift is always the one nobody looks at.

They answer different questions. Grafana: *where did the time go*.
Langfuse: *what was the model actually asked*. Verified live — Tempo shows
`enjaz-chat / chat.turn` with matching durations, and Langfuse groups the
turns of one conversation into **one session** attributed to the user, with
four observations per answered turn.

**Tracing is fail-open — the only thing in this repository that is.** An
unreachable collector, a bad endpoint or a missing package must never stop
a project manager getting an answer. Proven by running a turn with a tracer
that raises on every span and asserting the answer still arrives.

⚠️ **`LANGFUSE_CAPTURE_CONTENT` is an AC09 decision, not a preference.** A
trace carries the prompt, and the prompt carries document passages. **A
trace store has no notion of project assignment**: anyone who can read
Langfuse can read every project's passages. That is acceptable here only
because Langfuse is a container on the same machine already holding the
same corpus. Against a shared or hosted trace store it is not, and turning
it off leaves timing and token counts — most of the operational value —
intact.

### 19d. Two defects the tests found

- **Reading the tracer back from OpenTelemetry's global provider made
  `configure()` un-repeatable.** OTel permits the global to be set once per
  process and warns-and-ignores afterwards, so every test after the first
  silently emitted into the first test's exporter. The module now owns its
  provider and only *attempts* to set the global.
- **My own p95 test was wrong, not the code.** It assumed one outlier in
  twenty should fail the budget. On inspection that is not what "under
  normal conditions" means, so the reading is now written down and tested
  in both directions rather than left implicit.

### 19e. Mutation testing — 21 mutations, all caught

Three real test gaps and one real design gap surfaced:

- **The content switch was only half-tested.** The assertion checked the
  prompt but not the completion — and a completion quotes the passages
  back, so a mutation capturing it regardless of the switch survived.
- **Nothing pinned batched export.** A `SimpleSpanProcessor` would put the
  collector's latency straight into the AC08 budget, which is the failure
  mode of naive tracing. Now a structural test.
- **The budget verdict and the skip accounting were unreachable except
  through a run needing a database and paid calls**, so neither was tested
  and both mutations survived. Extracted as pure functions — which is
  better design independently — and tested.
- **One no-op mutation.** Dropping `None` attributes turns out to be
  redundant: OpenTelemetry drops them itself, so removing the filter
  changes no exported span. What it actually prevents is a warning per
  attribute per span per turn, so the test now asserts the log rather than
  the export. Same species as §15d's `min()` mutation.

---

## 18. AC03 continuity, AC04 welcome — and the AC01 defect a live run found (2026-08-16)

`Application/backend/conversation.py` gains a `Conversation`: the session,
the transcript, the welcome, and one `ask()` per turn. Step 4 of
`NEXT_BUILD_CHAT_BACKEND.md` is now complete.

### 18a. AC09 extended through time

**The session is fixed at construction and cannot be replaced.** A
`Conversation` therefore cannot come to hold two projects' content, and a
follow-up cannot resolve against a project the user has since left.
Switching project means a new `Session` from the gate and so a new
`Conversation` — which is also the honest model of what a switch *is*.
Mutation-tested by making the attribute writable.

### 18b. The follow-up design was measured before it was built

The assumption going in was "a follow-up carries no subject, so retrieval
fails". Measured against PRJ-06, that is **half true**, and the half that
is false changes the design:

| Follow-up | best score | above floor | outcome |
|---|---|---|---|
| *وتاريخ الانتهاء؟* | 0.253 | 1 | answered correctly |
| *And which department sponsors it?* | 0.355 | 8 | answered correctly |
| *لماذا؟* | **0.163** | **0** | refused |
| *Give me more detail on the second one* | **0.181** | **0** | refused |

A follow-up carrying **any topical noun** already retrieves fine — the
collection is one project and the project record is always present. Only
**purely referential** follow-ups fail, and they fail completely.

Prefixing the previous question fixes exactly those, and demonstrably
resolves the reference rather than adding noise:

| Composed query | best | top match |
|---|---|---|
| *ما هي حالة المشروع الحالية؟ لماذا؟* | 0.507 | **Weekly Status Report** |
| *ما هي المخاطر الحالية للمشروع؟ لماذا؟* | 0.390 | **Risk Register** |
| *Who is the project manager? Tell me more* | 0.357 | Meeting Minutes |

Same follow-up, different anchor, different — and correct — document.

**So composition is a fallback, not a rewrite.** It fires only when the
bare question found no evidence. Composing every query would spend an
embedding where none is needed and drag the old topic into a genuine change
of subject. An LLM query-rewrite would cost a round-trip on *every* turn
against the AC08 budget to do what one extra embedding does on the minority
of turns that need it. Both embeddings are reported, so AC08 is not
understated by a hidden retry.

### 18c. History, and the one new risk it introduces

Prior turns go to the model as ordinary chat messages — the user's earlier
text in a user role, ours in an assistant role — so the system message
stays purely our instructions and passage data. Bounded by turns **and**
tokens, because a turn cap says nothing about size. Only turns that had a
topic are carried; a greeting is kept in the transcript (it is what the
user saw) but would otherwise crowd out the exchange that matters.

⚠️ **History can invite a citation for a passage that is no longer in
context.** Passages are renumbered every turn, so an earlier turn's `[2]`
is not this turn's `[2]`. The existing index validation already drops the
stale one — the structural defence from §15b holding up under a new
attack — and the prompt now says so explicitly. Tested with a model that
deliberately cites stale indexes.

### 18d. The live run found a real AC01 defect

Turn 10 of an 11-turn live conversation answered **"Quel est le budget?"
in English.** At 19 characters it fell under the 20-character floor that
decided whether the Latin identifier was consulted at all — so it never
was. **The probe set could not have caught this: every non-English probe
in it was a full sentence.**

Length turned out to be a poor proxy for the rule it stood in for.
Measured:

| | result |
|---|---|
| Identifier on short non-English (10–24 chars) | **10/10 correct** |
| Its one false positive on real English (*"When does it end?"* → German) | confidence **0.473** |
| Genuine non-English sentences | confidence **0.997–1.000** |

So the gate is now **confidence**, which is what "rejection needs
evidence" actually meant. Result: 3/11 → **7/11** of the short non-English
probes rejected, with **zero** false rejections of English.

⚠️ **Fragments below the threshold (`le budget?` 0.386, `das Budget?`
0.366) are still accepted, deliberately.** The asymmetry is the whole
point: the first legitimate English question turned away costs more than
every French fragment let through. Do not "fix" this by lowering the
threshold — 0.473 is how close the nearest false rejection sits.

**The general lesson: a probe set inherits the blind spots of whoever
wrote it.** Every non-English probe was a well-formed sentence because
that is what came to mind. The probe set is now 54 and includes short
non-English *and* the English false-positive guard.

### 18e. Mutation testing — 19 + 19, and two real gaps

The AC03/AC04 round ran 19; **two survived and both were genuine**:

- **A failed retry silently replacing the original result.** Invisible to
  a behavioural test — the outcome is `out_of_scope` either way and only
  the *reported* score differs, attributing the composed query's score to a
  question the user never asked. That is the "assert the value, not the
  behaviour" lesson (§6) for the third time in this project.
- **The composition anchor did not check whether the previous turn had a
  topic.** Budget question → *"thanks"* → *"why?"* would have searched for
  *"thanks why?"*.

Both closed, then 19/19. The AC01/AC02 set was re-run against the
confidence-gated code with three new mutations on the gate itself —
removed, inverted, and threshold ignored — 19/19.

### 18f. Verified live — 11 turns, one session, no restart

The welcome renders bilingually before any question. `لماذا؟` after the
risks question retrieved the Risk Register and answered correctly.
`وتاريخ الانتهاء؟` resolved against the budget question. An English
follow-up pair worked mid-conversation after six Arabic turns. A
cross-project question was refused with no leak. 11 turns recorded, 6
carried into the prompt.

**Latency:** 2.5–3.3 s for a normal turn, 3.9–5.9 s where a retry embedded
twice or the process was cold. Warm turns sit inside the AC08 budget; the
retry pushes the worst case close to it, which is a real input to step 5.

---

## 17. AC01 language verification and AC02 intent routing (2026-08-16)

The conversational layer's first slice. `Application/backend/language.py`
(detect), `intent.py` (route), `messages.py` (every string a user reads
that the model did not write), and `conversation.py` (the composer where
the two are actually applied). AC05 and AC07 improved as a consequence.

**`answer_question` stays the engine; `handle_question` is the entry
point.** Rejecting a language and routing a greeting are not the same job
as grounding an answer, and folding them together would have put the AC01
check inside the module whose one responsibility is AC06.

⚠️ **It is an entry point, not a chokepoint.** `answer_question` remains
directly callable — `START_HERE.md`'s own example does exactly that — so
AC01 binds callers that come through `conversation.py`, not by
construction the way `Session` binds project scope. Deliberate: the rule
that must never be bypassable is already structural, and duplicating the
language check would put one rule in two places. **Anything user-facing
must enter through `handle_question`.**

### 17a. AC01 — script first, statistics only where script runs out

Script is decisive and free. Counting codepoints settles Arabic against
Han, Cyrillic, Hebrew, Devanagari and Greek without a model, without a
network call and without a millisecond that matters. The one thing it
cannot do is separate English from French or Spanish, all Latin — so
`py3langid` (offline, deterministic, ~2 MB, injectable at one call site)
runs **only** on Latin-dominant text.

**The dependency was measured, not assumed.**
`evaluation/conversation_probe.py` scores 47 tagged probes and can run
with the discriminator disabled:

| | language accuracy | unsupported recall |
|---|---|---|
| script only (`--no-discriminator`) | 0.936 | **5/8 = 0.62** |
| with the Latin discriminator | **1.000** | **8/8 = 1.00** |

The three misses are exactly French, Spanish and German, all reported as
English. If that gap ever closes on its own the dependency is buying
nothing and should go — which is the point of measuring it.

**Rejection needs evidence; acceptance does not.** The asymmetry appears
twice and is deliberate. Text with no letters (`?!?!`, `١٢٣`, emoji) is
`undetermined`, **not** unsupported — telling someone who typed `?!?!`
that we do not support their language is simply wrong, and the two get
different messages. And the discriminator's verdict is **ignored** below
`latin_discriminator_min_chars`, because it answers `en` for almost any
short string: the failure we must never make is turning away a legitimate
English question.

**Three traps, each of which looks right and is wrong:**

| Trap | Why it bites |
|---|---|
| Arabic-Indic digits (`٠١٢`) are inside the Arabic Unicode block | A number typed on an Arabic keyboard would classify as an Arabic sentence |
| A Latin project code is not a second language | `ما حالة PRJ-06؟` is **33% Latin letters** — over any sane mixed threshold |
| Hebrew is not Arabic | Adjacent RTL scripts, different blocks; an "is it RTL" test accepts one as the other |

Trap 2 was **found by the test, not by inspection**. The fix is not a
higher threshold — that would trade this bug for the opposite one, missing
genuine code-switching in short questions — but a rule that a
whitespace token containing a numeral is an *identifier*, not language.
A project code, a date, a version or a ticket number is not a language.

### 17b. AC02 — two tiers, and what was deliberately not built

The BRD does not define "special handling", so the intent set is named
explicitly rather than invented silently: `greeting`, `courtesy`,
`capability`, `session_meta`, `out_of_scope`, and `general_retrieval`.

**Tier 1** matches a *whole short utterance* against a phrase set held as
data (`Application/backend/intent_phrases.json`), normalised for Arabic
orthography — diacritics, tatweel, and alef/yaa/taa-marbuta folding, so
the file does not have to enumerate every spelling of every greeting.
Its value is not classification accuracy; it is that `hello` should not
cost an embedding, a search and a completion only to return
`NOT_IN_DOCUMENTS`.

**Whole-utterance matching is the load-bearing rule.** `مرحبا` is a
greeting; `مرحبا، ما ميزانية المشروع؟` is a question with a greeting
attached, and a substring matcher would answer the first and silently lose
the second. A word cap guards the same failure independently.

**Tier 2** is everything else, where the relevance floor already refuses
out-of-scope questions without calling the model (§15b). An LLM
classifier would add a round-trip to *every* question against a
five-second budget, to re-decide what tier 2 decides for free.

⚠️ **`write_action` and `cross_project` were left to tier 2 on purpose,
and then measured.** Both are already *safe* there — `spm_app` is
`SELECT`-only whatever the model is asked, and another project's documents
are not in this collection. Four live probes confirmed it:

| Probe | Outcome | Leak |
|---|---|---|
| *قم بتحديث حالة المشروع إلى مكتمل* | `out_of_scope` refusal | none |
| *Update the project status and email the sponsor* | `out_of_scope` refusal | none |
| *ما هي ميزانية مشروع PRJ-03؟* | `out_of_scope` refusal | none — PRJ-06's figure not substituted |
| *Compare this project's budget with PRJ-03* | `out_of_scope` refusal | none |

**So the gap is wording, not correctness**: they get a generic "not in
this project's documents" instead of "I cannot change data" or "this
session is locked to one project". Recorded as measured rather than
fixed, so a classifier is justified by observed misrouting if it is ever
built.

### 17c. AC05 and AC07 improved as a consequence

- **AC05** no longer depends on the model inferring the language. The
  detected language is stated in the prompt. **Appended** to the system
  prompt rather than replacing rule 4, so mixed input still falls back to
  inference instead of being forced into a guess — and only a *named*
  language is interpolated, so a caller-supplied code cannot reach the
  system message. A test pins that.
- **AC07** finally has a message. `NOT_IN_DOCUMENTS` is a marker for us,
  not a sentence for a project manager, and `conversation.py` is where it
  becomes one, in the question's language. A test asserts the marker never
  reaches a user.

### 17d. Verified live, not only against stubs (§16a's lesson)

17 probes through a real `noura.alharbi` → `PRJ-06` session, counting real
client calls:

| Path | embed | search | LLM | latency |
|---|---|---|---|---|
| 4 × AC01 rejection | **0** | **0** | **0** | 0–51 ms |
| 4 × AC02 tier 1 | **0** | **0** | **0** | 0 ms |
| grounded answer, ar / en | 1 | 1 | 1 | 1.58 s / 1.63 s |
| greeting **+** question | 1 | 1 | 1 | 3.63 s (cold embed) |
| AC07 fallback, ar / en | 1 | 1 | 1 | 1.37 s / 1.36 s |

The 51 ms is `py3langid` loading its model on first use; every call after
is free. **Every warm path sits well inside the AC08 budget.**

⚠️ **The AC07 fallback still costs a completion when a project record
exists.** That is correct per §16 — a record alone *is* evidence, so the
model is asked and honestly refuses — but it means "out of scope" is only
free when nothing at all clears the floor. Worth knowing before AC08 is
tuned.

### 17e. Mutation testing — 18 mutations, all caught

Covering: identifier tokens counted as language; the letter filter
dropped; the unsupported-script check disabled; the discriminator's
verdict ignored; the discriminator trusted on short text; Hebrew folded
into the Arabic ranges; empty and letterless input treated as supported;
substring matching; the word cap removed; Arabic normalisation dropped; a
missing phrase file made fatal; a rejected language falling through to
retrieval; intent routed before the language check; `NOT_IN_DOCUMENTS`
shown to a user; a caller language code interpolated into the prompt; and
the apology reduced to one language.

**One survived the first round and found a real design flaw.** "Digits
counted as letters" survived because the *token* rule already dropped
digit-bearing tokens one level up — two rules quietly guarding the same
thing, neither testable independently of the other. On inspection the
token rule matched category `Nd` only, so fractions (`½`, `No`) and Roman
numerals (`Ⅷ`, `Nl`) still fell through to the character filter. Each rule
now has exactly one job. Re-run in the form that implements the actual
threat — dropping the letter filter — it is caught.

⚠️ **And the "commit before mutating" rule bit again.** The runner's
`git checkout -- .` took two uncommitted fixes with it mid-session. No
loss beyond re-typing, because the work was small — but this is the second
time (§14e was the expensive one), and the rule is in
`NEXT_BUILD_CHAT_BACKEND.md` §4.6 for a reason.

---

## 16. The live SPM source, and the leak it exposed (2026-08-12)

The second of §3.3's three sources: the project record itself — status,
dates, budget, sponsor — alongside the document passages.
`Application/backend/spm.py`.

**Two labelled sections, not one merged list.** That separation is what
makes §3.3's "attribute each to its source" possible: a project manager
comparing two claims needs to know which came from the system of record and
which from a document somebody wrote. The both-sides rule is implemented as
a *prompt instruction*, which is what §3.3 specifies — hand the model both
sources with provenance and have it surface disagreement, never resolve it
in code. The "only for a genuine factual difference, not the same fact
worded differently" clause is there because over-triggering turns a useful
signal into noise.

**The refusal rule changed shape** from "no passages" to "no evidence at
all". A project record alone *is* evidence: *"what is the budget?"* is
answerable from the system of record even when nothing clears the
relevance floor, and refusing then would hide the most authoritative source
available.

### 16a. A cross-project leak, caused by our own migration

The first live run returned **PRJ-02's record inside a session scoped to
PRJ-06**, and the answer reported PRJ-02's budget as this project's — then
flagged it as a *disagreement* with the document's correct figure. Silently
wrong, confidently presented, with a citation attached.

The cause was **migration `014`**. The read relied on row-level security
alone, which is correct for `document_links` and `document_content`. It is
wrong for `projects`, because `014` deliberately widened that one table so
a user can read every project they are assigned to and the switcher can
show names. **Permissive policies combine with OR**, so inside a scoped
session `projects` returns the session's project *plus* every other project
the user holds, and an unfiltered `fetchone()` takes whichever comes first.

Two things made it invisible until it ran:

- **Every stubbed test passed**, because the stub returned exactly one row.
- **One test asserted the *absence* of a `WHERE` clause** — it locked the
  defect in rather than catching it. That test is now inverted.

Two live tests were added that a stub cannot reproduce: each of a
multi-project user's sessions must read its own record, and the raw read
must genuinely return several rows — so the filter is never mistaken for
redundant belt-and-braces and removed later.

⚠️ **The general lesson:** *a table whose visibility rules were widened for
one caller stops being a safe implicit single-row read for another.* Worth
checking wherever RLS is trusted to return exactly one row.

### 16b. Attribution has no last-updated date, and cannot invent one

`projects` carries only `created_at`. §3.3 requires attribution to carry
dates — *"SPM, updated 2026-08-01"* against *"Weekly Status Report,
2026-07-12"* is often the whole answer a PM needs. So the record is
attributed as **"record created …"**, labelled as what it actually is.

This is a *mock* SPM; inventing a last-modified timestamp would manufacture
provenance the real system never supplied. **A real SPM integration must
carry its own last-updated field**, and until it does, dated comparison
between this source and a document is weaker than §3.3 assumes.

### 16c. Mutation testing — 9 mutations, all caught

Including the leak itself (dropping the filter), pointing the filter at a
caller value instead of the session, unscoping the read, citing a
claimed-but-unfetched record, merging the record into the passages,
replacing both-sides with pick-the-most-recent, dropping the date from
attribution, and treating a record as insufficient evidence.

One first attempt failed to compile, so Python caught it rather than a
test — re-run as valid code, it is caught by a test. Same discipline as
§15d: **a mutation that does not compile proves nothing.**

---

## 15. Grounded answering, RAG only (2026-08-12)

The first slice a user could actually converse with: a `Session`, a
question, and a cited answer. **RAG only** — no SPM composition, no
conflict detection, no uploads, no HTTP layer. Those need the skeleton to
exist before they can be judged.

`Application/backend/retrieval.py` (search, scoped) and
`Application/backend/answer.py` (prompt, LLM call, citation resolution).

### 15a. The smoke test came first, and changed the design

Everything here assumed the embeddings client pattern carried over to chat
completions — a *different* Azure account, Foundry-style endpoint. That was
an assumption, so it was checked once for a few hundred tokens before
anything was built on it. It carried over, and answered three questions:

| Probe | Result | Consequence |
|---|---|---|
| `temperature=0` | accepted | deterministic answers |
| strict JSON schema output | **works** | citations by validated index, not parsed prose |
| honours a refusal instruction | returns the marker | the "not in documents" path is real |

The schema finding improved the design. Without it, citations would have
been parsed out of the model's prose.

### 15b. Three mechanisms enforce AC06

1. **The model never sees a filename it could invent a citation from.**
   Passages are numbered; it nominates the numbers it used; every citation
   is then built from the *retrieved payload* at that index. A hallucinated
   `[9]` when five passages were sent is dropped. **The structural form is
   stronger than the runtime check**: the response schema gives the model
   no field in which to put a filename, and that shape is pinned by a test
   (§15d).
2. **Nothing retrieved, nothing asked.** Below `ANSWER_MIN_SCORE` the LLM
   is not called at all. Verified live: *"What is the capital of France?"*
   against PRJ-06 scores 0.109, returns `NOT_IN_DOCUMENTS`, costs nothing.
3. **The model is instructed to refuse**, and does.

**Retrieval takes a `Session`, never a project code.** The collection is
derived from it, so it cannot be called with a project the user was not
granted — AC09 extends into retrieval by construction.

**Passage text is treated as hostile.** A document can contain a sentence
shaped like an instruction. The blast radius is small (no tools, no
database, and the scope was fixed before retrieval ran), but passages are
fenced, numbered, and declared as data. Tested by retrieving a poisoned
passage and asserting it stays inside its fence.

**Synchronous, deliberately.** The three steps of one answer are
sequential, so async buys nothing per request; FastAPI runs sync handlers
in a worker thread, and the database pool is sync. Recorded so it is
revisited with measurements rather than re-argued.

### 15c. Verified live against PRJ-06

| Question | Answer | Citation | Time |
|---|---|---|---|
| *ما الميزانية المعتمدة… وتاريخ الانتهاء المخطط؟* | 22,900,000 ريال · 30/09/2026 — in Arabic | Project Charter, page 1 | **3.4 s** |
| *Who is the project manager and which department sponsors…?* | Eng. Sultan bin Abdulaziz Al-Dosari · General Budget Agency — in English | Weekly Status Report, page 1 | **1.8 s** |
| *What is the capital of France?* | `NOT_IN_DOCUMENTS` | none | no LLM call |
| *مخاطر مشروع PRJ-03…?* (from a PRJ-06 session) | `NOT_IN_DOCUMENTS` | none | no leak |

**The first real numbers against AC08.** The split is recorded per phase —
embed, search, SPM, LLM.

⚠️ **Cold start misses the budget; warm runs meet it.** Re-measured
2026-08-12 on a fresh process: the first question spends **~2.5 s embedding
alone** and totals **~5.1 s**. A second question in the same process embeds
in ~0.6 s and finishes under 3 s. The LLM call is steady at ~1.2–2.5 s
throughout, so the variance is client or connection setup, not the model.

Nothing has been tuned for this. It belongs with step 5, and a long-lived
HTTP process would pay that cost once rather than per question — which is
itself an argument for measuring AC08 *after* the HTTP layer exists rather
than against a cold script. Recorded so the next person to measure it does
not read it as a regression.

### 15d. Mutation testing — 18 mutations

Every real threat caught. Three did not resolve first time, each for a
different reason, and separating them mattered:

- **Two were tooling, not test gaps.** A level of backslash escaping was
  consumed between the shell heredoc and Python, so the fence-removal
  pattern matched nothing and the mutation *never applied* while being
  counted as a survivor. Re-run correctly, both were caught.
- **One was a badly designed mutation** — it wrapped an already-validated
  index in `min()`, a no-op. Chasing it found something better: the runtime
  defence was tested, the structural one was not. Three new mutations now
  try to open that door (add a `source_file` field, loosen
  `additionalProperties`, drop `strict`) and all three are caught.
- **A mutation that fails to compile proves nothing.** The first fence
  removal was a syntax error, so Python caught it rather than a test. Re-run
  as valid code, two tests catch it.

---

## 14. The AC09 session gate (2026-08-12)

The first component of the chat application. `Application/` is its home;
`pipeline/` and `evaluation/` stay where they are.

### 14a. What RLS could never do, demonstrated before building

Row-level security **serves whatever project the session declares**.
Verified live before a line was written: a user assigned to PRJ-02/05/06,
scoping a session to PRJ-01, was handed PRJ-01's row without complaint.
That is RLS working correctly — scoping is an *input* to it. Deciding
whether a scope is permitted happens exactly once, in
`Application/backend/session.py`.

| Layer | Enforced by | Can it be forgotten? |
|---|---|---|
| One Qdrant collection per project | infrastructure | no — no shared index |
| Row-level security | the database | no — fail-closed |
| **Is this project theirs?** | **the gate** | **yes. Hence the tests** |

### 14b. Migration 014 — the switcher

`projects` was scoped by `app.current_project`, so before a session exists
a user saw **zero rows** and a switcher could offer codes with no names.
A second policy lets a user read the projects they are assigned to. The
alternative — one scoped query per project — moves an access rule into
application code, the weakness `013` existed to remove.

Verified it widens nothing else: with a username and no project, documents
and OCR content both still return 0 rows.

⚠️ It does **not** close 14a. Permissive policies combine with OR, so
`project_scope` still serves a declared project. The gate is what closes it.

### 14c. Three defects found by building and attacking it

- **The pool was silently broken on first write.** `RESET ALL` on a
  non-autocommit connection opens a transaction, and psycopg_pool discards
  any connection returned `INTRANS` — so it threw away every connection and
  reconnected per request. Caught by asserting reuse, now a test: a pool
  that never reuses hides the very leak it exists to expose.
- **A NUL byte in a project code escaped the module's contract**, raising
  `psycopg.DataError` rather than `AccessError`. Found by the adversarial
  tests. Rejected now — minimally, since only NUL is genuinely unstorable.
- **A destructive test relied on the system under test for its own safety**
  (§14e). That one cost a corpus.

### 14d. Mutation testing — 16 mutations

Round 1: 12 run, 9 caught, 3 survived. Of the survivors, **two were badly
designed mutations that did not implement the threat they claimed** — re-run
realistically, both were caught. The third was genuine: the pool's
`RESET ALL` is defence in depth, invisible while `SET LOCAL` works, so a
test was added that exercises what it actually guards (a plain `SET`
surviving a borrow). Round 2: 4 realistic mutations, all caught.

**Every real threat is now caught**, including one that survived the first
round entirely: switching only the *username* scope to connection-level.
The project-scope test asserted on `document_links`, which is project-
scoped, so a leaking username was invisible to it.

### 14e. The corpus was destroyed, and how

Mutation 5 pointed the pool at the owner role, to prove the tests would
notice. They did — 15 failures. But `test_a_valid_session_still_cannot_write`
ran real `DELETE`/`UPDATE` statements and relied on the **role** to refuse
them, through the pool, which **commits on clean exit**. As `spm_admin`
they succeeded and committed before `pytest.raises` reported the missing
exception.

Lost: 58 `document_links` rows, 58 cascaded `document_content` rows (the
paid OCR cache), and all 9 `projects.name_en` values.

**The lesson generalises well past this repo: a destructive test must not
depend on the system under test for its own safety.** Those tests now run
inside a transaction they unconditionally roll back, so a statement that
unexpectedly succeeds still cannot commit — and
`test_the_write_denial_tests_cannot_commit` guards that guard. The mutation
runner additionally takes a `pg_dump` and verifies a corpus fingerprint
after **every** mutation.

Recovery, in order: project names restored from seed `002` (compared
column-by-column — only `name_en` had drifted), `document_links` reseeded
from `008`/`009`, `pipeline.scanner` refilled hashes (`insert 0`, so seeds
and disk agree exactly), the 9 stale Qdrant collections dropped, re-OCR,
re-index. **Cost: 154 pages ≈ $1.54.** Result: 678 chunks and 295,232
tokens — *identical* to the original, per project.

Two things the rebuild exposed:

- **The native fallback masks transient failures.** One `.docx` fell back
  after a `ServiceResponseError` — a service blip, not the permanent
  rejection the fallback was designed for — silently downgrading extraction
  quality while the run reported "RECOVERED". Re-OCR'd it to restore the
  57/1 split. **The fallback should distinguish a transient error from a
  rejection and retry; it does not. Recorded, not changed.**
- **Recall@k is not stable across index rebuilds.** On `ar-05-actions`,
  ranks 5/6/7 sit at 0.3118/0.3114/0.3111 — a 0.0007 spread. Qdrant's HNSW
  index is approximate, so rebuilding reshuffles near-ties across a
  cut-off. One question moved out of the top 5 and another moved 2→1. Treat
  a one-question recall change as noise; MRR and mean top score are
  steadier (mean top score was identical to four decimals).

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

**And on checking, its severity is largely an artifact of the test
grouping.** `TEST-PROJECT` is a deliberate bag of 13 unrelated real
documents filed as one "project"; a genuine project holds one project's
documents. Counting same-type collisions across the corpus:

| Project | Duplicated type | Count |
|---|---|---|
| TEST-PROJECT | Project Charter | 4 |
| TEST-PROJECT | Risk Management Plan Template | 2 |
| TEST-PROJECT | Business Requirements Document | 2 |
| **PRJ-03** | **Weekly Status Report** | **2** |

Exactly one *realistic* project has two documents of the same type — and
both state their own identifying date in the body text (`22/06/2026` and
`27/07/2026` respectively, in the first 1,400 characters), so they are
distinguishable from content alone without the filename.

So the defect is real but rare in the shape a real SharePoint would take.
**Re-indexing the whole corpus to fix a problem that mostly does not occur
outside an artificial test grouping is not obviously worth it** — and the
payload schema is a live contract on 678 points. Recorded here so the
finding is not acted on more forcefully than the evidence supports.

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
