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

- [ ] **Mock corpus and mock AD — decided, NOT yet implemented.**
  ⚠ **Do not create any folders/files for this until explicitly told** —
  the user may supply their own mock SPM data and SharePoint project
  folders/documents rather than have these generated. Decisions made
  (2026-08-04):
  - **Scale**: 6-8 mock projects.
  - **Document types**: broader realistic set (beyond the BRD's own named
    examples of charter/weekly-report/lessons-learned — also meeting
    minutes, risk register, budget/financial report, handover doc, etc.).
  - **Document realism**: **real files** (Word/PDF, bilingual
    Arabic/English, at least one with a table), run through the actual
    verified `prebuilt-layout` OCR pipeline — not plain-text stubs. This
    is deliberate: it's the only way to actually exercise the OCR stage
    before real SharePoint content arrives.
  - **Storage location**: outside the repo, at **`D:\MOF_DataSample`**
    (user's own chosen path — not `D:\MOF_Installations`, which is
    reserved for Docker/tool persistent data per
    `SOLUTION_ARCHITECTURE.md` §4.4).
  - **Mock AD users/assignments**: left to the assistant's judgment
    (not yet designed) — plan is a handful of users, some single-project,
    at least one multi-project (to exercise the project switcher), and at
    least one project deliberately unassigned to any test user (to prove
    isolation). Not implemented yet either.
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
| 2026-08-04 | Mock corpus/AD shape decided (6-8 projects, broader document-type set, real files through real OCR, stored at `D:\MOF_DataSample`) — **implementation explicitly on hold**, user may supply their own mock data | §2 above — do not create files/folders for this without being told |

---

## 5. Immediate next step

**Waiting on the user** — either they supply real mock SPM data and
SharePoint project folders/documents, or they explicitly say to generate
the mock corpus per the decisions in §2. Nothing to build here without one
of those two signals. SPM Postgres schema and ingestion pipeline design
both depend on this.

## 6. Gotchas hit during this phase

- **Git Bash mangles UTF-8 in inline shell arguments.** Testing the Azure
  OpenAI endpoint with Arabic text via `curl -d '{"...عربي..."}'` produced
  garbled input the model couldn't read; writing the same JSON to a file
  first and using `curl --data-binary @file` worked correctly. Same class
  of issue as the colleague's "PowerShell mangles UTF-8" gotcha — different
  shell, same root cause (don't trust inline args with non-ASCII on
  Windows). Always test Arabic payloads via a file, not an inline `-d`.
