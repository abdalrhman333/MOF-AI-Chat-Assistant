# START HERE — MOF Enjaz Chatbot, project entry point

**If you are an AI assistant (or a human) picking this project up, read this
file first.** It tells you what this project is, which documents to read in
what order, and how to verify the real state of the system before changing
anything.

**Last verified:** 2026-09-14 — infra, full SPM schema (17 migrations,
row-level security enabled), complete mock corpus, and **the entire
ingestion pipeline built and run over all 58 documents**: discovery → OCR →
chunk → embed → index, ending in **678 vectors across 9 per-project Qdrant
collections**. Retrieval quality is measured, not guessed
(`evaluation/`). `Application/` holds the AC09 session gate, grounded
answering over RAG + live SPM, the whole conversational layer (AC01–AC05,
AC07), **an HTTP layer, demo sign-in and the full UI** — so the system runs
in a browser. AC08 is measured, not asserted (p50 2333 ms / p95 2994 ms against
a 5 s budget), and tracing goes to Grafana and Langfuse from one
instrumentation.

Run the whole thing, and check it:

```bash
cd infra && docker compose up -d                    # everything, the app included → http://localhost:8000
.venv/Scripts/python -m pytest tests/ -q            # expect 700 passed
```

The app is the `mof-chat-api` container. After changing its code, rebuild it
with `docker compose up -d --build chat-api`; its log is
`docker logs -f mof-chat-api`. ⚠️ Docker Desktop auto-start is currently
**off**, so after a reboot open Docker Desktop first.

That single `pytest` covers the frontend too: `tests/test_frontend.py`
shells out to `node --test` for the 64 JavaScript tests in
`tests/frontend/`, and **skips rather than fails** where node is absent.
Sign in with any seeded address (e.g. `noura.alharbi@enjaz.gov.sa`) and
the demo password from `INFRASTRUCTURE.md` §276.

**Every acceptance criterion in the BRD is implemented.** Sign-in is
*simulated* — email and password against seeded accounts, then a signed
session cookie — because real AD SSO and OTP are external dependencies that
do not exist here.

```bash
# http://localhost:8000 — served by the mof-chat-api container
# sign in as noura.alharbi@enjaz.gov.sa / Enjaz@2026  (she holds 3 projects)
```

**After signing in, the project list comes first**, and it cannot be
dismissed: nothing opens until the user picks a project and locks the
session to it with "تقييد الجلسة بالمشروع". **Only a sign-in asks.** A page
refresh resumes the same project, the conversation under way and the screen
the user was on; switching is done from the project selector; signing out
forgets the selection. Nothing is pre-selected —
"recently used" projects are listed first, but choosing is always explicit.
The locked project's name then stays in the **top bar** on every screen.
The assistant addresses the user by **first name only** ("أهلاً بك يا نورة" /
"Welcome, Noura"), and its welcome states what it is for and where it stops
— this project only, only from its sources, no data changes, no guessing,
Arabic or English. Each of those limits is one the system enforces; the
mapping sits beside the text in `Application/backend/messages.py`.

⚠️ **All three Azure roles share one resource and one key** since
2026-09-07 (§22). The LLM moved to account #2 when account #1's key stopped
authenticating, so a single expired key now takes down generation,
retrieval *and* ingestion together. `config.py` groups model settings by
role rather than by account, which is why that move was a `.env` change.

⚠️ **The model is the router.** Since 2026-08-17 the language check and the
intent classifier are gone from the code: every message goes to the model,
which detects the language, decides the intent and answers in one call,
governed by the system prompt in `answer.py`. That reverses the earlier
deterministic design — read `STATUS_AND_ROADMAP.md` §21 before reinstating
anything that decides a message's meaning outside that prompt.

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
docker compose ps                          # 14 containers, all Up/healthy
docker ps --format "table {{.Names}}\t{{.Status}}"
```

Expected containers (project name `mof-enjaz-chatbot`, network `mof-net`):
`mof-postgres-spm`, `mof-qdrant`, `mof-redis`, `mof-langfuse-postgres`,
`mof-langfuse-clickhouse`, `mof-langfuse-redis`, `mof-langfuse-minio`,
`mof-langfuse-worker`, `mof-langfuse-web`, `mof-grafana-lgtm`,
`mof-portainer`, plus the application code: the ingestion pipeline
(`mof-pipeline-worker`, `mof-pipeline-beat`) and the chat app
(`mof-chat-api`).
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
python infra/postgres-spm/apply_migrations.py --status   # read-only; expect 17 applied, 0 pending, 0 drifted
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

# just one stage: scan | ocr | classify | index | ingest
docker compose exec pipeline-worker python -m pipeline.tasks scan --wait

docker logs -f mof-pipeline-worker      # per-document progress
```

⚠️ **Pipeline code changes do nothing until the containers are rebuilt.**
The worker and beat run the code baked into their image, not the files in
the repository:

```bash
cd infra && docker compose up -d --build pipeline-worker pipeline-beat
```

This is not hypothetical: the classify stage was added on 2026-09-07, and
every scheduled run skipped it until the image was rebuilt on 2026-09-14.

**A missed run fires late, on purpose.** If Docker was off at 13:00, beat
sends the missed run as soon as it starts again — even the next morning. On
an unchanged corpus that costs nothing, so it is kept. Services are often
still starting at that moment, so a full run retries a stage that cannot
*connect* (10 s, 20 s, 40 s) before reporting it failed; any other error
fails at once.

