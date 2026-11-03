# MOF Enjaz Chatbot — Infrastructure

Local, on-prem-style infra for the Enjaz AI assistant demo, running entirely
through Docker Desktop. No application code lives in *this folder* — but the
compose file runs the application too: the ingestion pipeline
(`pipeline-worker` / `pipeline-beat`) and the chat app (`chat-api`), all
built from the repository root's `Dockerfile` (targets `pipeline` and `app`).
**14 containers** in all.

⚠️ **Give Docker 8 GB.** Set `memory=8GB` in `%USERPROFILE%\.wslconfig`. On
4 GB the VM restarts itself every few minutes while containers still report
"Up" — see `../Documentation/INFRASTRUCTURE.md` §4.4d.

## Before the first `docker compose up` — move Docker's disk off C:

Docker Desktop on Windows stores all image layers, containers, and named
volumes inside a WSL2 virtual disk that defaults to `C:`. Bind-mounting app
data to `D:\MOF_Installations` (which this compose file does) is not enough
on its own — Docker's internal store still needs to move. Do this once,
through Docker Desktop's own UI (safe, no manual WSL surgery):

1. Open **Docker Desktop → Settings → Resources → Advanced**.
2. Under **Disk image location**, click **Browse** and choose
   `D:\MOF_Installations\docker-data`.
3. Click **Apply & Restart**. Docker Desktop moves the existing image itself
   — no data loss, no manual `wsl --export`/`--import` needed.
4. Confirm afterward: `docker info` should show `Docker Root Dir` under that
   path (run from WSL) — or just check that
   `D:\MOF_Installations\docker-data` has grown and `C:\...\DockerDesktop`
   hasn't.

This is a one-time machine-level setting, done through the app's own GUI —
not something to script blindly, since it restarts the Docker engine.

⚠️ **Only on a fresh install.** That control *moves* the current disk. If a
Docker Desktop update has later reset the location to C: (it happened on
2026-09-13, presenting an empty engine), do **not** use the GUI — it would
move the empty disk over the real one. Fix `settings-store.json` with Docker
quit instead: `../Documentation/INFRASTRUCTURE.md` §4.4b.

## Folder layout

```
D:\MOF Enjaz Chatbot\infra\        <- this folder, tracked in git
  docker-compose.yml
  .env                              <- real secrets, gitignored, never commit
  .env.example                      <- template, safe to commit
  postgres-spm\init\                <- SQL run once on first postgres-spm start
                                       (extensions only)
  postgres-spm\migrations\          <- ongoing, additive schema (001-020),
                                       all applied. NEVER apply by hand
  postgres-spm\apply_migrations.py  <- the migration runner: one transaction
                                       per file, records only on success,
                                       re-verifies checksums to catch a
                                       migration edited after being applied.
                                       Needs no third-party packages, so the
                                       database can be rebuilt before the
                                       project venv exists

D:\MOF_Installations\               <- NOT in git, all persistent data + tools
  docker-data\                      <- Docker Desktop's relocated WSL2 disk
  volumes\
    postgres-spm\
    qdrant\
    langfuse-postgres\
    langfuse-clickhouse\
    langfuse-redis\
    langfuse-minio\
    grafana-lgtm\
    portainer\
    pipeline-beat\               <- beat's schedule state, so a restart doesn't
                                    lose track of what last ran
```

## Bringing the stack up

```bash
cd "infra"
docker compose up -d
```

First boot pulls several images (Langfuse v3's stack in particular —
Postgres, ClickHouse, Redis, MinIO, web, worker — plus Grafana's all-in-one
LGTM image) so it can take a few minutes. `langfuse-minio-init` runs once to
create the MinIO bucket Langfuse needs, then exits — that's expected, it's
not supposed to stay running.

## Services

