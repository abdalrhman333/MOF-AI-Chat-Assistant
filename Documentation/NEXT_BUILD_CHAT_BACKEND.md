# Next build — the chat backend

**A brief for whoever builds this next, assuming no memory of earlier
sessions.** Read `START_HERE.md` first, then this.

⚠️ **This is now a record, not a to-do.** Every step below is built, and
the system runs in a browser: `docker compose up -d` from `infra/`, then
http://localhost:8000 (the app is the `mof-chat-api` container since 2026-09-14). The only unbuilt acceptance criterion is SSO/OTP, a
deferred external dependency. Kept in full because the *reasoning* — what
was specified, what was built, and where they differ — is what the next
person needs, not the to-do list.

This document is written in the same shape as the pipeline brief, because
that shape earned its keep: it caught five places the build had to deviate
from what was specified, and each deviation got recorded instead of
quietly forgotten.

---

## 1. What it has to satisfy

Straight from the BRD (`..\My_Prompts\SOLUTION_SCOPE.md`), not paraphrased:

| AC | Requirement | Who satisfies it |
|---|---|---|
| **AC01** | Verify input language; reject unsupported ones with a clear apology | ✅ **done** — the model, §21 |
| **AC02** | Distinguish "special handling" queries from general SPM-knowledge retrieval | ✅ **done** — the model, §21 |
| **AC03** | Session continuity — multiple questions, no restart | ✅ **done** — `Conversation`, §18 |
| **AC04** | Bilingual welcome message immediately on session start | ✅ **done** — §18a |
| **AC05** | Reply in the same language as the question | ✅ **done** — told, not inferred, §17c |
| **AC06** | Answers grounded **only** in indexed documents, with the source cited | ✅ **done** — §15, §16 |
| **AC07** | Clear, safe fallback for out-of-scope queries | ✅ **done** — §17c |
| **AC08** | Response time ≤ 5 seconds under normal conditions | ✅ **measured** — p50 2333 / p95 2994 ms, §19 |
| **AC08** *(dup)* | Authorized user signs in via SSO/OTP | ⚠️ **simulated** — email + password + signed cookie, §21c. Real SSO/OTP still deferred |
| **AC09** | No user can view documents of a project not assigned to them | ✅ **done** — four layers, §2, §14, §20a–b |
| **AC10** | Knowledge-base page shows document type + indexing status | ✅ **done** — §20 |
| **AC11** | Documents linked to projects | ✅ **done** — `document_links` |

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
| SQL | Row-level security; `spm_app` is `SELECT`-only and scoped per transaction | ✅ migration `013`, fail-closed, **23 regression tests since 2026-08-09** |
| **Session gate** | Is the requested project actually assigned to this user? | ✅ **built 2026-08-12** — `Application/backend/session.py`, 40 tests, 16 mutations |

**All three layers now exist.** The gate was demonstrated to be necessary
before it was written: a user assigned to PRJ-02/05/06, scoping a session to
PRJ-01, was handed PRJ-01's row by RLS without complaint. Full record in
`STATUS_AND_ROADMAP.md` §14.

⚠️ **`SET LOCAL` cannot take a bind parameter.** Building it by string
concatenation means interpolating a request-supplied project code into the
statement that establishes the security scope. Use
`SELECT set_config('app.current_project', %s, true)` — the parameterised
equivalent. `tests/test_rls.py` pins the two forms together.

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

### Step 1 — a retrieval evaluation set ✅ **BUILT** (2026-08-09)

