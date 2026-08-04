# Infrastructure — MOF Enjaz Chatbot

**Last verified:** 2026-08-04
**Machine:** owner's own PC — Windows, RTX 3070, 32GB RAM, Docker Desktop
(Compose v5, WSL2 backend)

This is the operational reference: what runs, where, how to bring it up,
and every gotcha hit while building it. For *why* these choices were made,
see `SOLUTION_ARCHITECTURE.md`.

---

## 1. Architecture at a glance

```
Windows (Docker Desktop, WSL2 backend, engine disk relocated to D:)
└── docker compose @ "D:\MOF Enjaz Chatbot\infra"  (project: mof-enjaz-chatbot,
    network: mof-net, all services restart: unless-stopped)
    ├── postgres-spm ......... postgres:16-alpine   :5432   SPM project entity data
    ├── qdrant ............... qdrant/qdrant:latest :6333/:6334   vector DB, one
    │                          collection per project, API-key auth
    ├── redis ................ redis:7-alpine       :6379   app job queue
    │                          (SharePoint ingest / OCR / embed — not wired yet)
    │
    ├── langfuse-postgres .... postgres:16-alpine    :5433   Langfuse's own metadata DB
    ├── langfuse-clickhouse .. clickhouse-server:24  :8123, :9004   Langfuse trace store
    │                          (named Docker volume, NOT bind-mounted — see §4.1)
    ├── langfuse-redis ....... redis:7-alpine        (no host port)   Langfuse's queue
    ├── langfuse-minio ....... minio/minio:latest    :9002, :9003   Langfuse blob store
    ├── langfuse-minio-init .. minio/mc (one-shot) .. creates the bucket, exits — expected
    ├── langfuse-worker ...... langfuse/langfuse-worker:3  :3030
    ├── langfuse-web ......... langfuse/langfuse:3        :3001 (container's :3000)
    │
    ├── grafana-lgtm ......... grafana/otel-lgtm:latest  :3000 UI, :4317/:4318 OTLP
    │                          (Prometheus/Mimir + Loki + Tempo + Grafana, one container)
    └── portainer ............ portainer/portainer-ce  :9000, :9443   Docker web UI
```

**11 containers total** (`langfuse-minio-init` runs once and exits — that's
correct, not a failure).

---

## 2. The "nothing on C:" constraint, and how it's actually satisfied

Docker Desktop on Windows normally stores its entire WSL2 virtual disk (all
images, containers, and named volumes) on C:, regardless of what a compose
file's bind mounts point to. Two things were done to actually satisfy the
constraint:

1. **Docker Desktop's own engine disk was relocated**, via
   Settings → Resources → Advanced → Disk image location →
   `D:\MOF_Installations\docker-data`, then Apply & Restart. This is a GUI
   action (safe, no data loss, Docker handles the move) — not something to
   script via raw `wsl --export`/`--import`. Verified: the folder is several
   GB and growing.
2. **Every service's persistent data bind-mounts** to
   `D:\MOF_Installations\volumes\<service>\` (set via `DATA_ROOT` in
   `infra/.env`) — a plain, browsable folder per service.

**Exception:** ClickHouse uses a **named Docker volume**, not a bind mount
— see §4.1 for why. Named volumes still live on D: (inside the relocated
engine disk from step 1), just not as a visible folder under
`D:\MOF_Installations\volumes\`.

---

## 3. Folder layout

```
D:\MOF Enjaz Chatbot\infra\        <- tracked in the repo (once git-initialized)
  docker-compose.yml
  .env                              <- real secrets, GITIGNORED, never commit
  .env.example                      <- template, safe to commit
  postgres-spm\init\                <- SQL run once on first postgres-spm start
                                        (currently just extensions — real SPM
                                        schema is a separate design step)
  README.md                         <- setup + service/port/credential reference

D:\MOF_Installations\               <- NOT in the repo, all persistent data + tools
  docker-data\                      <- Docker Desktop's relocated engine disk
  volumes\
    postgres-spm\  qdrant\  redis\
    langfuse-postgres\  langfuse-redis\  langfuse-minio\
    grafana-lgtm\  portainer\
    (langfuse-clickhouse is a named volume, not here — see §2)
```

---

## 4. Known gotchas (hit and fixed while building this)

### 4.1 ClickHouse cannot use a bind-mounted Windows path

**Symptom:** container `Restarting`/`unhealthy`; logs show
`filesystem error: in rename: Permission denied` on internal MergeTree part
renames.

**Cause:** ClickHouse's storage engine needs atomic POSIX rename semantics
that the WSL2↔Windows bind-mount filesystem bridge doesn't provide.

**Fix:** give ClickHouse's data + log directories a **named Docker volume**
instead of a bind mount (`langfuse_clickhouse_data`,
`langfuse_clickhouse_logs` in `docker-compose.yml`). Named volumes live
inside Docker's own (already-relocated) engine disk — a real Linux
filesystem — so this doesn't reintroduce the C: problem.

### 4.2 ClickHouse healthcheck false-unhealthy

**Symptom:** ClickHouse is actually serving fine (`curl localhost:8123/ping`
from the host returns `Ok.`), but Docker reports it unhealthy and dependent
containers (Langfuse web/worker) refuse to start.

**Cause:** the healthcheck ran `wget http://localhost:8123/ping` *inside*
the container, where `localhost` resolves to `::1` (IPv6) first — and
ClickHouse's IPv6 listener fails to bind in this environment, so the
healthcheck gets connection-refused even though IPv4 is fine.