| Service | URL | Purpose | Credentials |
|---|---|---|---|
| Postgres (SPM) | `localhost:5432` | SPM project entity data (demo: filled directly) | `POSTGRES_SPM_USER` / `POSTGRES_SPM_PASSWORD` in `.env` |
| Qdrant | http://localhost:6333/dashboard | Vector DB, one collection per project — **9 collections, 678 points** | API key: `QDRANT_API_KEY` in `.env`. ⚠️ The dashboard page loads *without* a key while its API returns 401, so it looks broken rather than locked (`Qdrant v???`, empty collections). Click **API Key**, paste, **Apply** |
| Redis | `localhost:6379` | **Celery broker + result backend** for the ingestion worker, and the store behind the cross-process stage locks | `REDIS_PASSWORD` in `.env` |
| Pipeline worker | — (no HTTP) | **The ingestion pipeline as a background job.** Corpus mounted read-only at `/data` | `docker compose exec pipeline-worker python -m pipeline.tasks ingest --wait` |
| Pipeline beat | — (no HTTP) | Fires the scheduled full ingest — **daily at `INGEST_SCHEDULE_HOUR`:`_MINUTE` in `INGEST_SCHEDULE_TIMEZONE`** (13:00 Asia/Amman). The timezone matters: containers run on UTC | `docker logs mof-pipeline-beat` |
| **Chat app** | **http://localhost:8000** | **The assistant** — API and frontend. Bound to this machine only. Rebuild after code changes: `docker compose up -d --build chat-api` | Demo sign-in: any seeded address with password `Enjaz@2026` (see `INFRASTRUCTURE.md` §6) |
| Langfuse | http://localhost:3001 | LLM tracing — every chat turn, with each step's input and output | Langfuse's own account, created in-browser (unrelated to Enjaz sign-in); project API keys in `.env` |
| Grafana (LGTM) | http://localhost:3000 | App workflow observability: metrics (Prometheus/Mimir), logs (Loki), traces (Tempo) — fed via OTLP on ports 4317/4318 | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` in `.env` |
| Portainer | https://localhost:9443 | Docker/container management UI | Set admin password on first visit |
| MinIO console | http://localhost:9003 | Langfuse's internal blob storage (debugging only, not app-facing) | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` in `.env` |

Grafana and Langfuse serve two different jobs: Langfuse shows what the
model was asked and answered; Grafana shows where the time went across a
request. **Both are wired up** — the chat app sends one set of
OpenTelemetry spans to both (`grafana-lgtm:4318` and Langfuse's OTLP
endpoint). ⚠️ With `LANGFUSE_CAPTURE_CONTENT=true` **both** receive project
content, so anyone who can sign in to either can read every project's
passages (`INFRASTRUCTURE.md` §6).

## The database schema

Applied through the runner, never by hand — it is the only thing keeping
`schema_migrations` in step with reality:

```bash
python infra/postgres-spm/apply_migrations.py --status   # read-only preview
python infra/postgres-spm/apply_migrations.py            # apply anything pending
```

Expect `20 applied, 0 pending, 0 drifted`. Full detail, including the
line-ending gotcha that made raw-byte checksums unreliable on Windows, is
in `../Documentation/INFRASTRUCTURE.md` §7b.

**Two roles, deliberately** (§7c): `spm_admin` owns the tables and bypasses
row-level security — the ingestion pipeline uses it, because it genuinely
needs cross-project reach. `spm_app` is the chat application's role:
`SELECT`-only and constrained by RLS to one project per transaction, which
**must** be set with `SET LOCAL`, never plain `SET`. A plain `SET` persists
on a pooled connection and leaks that project scope into the next request —
the isolation mechanism becoming the leak.

## Notes / things still open

- **Application code runs in this stack** — `pipeline-worker`,
  `pipeline-beat` and `chat-api`. Pipeline stages can still be run by hand
  from the host (`python -m pipeline.scanner` etc.) and take the same Redis
  locks, so a manual run and the schedule cannot collide.
- **Rebuild after changing code** — containers run their image, not the
  repository: `docker compose up -d --build chat-api` for the app,
  `docker compose up -d --build pipeline-worker pipeline-beat` for the
  pipeline.
- **Stop Docker with `docker desktop stop`**, not by killing it: a force-quit
  leaves socket files that crash the next start (`INFRASTRUCTURE.md` §4.4c).
- **Docker Desktop auto-start is currently off** — after a reboot, open
  Docker Desktop and everything, the app included, comes back.
- `postgres-spm/init/001_extensions.sql` only ever runs on a fresh volume,
  and only creates extensions. Everything else lives in `migrations/`.

## Corrections to earlier versions of this file

Two statements here were wrong and are worth naming rather than silently
deleting, since they described the state of the project rather than a
detail:

- *"Cloud LLM/OCR/embedding provider keys are stubbed out — nothing reads
  them yet, fill in once providers are chosen."* Providers **are** chosen
  (all Azure), the keys are real, and `pipeline/ocr.py` reads the Document
  Intelligence ones on every run. `.env` also now carries the pipeline's own
  settings — `DOCUMENT_ROOT`, `SCANNER_*`, `OCR_*`, `CHUNK_*` — all
  documented in `.env.example`.
- *"`init/001_extensions.sql` is a placeholder — the real SPM entity schema
  is a separate design step."* That schema exists: nine tables across 20
  applied migrations, seeded, with row-level security enabled.
- *"No chatbot backend or frontend container yet"* and *"Nothing sends
  traces yet."* Both were true on 2026-08-08. The chat app has run as
  `chat-api` since 2026-09-14, and tracing has reached Grafana and Langfuse
  since 2026-08-16.
