# AI Solution Playbook — what we used, and how we worked

**Written for colleagues starting their own AI use case.** This is not a
description of the Enjaz chatbot; it is the reusable part — the stack, the
method, the decisions that were not obvious, and the mistakes that cost us
time. Every number in it was measured on the running system, not estimated.

**Source project:** منصة إنجاز assistant (Ministry of Finance) — a
retrieval-grounded bilingual assistant over project documents, with absolute
per-project isolation. Built on one developer PC between 2026-08-04 and
2026-09-17.

**If you read only one section, read [§7 The method](#7-the-method--the-part-that-transfers).**
The tools change every six months; the method is what survives.

---

## 1. The stack at a glance

| Layer | What we used | Why this one |
|---|---|---|
| Language (backend) | **Python 3.13** | The AI SDKs live here. No argument to have. |
| Language (frontend) | **Plain HTML/CSS/JavaScript** | No framework, no bundler, no `node_modules`. Five screens did not justify a build step. |
| Web framework | **FastAPI 0.121** + **uvicorn** | Async, typed request/response models, free OpenAPI docs at `/docs`. |
| Database | **PostgreSQL 16** | Row-Level Security is the reason. See §4. |
| DB driver | **psycopg 3.3** + `psycopg_pool` | **Raw SQL, no ORM** — deliberate, see §6. |
| Vector store | **Qdrant 1.19** | One collection per tenant is a first-class operation. |
| Cache / queue / state | **Redis 7** | Celery broker, conversation store, cross-process locks. |
| Background jobs | **Celery 5.6** (worker + beat) | Scheduled ingestion that survives a restart. |
| LLM | **Azure OpenAI `gpt-5.4-mini`** | See §3. |
| Embeddings | **Azure `text-embedding-3-S`**, 1536 dims | Same resource, same key. |
| OCR / extraction | **Azure Document Intelligence**, `prebuilt-layout` | Arabic + scanned PDFs + tables → markdown. |
| Token counting | **tiktoken 0.13** | Budget the prompt before sending it. |
| Tracing | **OpenTelemetry SDK 1.39** → OTLP/HTTP | One instrumentation, two backends. |
| Trace UI (dev) | **Grafana `otel-lgtm`** (Tempo/Loki/Prometheus) | Timing and waterfalls. |
| Trace UI (LLM) | **Langfuse v3** | Prompts, completions, tokens, cost. |
| Containers | **Docker Compose**, 14 services | The whole system, including the app. |
| Container UI | **Portainer CE** | Optional, useful for non-CLI teammates. |
| DB browsing | **DBeaver** | Optional. |
| Tests | **pytest 9.1** + **`node --test`** | 723 Python + 79 JavaScript. |

### What we deliberately did NOT use

| Not used | Why not |
|---|---|
| **LangChain / LlamaIndex** | We call the `openai` SDK directly. The framework's value is swapping components quickly; we had exactly one LLM, one embedder, one vector store, and a prompt we needed total control over. A framework would have added a layer between us and the thing we most needed to read: the exact bytes sent to the model. |
| **An ORM (SQLAlchemy/Django)** | Row-Level Security and `SET LOCAL` scoping are SQL-level concerns. An ORM would have hidden the exact thing that enforces our security boundary. |
| **A frontend framework** | Five screens, one file, no build step. Revisit at ~15 screens. |
| **A local/self-hosted model** | See §3. |
| **A reranker** | Measured first: retrieval quality was already good enough that a reranker's latency cost outweighed its gain. Measure before adding. |

> **The lesson is not "avoid frameworks."** It is that each of these was a
> decision with a reason, not a default. Write your reason down. If you cannot
> state what a dependency buys you, you do not need it yet.

---

## 2. Architecture — the three-package shape

The single most useful structural decision: **split by job, and give each job
its own database privileges.** They run as separate processes.

```
pipeline/      BUILDS the knowledge base     → DB role: admin (all tenants)
evaluation/    JUDGES it                     → DB role: read-only
Application/   SERVES it                     → DB role: app (SELECT only,
                                                one tenant per transaction)
observability/ WATCHES all three             → no DB access
```

**The component facing users has the least access.** The ingestion pipeline
legitimately needs to read every project; the chat app must never be able to.
Making that a *role* difference rather than a *code* difference means the
restriction survives a careless query.

### The data flow

```
Documents → [scan] → [OCR] → [classify] → [chunk] → [embed] → [index]
                                                                  ↓
                                                    Qdrant (one collection
                                                        per project)
                                                                  ↓
Question → [embed] → [search THIS project only] ─────────────────┘
                            ↓
                    + live database record
                            ↓
                    [one LLM call: detect language,
                     classify intent, answer, cite]
                            ↓
                    Answer + citations rebuilt server-side
```

**Two sources, kept separate in the prompt.** Retrieved document passages and
the structured database record go into the prompt as *separately labelled
sections*, never merged. That separation is what lets the model attribute a
fact to "the system of record" versus "a document" — and lets us instruct it
to **report both and name each** when they disagree, rather than averaging or
silently picking one. For a finance audience that rule matters more than any
accuracy metric.

---

## 3. Models — what we chose and why

| Role | Deployment | API version |
|---|---|---|
| Generation | `gpt-5.4-mini` | `2024-12-01-preview` |
| Embeddings | `text-embedding-3-S` (1536 dims) | `2024-12-01-preview` |
| Document extraction | Document Intelligence `prebuilt-layout` | — |

**Cloud, not local.** We had an RTX 3070 and considered a local model. Cloud
won on: Arabic quality, no GPU ops burden, and because the demo had to run
reliably on someone else's schedule. For a ministry deployment the calculus
may reverse on data residency — decide that explicitly, early, because it
changes your whole infrastructure.

**A small model was enough.** We did not need a frontier model. The retrieval
does the hard work; the model's job is to read supplied passages and write a
careful answer. **Try the cheap model first and measure** — most RAG systems
are limited by retrieval, not by generation.

**`temperature = 0.0`.** This is a factual assistant over financial records.
There is nothing creative to want.

### ⚠️ One key, three roles — a real operational trade

All three Azure capabilities share one resource and one API key. That is
simple, and it means **one expired key takes down generation, retrieval AND
ingestion simultaneously**. We chose it knowingly after an earlier two-account
split caused its own outage. Whichever you choose, know which failure you are
buying.

### Tuning values that worked (start here, then measure)

| Setting | Value | Note |
|---|---|---|
| Chunk target | **600 tokens** | With **80** overlap, **120** minimum |
| Chunk split at | **heading level 2** | Structure-aware, not fixed-size |
| Retrieved (`top_k`) | **8** | Retrieving more is free |
| Sent to model | **≤ 6000 tokens** | *Sending* more costs money and seconds |
| Relevance floor | **0.25** | Genuine hits measured 0.31–0.56 |
| Max completion | **2000 tokens** | |

**`top_k` and the context budget are separate on purpose.** They bound
different costs. Retrieve generously, send selectively.

---

## 4. Security — structural, not conventional

This is the most transferable engineering idea in the project.

**The problem:** with multi-tenant data, isolation usually lives in the
application's `WHERE` clause. One forgotten filter and everything leaks, with
no error and no warning.

**What we did instead — three independent layers:**

1. **An access gate.** Before a session opens, the requested tenant is checked
   against the user's assignments. A tenant that does not exist and a tenant
   the user cannot access return the *identical* refusal — otherwise the error
   message becomes a way to enumerate tenants.

2. **PostgreSQL Row-Level Security.** The app connects as a `SELECT`-only role.
   Each transaction declares its tenant (`SET LOCAL app.current_project`), and
   policies filter every table automatically. A careless `SELECT * FROM
   documents` that would have returned 58 rows across 9 tenants returns only
   this tenant's 6.

3. **Physical separation in the vector store.** One Qdrant collection per
   tenant. Cross-tenant retrieval is not filtered out — it is not addressable.

**The property that matters: fail-closed.** If the app forgets to set the
tenant, `current_setting(...)` is NULL, the comparison is never true, and the
result is **zero rows — not all rows**. Forgetting produces nothing, never
everything. Design your isolation so the failure mode is silence.

⚠️ **Two traps we hit:**
- **A table's owner bypasses RLS.** The app therefore *cannot* connect as the
  owning role. If you test isolation as the admin user, you will prove nothing.
- **Use `SET LOCAL`, never `SET`, with a connection pool.** `SET` persists on
  the pooled connection and leaks the previous request's tenant into the next
  one. This is the single most dangerous line in the system.

**Prompt injection is a data problem, not a model problem.** Document passages
go into the prompt inside explicit `<<<PASSAGE n>>>` fences, with a rule
stating passages are data and never instructions. Your corpus is uploaded
files — assume one will eventually contain text shaped like a command.

**Citations are rebuilt server-side.** The model returns only *integers*
identifying which passages it used. There is no field in the response schema
for a filename. A hallucinated citation is therefore not possible — not
unlikely, impossible. **Constrain the model's output shape so the failure you
fear cannot be expressed.**

---

## 5. Observability — decide this on day one

**One instrumentation, two backends.** We emit OpenTelemetry spans once and
export to both Grafana (timing) and Langfuse (prompts, tokens, cost).

The trace for one question:

```
POST /api/conversations/{token}/messages   2611 ms
  chat.turn                                2606 ms
    retrieval.embed                         768 ms   ← Azure
    retrieval.search                         73 ms   ← Qdrant
    spm.fetch                                 1 ms   ← Postgres
    chat gpt-5.4-mini                      1760 ms   ← Azure
```

**That waterfall answered a question we would otherwise have argued about.**
The two network calls to Azure are ~97% of the time. Vector search is 73 ms.
No amount of tuning the database or the search moves the total. Without the
trace we would have optimised the wrong thing — confidently.

**Build this before you need it.** It costs an afternoon and it is how you stop
guessing.

⚠️ **Traces contain your documents.** With content capture on, anyone who can
log into the trace UI can read every tenant's passages, regardless of your
access rules. A trace store has no notion of your security model. Decide
deliberately who gets that login — this remains an open item on our own build.

**Tracing must fail open.** A tracing outage must never stop the app. Ours has
no startup dependency on either backend.

---

## 6. Decisions that went against the obvious choice

Each of these was argued, and in each case we chose the less-recommended
option for a stated reason. Listed because *how* they were decided matters
more than the choices themselves.

| Decision | Obvious choice | What we did | Why |
|---|---|---|---|
| Database access | An ORM | **Raw SQL** | RLS and `SET LOCAL` are the security boundary. Hiding them behind an ORM hides the thing being relied on. |
| Document extraction | Native parsers for Office, OCR only for scans | **Everything through Document Intelligence** | We *measured* across 11 files. The measurement reversed our initial recommendation — native extraction mangled Arabic tables. |
| Routing | Deterministic language detector + intent classifier | **One model call does all three** | Simpler and more accurate on mixed-language input. **The cost is real:** routing is no longer free, offline or deterministic, and a prompt change can alter it. Mitigated by a router evaluation set that must not regress. |
| Frontend tests | jsdom + a bundler | **A hand-written 100-line DOM shim** | It throws for any element id the markup does not contain, so a typo fails in tests instead of on a user's click. |

**The pattern:** when you go against the recommendation, write down the
measurement or the reason. A year later, nobody remembers which choices were
considered and which were accidents.

---

## 7. The method — the part that transfers

### 7.1 Verify live. Never trust a document, including your own.

Every session started by running the system and looking at real output —
`docker compose ps`, the migration status, the health check, the browser.
Our documentation carries a standing instruction: *ground truth is the running
system, not any document.*

This repeatedly caught things: a health check reporting success for a run the
worker never received; containers running week-old code because nobody
rebuilt the image; a "fixed" figure that had drifted.

### 7.2 Measure before you tune

We built an evaluation harness (29 tagged questions, MRR and recall@k) **before**
optimising retrieval, and recorded a baseline as a file in the repo.

It earned its cost immediately. The team belief was "Arabic ranking is our
known weakness." Measured: Arabic similarity *scores* were lower (0.483 vs
0.551) but *ranking* was essentially equal (MRR 0.825 vs 0.850), and Arabic
won at recall@5. **We were about to optimise a problem we did not have.**