**Is it healthy?** One command answers the operational questions —
when ingestion last succeeded, whether anything failed, and whether Qdrant
still agrees with Postgres. **Exit 0 healthy, 1 needs attention**, so it
works as a cron check without parsing the output.

It judges the schedule by the **last recorded full run**, kept in Redis by
every full-corpus ingest: an error if the latest run failed, a warning if
none has succeeded for 26 hours. (Until 2026-09-14 it judged by the newest
*indexed document*, so an unchanged corpus processed correctly every day
warned "nothing indexed for 33 days — check beat and the worker" while both
were running.) "last indexed" is still shown, labelled as what it is — when
a document last changed. Run it inside the worker, where the settings match
the container:

```bash
cd infra && docker compose exec pipeline-worker python -m pipeline.status
docker compose exec pipeline-worker python -m pipeline.status --json
#   last run    : 2026-09-14 12:42 UTC (0 min ago) — ok
#   last success: 2026-09-14 12:42 UTC (0 min ago) — ok
```

The Postgres↔Qdrant comparison is the part nothing else does: the two are
written in one per-document transaction but are separate systems, so a
worker killed between the Qdrant upsert and the Postgres commit leaves them
disagreeing while every stage still reports no work left.

A healthy no-op run looks like this — every stage reporting nothing to do:

```json
{"ok": true, "stages": [
  {"stage": "scan",     "skipped": false, "files": 58, "written": 0},
  {"stage": "ocr",      "skipped": false, "succeeded": 0, "pages_billed": 0},
  {"stage": "classify", "skipped": false, "candidates": 0, "classified": 0},
  {"stage": "index",    "skipped": false, "indexed": 0, "chunks": 0}]}
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
| `evaluation/` | **Judges** it — the tagged question set, scoring, recorded baseline, `latency_bench.py` for AC08 | read-only |
| `Application/` | **Serves** it — `backend/api/` (FastAPI, sign-in, **the identity boundary**), `backend/answer.py` (**the system prompt — the whole router**), `backend/conversation.py` (the entry point), `backend/session.py` (the AC09 gate), `backend/db.py` (pool + scope), `backend/retrieval.py`, `backend/spm.py`, `backend/messages.py`, `backend/clients.py` (the AC08 client cache), `frontend/index.html` (the UI) | `spm_app` — SELECT-only, one project per transaction |
| `observability/` | **Watches** them — one OpenTelemetry instrumentation, exported to Grafana *and* Langfuse. Belongs to none of the three, which is why it is its own package | none |

**The pipeline runs as a containerised Celery worker** —
`mof-pipeline-worker` and `mof-pipeline-beat`.

**The HTTP layer and the UI exist** (`backend/api/`, `frontend/index.html`,
§20). One process serves both, on the same origin.

⚠️ **`api/identity.py`, `api/credentials.py` and `api/cookies.py` are the
second most security-sensitive files here, after `session.py`.** The gate
answers *"may this user open this project?"* and never *"is this really
that user?"* — these answer the second question. A username never comes
from a request body or query string, and there is deliberately no field to
put one in. The default `session_login` mode verifies a password and mints
an **HMAC-signed** cookie; an unsigned cookie would be a username the
browser can edit. `trusted_header` is production-*shaped*, not
production-*safe* — correct only behind a proxy that overwrites the header.

⚠️ **The signing secret (`API_SESSION_SECRET`) has no default.** A
hard-coded fallback would let anyone with the source mint a session for any
username. Missing means sign-in refuses.

⚠️ **A conversation id is a handle, not a credential.** Every lookup is
owner-checked; without that, a leaked id bypasses AC09 entirely, because
the gate validated the *owner's* assignment. Conversations are kept in
Redis, so they survive a restart and a second worker, and every read
re-runs the gate — a stored project code is a claim, not a permission.

Holding a real conversation end to end, today. **Use `open_conversation`**
— it applies AC01 and AC02 before spending anything, carries history for
AC03, and offers AC04's welcome. `answer_question` underneath is the
grounded-answering engine and skips all of that:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -c "from Application.backend.session import open_session; from Application.backend.conversation import open_conversation; from Application.backend import db; t=open_conversation(open_session('noura.alharbi','PRJ-06')); print(t.welcome); [print(q,'->',t.ask(q).text[:90]) for q in ['ما الميزانية المعتمدة للمشروع؟','وتاريخ الانتهاء؟']]; db.close_pool()"
```

`handle_question(session, text)` is the single-turn form underneath it, for
a caller that owns its own history.

⚠️ **These are entry points, not chokepoints.** `answer_question` is still
importable and bypasses the language check, so anything user-facing must
come through `conversation.py`. The rule that must never be bypassable —
project scope — is structural already (`Session`), which is why this one is
not duplicated. A `Conversation` extends that through time: its session is
fixed at construction, so switching project means a new one.

⚠️ **`evaluation/conversation_probe.py` was deleted on 2026-08-17.** It
scored a deterministic language detector against 54 tagged probes, offline
and free. The model decides now, so the equivalent measurement costs a
completion per probe — worth rebuilding as a paid eval, and named here so
its absence is a decision rather than an oversight (`STATUS_AND_ROADMAP.md`
§21).


