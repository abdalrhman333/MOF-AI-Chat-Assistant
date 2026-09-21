# AI Solution Template

A standard way to build a document-grounded AI assistant: which tools to use,
how to structure the solution, and what order to build it in.

Follow it as a starting point. Change anything you have a reason to change.

---

## 1. The stack

| Layer | Use | Why |
|---|---|---|
| Backend language | **Python 3.13** | Every AI SDK lives here |
| Web framework | **FastAPI** + **uvicorn** | Async, typed, free API docs at `/docs` |
| Database | **PostgreSQL 16** | Row-Level Security for multi-user data |
| DB driver | **psycopg 3** + `psycopg_pool` | Raw SQL; keep the security rules visible |
| Vector store | **Qdrant** | One collection per tenant is built in |
| Cache / queue | **Redis 7** | Job queue, sessions, locks |
| Background jobs | **Celery** (worker + beat) | Scheduled processing |
| LLM | **Azure OpenAI** (a small model) | Start cheap, upgrade only if measured |
| Embeddings | **Azure `text-embedding-3-S`** | 1536 dimensions |
| Document extraction | **Azure Document Intelligence** (`prebuilt-layout`) | Arabic, scans, tables → markdown |
| Token counting | **tiktoken** | Budget the prompt before sending |
| Tracing | **OpenTelemetry** → **Langfuse** + **Grafana** | Prompts and cost / timing |
| Frontend | **Plain HTML + CSS + JavaScript** | Add a framework only past ~15 screens |
| Containers | **Docker Compose** | Everything, including your own app |
| Tests | **pytest** (+ `node --test` for UI) | |

**Skip these unless you have a specific need:**

| Skip | Reason |
|---|---|
| LangChain / LlamaIndex | You need to control the exact prompt sent to the model |
| An ORM | Hides the SQL that enforces your access rules |
| A frontend framework | Not worth a build step for a few screens |
| A local/self-hosted model | Only if data residency requires it |
| A reranker | Measure first; often not needed |

---

## 2. Solution structure

Four packages, split by job. Each gets its own database privileges.

```
pipeline/        Builds the knowledge base      DB role: admin (full access)
evaluation/      Measures quality               DB role: read-only
application/     Serves users                   DB role: SELECT only,
                                                one tenant per transaction
observability/   Tracing for all of them        No DB access
```

**The part facing users gets the least access.** Make that a database role,
not a code convention.

Repository layout:

```
project/
  infra/
    docker-compose.yml
    .env                    secrets, never committed
    .env.example            template, committed
    postgres/migrations/    numbered SQL files
  pipeline/
    config.py               the only place that reads the environment
    scanner.py              find documents
    extract.py              OCR / text extraction
    chunker.py              split into passages
    indexer.py              embed + store
    tasks.py                scheduled jobs
  application/
    backend/
      api/                  FastAPI routes, sign-in
      session.py            the access gate
      retrieval.py          vector search
      answer.py             the system prompt
      db.py                 connection pool
    frontend/index.html
  evaluation/
    questions.json          tagged test questions
    retrieval_eval.py       scoring
    baseline.json           recorded results
  observability/
  tests/
  requirements.txt
```

---

## 3. How it works

**Ingestion — runs on a schedule, builds the knowledge base:**

```
Documents → scan → extract (OCR) → chunk → embed → store in vector DB
```

**Answering — runs per question:**

```
Question → embed → search (this tenant only) ─┐
                                              ├→ one LLM call → answer + citations
           live database record ──────────────┘
```

Two rules that shape the prompt:

- Keep retrieved passages and structured database records in **separate
  labelled sections**, never merged. That is what lets the model say which
  fact came from which source.
- Have the model reference sources **by number**, and rebuild the citations
  on the server. Never let it write a filename — then a fake citation is
  impossible, not just unlikely.

---

## 4. Build order

**Foundation**

1. Write the one requirement everything else serves (e.g. *users only ever see
   their own data*). Judge later decisions against it.
