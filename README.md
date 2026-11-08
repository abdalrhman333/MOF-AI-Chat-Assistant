# MOF Enjaz Chatbot

An AI assistant for **منصة إنجاز** (Enjaz Platform), Ministry of Finance,
Saudi Arabia. Project managers ask questions in Arabic or English about
**one** project they are assigned to, and get answers grounded in that
project's indexed documents, its live SPM record and its four registers —
always cited, never guessed.

**Absolute per-project isolation (AC09) is the requirement everything else
serves.** A session is locked to one project; nothing else is reachable
from it, by construction rather than by application logic.

> **Status:** complete and running as a demo build on the owner's own PC.
> Sign-in is *simulated* and SharePoint is *mocked*, because real Active
> Directory, OTP and a live SPM API are external dependencies that do not
> exist here. Those deferrals are deliberate and documented, not
> oversights.

---

## New here? Read [`Documentation/START_HERE.md`](Documentation/START_HERE.md)

It is the entry point: what this is, which document to read in what order,
and — more importantly — **how to verify the real state of the system
rather than trusting any document, including this one.**

---

## What it does

| | |
|---|---|
| **Languages** | Arabic, English, and mixed. The model detects the language and answers in it |
| **Grounding** | Retrieval over the project's own documents + its live SPM record + its risk, issue, change-request and deliverable registers |
| **Citations** | Every answer names its sources. No source, no claim |
| **Isolation** | One project per session. Separate Qdrant collection per project *and* Postgres row-level security |
| **Refusals** | Out of scope, no data changes, no guessing — each limit stated to the user is one the system actually enforces |

## How it is built

```
Browser ── FastAPI (chat-api) ── Postgres (SPM data, RLS)
                 │               Qdrant   (vectors, one collection per project)
                 │               Redis    (conversations, locks, Celery)
                 └── Azure OpenAI (answers + embeddings)

Celery worker + beat ── daily 13:00 Asia/Amman ── discover → OCR → chunk → embed → index
```

Three Python packages, each with one job **and its own database
privileges** — the one that faces users has the least access:

| Package | Job | Database role |
|---|---|---|
| `pipeline/` | Builds the knowledge base | `spm_admin` — needs every project |
| `evaluation/` | Measures retrieval and routing quality | read-only |
| `Application/` | Serves the assistant | `spm_app` — SELECT-only, one project per transaction |
| `observability/` | One OpenTelemetry instrumentation → Grafana *and* Langfuse | none |

## Running it

Requires Docker Desktop and Python 3.13.

```bash
cd infra && docker compose up -d
```

```bash
.venv/Scripts/python -m pytest tests/ -q
```

Then open <http://localhost:8000>.

**On a machine that holds only a clone**, run the bootstrap instead — it
checks the prerequisites and the four things git deliberately does not
carry, then brings everything up:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -CheckOnly
```

⚠️ A container runs the code baked into its image. After changing
application code: `docker compose up -d --build chat-api`.

## What a clone does not include

Four things, by design. [`Documentation/MIGRATION.md`](Documentation/MIGRATION.md)
explains how to move each one.

- **`infra/.env`** — every password and key. Start from `infra/.env.example`
- **The document corpus** — 58 files. ⚠️ 13 of them are **real** client and
  ministry documents and must not be committed to any remote
- **DIN Next LT Arabic** — a licensed Monotype font. The UI falls back to a
  system face without it
- **The OCR cache** — rebuilding it re-bills Azure Document Intelligence
  (~$1.50 for the corpus)

## Documentation

| Document | For |
|---|---|
| [START_HERE](Documentation/START_HERE.md) | **Read first.** Orientation and ground-truth verification |
| [SOLUTION_ARCHITECTURE](Documentation/SOLUTION_ARCHITECTURE.md) | Scope, the retrieval architecture, and every non-obvious decision with its reasoning |
| [INFRASTRUCTURE](Documentation/INFRASTRUCTURE.md) | The Compose stack: services, ports, verified gotchas |
| [STATUS_AND_ROADMAP](Documentation/STATUS_AND_ROADMAP.md) | What is done, what is not, the decisions log, and what each session learned |
| [MIGRATION](Documentation/MIGRATION.md) | Moving the whole solution to another device |
| [INVENTORY](Documentation/INVENTORY.md) | Every folder, container, model and library, and why it is there |
| [SPM_DATA_MODEL_ERD](Documentation/SPM_DATA_MODEL_ERD.md) | The data model against the official SPM schema v0.3 (sheet UC3) |
| [SYSTEM_WORKFLOW.html](Documentation/SYSTEM_WORKFLOW.html) | Eight diagrams of what is built, in plain language |
| [Enjaz_Solution_Guide.docx](Documentation/Enjaz_Solution_Guide.docx) | For non-technical readers and stakeholders |
| [AI_SOLUTION_PLAYBOOK](Documentation/AI_SOLUTION_PLAYBOOK.md) | A generic template for colleagues starting a different AI use case |

## Testing

```bash
.venv/Scripts/python -m pytest tests/ -q
```

That one command covers the frontend too — `test_frontend.py` shells out to
`node --test`, and **skips rather than fails** where node is absent.

Everything that costs money is stubbed, so the full ingestion and answering
paths are tested without a single paid call. Changes are checked by
**mutation testing**: deliberately break the new logic and confirm a test
fails. Survivors are reported rather than quietly dropped.

## Security notes

- `Application/backend/session.py` is the AC09 gate; `api/identity.py`,
  `credentials.py` and `cookies.py` establish *who* the caller is. Read
  `Application/README.md` before touching anything user-facing
- **`API_SESSION_SECRET` has no default.** A hard-coded fallback would let
  anyone with the source mint a session for any username, so sign-in
  refuses without it
- **Row-level security is granted per table.** A new table without a policy
  is readable across every project and nothing warns. `tests/test_rls.py`
  asks the catalog rather than a fixed list — keep it that way
- **No credential belongs in a migration.** Migrations reference
  `${VAR}` from `infra/.env`, substituted as a quoted SQL literal at apply
  time; a test fails on any literal password
- A conversation id is a handle, not a credential — every lookup is
  owner-checked and re-runs the gate

## Deliberately not built

Real AD/SSO/OTP, a live SharePoint connection, and mid-chat document
upload. Each is recorded as a decision in `STATUS_AND_ROADMAP.md` rather
than faked.

---

Demo/development build. Not a production deployment, and not an official
Ministry of Finance release.