**Fix:** point the healthcheck at `http://127.0.0.1:8123/ping` explicitly
instead of `localhost`.

### 4.3 Portainer's first-run admin setup has a 5-minute window

**Symptom:** Portainer's initial "Create the initial administrator user"
screen shows a "the instance timed out for security purposes" error if you
don't finish before the window closes.

**Fix:** `docker restart mof-portainer` reopens a fresh 5-minute window,
and prints a new setup token to its logs:
```bash
docker logs mof-portainer 2>&1 | grep -i setup_token
```
Paste that token into the setup form's "Setup token" field alongside your
chosen username/password.

### 4.4 Docker Desktop `AutoStart` was off

**Checked via** `%APPDATA%\Docker\settings-store.json` → `"AutoStart": false`.
This is the equivalent of a problem the colleague's RiskIntel build hit
(WSL not staying alive headless) — if Docker Desktop isn't running, no
container comes back after a reboot/logout, regardless of
`restart: unless-stopped`.

**Fix (manual, app-level GUI setting — not something to hand-edit in the
live config file):** Docker Desktop → Settings → General → check "Start
Docker Desktop when you sign in". **Status: done** — confirmed
`AutoStart: true` in `settings-store.json` on 2026-08-04.

### 4.5 Git Bash path mangling when touching container paths

Commands like `docker exec <container> tail /var/log/...` run through Git
Bash's MSYS layer will mangle the leading `/var/...` into a Windows path
and fail with "No such file or directory". Prefix with
`MSYS_NO_PATHCONV=1` when running such commands from this environment's
Bash tool.

---

## 5. Bringing the stack up / health-check sweep

```bash
cd "D:\MOF Enjaz Chatbot\infra"
docker compose up -d
docker compose ps                                  # all 11 (+1 one-shot) present
```

First boot pulls a lot (Langfuse's full v3 stack + Grafana's all-in-one LGTM
image) — several minutes is normal.

```bash
# Quick per-service sanity checks
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3001    # Langfuse web  -> 200
curl -s http://localhost:6333/                                     # Qdrant        -> version JSON
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000    # Grafana LGTM  -> 200
curl -s -o /dev/null -w "%{http_code}\n" -k https://localhost:9443 # Portainer     -> 307
docker exec mof-redis redis-cli -a <REDIS_PASSWORD from .env> ping # -> PONG
```

---

## 6. Services, URLs, credentials

| Service | URL | Purpose | Credentials |
|---|---|---|---|
| Postgres (SPM) | `localhost:5432` | SPM project entity data (demo: filled directly) | `POSTGRES_SPM_USER` / `POSTGRES_SPM_PASSWORD` in `.env` |
| Qdrant | http://localhost:6333/dashboard | Vector DB, one collection per project | `QDRANT_API_KEY` in `.env` |
| Redis | `localhost:6379` | App job queue (not yet wired to app code) | `REDIS_PASSWORD` in `.env` |
| Langfuse | http://localhost:3001 | LLM execution tracing | Own account, created in-browser (unrelated to Enjaz/AD auth). API keys: `LANGFUSE_SECRET_KEY`/`LANGFUSE_PUBLIC_KEY` in `.env`, not yet consumed by any app code |
| Grafana (LGTM) | http://localhost:3000 | App workflow observability: metrics/logs/traces via OTLP on :4317/:4318 | `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` in `.env` |
| Portainer | https://localhost:9443 | Docker/container management UI | Own admin account, created in-browser (see §4.3 for first-run) |
| MinIO console | http://localhost:9003 | Langfuse's internal blob storage (debugging only) | `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` in `.env` |

All real values live in `infra/.env` (gitignored) — never duplicated here.
`infra/.env.example` documents every variable name with a blank value.

---

## 7. Reload / change rules

| You changed | What to do |
|---|---|
| `docker-compose.yml` | `docker compose up -d` (recreates only what changed) |
| `.env` | `docker compose up -d` (containers using the changed var need recreation — compose handles this automatically on `up -d`) |
| `postgres-spm/init/*.sql` | Only runs on **first** container creation. To re-apply: stop the container, delete `D:\MOF_Installations\volumes\postgres-spm\`, then `docker compose up -d postgres-spm` (destructive — wipes existing data) |
| Adding a new service | Follow the pattern in §2: bind-mount its data to `D:\MOF_Installations\volumes\<service>`, add env vars to both `.env` and `.env.example`, never write real secrets into `.env.example` |
