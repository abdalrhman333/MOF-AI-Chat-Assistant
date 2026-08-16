# START HERE — MOF Enjaz Chatbot, project entry point

**If you are an AI assistant (or a human) picking this project up, read this
file first.** It tells you what this project is, which documents to read in
what order, and how to verify the real state of the system before changing
anything.

**Last verified:** 2026-08-12 — infra, full SPM schema (14 migrations,
row-level security enabled), complete mock corpus, and **the entire
ingestion pipeline built and run over all 58 documents**: discovery → OCR →
chunk → embed → index, ending in **678 vectors across 9 per-project Qdrant
collections**. Retrieval quality is measured, not guessed
(`evaluation/`). The **AC09 session gate is built** — `Application/` is
where all chat-application code lives. There is still no HTTP layer and no
frontend; retrieval composition (AC06) is the next build.

---

## 1. What this project is, in three sentences

An AI-powered digital assistant embedded in **منصة إنجاز** (Enjaz Platform)
that lets project managers ask natural-language questions (Arabic/English/
mixed) about their assigned projects and get answers grounded in the actual
project documentation from SharePoint/SPM — no manual searching. Each
session is locked to exactly one of the user's assigned projects, with
**absolutely no cross-project access** under any circumstance. This is a
demo/development build on the owner's own PC (RTX 3070, 32GB RAM, Docker
Desktop) — real Active Directory and live SPM/SharePoint are external
dependencies not available yet, so they're mocked, while OTP and other hard
external dependencies are explicitly deferred rather than faked.

This is a **different use case** than a colleague's separate build
(`RiskIntel`, project risk prediction) against the same unified BRD — see
`SOLUTION_ARCHITECTURE.md` §5 for how the two relate.

---

## 2. Which document to read, in order

| # | Document | What it's for |
|---|---|---|
| 1 | **This file** | Entry point, orientation, ground-truth verification |
| 2 | `SOLUTION_ARCHITECTURE.md` | What the solution must do (BRD-derived scope), the retrieval architecture, and every non-obvious design decision + why |
| 3 | `INFRASTRUCTURE.md` | The Docker Compose stack: services, ports, how to bring it up, verified gotchas |
| 4 | `STATUS_AND_ROADMAP.md` | What's actually done vs not, decisions log, lessons learned |
| 5 | **`NEXT_BUILD_CHAT_BACKEND.md`** | **If you are here to build the chat backend — the next thing — read this.** Acceptance criteria, the AC09 session gate, build order, non-negotiable rules, known-unknowns |
| 6 | `NEXT_BUILD_PIPELINE.md` | The ingestion pipeline's brief. **Now a record, not a to-do** — what was specified, what was built, and the five places they differ |
| 7 | `INVENTORY.md` | Every folder, container, cloud model, and library used on this machine — what it is, why, and how to access it |
| — | `Application/README.md` | **The chat application's layout and its trust boundary.** Read before touching anything user-facing: the gate trusts the identity it is handed, and that is a deliberate, documented deferral |
| — | `SYSTEM_WORKFLOW.html` | **Visual walkthrough of what is built, in plain language.** Eight diagrams: the whole machine, the container map, the schema and the two database roles, all four pipeline stages, the scheduler and its locks, and the health check. Open it in a browser. Describes the current state only — no roadmap |
| — | `Enjaz_Solution_Guide.docx` | **For non-technical readers / stakeholders.** Plain-language guide to the whole solution: what it does, how it works, why each choice was made, and how to rebuild it. Not needed for development work |

The original BRD-derived scope document also lives at
`..\My_Prompts\SOLUTION_SCOPE.md` (verified directly against the source PDF —
see `SOLUTION_ARCHITECTURE.md` §1 for the source reference).

The mock corpus source data is `mock-data\spm_manifest.json` (tracked in
git — small, structured). The actual mock documents (bulky, not tracked)
live at `D:\MOF_DataSample\<project-code>\`, outside the repo. Full detail
in `STATUS_AND_ROADMAP.md` §7.

---

## 3. Ground truth is the running system, not any document

Docs drift. Before making changes, verify reality:

```bash
cd "D:\MOF Enjaz Chatbot\infra"
docker compose ps                          # 13 containers, all Up/healthy
docker ps --format "table {{.Names}}\t{{.Status}}"
```

Expected containers (project name `mof-enjaz-chatbot`, network `mof-net`):
`mof-postgres-spm`, `mof-qdrant`, `mof-redis`, `mof-langfuse-postgres`,
`mof-langfuse-clickhouse`, `mof-langfuse-redis`, `mof-langfuse-minio`,
`mof-langfuse-worker`, `mof-langfuse-web`, `mof-grafana-lgtm`,
`mof-portainer`, plus the ingestion pipeline itself:
`mof-pipeline-worker` and `mof-pipeline-beat`.
(`mof-langfuse-minio-init` runs once and exits — that's expected, not a
failure.)

If containers are down: `docker compose up -d` from `infra/` brings
everything back (all services are `restart: unless-stopped`). See
`INFRASTRUCTURE.md` §5 for the full health-check sweep and known gotchas.

The SPM database and mock document corpus are real and populated —
verify them too, don't assume from this doc:

```bash
MSYS_NO_PATHCONV=1 docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data -c "SELECT code, status FROM projects ORDER BY code;"
# expect 9 rows: PRJ-01..PRJ-08 + TEST-PROJECT