Recorded baseline (2026-08-12, 29 questions):

| Metric | Value |
|---|---|
| MRR | 0.839 |
| recall@1 / @3 / @5 | 0.793 / 0.897 / 0.897 |

⚠️ **Record the caveats with the numbers.** Qdrant's HNSW index is
*approximate*, so rebuilding it reshuffles near-ties. On our corpus a
one-question recall change (~0.03) is noise. A metric without its noise floor
invites celebrating randomness.

There is a second evaluation for routing (language and intent accuracy,
currently 0.966 / 0.983) which must not regress when the prompt changes.
**If your prompt does classification, your prompt needs a test set.**

### 7.3 Mutation testing — the discipline that made the tests real

**Every change got tests. Then we deliberately broke the new logic and
confirmed a test failed.** A surviving mutation means the test is decorative.

This found genuine problems repeatedly, including:

- A test asserting "the model's language choice beats the alphabet heuristic"
  that used a sentence *both* methods got right. It passed with the logic
  deleted.
- A test that never opened the menu it claimed to test, because the DOM shim
  seeds no classes from markup — so the first toggle *closed* an already-closed
  element.
- A test suite that wrote fake success records into the real Redis, making the
  health check report a run that never happened — a test creating the exact
  failure the work was removing.

**In roughly half the cases, the surviving mutation exposed a bad test, not
missing coverage.** Passing tests tell you nothing about whether they *would*
fail. Mutation testing is how you find out, and it takes minutes.

