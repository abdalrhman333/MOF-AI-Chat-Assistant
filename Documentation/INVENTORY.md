# Inventory — Folders, Tools, Models, Libraries

**Last verified:** 2026-09-14

A tracking reference for everything installed, downloaded, or created on
this machine for this project — what it is, why it exists, and how to
reach it. For *why* architectural choices were made, see
`SOLUTION_ARCHITECTURE.md`; this document is the "where is it and how do I
open it" reference.

---

## 1. Folders on this PC

### `D:\MOF Enjaz Chatbot\` — the git repository (source of truth, all committed)

| Folder/file | Contents | Why here, not elsewhere |
|---|---|---|
| `Documentation\` | This file + `SOLUTION_ARCHITECTURE.md`, `INFRASTRUCTURE.md`, `STATUS_AND_ROADMAP.md`, `NEXT_BUILD_PIPELINE.md`, `NEXT_BUILD_CHAT_BACKEND.md`, `START_HERE.md`, `SYSTEM_WORKFLOW.html`, `Enjaz_Solution_Guide.docx` (plain-language guide for non-technical readers — how the solution was built, Stages 1–16), `AI_SOLUTION_PLAYBOOK.md` (**a generic template for colleagues starting their own AI use case** — tools, structure, build order, starting settings, rules, release checklist), `UC3_Enterprise_Assistant_ERD.pdf` + `diagrams\uc3_erd.py` (**the ERD as a printable two-page A3 document and the script that generates it**), `SPM_DATA_MODEL_ERD.md` (**the end-to-end ERD against the official SPM schema v0.3, sheet UC3** — 11 entities, 119 fields with types, mapped onto our tables; what exists, what to add, open questions) | Small text, versioned with the code it describes |
| `My_Prompts\` | `SOLUTION_SCOPE.md` (BRD-derived scope, assistant-authored) + `PRE_PIPELINE_ENHANCEMENTS.md` (user-authored) | Requirements/planning source material |
| `infra\` | `docker-compose.yml`, `.env` / `.env.example`, `postgres-spm\init\` (one-time) + `migrations\` (17 SQL files, ongoing), `apply_migrations.py`, `README.md` | All infrastructure-as-code |
| `mock-data\` | `spm_manifest.json` — the 8-project / 4-user / 45-document source data | Small structured data, worth versioning |
| `pipeline\` | `config.py` (all settings), `chunker.py`, `db.py` (the `spm_admin` connection), `scanner.py` (Stage 0 — discovery), `ocr.py` (Stage 1 — extraction, with transient-error retries), `classify.py` + `doc_type_labels.json` (Stage 1b — document type, labels held as **data**), `native_extract.py` (Office fallback), `indexer.py` (Stages 2+3 — chunk, embed, index), `locking.py` (cross-process stage locks), `tasks.py` (Celery tasks + schedule; retries a stage that cannot connect), `run_record.py` (the outcome of the last full run, kept in Redis), `status.py` (operational health check, judged on the last successful run) | Builds the knowledge base. Complete, and runs as a containerised worker. The chat application lives in `Application\` |
| `Application\` | `README.md` (layout + the trust boundary), `backend\errors.py` (typed access failures), `backend\db.py` (the `spm_app` pool, transaction boundaries, scope applied with a bind parameter), `backend\session.py` (the AC09 gate), `backend\retrieval.py` (vector search, scoped to a Session), `backend\spm.py` (the live project record), `backend\answer.py` (**the system prompt — the assistant's brain** — the LLM call, citation resolution), `backend\conversation.py` (history + follow-up rewriting), `backend\messages.py`, `backend\clients.py`. **`backend\api\`**: `app.py` (FastAPI routes, tracing middleware), `identity.py`, `credentials.py` (PBKDF2 demo passwords), `cookies.py` (HMAC-signed session cookies), `store.py` + `redis_store.py` (conversation stores), `ratelimit.py`, `schemas.py`. **`frontend\index.html`**: the entire UI in one static file — sign-in, project picker, chat, knowledge base — styled to the MOF design screens, no build step, no framework, served by the API process on the same origin. **`frontend\assets\`**: `logo.svg` (committed) and `fonts\` — DIN Next LT Arabic, **gitignored** because it is a licensed Monotype font; the folder's README says which files to place there, and the page falls back to a system font without them | **All chat-application code lives here**, kept apart from the pipeline that builds the knowledge base and the harness that measures it — they run as different processes under different database roles |
| `evaluation\` | `dataset.py` (load + validate the question set), `retrieval_eval.py` (embed, search, score) + `questions.json` (29 tagged questions) + `baseline.json`; `router_eval.py` (drives 59 tagged probes through the **real** answer path and scores language + intent) + `router_probes.json` + `router_baseline.json`; `conversation_probes.json`; `latency_bench.py`. Question sets are **data, not code** — paths configurable | Measures quality so a change can be shown to help. Separate from `pipeline\` because the pipeline *produces* the vector store and this *judges* it. The router eval exists because the LLM-first redesign moved language and intent into the model, and a component with no measurement is a component nobody can safely change |
| `tests\` | `test_answering.py` (59) + `test_conversation.py` (49) + `test_session_gate.py` (42) + `test_ocr.py` (40) + `test_retrieval_eval.py` (39) + `test_observability.py` (48) + `test_scanner.py` (38) + `test_auth.py` (71) + `test_continuity.py` (35) + `test_tasks.py` (39) + `test_api.py` (34) + `test_chunker.py` (26) + `test_rls.py` (23) + `test_indexer.py` (23) + `test_redis_store.py` (22) + `test_native_extract.py` (22) + `test_status.py` (21) + `test_run_record.py` (7) + `test_classify.py` (15) + `test_config.py` (15) + `test_router_eval.py` (12) + `test_locking.py` (12) + `test_deployment.py` (19) + `test_ratelimit.py` (10) + `test_frontend.py` (2) + `tests\frontend\` (`harness.mjs` + `ui.test.mjs`, 79 JS tests) + `tests\fixtures\` (3 real OCR samples) | **723 Python tests, plus 79 JavaScript.** `test_deployment.py` reads the real Compose file and Dockerfile, so the container decisions — port bound to this machine, service names not localhost, no startup dependency on tracing, no root user, no secrets in the image — cannot be undone by a quiet edit. The frontend suite is JavaScript because the thing under test is — `test_frontend.py` runs it through `node --test` so one `pytest` command still covers the whole system, and **skips rather than fails** where node is absent. Its DOM shim throws for any element id the markup does not contain, so a `$()` typo fails there instead of on a user's click. The Postgres-backed ones roll back and leave nothing behind; `test_locking.py` and `test_tasks.py` need Redis. Everything that costs money is stubbed — `test_ocr.py` stubs Azure; `test_indexer.py`, `test_retrieval_eval.py` and `test_answering.py` stub the embedding API, Qdrant and the LLM — so the full write and answer paths are tested with no paid call. **`test_rls.py` and `test_session_gate.py` are the only files that connect as `spm_app`** — every other file uses `spm_admin`, which bypasses row-level security, which is why RLS went untested until 2026-08-09 |
| `observability\` | `tracing.py` — **one** instrumentation feeding **two** backends (Grafana Tempo and Langfuse), plus the HTTP semantic-convention names. ⚠️ Fails **open**: a tracing outage must never become a service outage | Tracing is cross-cutting; it belongs to neither the pipeline nor the chat application |
| `Dockerfile` / `.dockerignore` | **Two images from one file**: target `pipeline` (worker + beat) and target `app` (the chat app), on a shared dependency layer | `docker compose build pipeline-worker` / `chat-api`. Rebuilding one does not recreate the other. The context excludes `.venv` and `infra/.env`; the licensed fonts are copied into the local app image, which is never pushed |
| `requirements.txt` / `requirements-dev.txt` | Pinned Python dependencies | Reproducible environment |
| `.venv\` | Project virtualenv | **Gitignored** — machine-specific, never committed |
| `Tools ACC\` | One screenshot | Not created by the assistant — the chat app's own attachment storage |

### `D:\MOF_Installations\` — everything *installed*, not *written* (outside the repo)

Chosen location per the user's own instruction: "will use this folder to
install any tool or model we need," and later extended to "anything
downloaded/created for this project stays under D:, nothing touches C:."

| Folder | Contents | Why |
|---|---|---|
| `docker-data\` | Docker Desktop's entire engine disk (images, containers, named volumes) | Relocated via Docker Desktop's own Settings GUI so nothing Docker-related lands on C: |
| `tools\dbeaver-ce-portable\` | DBeaver Community Edition (~201 MB, portable, no installer, bundles its own JRE) | Requested: a visual Postgres browser, kept off C: |
| `volumes\<service>\` | Bind-mounted persistent data per container: `postgres-spm`, `qdrant`, `redis`, `langfuse-postgres`, `langfuse-redis`, `langfuse-minio`, `grafana-lgtm`, `portainer` | Each container's data as plain, inspectable folders. (`langfuse-clickhouse` is the one exception — a named Docker volume instead, because ClickHouse's storage engine needs real POSIX rename semantics the Windows bind-mount bridge can't provide; still physically on D:, inside `docker-data\`, just not a visible folder here — see `INFRASTRUCTURE.md` §4.1) |

### `D:\MOF_DataSample\` — the mock document corpus (not in git — bulky, binary)

| Folder | Contents | Source |
|---|---|---|
| `PRJ-01` … `PRJ-08` | 45 `.docx`/`.pdf` files | Generated from `mock-data\spm_manifest.json` via 8 background agents |
| `TEST-PROJECT` | 13 real files | User-supplied real project documents, grouped as one project per explicit instruction |

---

## 2. Docker containers (14 total, all `restart: unless-stopped`)

| Container | Image | Purpose | Access |
|---|---|---|---|
| `mof-postgres-spm` | postgres:16-alpine | SPM data: projects, users, assignments, document links | `localhost:5432` · `docker exec -it mof-postgres-spm psql -U spm_admin -d spm_data` · or DBeaver |
| `mof-qdrant` | qdrant/qdrant | Vector store — **9 collections, 678 points**, one collection per project | http://localhost:6333/dashboard · the page loads without a key but the API 401s, so paste `QDRANT_API_KEY` into its **API Key** box first |
| `mof-redis` | redis:7-alpine | Celery broker + result backend for the ingestion worker, and the store behind the cross-process stage locks | `localhost:6379` |
| `mof-langfuse-web` | langfuse/langfuse:3 | LLM tracing UI | http://localhost:3001 |
| `mof-langfuse-worker` | langfuse/langfuse-worker:3 | Langfuse background processing | internal |
| `mof-langfuse-postgres` | postgres:16-alpine | Langfuse's own metadata store | internal |
| `mof-langfuse-clickhouse` | clickhouse-server:24 | Langfuse's trace/analytics store | internal |
| `mof-langfuse-redis` | redis:7-alpine | Langfuse's own queue | internal |
| `mof-langfuse-minio` | minio/minio | Langfuse's blob storage | internal |
| `mof-grafana-lgtm` | grafana/otel-lgtm | App-wide metrics/logs/traces (distinct from Langfuse — infra/app observability, not LLM content) | http://localhost:3000 |
| `mof-portainer` | portainer-ce | Docker management UI | https://localhost:9443 |
| `mof-pipeline-worker` | `mof-enjaz-pipeline` (built from this repo) | **The ingestion pipeline as a background worker.** Runs scan / OCR / index as Celery tasks; the corpus is mounted **read-only** at `/data` | `docker compose exec pipeline-worker python -m pipeline.tasks ingest --wait` |
| `mof-pipeline-beat` | same image | Fires the scheduled full ingest — **daily at 13:00 `Asia/Amman`**. Keeps its schedule under `D:\MOF_Installations\volumes\pipeline-beat` so a restart doesn't lose it | `docker logs mof-pipeline-beat` |
| `mof-chat-api` | `mof-enjaz-chat` (built from this repo, target `app`) | **The chat application** — API and frontend. Reaches every dependency by service name; **bound to 127.0.0.1 only**, because the demo sign-in shares one password. Runs as a container since 2026-09-14 so it no longer dies with the terminal that started it | http://localhost:8000 · `docker logs -f mof-chat-api` · rebuild: `docker compose up -d --build chat-api` |

All credentials live in `infra/.env` (gitignored); variable names/purposes documented in `infra/.env.example`. Full detail: `INFRASTRUCTURE.md` §6.

---

## 3. Cloud AI models — all Azure, ONE account since 2026-09-07, all verified live

| Capability | Model | Account | Why this one |
|---|---|---|---|
| **LLM** | `gpt-5.4-mini`, via an Azure AI Foundry unified endpoint (`sce-foundry1`) | Account 2 *(moved 2026-09-07)* | Chat responses, structured output |
| **Embeddings** | `text-embedding-3-small` (1536-dim, `sce-foundry1`) | Account 2 | Text → searchable vectors |
| **OCR** | Azure AI Document Intelligence, `prebuilt-layout` model | Account 2 | Chosen over `prebuilt-read` specifically for table/structure extraction — verified live on real scanned Arabic content |

Credentials in `infra/.env` under `AZURE_OPENAI_LLM_*`, `AZURE_OPENAI_EMBEDDING_*`, `AZURE_DOCUMENT_INTELLIGENCE_*`. Full reasoning: `SOLUTION_ARCHITECTURE.md` §4.1.

⚠️ **The LLM and embedding endpoint/key pairs now hold identical values**,
because all three roles share one resource. They stay separate variables on
purpose — `config.py` groups model settings by role, not by account, which
is what made the move a two-line `.env` change. The trade is a single blast
radius: one expired key stops generation, retrieval and ingestion together.
See `STATUS_AND_ROADMAP.md` §22.

---

## 4. Python libraries (pinned in `requirements.txt`, installed into `.venv\`)

| Library | Version | Why |
|---|---|---|
| `psycopg[binary]` | 3.3.4 | Postgres driver |
| `qdrant-client` | 1.19.0 | Qdrant client |
| `celery[redis]` | 5.6.3 | Task queue — chosen over a lighter alternative for maturity, deliberately, after weighing the tradeoff |
| `azure-ai-documentintelligence` | 1.0.2 | OCR API client |
| `openai` | 2.53.0 | Azure OpenAI client (LLM + embeddings) |
| `tiktoken` | 0.13.0 | Token counting for chunk sizing |
| `fastapi` | 0.121.2 | The HTTP layer |
| `uvicorn[standard]` | 0.40.0 | ASGI server. Multiple workers are safe now that conversations live in Redis; with `API_CONVERSATION_STORE=memory` they are per-process and a second worker would not see the first's |
| `psycopg_pool` | 3.3.1 | Connection pool, for the chat application only. Pooling is where the `SET LOCAL` rule stops being style and becomes the isolation boundary — a plain `SET` persists onto whoever borrows the connection next |
| `opentelemetry-sdk` | 1.39.0 | Tracing. **One instrumentation, two backends** — the same spans go to Grafana (OTLP :4318) and Langfuse (its own OTLP endpoint, verified 200 before use), so LLM tracing needs no second SDK |
| `opentelemetry-exporter-otlp-proto-http` | 1.39.0 | The exporter. HTTP rather than gRPC because Langfuse only speaks HTTP and grafana-lgtm speaks both — one package covers both backends. ⚠️ Pins protobuf to 6.x (was 7.35.1); verified afterwards that Qdrant reads and `pipeline.status` still work |
| `python-dotenv` | 1.2.2 | Loads `infra/.env` |
| `python-docx` | 1.2.0 | **Fallback only** — extracts `.docx` that Document Intelligence rejects. Never the primary path |
| `python-pptx` | 1.0.2 | **Fallback only** — same, for `.pptx` |
| `pytest` | 9.1.1 *(dev only)* | Test suite |

⚠️ **`pdfplumber` is deliberately NOT a dependency**, though it may linger
in a developer venv from the extraction experiment. It is why there is no
PDF fallback: native PDF text was measured as unusable — 36 Arabic
characters against Document Intelligence's 2,623, because text arrives as
Arabic presentation forms in visual order. PDF text must not be extracted
that way (`SOLUTION_ARCHITECTURE.md` §4.9).

Versions were **resolved then pinned from what actually installed**, not hand-guessed — an earlier attempt at guessing a version failed outright. Install with:

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

Never into system Python — one of the four gaps closed before the pipeline phase (`STATUS_AND_ROADMAP.md`).

---

## 5. Desktop tools installed

| Tool | Location | Why | How to open |
|---|---|---|---|
| **DBeaver CE** | `D:\MOF_Installations\tools\dbeaver-ce-portable\dbeaver\dbeaver.exe` | Visual Postgres browser — Postgres has no web UI of its own | Double-click the `.exe`. No installer; bundles its own JRE. Connection: `localhost:5432`, db `spm_data`, user `spm_admin`, password in `infra/.env` |

---

## 6. What's deliberately *not* built

The API and the frontend both exist, and run as the `mof-chat-api`
container — `docker compose up -d` from `infra/`, then
http://localhost:8000. What remains deferred is deferred on purpose:

- **Real Active Directory, real SharePoint, real OTP** — external
  dependencies, flagged as deferred rather than faked (per the BRD's own
  practical note for a demo build). Sign-in is a simulation over the users
  already seeded in SPM, and the sign-in screen says so on its own face
- **Mid-chat document upload and CAG** — deferred by explicit instruction.
  The redesigned knowledge-base screen (2026-09-14) has no upload control at
  all, matching the MOF design; nothing on screen implies it exists
- **LangChain** — the chosen RAG orchestration layer, still not installed.
  Nothing yet needs it, and a large unused dependency tree is churn

---

## 7. Quick verification commands

```bash
docker ps --format "{{.Names}}: {{.Status}}"                     # 14 containers
python infra/postgres-spm/apply_migrations.py --status           # 17 applied, 0 pending
.venv/Scripts/python -m pytest tests/ -q                         # 723 passed
find /d/MOF_DataSample -maxdepth 1 -type d                       # 9 project folders

