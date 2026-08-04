# Status & Roadmap — MOF Enjaz Chatbot

**Last verified:** 2026-08-04

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
- [x] **Infrastructure fully built and verified.** 11 containers, all
  healthy: Postgres (SPM), Qdrant, Redis, full Langfuse v3 stack, Grafana
  LGTM, Portainer. See `INFRASTRUCTURE.md`.
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
- [ ] **Remaining SPM schema** — users (AD mock), project assignments,
  document-project links, indexing status. Only `projects` exists so far;
  informed by BRD user stories 4–5 (AC08–AC11).
- [ ] **Ingestion pipeline** (SharePoint/mock-corpus scan → OCR → embed →
  index into per-project Qdrant collections). Redis is already running for
  this; no task code exists yet. Depends on the two items above.
- [ ] **Workflow engine / chat backend.** No application code exists at
  all yet — this project is infrastructure-only so far. Needs: language
  detection + rejection of unsupported languages (AC01), intent routing
  (special vs. general/SPM-retrieval, AC02), the three-source retrieval
  composition (§3 of `SOLUTION_ARCHITECTURE.md`), grounded responses with
  citations (AC06), safe fallback for out-of-scope/missing-knowledge
  queries (AC07), 5-second response budget (AC08).
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

---

## 5. Immediate next step

The `projects` table exists and is seeded; the mock corpus (fictional +
real) is fully placed on disk with every folder now having a matching SPM
row. Remaining SPM schema pieces (users, assignments, document-project
links, indexing status) are next, followed by the ingestion pipeline
design.

## 6. Gotchas hit during this phase

- **Git Bash mangles UTF-8 in inline shell arguments.** Testing the Azure
  OpenAI endpoint with Arabic text via `curl -d '{"...عربي..."}'` produced
  garbled input the model couldn't read; writing the same JSON to a file
  first and using `curl --data-binary @file` worked correctly. Same class
  of issue as the colleague's "PowerShell mangles UTF-8" gotcha — different
  shell, same root cause (don't trust inline args with non-ASCII on
  Windows). Always test Arabic payloads via a file, not an inline `-d`.

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
