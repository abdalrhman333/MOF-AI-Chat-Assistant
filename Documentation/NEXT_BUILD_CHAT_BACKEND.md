# Next build — the chat backend

**A brief for whoever builds this next, assuming no memory of earlier
sessions.** Read `START_HERE.md` first, then this.

The ingestion pipeline is finished and running unattended
(`NEXT_BUILD_PIPELINE.md`). 58 documents are indexed as 678 vectors across
9 per-project Qdrant collections, and retrieval has been spot-checked in
both languages. What does not exist is anything a user can talk to.

This document is written in the same shape as the pipeline brief, because
that shape earned its keep: it caught five places the build had to deviate
from what was specified, and each deviation got recorded instead of
quietly forgotten.

---

## 1. What it has to satisfy

Straight from the BRD (`..\My_Prompts\SOLUTION_SCOPE.md`), not paraphrased:

| AC | Requirement | Who satisfies it |
|---|---|---|
| **AC01** | Verify input language; reject unsupported ones with a clear apology | backend |
| **AC02** | Distinguish "special handling" queries from general SPM-knowledge retrieval | backend |
| **AC03** | Session continuity — multiple questions, no restart | backend |
| **AC04** | Bilingual welcome message immediately on session start | backend + frontend |
| **AC05** | Reply in the same language as the question | backend |
| **AC06** | Answers grounded **only** in indexed documents, with the source cited | backend |
| **AC07** | Clear, safe fallback for out-of-scope queries | backend |
| **AC08** | Response time ≤ 5 seconds under normal conditions | backend |
| **AC08** *(dup)* | Authorized user signs in via SSO/OTP | **deferred** — external dependency |
| **AC09** | No user can view documents of a project not assigned to them | **partly done** — see §2 |
| **AC10** | Knowledge-base page shows document type + indexing status | frontend (data ready) |
| **AC11** | Documents linked to projects | **done** — `document_links` |

AC08 is genuinely duplicated in the source document — used for both
response time and auth success. Reported as written rather than renumbered.

---

## 2. AC09 — read this before writing any code

AC09 is worded absolutely in the source: **لا يستطيع أي مستخدم الاطلاع على
وثائق مشروع غير مخصص له** — no user can access an unassigned project's
documents under any circumstance. Two of the three layers already exist:

| Layer | Mechanism | State |
|---|---|---|
| Vector search | One Qdrant collection per project — no shared index to misfilter | ✅ 678 payloads verified, 0 contamination |
| SQL | Row-level security; `spm_app` is `SELECT`-only and scoped per transaction | ✅ migration `013`, fail-closed |
| **Session gate** | Is the requested project actually assigned to this user? | ❌ **not built** |

**The gate is the one thing infrastructure cannot enforce.** RLS and
per-project collections both assume the session is scoped *correctly* —
neither can tell a wrong-but-valid project from the right one. Validate the
requested project against `project_assignments` **before** the session
opens, and treat that function as the most security-sensitive code in the
project.

Two operational rules that come with it:

- **`SET LOCAL`, never plain `SET`.** A plain `SET` persists on a pooled
  connection and leaks the previous request's project scope into the next —
  the isolation mechanism becoming the leak. Verified behaviour, see
  `INFRASTRUCTURE.md` §7c.
- **The backend connects as `spm_app`, never `spm_admin`.** The pipeline
  uses the admin role because it legitimately needs cross-project reach.
  The backend must not have it.

Test data is already shaped for this: `PRJ-07`, `PRJ-08` and
`TEST-PROJECT` have **zero** assignments, and Noura has three projects to
exercise the switcher.

---

## 3. Suggested build order

Deliberately not the order the ACs are numbered in.

### Step 1 — a retrieval evaluation set (do this first)

**Right now nobody can answer "did that change make retrieval better?"**
We have anecdotes: Arabic scores ~0.40 against English's ~0.60, and the
correct chunk often lands 2nd–3rd rather than 1st. That is fine as an
observation and useless as a baseline.

Build 20–30 realistic project-manager questions across Arabic and English,
each tagged with the document that should answer it, and measure
recall@1 / @3 / @5. It costs pennies (embeddings only) and it is the only
way to tell a real improvement from a comfortable feeling.

**It also de-risks the AC06/AC08 tension** described in §5.

### Step 2 — session gate + project scoping (AC09)

The absolute requirement, and the piece everything else depends on. Build
it, test it adversarially, before anything sits on top of it.

### Step 3 — retrieval composition (AC06)

The three sources from `SOLUTION_ARCHITECTURE.md` §3.3:

| Source | What | Persisted |
|---|---|---|
| RAG | Vector search in the session's project collection | yes — the collection |
| Live SPM | Structured fields by project code from Postgres | yes — Postgres |
| CAG | Documents uploaded mid-chat, OCR'd into session context only | **no** — session-scoped |

Citations come free: every payload already carries `filename`, `doc_type`,
`page`, `section`, plus `char_start`/`char_end` for parent-document
expansion if it turns out to be needed.

### Step 4 — conversational layer (AC01–AC05, AC07)

Language detection and rejection, intent routing, bilingual welcome,
reply-in-kind, safe fallback, multi-turn loop.