# The whole pipeline has run. Every stage is free to re-run unchanged:
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.scanner --dry-run
#   -> unchanged 58, rows written 0
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.ocr --dry-run
#   -> candidates : 0        (nothing left to OCR, nothing to re-bill)
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.indexer --dry-run
#   -> candidates : 0        (678 vectors already indexed)
```

The application itself, and whether tracing reached both backends:

```bash
docker compose up -d                                 # from infra/ — then open :8000
curl -s localhost:8000/health
#   -> {"status":"ok","auth_mode":"session_login",
#       "tracing":["grafana","langfuse"],"conversation_store":"RedisConversationStore"}
```

`tracing` listing both names means both exporters were built, not that
either accepted a span — tracing fails open, so a broken backend is
invisible here by design. To check a trace actually landed, ask each
backend for it. Tempo's query port is not published, so it is reached
through Grafana's datasource proxy:

```bash
# Grafana / Tempo — spans, with the route TEMPLATE not the raw path
curl -s -u admin:$GRAFANA_ADMIN_PASSWORD \
  "localhost:3000/api/datasources/proxy/uid/tempo/api/search?q=%7B%7D&limit=5"

# Langfuse — the same turns, from the same instrumentation
curl -s -H "Authorization: Basic $(printf '%s:%s' \
  "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 -w0)" \
  "localhost:3001/api/public/traces?limit=5"
```

A chat turn appears in both as
`POST /api/conversations/{token}/messages` → `chat.turn` →
`retrieval.embed`, `retrieval.search`, `spm.fetch`, `chat gpt-5.4-mini`.

Qdrant's dashboard (http://localhost:6333/dashboard) loads without a key
but its API returns 401, so it looks broken until you paste
`QDRANT_API_KEY` into its **API Key** box. To check it from the terminal
instead — settings supply the key, nothing is typed:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -c "from qdrant_client import QdrantClient; from pipeline.config import get_settings; s=get_settings(); q=QdrantClient(url=s.qdrant.url, api_key=s.qdrant.api_key); [print(f'{c.name:<26} {q.count(c.name).count:>4}') for c in q.get_collections().collections]"
#   -> 9 collections, 678 points
```
