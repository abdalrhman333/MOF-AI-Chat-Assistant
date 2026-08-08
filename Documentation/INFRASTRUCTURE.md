# Infrastructure — MOF Enjaz Chatbot

**Last verified:** 2026-08-08
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
D:\MOF Enjaz Chatbot\infra\        <- tracked in the repo
  docker-compose.yml
  .env                              <- real secrets, GITIGNORED, never commit
  .env.example                      <- template, safe to commit
  postgres-spm\init\                <- SQL run ONCE on first container creation
                                        (extensions only)
  postgres-spm\migrations\          <- ongoing, additive SPM schema (001-013).
                                        Apply with the runner, never by hand
                                        — see §7b.
  postgres-spm\apply_migrations.py  <- the migration runner
  README.md                         <- setup + service/port/credential reference

D:\MOF Enjaz Chatbot\
  .venv\                            <- project virtualenv, GITIGNORED.
                                       Never install into system Python.
  requirements.txt                  <- pinned runtime deps
  requirements-dev.txt              <- + pytest
  pipeline\                         <- application code
    __init__.py
    config.py                       <- ALL settings, loaded from infra\.env.
                                       No module reads os.environ directly
                                       or hardcodes a path/deployment name.
    chunker.py                      <- markdown -> retrieval chunks
    db.py                           <- the spm_admin connection, and only
                                       that one. A pipeline module wanting
                                       spm_app is doing something wrong.
    scanner.py                      <- Stage 0: walk DOCUMENT_ROOT, hash,
                                       upsert document_links. Run with
                                       `python -m pipeline.scanner
                                        [--project CODE] [--dry-run] [--list]`
    ocr.py                          <- Stage 1: Document Intelligence ->
                                       document_content. Run with
                                       `python -m pipeline.ocr
                                        [--project CODE] [--dry-run]
                                        [--limit N] [--retry-failed]`
    native_extract.py               <- fallback ONLY, for Office files DI
                                       rejects. Never the primary path,
                                       and never used for PDFs — see
                                       SOLUTION_ARCHITECTURE.md 4.9

D:\MOF_DataSample\                  <- NOT in the repo, the mock document corpus
  PRJ-01\ ... PRJ-08\                  (fictional, generated from
                                        mock-data\spm_manifest.json)
  TEST-PROJECT\                        (13 real sample documents)

D:\MOF_Installations\               <- NOT in the repo, all persistent data + tools
  docker-data\                      <- Docker Desktop's relocated engine disk
  tools\dbeaver-ce-portable\        <- DBeaver CE, portable (no installer),
                                        for visually browsing spm_data
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

### 4.5a Python crashes printing Arabic to the console

**Symptom:** `UnicodeEncodeError: 'charmap' codec can't encode characters`
when a script prints Arabic — even though the data itself is fine.

**Cause:** Windows consoles default to cp1252, which has no Arabic. It's a
*display* failure, not a data failure, and it will crash a script that was
otherwise working.