### 7.4 Do not fake what you cannot build

Real Active Directory SSO, OTP and a live SharePoint connection were not
available. We **did not** build convincing-looking fakes. Sign-in is simulated
and labelled as simulated, on screen and in the code.

Three properties were kept genuine anyway, because getting them wrong teaches
the wrong shape to whoever replaces the simulation: passwords are hashed
(PBKDF2, per-user salt, constant-time compare); a wrong password and an unknown
address are indistinguishable in both message and timing; an account with no
password cannot be signed into.

Likewise in the UI: the design showed notification badges with counts. Nothing
produced those numbers, so we shipped no numbers. **A fabricated number on a
ministry screen is a claim, not a decoration.** The other agents are marked
"قريباً" rather than linking nowhere.

**A stakeholder who discovers a faked feature stops trusting the real ones.**

### 7.5 Containers run their image, not your files

Stated because it cost us a week of silently wrong behaviour: a processing
stage was added, and every scheduled run skipped it for seven days because the
image was never rebuilt. The health check reported success the whole time.

```bash
docker compose up -d --build <service>    # after ANY code change
```

Related: the one component we left outside Docker — the app itself — was the
one that was down when someone opened the demo the next day. The terminal
session that started it had ended. **Containerise everything, including the
thing you are demoing.**

