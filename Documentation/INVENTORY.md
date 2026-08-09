# Inventory — Folders, Tools, Models, Libraries

**Last verified:** 2026-08-09

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
| `Documentation\` | This file + `SOLUTION_ARCHITECTURE.md`, `INFRASTRUCTURE.md`, `STATUS_AND_ROADMAP.md`, `NEXT_BUILD_PIPELINE.md`, `NEXT_BUILD_CHAT_BACKEND.md`, `START_HERE.md` | Small text, versioned with the code it describes |
| `My_Prompts\` | `SOLUTION_SCOPE.md` (BRD-derived scope, assistant-authored) + `PRE_PIPELINE_ENHANCEMENTS.md` (user-authored) | Requirements/planning source material |
| `infra\` | `docker-compose.yml`, `.env` / `.env.example`, `postgres-spm\init\` (one-time) + `migrations\` (13 SQL files, ongoing), `apply_migrations.py`, `README.md` | All infrastructure-as-code |
| `mock-data\` | `spm_manifest.json` — the 8-project / 4-user / 45-document source data | Small structured data, worth versioning |
| `pipeline\` | `__init__.py`, `config.py` (all settings), `chunker.py`, `db.py` (the `spm_admin` connection), `scanner.py` (Stage 0 — discovery), `ocr.py` (Stage 1 — extraction), `native_extract.py` (Office fallback), `indexer.py` (Stages 2+3 — chunk, embed, index), `locking.py` (cross-process stage locks), `tasks.py` (Celery tasks + schedule), `status.py` (operational health check) | The only application code that exists so far. The ingestion pipeline is complete and runs as a containerised worker; there is no chat backend or frontend |
| `evaluation\` | `dataset.py` (load + validate the question set), `retrieval_eval.py` (embed, search, score), `questions.json` (29 tagged questions — **data, not code**, path configurable via `EVAL_QUESTIONS_PATH`), `baseline.json` (the measured baseline, a record) | Measures retrieval quality so a change can be shown to help. Separate from `pipeline\` because the pipeline *produces* the vector store and this *judges* it |
| `tests\` | `test_retrieval_eval.py` (39) + `test_scanner.py` (38) + `test_tasks.py` (27) + `test_chunker.py` (26) + `test_ocr.py` (23) + `test_indexer.py` (23) + `test_rls.py` (23) + `test_native_extract.py` (22) + `test_status.py` (15) + `test_config.py` (13) + `test_locking.py` (12) + `tests\fixtures\` (3 real OCR samples) | **261 tests.** The Postgres-backed ones roll back and leave nothing behind; `test_locking.py` and `test_tasks.py` need Redis. `test_ocr.py` stubs Azure, `test_indexer.py` and `test_retrieval_eval.py` stub both the embedding API and Qdrant, so everything that costs money is fully tested with no paid call and no writes to the live vector store. **`test_rls.py` is the only file that connects as `spm_app`** — every other file uses `spm_admin`, which bypasses row-level security, which is why RLS went untested until 2026-08-09 |
| `Dockerfile` / `.dockerignore` | Image for the ingestion worker | Built by `docker compose build pipeline-worker`; excludes `.venv` and `infra/.env` from the build context |
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

## 2. Docker containers (13 total, all `restart: unless-stopped`)

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

All credentials live in `infra/.env` (gitignored); variable names/purposes documented in `infra/.env.example`. Full detail: `INFRASTRUCTURE.md` §6.

---

## 3. Cloud AI models — all Azure, two separate accounts, all verified live

| Capability | Model | Account | Why this one |
|---|---|---|---|
| **LLM** | `gpt-5.4-mini`, via an Azure AI Foundry unified endpoint | Account 1 | Chat responses, structured output |
| **Embeddings** | `text-embedding-3-small` (1536-dim) | Account 2 | Text → searchable vectors |
| **OCR** | Azure AI Document Intelligence, `prebuilt-layout` model | Account 2 | Chosen over `prebuilt-read` specifically for table/structure extraction — verified live on real scanned Arabic content |

Credentials in `infra/.env` under `AZURE_OPENAI_LLM_*`, `AZURE_OPENAI_EMBEDDING_*`, `AZURE_DOCUMENT_INTELLIGENCE_*`. Full reasoning: `SOLUTION_ARCHITECTURE.md` §4.1.

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

## 6. What's deliberately *not* installed anywhere yet

- No FastAPI app and no frontend — `pipeline/` (ingestion) and `evaluation/` (measuring retrieval quality) are the only application code so far. The Celery worker **does** now exist (`mof-pipeline-worker` / `mof-pipeline-beat`), running that same pipeline code in a container
- Real Active Directory, real SharePoint connection, real OTP — external dependencies, explicitly flagged as deferred rather than faked (per the BRD's own practical note for a demo build)

---

## 7. Quick verification commands

```bash
docker ps --format "{{.Names}}: {{.Status}}"                     # 13 containers
python infra/postgres-spm/apply_migrations.py --status           # 13 applied, 0 pending
.venv/Scripts/python -m pytest tests/ -q                         # 261 passed
find /d/MOF_DataSample -maxdepth 1 -type d                       # 9 project folders

# The whole pipeline has run. Every stage is free to re-run unchanged:
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.scanner --dry-run
#   -> unchanged 58, rows written 0
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.ocr --dry-run
#   -> candidates : 0        (nothing left to OCR, nothing to re-bill)
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.indexer --dry-run
#   -> candidates : 0        (678 vectors already indexed)
```

Qdrant's dashboard (http://localhost:6333/dashboard) loads without a key
but its API returns 401, so it looks broken until you paste
`QDRANT_API_KEY` into its **API Key** box. To check it from the terminal
instead — settings supply the key, nothing is typed:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -c "from qdrant_client import QdrantClient; from pipeline.config import get_settings; s=get_settings(); q=QdrantClient(url=s.qdrant.url, api_key=s.qdrant.api_key); [print(f'{c.name:<26} {q.count(c.name).count:>4}') for c in q.get_collections().collections]"
#   -> 9 collections, 678 points
```