find "/d/MOF_DataSample" -maxdepth 1 -type d
# expect 9 folders, same codes as above
```

```bash
python infra/postgres-spm/apply_migrations.py --status   # read-only; expect 14 applied, 0 pending, 0 drifted
```

Stages 0 and 1 have run: every `document_links` row carries a real SHA-256
and a real path (not the seeded placeholders), and every document has been
extracted to markdown.

```bash
MSYS_NO_PATHCONV=1 docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data -c \
  "SELECT count(*) total, count(file_hash) hashed FROM document_links;"
# expect 58 | 58
MSYS_NO_PATHCONV=1 docker exec -i mof-postgres-spm psql -U spm_admin -d spm_data -c \
  "SELECT ocr_model, count(*) FROM document_content GROUP BY 1;"
# expect prebuilt-layout | 57  and  native:python-docx | 1

# Every stage is free to re-run on an unchanged corpus — that is the point:
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.scanner --dry-run   # unchanged 58, rows written 0
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.ocr --dry-run       # candidates : 0
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.indexer --dry-run   # candidates : 0
```

⚠️ **`pipeline.ocr` spends money** (Document Intelligence is billed per
page). It cannot re-bill an unchanged corpus — the presence of a
`document_content` row is the "OCR done" marker — but always `--dry-run`
first, and note `--limit N` exists. The whole corpus is ~154 pages ≈ $1.50.
`pipeline.indexer` also spends, but embeddings are trivially cheap: the
entire corpus is 295,232 tokens, under a cent.

**Running the pipeline.** It runs itself — `mof-pipeline-beat` fires a full
ingest **daily at 13:00 `Asia/Amman`** (`INGEST_SCHEDULE_HOUR` /
`_MINUTE` / `_TIMEZONE`), so dropping a file into a project folder is
enough. ⚠️ The timezone is part of the schedule: containers run on UTC, so
`hour=13` without it would fire at 16:00 local. To trigger one by hand:

```bash
# whole corpus, wait for the result
docker compose exec pipeline-worker python -m pipeline.tasks ingest --wait

# one project only
docker compose exec pipeline-worker python -m pipeline.tasks ingest --project PRJ-05 --wait

# just one stage: scan | ocr | index | ingest
docker compose exec pipeline-worker python -m pipeline.tasks scan --wait

docker logs -f mof-pipeline-worker      # per-document progress
```

**Is it healthy?** One command answers the operational questions —
when ingestion last succeeded, whether anything failed, and whether Qdrant
still agrees with Postgres. **Exit 0 healthy, 1 needs attention**, so it
works as a cron check without parsing the output:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.status
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.status --json
```

The Postgres↔Qdrant comparison is the part nothing else does: the two are
written in one per-document transaction but are separate systems, so a
worker killed between the Qdrant upsert and the Postgres commit leaves them
disagreeing while every stage still reports no work left.

A healthy no-op run looks like this — every stage reporting nothing to do:

```json
{"ok": true, "stages": [
  {"stage": "scan",  "skipped": false, "files": 58, "written": 0},
  {"stage": "ocr",   "skipped": false, "succeeded": 0, "pages_billed": 0},
  {"stage": "index", "skipped": false, "indexed": 0, "chunks": 0}]}
```

**Failure modes worth recognising:**

| Symptom | Meaning |
|---|---|
| `"skipped": true, "reason": "…already running"` | Another run holds that stage's lock. Not an error — it stood down deliberately |
| CLI exits **2**, `Stage 1 OCR — SKIPPED` | Same, from a hand-run command while the worker is busy |
| `RuntimeError: DOCUMENT_ROOT does not exist` | Wrong root. Fails once, loudly, instead of marking 58 documents failed |
| `No files found anywhere under DOCUMENT_ROOT … database already holds N` | Root points somewhere wrong; refused so existing rows aren't all reported missing |
| `file_path is absolute … run pipeline.scanner` | A row predating the relative-path fix. One scan repairs it |
| `"ok": false` with a stage `error` | That stage raised; the later stages still ran. Check `error_message` in `document_links` |