`evaluation/` — 29 questions across Arabic, English and mixed, spanning all
9 projects, each tagged with the document(s) that genuinely contain the
answer. Run it:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.retrieval_eval --dry-run
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.retrieval_eval
```

**The measured baseline** (recorded in `evaluation/baseline.json`):

| | n | MRR | recall@1 | @3 | @5 | mean top score |
|---|---|---|---|---|---|---|
| **overall** | 29 | **0.829** | 0.76 | 0.90 | 0.93 | 0.507 |
| Arabic | 16 | 0.825 | 0.75 | 0.88 | 0.94 | 0.483 |
| English | 10 | 0.850 | 0.80 | 0.90 | 0.90 | 0.551 |
| mixed | 3 | 0.778 | 0.67 | 1.00 | 1.00 | 0.491 |

**MRR leads, not recall.** The known problem is ranking, and recall@5 can
sit unchanged at 1.00 while the answer moves from 3rd to 1st — a large real
improvement recall cannot see. MRR moves 0.33 → 1.00 for that same change.

**Two findings that change what §5 says.** See `STATUS_AND_ROADMAP.md` §13.

1. **The Arabic ranking gap is largely not real.** Arabic *similarity
   scores* are lower (0.483 against 0.551), but ranking quality is
   essentially equal — MRR 0.825 vs 0.850, and Arabic actually beats
   English at recall@5. Score magnitude and ranking quality are different
   things, and the anecdote conflated them. Reranking is therefore a much
   weaker bet than it looked.
2. **The one systematic failure is missing signal, not weak ranking.**
   `TEST-PROJECT` holds six real charters from six different projects. The
   word "Emdad" appears **nowhere** in that charter's extracted text — only
   in its filename, which is never embedded — and all four charters carry
   the byte-identical breadcrumb `Project Charter — Test Project - Real
   Document Samples`. Retrieval has no way to tell them apart. That is an
   ingestion question (put the filename in the breadcrumb), it forces a
   re-index, and it is now measurable rather than arguable.

**It also de-risks the AC06/AC08 tension** described in §5: any change that
costs seconds can now be justified — or refuted — with a number.

### Step 2 — session gate + project scoping (AC09) ✅ **BUILT** (2026-08-12)

`Application/` — all chat-application code lives there, apart from the
pipeline and the evaluation harness.

```python
from Application.backend.session import open_session, list_assigned_projects

for project in list_assigned_projects(username):      # the switcher
    ...
session = open_session(username, project_code)        # raises AccessDenied
with session.scoped() as cursor:                      # scoped to that project
    cursor.execute("SELECT ... FROM document_links")
```

| Module | Responsibility |
|---|---|
| `backend/errors.py` | `AccessDenied` / `BackendUnavailable`, sharing `AccessError` so a caller that only fails closed still does the right thing |
| `backend/db.py` | The `spm_app` pool, explicit transaction boundaries, scope applied with a bind parameter |
| `backend/session.py` | The gate, the `Session` value object, and the switcher |

Properties, each mutation-tested:

- **Denial is non-enumerable.** "Not yours" and "does not exist" produce an
  identical message and reason, or the error becomes a project directory.
- **Failure is never permission.** An unreachable database raises rather
  than returning an empty (and therefore apparently permitted) result.
- **The app refuses to start as `spm_admin`.** That role bypasses RLS, and
  every isolation test would still pass, because the tests scope correctly.
- **`Session` is the only way to reach data.** It is obtainable only from
  the gate, so a function requiring one cannot be handed an unvalidated code.

⚠️ **Migration `014`** lets a user read the projects they are assigned to,
by name, before any project is scoped — `013` alone returned zero rows in
that state. It does **not** close the gap above; permissive policies combine
with OR, so `project_scope` still serves a declared project.

⚠️ **The gate trusts the username it is handed.** AD SSO and OTP are
external dependencies that do not exist here, so it answers *"may this user
open this project?"* and never *"is this really that user?"*. Whatever
supplies the identity later becomes part of this security boundary; nothing
downstream should accept a username from a request body.

### Step 3 — retrieval composition (AC06) — **RAG half built** (2026-08-12)

The three sources from `SOLUTION_ARCHITECTURE.md` §3.3:

| Source | What | Persisted | State |
|---|---|---|---|
| RAG | Vector search in the session's project collection | yes — the collection | ✅ **built**, §15 |
| Live SPM | Structured fields by project code from Postgres | yes — Postgres | ✅ **built**, §16 |
| CAG | Documents uploaded mid-chat, OCR'd into session context only | **no** — session-scoped | ⬜ open design question |

```python
from Application.backend.session import open_session
from Application.backend.answer import answer_question

session = open_session(username, project_code)
answer  = answer_question(session, "ما الميزانية المعتمدة للمشروع؟")