**Fix:** prefix the command with `PYTHONIOENCODING=utf-8`:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/ -q
PYTHONIOENCODING=utf-8 .venv/Scripts/python some_script.py
```

Writing to a file with `encoding="utf-8"` is unaffected — only stdout is.
Expect to need this constantly, since most of the corpus is Arabic.

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

## 6. Services, URLs, credentials — how to actually get into each one

All real credential values live in `infra/.env` (gitignored) — never
duplicated here. `infra/.env.example` documents every variable name with
a blank value.

| Service | URL | Purpose | Credentials (var name in `.env`) |
|---|---|---|---|
| Postgres (SPM) | `localhost:5432` | SPM data — `projects`, `users`, `project_assignments`, `document_links` (see §8) | `POSTGRES_SPM_USER` / `POSTGRES_SPM_PASSWORD`, db `POSTGRES_SPM_DB` |
| Qdrant | http://localhost:6333/dashboard | Vector DB, one collection per project | `QDRANT_API_KEY` |
| Redis | `localhost:6379` | App job queue (not yet wired to app code) | `REDIS_PASSWORD` |
| Langfuse | http://localhost:3001 | LLM execution tracing | Own account, created in-browser (unrelated to Enjaz/AD auth). API keys: `LANGFUSE_SECRET_KEY`/`LANGFUSE_PUBLIC_KEY`, not yet consumed by any app code |
| Grafana (LGTM) | http://localhost:3000 | App workflow observability: metrics/logs/traces via OTLP on :4317/:4318 | `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` |
| Portainer | https://localhost:9443 | Docker/container management UI | Own admin account, created in-browser (see §4.3 if the 5-min setup window expires) |
| MinIO console | http://localhost:9003 | Langfuse's internal blob storage (debugging only) | `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` |

**Postgres (SPM) — no browser access, it's not a webpage.** Three ways in:

1. **Ask the assistant** — has Docker access, can run any query directly.
2. **`psql` via Docker, no local install**:
   ```bash
   docker exec -it mof-postgres-spm psql -U spm_admin -d spm_data
   ```
   Then `\dt` (list tables), `\d <table>` (schema), `\q` (exit).
3. **DBeaver** (portable, installed at
   `D:\MOF_Installations\tools\dbeaver-ce-portable\dbeaver\dbeaver.exe` —
   see §3): launch it, **Database → New Database Connection → PostgreSQL**,
   host `localhost`, port `5432`, database `spm_data`, username
   `spm_admin`, password from `.env`. Let it download the PostgreSQL JDBC
   driver on first connect (its own small internal driver cache, not a
   separate install). Gives a full visual table browser + SQL editor.

**Qdrant, Grafana, Langfuse, MinIO console** — all plain web UIs, just open
the URL in a browser.

**Portainer** — first visit needs an admin account created within a
5-minute window (§4.3); if you miss it, `docker restart mof-portainer`
reopens the window.

**Redis** — no UI; check it's alive with
`docker exec mof-redis redis-cli -a <REDIS_PASSWORD from .env> ping` (→
`PONG`), or ask the assistant.

---

## 7. Reload / change rules

| You changed | What to do |
|---|---|
| `docker-compose.yml` | `docker compose up -d` (recreates only what changed) |
| `.env` | `docker compose up -d` (containers using the changed var need recreation — compose handles this automatically on `up -d`) |
| `postgres-spm/init/*.sql` | Only runs on **first** container creation. To re-apply: stop the container, delete `D:\MOF_Installations\volumes\postgres-spm\`, then `docker compose up -d postgres-spm` (destructive — wipes existing data) |
| `postgres-spm/migrations/*.sql` | Ongoing, additive schema changes. **Add the file, then run the migration runner** (below) — never apply by hand. Numbered sequentially; each file should still be written idempotently (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`). This is the "raw SQL migrations" pattern from `SOLUTION_ARCHITECTURE.md` §4.5 — distinct from `init/`, which only ever runs once on a fresh volume. |
| Adding a new service | Follow the pattern in §2: bind-mount its data to `D:\MOF_Installations\volumes\<service>`, add env vars to both `.env` and `.env.example`, never write real secrets into `.env.example` |

---

## 7b. The migration runner

Never apply migrations by hand — use the runner. It's the only thing that
keeps `schema_migrations` (the record of what's actually been applied) in
sync with reality.

```bash
python infra/postgres-spm/apply_migrations.py --status   # preview, changes nothing
python infra/postgres-spm/apply_migrations.py            # apply anything pending
```

Rebuilding the database from empty is then one command instead of twelve.

**What it does for you:**

| Guarantee | How |
|---|---|
| A migration never half-applies | Each file runs in a **single transaction** — `psql` otherwise autocommits per statement, so a file failing on statement 3 would leave 1–2 applied |
| No false "applied" records | A migration is recorded **only after** it succeeds |
| Edited-after-applying is caught | Each file's **SHA-256 is stored and re-verified** every run. Drift stops the run with an explanation |
| Clear failure when Docker is down | Pre-flight container check, rather than a cryptic docker error |
| Arabic in seed files survives | SQL is piped to `psql` as raw **bytes**, sidestepping the shell codepage (see §4.5) |

**On drift:** if you edit an already-applied migration, the run refuses —
the database no longer matches the file that supposedly produced it. Review
the edit; if it was genuinely cosmetic, `--allow-drift` refreshes the
recorded checksums.

⚠️ **Checksums are computed on content with line endings normalised to LF,
not on raw bytes.** This matters: `core.autocrlf=true` on Windows rewrites
line endings on checkout, so raw-byte hashing would flag every file as
drifted on a fresh clone. `.gitattributes` pins `*.sql` to LF as a second
defence — both were needed, and the LF normalisation is the one that
actually holds.

---

## 7c. Row-level security — the SQL isolation boundary

Two database roles, deliberately:

| Role | Used by | Access |
|---|---|---|
| `spm_admin` | **Ingestion pipeline** | Owner — full access, **bypasses RLS** (it legitimately needs cross-project reach to process every document) |
| `spm_app` | **Chat application** | `SELECT` only, **RLS-constrained to one project** |

`spm_app` is `SELECT`-only because the BRD states the assistant performs no
write operations against any system — that constraint is now enforced by
the database rather than by convention (verified: `UPDATE` and `DELETE`
both return `permission denied`).

### ⚠️ The app MUST use `SET LOCAL`, never `SET`

```sql
BEGIN;
SET LOCAL app.current_project  = 'PRJ-03';
SET LOCAL app.current_username = 'noura.alharbi';
-- ... queries ...
COMMIT;   -- scope resets automatically
```

**A plain `SET` persists on the connection after the request ends.** With a
connection pool, the next request to borrow that connection inherits the
previous project's scope — a cross-project leak *created by the isolation
mechanism itself*. `SET LOCAL` is scoped to the transaction and resets on
commit or rollback (verified: 5 rows visible inside the transaction, 0
after commit).

### What this buys

A forgotten `WHERE` clause stops being a breach:

```sql
-- as spm_app, scoped to PRJ-01
SELECT * FROM document_links;   -- 5 rows, all PRJ-01 (not 58 across 9 projects)
SELECT * FROM document_links WHERE project_code = 'PRJ-03';   -- 0 rows
```

**Fail-closed by construction:** `current_setting(..., true)` returns `NULL`
when unset, and `code = NULL` is never true — so a session that forgets to
scope itself sees **nothing**, rather than everything.

### What RLS does *not* solve

If the session is scoped to the wrong project, RLS faithfully serves that
wrong project. Validating the requested project against
`project_assignments` before opening the session remains application code —
but that reduces the places correctness matters from *every query ever
written* to *one gate that can be reviewed and tested once*.

---

## 8. SPM database schema (as of migrations 001–013)

| Table | Purpose | Key columns |
|---|---|---|
| `projects` | Project entity data | `code` (PK), `name_ar`/`name_en`, `status`, `start_date`, `planned_end_date`, `budget_sar`, `sponsoring_department_ar`/`_en` |
| `users` | Mock AD accounts | `username` (PK), `full_name_ar`/`_en`, `email` |
| `project_assignments` | **The access-control boundary** — a project with no row here is invisible to every user, by construction, not application logic (BRD AC09) | `username`, `project_code` (composite PK, both FKs) |
| `document_links` | Structured project↔document relationship (AC11), source for the knowledge-base page's type + indexing-status columns (AC10) | `project_code` (FK), `filename`, `file_path`, `doc_type` (**free text, not an enum** — real documents don't fit a closed category set), `language`, `contains_table`, `indexing_status` (`pending`/`indexing`/`indexed`/`failed`). Pipeline diagnostics: `error_message` (why it failed; cleared on later success), `indexed_at` (last successful index — lets AC10 show "indexed 3 days ago", not just "indexed"), `chunk_count` (sanity check — a 20-page doc yielding 1 chunk means the chunker broke). Change detection: `file_hash` (SHA-256; `NULL` = never scanned, treated as new) + `file_size_bytes` (cheap `stat`-based pre-check before hashing) — lets the scanner tell new/unchanged/modified apart so unchanged files are skipped free and edited ones are re-indexed |
| `document_content` | OCR output cache, 1:1 with `document_links`. Exists so a chunking-strategy change never forces a re-OCR (which costs money per page). **The presence of a row here is the "OCR done" marker** — deliberately avoids a separate `ocr_status` column, so a failed embed/index step never re-pays for OCR | `document_link_id` (PK + FK, cascade), `markdown` (feeds chunking), `raw_response` (JSONB — full DI response, keeps structured tables/geometry that markdown drops), `page_count`, `ocr_model`, `ocr_api_version`, `extracted_at` |

Current data (2026-08-08): 9 projects, 4 users, 6 assignments, **58
document links, all 58 carrying a real SHA-256 and a real `file_path`**
(Stage 0 has run, so the seeded placeholders are gone), and **58
`document_content` rows — the full corpus is OCR'd** (Stage 1). All 58 are
`indexing_status='pending'`, waiting for Stage 2/3 to index them. Query it
via `docker exec -it mof-postgres-spm psql -U spm_admin -d spm_data`, or
visually via DBeaver (§3) — see `STATUS_AND_ROADMAP.md` §7 for exactly
what's in `TEST-PROJECT`.

`document_content` records **155 pages** of extraction in **3.3 MB**, of
which **154 were billed** — the one fallback-extracted document counts as
a page but cost nothing. The raw
Document Intelligence JSON is ~9.3 MB, and Postgres TOAST-compresses it.
Which extractor produced each row is recorded:

```sql
SELECT ocr_model, count(*) FROM document_content GROUP BY 1;
--  prebuilt-layout    | 57
--  native:python-docx |  1   <- DI rejected this file; see SOLUTION_ARCHITECTURE.md 4.9
```

⚠️ **Verifying `file_path` with `LIKE` will lie to you.** `\` is `LIKE`'s
escape character, so `file_path LIKE 'D:\MOF_DataSample\%'` matches
**nothing** and looks exactly like a data bug. Use
`starts_with(file_path, 'D:\MOF_DataSample\')` instead.
