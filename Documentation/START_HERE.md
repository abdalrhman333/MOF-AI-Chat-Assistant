# START HERE — MOF Enjaz Chatbot, project entry point

**If you are an AI assistant (or a human) picking this project up, read this
file first.** It tells you what this project is, which documents to read in
what order, and how to verify the real state of the system before changing
anything.

**Last verified:** 2026-08-04 (infra + SPM `projects` table + full mock
corpus in place; no application code yet)

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
| 4 | `STATUS_AND_ROADMAP.md` | What's actually done vs not, and the immediate next steps |

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
docker compose ps                          # 11 containers, all Up/healthy
docker ps --format "table {{.Names}}\t{{.Status}}"
```

Expected containers (project name `mof-enjaz-chatbot`, network `mof-net`):
`mof-postgres-spm`, `mof-qdrant`, `mof-redis`, `mof-langfuse-postgres`,
`mof-langfuse-clickhouse`, `mof-langfuse-redis`, `mof-langfuse-minio`,
`mof-langfuse-worker`, `mof-langfuse-web`, `mof-grafana-lgtm`,
`mof-portainer`. (`mof-langfuse-minio-init` runs once and exits — that's
expected, not a failure.)

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

**No application code exists yet** — only infrastructure, the SPM
`projects` table, and mock data. Do not look for `app.py`/`tasks.py`
equivalents; they haven't been built. See `STATUS_AND_ROADMAP.md` for the
exact done/not-done line.

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

---

## 5. Starter prompt for a new AI session

> I'm continuing work on the MOF Enjaz Chatbot (AI assistant for منصة إنجاز),
> at `D:\MOF Enjaz Chatbot`. Before doing anything, read
> `Documentation\START_HERE.md`, then `Documentation\SOLUTION_ARCHITECTURE.md`
> and `Documentation\INFRASTRUCTURE.md`. Then verify the live system
> (`docker compose ps` from `infra\`) rather than trusting the docs blindly.
>
> My request: <what you want>