answer.text          # in the question's language
answer.citations     # resolved from retrieved payloads, never model-invented
answer.found         # False when the documents do not answer it
answer.timings       # embed / search / llm / total, in ms — AC08
```

**Three properties worth not undoing** (all mutation-tested, §15d):

- **Retrieval takes a `Session`, never a project code.** The collection is
  derived from it, so cross-project retrieval is structurally impossible.
- **The model cannot fabricate a citation.** The response schema gives it
  no field for a filename — only integers, resolved here against what was
  actually retrieved. Widening that schema is how hallucinated citations
  would get in; a test pins its shape.
- **Below the relevance floor, the LLM is not called at all.** No evidence
  means an honest refusal, not a paid invitation to improvise.

⚠️ **Passage text is document content and is treated as hostile.** Passages
are fenced and declared as data. Anything that later reformats the prompt
must keep that boundary.

**When two sources disagree, show both.** Attribute each — "SPM, updated
2026-08-01" vs "Weekly Status Report, 2026-07-12" — and let the project
manager judge. Do not rank them and return a winner. §3.3 of
`SOLUTION_ARCHITECTURE.md` has the reasoning and the implementation notes.

Citations come free: every payload already carries `filename`, `doc_type`,
`page`, `section`, plus `char_start`/`char_end` for parent-document
expansion if it turns out to be needed.

### Step 4 — conversational layer — **AC01/AC02/AC05/AC07 built** (2026-08-16)

`Application/backend/conversation.py` is the entry point; `language.py`,
`intent.py` and `messages.py` are what it composes. Full record in
`STATUS_AND_ROADMAP.md` §17.

```python
reply = handle_question(session, user_text)
reply.outcome   # answered | rejected_language | no_language_detected
                # | handled_without_retrieval | out_of_scope
```

**Four properties worth not undoing:**

- **AC01 runs before anything is spent.** A rejected language costs zero
  embeddings, zero searches and zero completions — measured live, not
  inferred from reading the code.
- **A token containing a numeral is an identifier, not a language.**
  `ما حالة PRJ-06؟` is 33% Latin letters; without this rule the commonest
  shape of question in this system reports as bilingual. Do **not** "fix"
  a mis-detection by raising `mixed_min_share` — that trades this bug for
  the opposite one.
- **Tier 1 matches a whole short utterance, never a substring.** Otherwise
  `مرحبا، ما ميزانية المشروع؟` is answered "hello" and the question is lost.
- **Only a *named* language reaches the prompt.** `reply_language` is
  caller input; interpolating it unchecked puts caller text in the system
  message.

⚠️ **`write_action` and `cross_project` are deliberately unrouted.** Both
are safe in tier 2 — verified live, no leak — but get a generic refusal
rather than a specific one. That is a wording gap, measured and recorded
(§17b), so a classifier can be justified by observed misrouting rather
than by anticipation.

**Step 4 completed 2026-08-16 with AC03 and AC04** — `Conversation`, §18.
Three more properties worth not undoing:

- **A `Conversation`'s session is fixed at construction.** AC09 through
  time. Switching project means a new one.
- **The follow-up fallback fires only below the floor.** Measured:
  referential follow-ups score 0.15–0.18 and retrieve nothing; composing
  with the previous question lifts them to 0.35–0.51 *and lands on the
  right document*. Topical follow-ups already work, so composing every
  query would spend an embedding for nothing and blur a change of subject.
- **The composition anchor must be the last turn that had a topic.** A
  greeting in between would otherwise become the anchor — found by
  mutation testing, §18e.

⚠️ **The language rejection is gated on confidence, not string length.**
An earlier length gate let *"Quel est le budget?"* (19 characters) through
and a live conversation answered it in English. Do not reintroduce a length
threshold, and do not lower the confidence one — 0.473 is how close the
nearest false rejection of real English sits (§18d).

### Step 5 — latency (AC08) and observability ✅ **BUILT** (2026-08-16)

`observability/` (OpenTelemetry → Grafana **and** Langfuse from one
instrumentation), `Application/backend/clients.py` (the AC08 fix), and
`evaluation/latency_bench.py`. Full record in `STATUS_AND_ROADMAP.md` §19.

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.latency_bench
```

**Measured: p50 2333 ms, p95 2994 ms** against the 5 s budget, with the LLM
call ~80% of a turn. The fix was setup, not the model: every question had
been rebuilding all three clients, ~2.3 s of pure overhead.

**Four properties worth not undoing:**

- **`warmup()` at startup.** Without it the first turn is ~19 s, because
  client construction happens inside it.
- **Tracing is fail-open** — the only thing in this repo that is. An
  unreachable collector must never stop an answer.
- **Spans are exported in the background.** A synchronous processor puts
  the collector's latency straight into the AC08 budget.
