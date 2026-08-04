# MOF Enjaz Chatbot — Infrastructure

Local, on-prem-style infra for the Enjaz AI assistant demo, running entirely
through Docker Desktop. Everything here is data/tooling only — no application
code lives in this folder.

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

## Folder layout

```
D:\MOF Enjaz Chatbot\infra\        <- this folder, tracked in git
  docker-compose.yml
  .env                              <- real secrets, gitignored, never commit
  .env.example                      <- template, safe to commit
  postgres-spm\init\                <- SQL run once on first postgres-spm start

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
| Qdrant | http://localhost:6333/dashboard | Vector DB, one index per project | API key: `QDRANT_API_KEY` in `.env` |
| Redis | `localhost:6379` | App job queue — SharePoint ingest, OCR, embedding, scheduled scans (not yet wired to app code) | `REDIS_PASSWORD` in `.env` |
| Langfuse | http://localhost:3001 | LLM execution tracing (prompts/completions/tokens/cost) | Sign up on first visit (no AD/SSO wired up yet — this is Langfuse's own login, unrelated to Enjaz auth) |
| Grafana (LGTM) | http://localhost:3000 | App workflow observability: metrics (Prometheus/Mimir), logs (Loki), traces (Tempo) — fed via OTLP on ports 4317/4318 | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` in `.env` |
| Portainer | https://localhost:9443 | Docker/container management UI | Set admin password on first visit |
| MinIO console | http://localhost:9003 | Langfuse's internal blob storage (debugging only, not app-facing) | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` in `.env` |

Grafana and Langfuse serve two different jobs, deliberately kept separate:
Langfuse holds the actual prompts/completions/retrieved-context/token-counts
for each LLM call; Grafana (LGTM) holds cross-service timing, logs, and
traces so we can see which step of a query (language-detect → intent →
RAG/SPM/CAG → response) was slow or failed. The app's workflow engine will
need to emit OpenTelemetry traces/logs to `grafana-lgtm:4317` (OTLP gRPC) to
make that useful — not wired up yet, this is infra only.

## Notes / things still open

- No application container yet — this is infra-only. The chatbot backend/
  frontend get their own service(s) added to this compose file later.
- Cloud LLM/OCR/embedding provider keys are stubbed out at the bottom of
  `.env` / `.env.example` — nothing reads them yet, fill in once providers
  are chosen.
- Langfuse needs its API keys generated through its own UI after first
  login (Settings → API Keys inside a Langfuse project) — the app will need
  those once it starts sending traces.
- `postgres-spm/init/001_extensions.sql` is a placeholder — the real SPM
  entity schema (projects, users, assignments, document links) is a
  separate design step, not infra.