**Seeing the vectors.** Qdrant's dashboard
(http://localhost:6333/dashboard) loads *without* a key while its API
returns 401, so an unauthenticated dashboard looks broken rather than
locked. Paste `QDRANT_API_KEY` into its **API Key** box. Or from the
terminal, with nothing typed:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -c "from qdrant_client import QdrantClient; from pipeline.config import get_settings; s=get_settings(); q=QdrantClient(url=s.qdrant.url, api_key=s.qdrant.api_key); [print(f'{c.name:<26} {q.count(c.name).count:>4}') for c in q.get_collections().collections]"
# expect 9 collections, 678 points
```

**Python environment** — use the project venv, never system Python:

```bash
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
.venv/Scripts/python -c "from pipeline.config import get_settings; print(get_settings().document_root)"
```

If `.venv/` doesn't exist yet: `python -m venv .venv`, then install.
`infra/postgres-spm/apply_migrations.py` intentionally needs no
third-party packages, so the database can be rebuilt without the venv.

**Measuring retrieval quality.** `evaluation/` holds 29 tagged questions
and scores them, so a change can be shown to help rather than felt to. The
baseline is **MRR 0.829, recall@1 0.76 / @3 0.90 / @5 0.93**
(`evaluation/baseline.json`). It validates every tagged filename against
the database before embedding anything, and `--dry-run` costs nothing:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.retrieval_eval --dry-run
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.retrieval_eval
```

⚠️ The often-repeated "Arabic ranking is the known gap" was **measured on
2026-08-09 and largely refuted** — Arabic similarity *scores* are lower
(0.483 vs 0.551) but ranking is essentially equal (MRR 0.825 vs 0.850, and
Arabic wins at recall@5). See `STATUS_AND_ROADMAP.md` §13b before acting on
the old claim.

**The code is three packages, each with one job and its own database
privileges.** The separation is not filing tidiness — they run as separate
processes, and the one that faces users has the least access.

| Package | Job | Database role |
|---|---|---|
| `pipeline/` | **Builds** the knowledge base — `config.py` (all settings), `chunker.py`, `db.py`, `scanner.py` (Stage 0), `ocr.py` + `native_extract.py` (Stage 1), `indexer.py` (Stages 2+3), plus `locking.py`, `tasks.py`, `status.py` to run it unattended | `spm_admin` — needs every project |
| `evaluation/` | **Judges** it — the tagged question set, scoring, recorded baseline | read-only |
| `Application/` | **Serves** it — `backend/session.py` (the AC09 gate), `backend/db.py` (pool + scope), `backend/retrieval.py`, `backend/answer.py`, `backend/spm.py` | `spm_app` — SELECT-only, one project per transaction |

**The pipeline runs as a containerised Celery worker** —
`mof-pipeline-worker` and `mof-pipeline-beat`.

⚠️ **There is still no HTTP layer and no frontend.** `Application/` is
importable Python, not a running service. `backend/api/` and `frontend/`
are named in `Application/README.md` as their future home and do not exist.
See `STATUS_AND_ROADMAP.md` §5 for the exact done/not-done line.

Asking a real question end to end, today:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -c "from Application.backend.session import open_session; from Application.backend.answer import answer_question; from Application.backend import db; s=open_session('noura.alharbi','PRJ-06'); a=answer_question(s,'ما الميزانية المعتمدة للمشروع؟'); print(a.text); [print(' -',c.label) for c in a.citations]; print(a.timings.as_dict()); db.close_pool()"
```

⚠️ **Cold start blows the AC08 five-second budget; warm runs sit inside
it.** Measured on the command above: the *first* call in a fresh process
spends ~2.5 s just embedding the question, and lands around **5.1 s**
total. A second call in the same process embeds in ~0.6 s and finishes in
under 3 s. The LLM call itself is steady at ~1.2–2.5 s.

So AC08 is currently met **warm and missed cold**, and the cause is client
or connection setup rather than the model. Nothing has been tuned for this
yet — it belongs with the latency work (`STATUS_AND_ROADMAP.md` §5, step
5), and a long-lived HTTP process would pay the cold start once rather than
per question. Recorded so the first person to measure it does not think
they have found a regression.

The test suite is **353 tests** (answering 50, session_gate 42,
retrieval_eval 39, scanner 38, tasks 27, chunker 26, ocr 23, indexer 23,
rls 23, native_extract 22, status 15, config 13, locking 12). **Most need `mof-postgres-spm`
running, and the locking/tasks/status suites need Redis too** — they use the real schema inside a transaction they always roll
back, and skip with a clear message if the container is down. The OCR tests
stub Azure and the indexer tests stub both the embedding API and Qdrant, so
both paid stages are covered without a paid call or a write to the live
vector store.

---

## 4. Hard constraints — do not violate these

1. **Nothing Docker-related touches C:.** Docker Desktop's own engine disk
   was relocated to `D:\MOF_Installations\docker-data`; every service's
   persistent data bind-mounts to `D:\MOF_Installations\volumes\<service>`.
   Any new service must follow the same pattern.
2. **No cross-project data access, ever** (BRD AC09, worded as absolute).
   This is why the vector KB uses one separate Qdrant collection per
   project rather than a shared collection with a filter — see
   `SOLUTION_ARCHITECTURE.md` §4.2 for the full reasoning.
3. **Cloud models for LLM/OCR/embeddings this phase** — not on-prem, despite
   the RTX 3070 being available. This was decided explicitly, twice,
   including after reviewing a colleague's on-prem approach. Don't revisit
   without checking `SOLUTION_ARCHITECTURE.md` §5 first.
4. **Real secrets live only in `infra/.env`** (gitignored). `infra/.env.example`
   is the template with blanks. Never write real secret values into
   documentation or into `.env.example`.
5. **The chat app connects as `spm_app`, never `spm_admin`** — and every
   transaction must `SET LOCAL app.current_project`. A plain `SET` persists
   on a pooled connection and leaks that scope into the next request. Only
   the ingestion pipeline uses `spm_admin`, because it needs cross-project
   access. See `INFRASTRUCTURE.md` §7c.
6. **Never apply migrations by hand** — use
   `infra/postgres-spm/apply_migrations.py`, or `schema_migrations` stops
   reflecting reality. See `INFRASTRUCTURE.md` §7b.

---

## 5. Starter prompt for a new AI session

> I'm continuing work on the MOF Enjaz Chatbot (AI assistant for منصة إنجاز),
> at `D:\MOF Enjaz Chatbot`. Before doing anything, read
> `Documentation\START_HERE.md`, then `Documentation\SOLUTION_ARCHITECTURE.md`,
> `Documentation\INFRASTRUCTURE.md`, `Documentation\STATUS_AND_ROADMAP.md`
> (the decisions log and lessons learned — don't skip it, and read §13–§16
> for what the last sessions built and broke), and
> `Documentation\NEXT_BUILD_CHAT_BACKEND.md` for the build order. If you
> will touch anything user-facing, read `Application\README.md` too — it
> holds the trust boundary.
>
> **Where the build has reached:** the ingestion pipeline is finished and
> runs unattended. Retrieval quality is measured, not guessed
> (`evaluation/`). The AC09 session gate, the scoped data-access layer,
> grounded answering over RAG, and the live SPM source are all built and
> live in `Application/`. **There is still no HTTP layer and no frontend.**
> Next up is AC01 (language check) and AC02 (intent routing) — see
> `STATUS_AND_ROADMAP.md` §5.
>
> Then verify the live system rather than trusting the docs: 13 containers
> up, 14 migrations applied and 0 drifted, 353 tests passing, all three
> pipeline stages reporting nothing to do (`--dry-run` on
> `pipeline.scanner`, `pipeline.ocr` and `pipeline.indexer`), Qdrant
> holding 9 collections / 678 points, and `python -m pipeline.status`
> exiting 0. You can also ask a real question end to end:
> `open_session(username, project_code)` then `answer_question(session, q)`.
>
> Five working rules: use `.venv/Scripts/python`, never system Python;
> prefix commands that print with `PYTHONIOENCODING=utf-8`, since most of
> the corpus is Arabic; `pipeline.ocr` spends money — always `--dry-run`
> first; the application connects as `spm_app`, never `spm_admin`, and
> applies scope with `set_config(name, value, true)` because `SET LOCAL`
> cannot take a bind parameter; and every retrieval goes through a
> `Session` obtained from the gate, never a raw project code.
>
> Design principle to hold to: **everything must be dynamic, not hardcoded
> to this specific demo case** — settings belong in `pipeline/config.py`,
> not as literals in code.
>
> Working method that has repeatedly paid for itself here: prove a safety
> feature by deliberately breaking it and confirming the right test
> catches it, and commit before doing so. Roughly 90 mutations have been
> run; the ones that *survived* found the real gaps, and none were visible
> on inspection. Three cautions learned the hard way: a mutation that does
> not compile proves nothing; a destructive test must not depend on the
> thing it is testing to keep it safe (that one destroyed the corpus, §14e);
> and a passing stubbed suite is not evidence — run it live (§16a).
>
> My request: <what you want>
