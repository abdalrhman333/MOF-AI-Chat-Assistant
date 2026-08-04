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
- [ ] **Actual document files (.docx/.pdf) not yet generated.**
  ⚠ **Do not generate these until explicitly told** — the manifest
  describes what each document should contain; turning that into real
  files (candidate approach: use the assistant's document-creation skill
  to generate all 45 from the manifest directly) is a separate,
  not-yet-approved step. Target location once approved:
  **`D:\MOF_DataSample\<ProjectCode>\<filename>`** (outside the repo,
  user's own chosen path).
- [ ] **SPM Postgres schema.** Currently just a placeholder
  (`postgres-spm/init/001_extensions.sql` enables `uuid-ossp`/`pgcrypto`
  only). Needs: projects, users (AD mock), project assignments,
  document-project links, indexing status — informed by BRD user stories
  4–5 (AC08–AC11). Depends on the two items above.
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
| 2026-08-04 | SPM mock data manifest delivered by user (8 projects/4 users/45-doc manifest) and committed to `mock-data/spm_manifest.json` — **actual .docx/.pdf file generation still on hold** | §2 above — do not generate the 45 files without being told |

---

## 5. Immediate next step

**Waiting on the user** — the SPM data manifest is delivered and
committed, but generating the actual 45 `.docx`/`.pdf` files under
`D:\MOF_DataSample` needs explicit go-ahead first. Once that happens (or
in parallel), the SPM Postgres schema can be designed against the real
manifest shape (projects/users/assignments/documents tables now have a
concrete data source to match).

## 6. Gotchas hit during this phase

- **Git Bash mangles UTF-8 in inline shell arguments.** Testing the Azure
  OpenAI endpoint with Arabic text via `curl -d '{"...عربي..."}'` produced
  garbled input the model couldn't read; writing the same JSON to a file
  first and using `curl --data-binary @file` worked correctly. Same class
  of issue as the colleague's "PowerShell mangles UTF-8" gotcha — different
  shell, same root cause (don't trust inline args with non-ASCII on
  Windows). Always test Arabic payloads via a file, not an inline `-d`.