### Step 5 — latency (AC08) and observability

Measure against the 5-second budget with real tracing. **This is also when
the pipeline's deferred observability gets built** — Langfuse for LLM
traces (prompts, completions, retrieved context, tokens) and OTLP into
Grafana for cross-service timing. Both have been running unused since the
infrastructure was built; `grafana-lgtm:4317` accepts OTLP gRPC.

---

## 4. Rules that are not negotiable

1. **The backend connects as `spm_app`**, and every transaction does
   `SET LOCAL app.current_project`. See §2.
2. **Read config only through `pipeline.config.get_settings()`.** No module
   reads `os.environ` or hardcodes a path, endpoint, deployment name or
   collection prefix. This is what let the pipeline move into a container
   with zero code changes.
3. **The assistant performs no write operations** against any source
   system. `spm_app` is `SELECT`-only, so this is enforced by the database
   rather than by convention.
4. **Answers are grounded only in retrieved content**, with the source
   cited (AC06). An answer the model produced from its own knowledge is a
   defect, not a bonus.
5. **Prove a safety feature by breaking it.** Every check should be
   mutation-tested — deliberately broken, with the intended test confirmed
   to catch it. Commit *before* mutating: `git checkout` will take
   uncommitted work with it. 44 mutations were run across the pipeline and
   four real test gaps were found this way; none were visible on
   inspection.
6. **Verify against the running system, not against a document.** Three of
   the pipeline's worst defects were invisible to a passing test suite and
   appeared only when something was actually run (§6).

---

## 5. Known-unknowns, flagged honestly

- **⚠️ Source arbitration is an unresolved DESIGN decision, not a coding
  one.** `SOLUTION_ARCHITECTURE.md` §3.3 has carried it as open since the
  architecture was written: what wins when a weekly report and SPM's status
  field disagree about the same project? This blocks Step 3 and should be
  decided with the user, not guessed.
- **AC06 and AC08 pull against each other.** Reranking, retrieving more
  candidates, or expanding to parent documents all improve grounding and
  all cost seconds. The 5-second budget is the constraint; the evaluation
  set in Step 1 is what makes the trade measurable instead of felt.
- **Arabic ranking is the known quality gap.** The correct chunk *is*
  retrieved, just not first. ⚠️ **Do not "fix" this by changing chunking** —
  the obvious theory (HTML table markup diluting the vectors) was tested
  across 5 queries and 75 samples and **refuted**: high-markup and
  low-markup chunks lose the same similarity when stripped (0.029 vs
  0.027). Full record in `STATUS_AND_ROADMAP.md` §11c. Reranking, hybrid
  search, or simply retrieving more candidates are all untried.
- **Auth is deferred, not faked.** Real AD/SSO and OTP are external
  dependencies. The BRD's own practical note for a demo build says to
  simulate what can be simulated honestly and flag the rest — so mock the
  identity, and do **not** build something that merely looks like OTP.
- **CAG has no implementation precedent here.** Uploaded documents get
  OCR'd into session context and are deliberately never persisted. The OCR
  path exists (`pipeline/ocr.py`) but has only ever been used for corpus
  files on disk.
- **`doc_type` classification was deferred to post-OCR** and never built.
  Newly discovered files get `'Unclassified'`. Zero rows are affected
  today, but the KB page (AC10) will surface it.

---

## 6. What the pipeline build taught, that applies here

These cost real time to learn. All are recorded in full in
`STATUS_AND_ROADMAP.md` §6.

- **A passing suite proves nothing until a test has failed for the right
  reason.** Mutation testing found a self-referential test that passed
  against broken code.
- **A guard whose value is diagnostic is invisible to a behavioural test.**
  Happened twice. Assert the error *message*, not just the behaviour.
- **Injecting a connection into a test also excludes the commit path.**
  Flagged after Stage 0, then bit immediately in Stage 1: a connection that
  closed itself, with all 14 tests passing because none used that branch.
- **Containerising is a correctness test.** An absolute path stored in
  shared state was invisible until a second environment mounted the same
  data at a different root.
- **Idempotence is not concurrency-safety.** Every stage was idempotent and
  verified; none of that stops two runs starting together and both paying
  for the same OCR.
- **Test the mechanism, not just the outcome.** A plausible theory with a
  measurably true premise was still wrong (§11c).
- **A provider's error text can be wrong about the cause.** Azure called a
  valid `.docx` corrupted; `testzip()` was clean and it had 136 readable
  paragraphs.

---

## 7. Verify the starting point with

```bash
docker compose ps                                            # 13 containers
python infra/postgres-spm/apply_migrations.py --status        # 13 applied, 0 drifted
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/ -q    # 199 passed
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.status     # exit 0, HEALTHY
```

Expect: 58 documents, 678 chunks, 9 Qdrant collections, 0 failures, and the
schedule reading *daily at 13:00 Asia/Amman*.

The corpus is real and the assistant can be asked real questions today —
`pipeline/indexer.py` shows how to embed a query and search a collection.
Trying a project manager's question against `project_PRJ-03` before writing
anything is a good way to see what you are building on.