- **`LANGFUSE_CAPTURE_CONTENT` is an AC09 decision.** A trace carries
  document passages and a trace store has no notion of assignment. Despite
  its name it governs Grafana too — both receive the same attributes.

⚠️ **Judge AC08 on p95, not the mean or the max.** A mean hides the turns
that break the budget; a max fails the run on one slow upstream response.
"Under normal conditions" is a percentile statement.

---

### Step 6 — the HTTP layer and the frontend ✅ **BUILT** (2026-08-16)

`Application/backend/api/` and `Application/frontend/index.html`, §20.
**This brief is now a record, not a to-do.**

```bash
cd infra && docker compose up -d    # http://localhost:8000 — container mof-chat-api
```

**Three properties worth not undoing:**

- **A username never comes from a request**, enforced structurally: no
  schema has a field for one. `api/identity.py` is the AC09 boundary, and
  real AD SSO replaces it and nothing else.
- **A conversation id is a handle, not a credential** — every lookup is
  owner-checked. Removing that check fails three tests.
- **Refusals stay uniform.** Unassigned and nonexistent projects, and
  unknown and someone-else's conversations, return byte-identical 403s.

⚠️ **The conversation store is in memory and per-process** — run the API
single-process until it moves to Redis.

⚠️ **`trusted_header` mode is production-shaped, not production-safe.**
Exposed without a proxy that overwrites the header, any client can claim
any identity. The startup log says so; do not soften it.

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
5. **Never silently resolve a conflict between sources.** When SPM and an
   indexed document disagree about the same fact, present **both**,
   attribute each with its date, and let the project manager decide. The
   assistant is informational and has no authority to adjudicate — and
   picking a winner invisibly means the PM never learns the sources
   disagree, which is often the most useful thing in the answer.
6. **Prove a safety feature by breaking it.** Every check should be
   mutation-tested — deliberately broken, with the intended test confirmed
   to catch it. Commit *before* mutating: `git checkout` will take
   uncommitted work with it. 44 mutations were run across the pipeline and
   four real test gaps were found this way; none were visible on
   inspection.
7. **Verify against the running system, not against a document.** Three of
   the pipeline's worst defects were invisible to a passing test suite and
   appeared only when something was actually run (§6).

---

## 5. Known-unknowns, flagged honestly

- ~~Source arbitration is an unresolved design decision~~ — **decided
  2026-08-09, no longer blocking.** When a weekly report and SPM's status
  field disagree, **present both, attribute each to its source, and let the
  project manager decide.** Never silently pick a winner: the assistant is
  purely informational and has no authority to adjudicate, and resolving it
  invisibly means the PM never learns the sources disagree. Full reasoning
  and implementation notes in `SOLUTION_ARCHITECTURE.md` §3.3 — read them
  before building step 3, particularly that conflict detection is a
  prompting concern, not a deterministic diff, and that attribution must
  carry dates.
- **AC06 and AC08 pull against each other.** Reranking, retrieving more
  candidates, or expanding to parent documents all improve grounding and
  all cost seconds. The 5-second budget is the constraint; the evaluation
  set in Step 1 is what makes the trade measurable instead of felt.
- ~~**Arabic ranking is the known quality gap.**~~ — **measured 2026-08-09
  and largely refuted.** Arabic similarity *scores* are lower (0.483 against
  English's 0.551) but *ranking* is essentially equal: MRR 0.825 vs 0.850,
  and Arabic beats English at recall@5 (0.94 vs 0.90). A low score is not a
  bad rank. Full numbers in §3 step 1 and `STATUS_AND_ROADMAP.md` §13. The
  chunking warning below still stands unchanged, and is now doubly
  supported. The correct chunk *is*
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
docker compose ps                                            # 14 containers
python infra/postgres-spm/apply_migrations.py --status        # 17 applied, 0 drifted
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/ -q    # 672 passed
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pipeline.status     # exit 0, HEALTHY
```

Expect: 58 documents, 678 chunks, 9 Qdrant collections, 0 failures, and the
schedule reading *daily at 13:00 Asia/Amman*.

The corpus is real and the assistant can be asked real questions today —
`pipeline/indexer.py` shows how to embed a query and search a collection.
Trying a project manager's question against `project_PRJ-03` before writing
anything is a good way to see what you are building on.