2. List what you will **not** build, and why. Keep it visible.
3. Docker Compose for every dependency. Data on a known disk path.
4. Database schema as numbered migration files, from the first table.
5. If multi-tenant: create the restricted role and Row-Level Security **now**.
   Retrofitting a security boundary is much harder.
6. One settings module that is the only thing reading environment variables.

**Ingestion**

7. Extract documents → **cache the extracted text in your database** →
   chunk → embed → index. Extraction is what costs money; embeddings are
   nearly free, so cache extraction and let a cached row mean "done".
8. Make every stage safe to re-run on unchanged input at no cost.

**Retrieval and answering**

9. Build the evaluation set **before** tuning retrieval: 20–30 tagged
   questions, scored with MRR and recall@k. Save the result as a baseline file.
10. Write the system prompt. Constrain the model's reply with a strict JSON
    schema (language, intent, answer, sources used).
11. Add the access gate: check the user may open this tenant before anything
    else runs.

**Interface and operations**

12. API + a simple UI on the same origin.
13. Tracing to Langfuse and Grafana. Look at one real trace.
14. A health check command that exits 0 or 1, so it works from cron.
15. Containerise your own app too, with `restart: unless-stopped`.

---

## 5. Starting settings

Reasonable defaults. Tune them against your evaluation set, not by feel.

| Setting | Start with |
|---|---|
| Chunk size | 600 tokens |
| Chunk overlap | 80 tokens |
| Minimum chunk | 120 tokens |
| Split at | heading level 2 |
| Embedding dimensions | 1536 |
| Passages retrieved (`top_k`) | 8 |
| Passages sent to model | ≤ 6000 tokens |
| Relevance floor | 0.25 |
| Temperature | 0.0 |
| Max completion | 2000 tokens |
| Response time budget | 5 seconds |

Retrieve generously, send selectively: retrieving more costs nothing, sending
more costs money and time.

---

## 6. Rules to follow

**Security**

- Isolation must be **structural**: if a developer forgets a filter, the result
  should be *zero rows*, not *all rows*.
- The app connects as a `SELECT`-only role. A table's owner bypasses
  Row-Level Security, so never connect as the owner.
- With a connection pool use `SET LOCAL`, never `SET` — otherwise one
  request's tenant leaks into the next.
- Treat document text as **data, never instructions**. Fence it in the prompt
  and say so explicitly.
- Never accept a username from a request body. Identity comes from the
  authenticated session.

**Quality**

- Measure before you tune. Keep a baseline file in the repository.
- Write tests, then **break your own logic on purpose** and confirm a test
  fails. A test that never fails is decorative.
- Verify against the running system, not against your documentation.
- Open it in a browser and use it as a user would before calling it done.

**Honesty**

- Do not fake what you cannot build. Label simulated parts as simulated.
- Do not display a number nothing produces.
- If the model has no source for an answer, it must say so rather than guess.

**Operations**

- Containers run the code in their image. Rebuild after every change:
  `docker compose up -d --build <service>`.
- Tracing must fail open — a tracing outage must never stop the app.
- Trace stores contain your document content. Control who has that login.
- Update the documentation in the same commit as the code.

---

## 7. Checklist before release

- [ ] All services start with one `docker compose up -d`
- [ ] Migrations apply cleanly on an empty database
- [ ] A user cannot reach another tenant's data (tested as the restricted role)
- [ ] Retrieval quality measured, baseline recorded
- [ ] Every answer carries citations
- [ ] "Not in the documents" is answered honestly, not invented
- [ ] Response time measured against a stated budget
- [ ] Traces visible in Langfuse and Grafana
- [ ] Health check exits 0 when healthy, 1 when not
- [ ] Tests pass, and key tests verified by mutation
- [ ] Secrets only in `.env`, which is not committed
- [ ] Used in a browser end to end

---

## 8. Three things worth remembering

**Make the rule structural, not conventional.** If a security rule depends on
every query remembering it, it will eventually fail.

**Measure before you tune.** Otherwise you will optimise a problem you do not
have.

**Break your own tests on purpose.** Passing tells you nothing about whether
they would fail.