### 7.6 Documentation is part of the change

Docs were updated in the same commit as the code, and audited against the
running system periodically. Commit messages explain **why**, not what — the
diff already says what.

The format that worked: a chronological log where later entries supersede
earlier ones, each recording what was built, what was decided, what was
rejected, and what broke. When you return after three weeks, the reasoning is
what you have lost, not the code.

---

## 8. Calibration — what this actually cost

| | |
|---|---|
| Duration | ~6 weeks, one developer, part-time |
| Documents processed | 58 (154 pages) → 678 passages, 9 collections |
| **Total Azure spend, whole project** | **~$2.51** |
| — Document extraction | ~$1.50 (154 pages, the only real cost) |
| — Embeddings | <$0.01 (295,232 tokens) |
| Tests | 723 Python + 79 JavaScript |
| Database migrations | 17 |
| Containers | 14 |
| Response time | ~3.5–4.8 s (budget 5 s); ~6 s on the first call after idle |
| RAM required | 8 GB for the container stack |

**Two numbers worth internalising:**

**Extraction dominates ingestion cost; embeddings are effectively free.** So
cache extraction results in your database and let the presence of a cached row
be the "done" marker. We can re-chunk and re-embed the entire corpus for under
a cent, any time, without re-paying for OCR. Design for that.

**Almost all latency is network calls to the model provider.** Budget your
response time as "number of sequential API calls," because that is what it is.

