# Inventory — Folders, Tools, Models, Libraries

**Last verified:** 2026-08-05

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
| `Documentation\` | This file + `SOLUTION_ARCHITECTURE.md`, `INFRASTRUCTURE.md`, `STATUS_AND_ROADMAP.md`, `NEXT_BUILD_PIPELINE.md`, `START_HERE.md` | Small text, versioned with the code it describes |
| `My_Prompts\` | `SOLUTION_SCOPE.md` (BRD-derived scope, assistant-authored) + `PRE_PIPELINE_ENHANCEMENTS.md` (user-authored) | Requirements/planning source material |
| `infra\` | `docker-compose.yml`, `.env` / `.env.example`, `postgres-spm\init\` (one-time) + `migrations\` (13 SQL files, ongoing), `apply_migrations.py`, `README.md` | All infrastructure-as-code |
| `mock-data\` | `spm_manifest.json` — the 8-project / 4-user / 45-document source data | Small structured data, worth versioning |
| `pipeline\` | `__init__.py`, `config.py` (all settings), `chunker.py` | The only application code that exists so far |
| `tests\` | `test_chunker.py` (26 tests) + `tests\fixtures\` (3 real OCR samples) | Test suite plus the real-world data it guards against |
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

## 2. Docker containers (11 total, all `restart: unless-stopped`)

| Container | Image | Purpose | Access |
|---|---|---|---|
| `mof-postgres-spm` | postgres:16-alpine | SPM data: projects, users, assignments, document links | `localhost:5432` · `docker exec -it mof-postgres-spm psql -U spm_admin -d spm_data` · or DBeaver |
| `mof-qdrant` | qdrant/qdrant | Vector store, one collection per project | http://localhost:6333/dashboard |
| `mof-redis` | redis:7-alpine | App job queue (not yet wired to code) | `localhost:6379` |
| `mof-langfuse-web` | langfuse/langfuse:3 | LLM tracing UI | http://localhost:3001 |
| `mof-langfuse-worker` | langfuse/langfuse-worker:3 | Langfuse background processing | internal |
| `mof-langfuse-postgres` | postgres:16-alpine | Langfuse's own metadata store | internal |
| `mof-langfuse-clickhouse` | clickhouse-server:24 | Langfuse's trace/analytics store | internal |
| `mof-langfuse-redis` | redis:7-alpine | Langfuse's own queue | internal |
| `mof-langfuse-minio` | minio/minio | Langfuse's blob storage | internal |
| `mof-grafana-lgtm` | grafana/otel-lgtm | App-wide metrics/logs/traces (distinct from Langfuse — infra/app observability, not LLM content) | http://localhost:3000 |
| `mof-portainer` | portainer-ce | Docker management UI | https://localhost:9443 |

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
| `pytest` | 9.1.1 *(dev only)* | Test suite |

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

- No FastAPI app, no Celery worker process, no frontend — `pipeline/` is the only application code so far
- Real Active Directory, real SharePoint connection, real OTP — external dependencies, explicitly flagged as deferred rather than faked (per the BRD's own practical note for a demo build)

---

## 7. Quick verification commands

```bash
docker ps --format "{{.Names}}: {{.Status}}"                     # 11 containers
python infra/postgres-spm/apply_migrations.py --status           # 13 applied, 0 pending
.venv/Scripts/python -m pytest tests/ -q                         # 26 passed
find /d/MOF_DataSample -maxdepth 1 -type d                       # 9 project folders
```