---

## 9. Pitfalls that cost us real time

| Pitfall | What happens | Guard |
|---|---|---|
| Code changed, container not rebuilt | Behaviour unchanged, no error | Rebuild after every change |
| `SET` instead of `SET LOCAL` on a pooled connection | Previous request's tenant leaks into the next | `SET LOCAL`, and test with a pool |
| Testing isolation as the admin role | Owner bypasses RLS; you prove nothing | Test as the restricted role |
| Arabic text through a shell (`curl -d`, `python -c`) | Mangles → looks exactly like a routing bug | Use a file with explicit UTF-8 |
| Health check measuring the wrong thing | "Nothing indexed for 33 days" while everything worked | Record *runs*, not just *data changes* |
| Believing a metric to 4 decimals | Approximate indexes reshuffle near-ties | Record the noise floor beside the number |
| Trace stores holding document content | Bypasses your whole access model | Decide who gets that login, explicitly |
| The one thing outside Docker | Is the thing that will be down | Containerise everything |
| Under-provisioned VM (4 GB) | Containers report "Up" while unreachable | 8 GB for a stack like this |

---

## 10. Starting checklist for a new AI use case

**Before writing code**
1. Write the one-sentence requirement that everything else serves. Ours was
   *absolute per-project isolation*. Every later decision was judged against it.
2. Name what you will NOT build, and why. Keep the list visible.
3. Decide cloud vs local models now — it changes your infrastructure.

**Foundation (week 1)**
4. Docker Compose for every dependency. Data on a known disk, in a known path.
5. Database schema with numbered, checksummed migrations from the very first
   table.
6. If multi-tenant: RLS and a restricted role **before** any application code.
   Retrofitting a security boundary is much harder than starting with one.
7. A settings module that is the only thing reading the environment. No module
   reads `os.environ` directly or hardcodes a path.

**The AI part (weeks 2–3)**
8. Ingestion: extract → cache the extraction in your database → chunk → embed →
   index. Cache extraction, because it is the part that costs money.
9. Make every stage re-runnable on unchanged input at zero cost. You will run
   it fifty times.
10. **Build the evaluation set before tuning retrieval.** 20–30 tagged
    questions is enough. Record a baseline as a file in the repo.
11. Grounded answering: constrain the response with a strict schema, and have
    the model reference sources by *index* so citations are rebuilt
    server-side.

**Before anyone sees it**
12. Tracing to something you can read. Look at one real waterfall.
13. Mutation-test your most important tests. Expect to find a decorative one.
14. Run it in a browser and use it as a user would. Two of our genuine defects
    — an Arabic punctuation direction bug and five identical source chips —
    existed only there, invisible to every test.
15. Measure latency and state the budget you are measuring against.

---

## 11. Where to look in this repository

| You want | Read |
|---|---|
| Orientation + how to verify the system is real | `START_HERE.md` |
| Scope, retrieval design, every non-obvious decision | `SOLUTION_ARCHITECTURE.md` |
| Compose stack, services, ports, verified gotchas | `INFRASTRUCTURE.md` |
| What is done, decisions log, lessons learned | `STATUS_AND_ROADMAP.md` |
| Every folder, container, model and library | `INVENTORY.md` |
| Visual walkthrough, eight diagrams | `SYSTEM_WORKFLOW.html` |
| Plain-language guide for non-technical readers | `Enjaz_Solution_Guide.docx` |
| **The system prompt — the whole router** | `Application/backend/answer.py` |
| The tenant isolation gate | `Application/backend/session.py` |
| RLS policies | `infra/postgres-spm/migrations/013_*.sql` |
| Retrieval evaluation | `evaluation/` |

---

## 12. The three sentences worth remembering

**Make the rule structural, not conventional.** If isolation depends on every
query remembering a filter, it will fail. Put it where forgetting produces
nothing rather than everything.

**Measure before you tune, and record the noise floor.** We nearly optimised a
problem we did not have, and only the measurement stopped us.

**Break your own tests on purpose.** A test that passes tells you nothing about
whether it would fail. That check takes minutes and changes what your test
suite is worth.
