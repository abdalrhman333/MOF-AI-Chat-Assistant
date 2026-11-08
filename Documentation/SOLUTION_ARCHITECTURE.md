# Solution Architecture — MOF Enjaz Chatbot

**Last verified:** 2026-09-14

This document describes **what the solution must do** (derived from the
BRD) and **how we decided to build it** — the retrieval architecture, the
session flow, and every non-obvious design decision, with the reasoning
behind it so it doesn't get silently re-litigated later.

---

## 1. Source of truth

- **Original requirements document (Arabic):** وثيقة المتطلبات التفصيلية
  الموحدة – الوكيل الذكي (01/07/2026), Section 3 (المساعد الرقمي الذكي /
  شات بوت — منصة انجاز), National Center for Government Governance
  Regulation (NCGR). Lives at
  `C:\Users\abood\Desktop\وثيقة المتطلبات التفصيلية الموحدة - الوكيل الذكي 01-07-2026.pdf`
  (external to this repo).
- **Derived scope document (English, this repo):** `..\My_Prompts\SOLUTION_SCOPE.md`
  — read and verified section-by-section against the source PDF (pages
  29–37, "ثالثاً: المساعد الرقمي الذكي (شات بوت) — منصة إنجاز"). Confirmed
  to match exactly, including the AC-08 duplicate (used for both
  response-time and SSO/OTP acceptance criteria in the source itself, not a
  summarization error).

Read `SOLUTION_SCOPE.md` for the full scope-in/out, actors, user stories,
acceptance criteria (AC01–AC11), dependencies, assumptions, constraints, and
risk register. This document does not repeat that — it covers the design
decisions layered on top of it.

---

## 2. Purpose (one paragraph)

Project managers need to ask natural-language questions (Arabic, English, or
mixed) about their assigned projects and get answers grounded in actual
project documentation — currently scattered across SharePoint, requiring
manual search. The assistant reduces time-to-information, confines each
session to exactly one of the user's assigned projects with zero
cross-project access, and is purely informational (read-only relationship
to source documents, no write operations against any system).

---

## 3. The retrieval architecture (four sources per query)

Designed by the user before any implementation existed; this is the
governing shape for the whole backend.

### 3.1 Ingestion pipeline (offline / background)

Each SharePoint project folder (project charter, weekly reports, lessons
learned, etc.) is OCR'd, embedded, and indexed. **Each project gets its own
separate Qdrant collection** — not one shared collection with a
project-ID metadata filter. See §4.2 for why.

### 3.2 Session flow

The flow the BRD specifies, and the flow the demo actually runs:

```
AD login (SSO + OTP)                    DEMO: email + password against the users
                                        seeded in SPM, then a signed session cookie
  → system fetches the user's project list from SPM
  → user must select exactly one project      a required choice after every
                                              sign-in — nothing pre-selected,
                                              no way to dismiss it
  → session is locked to that project        project name held in the top bar
  → bilingual welcome (AC04)                 first name only; states what the
                                              assistant answers and its limits
  → user converses (multi-turn — AC03)
  → a page refresh RESUMES the project, the conversation and the screen;
    switching is done only from the project selector; sign-out forgets it
```

Real SSO/OTP replaces the sign-in step and nothing below it — see §4.12.

### 3.3 Four-way answer composition per query

| # | Source | What it is | Persisted? |
|---|---|---|---|
| 1 | **RAG** | Vector search against the selected project's dedicated Qdrant collection (the ingested SharePoint files) | Yes — the collection itself |
| 2 | **Live SPM entity data** | Structured project fields pulled directly by Project ID from SPM (status, dates, metadata — not files). Demo: filled directly into `postgres-spm`, no live SPM API yet | Yes — Postgres |
| 3 | **The project registers** | Risks, issues, change requests and deliverables — BRD v1.1 §5.3.6's remaining named inputs. Read as **rows** by `Application/backend/registers.py` and sent as a labelled block, never chunked or embedded. Added 2026-11-03; see `STATUS_AND_ROADMAP.md` §25 | Yes — Postgres |
| 4 | **CAG** | Documents uploaded mid-chat through the UI get OCR'd and injected directly into the current session's LLM context | **No** — session-scoped only, matches BRD scope item 5 ("supplementary upload," not permanent ingestion). **Still not built** |

⚠️ **Sources 2 and 3 are structured; source 1 is prose. That distinction is
the design, not an implementation detail.** Embedding a risk row would
destroy the fields that make it answerable — a question like "which risks
are still open" turns on `status`, which a similarity match cannot filter
on. Retrieval is for documents; SQL is for registers. The corollary is that
adding a register means adding a table and a read, never a new collection.

⚠️ **All four sources share ONE prompt budget.** Sources 2 and 3 are sent
whole — a half-listed register would make a count wrong — so they are
charged first and RAG takes what is left, with a floor so a large register
can never squeeze retrieval out entirely. Adding the registers *on top of*
a full passage budget took a measured turn from 3.5–4.8 s to 4.4–7.0 s and
broke AC08; see §25b.

**Resolved:**
- ~~What triggers re-ingestion / index refresh when files change~~ —
  **answered 2026-08-05 by content hashing.** `document_links.file_hash`
  (SHA-256, plus `file_size_bytes` as a cheap pre-check) lets the
  scanner distinguish new / unchanged / modified files, so re-ingestion
  is triggered by a hash mismatch. Chosen over filesystem modified-time,
  which is only a hint — copying resets it and SharePoint sync may not
  preserve it, and stale content producing confidently wrong answers is
  worse than a slower scan. Migration `012`.

- ~~Source arbitration when RAG-indexed files and live SPM data disagree~~
  — **answered 2026-08-09 by the user: present BOTH, attribute each to its
  source, and let the project manager decide.**

  The alternative was to rank the sources and silently return the winner
  (SPM's structured field beats a weekly report, or the most recent wins).
  Rejected, for three reasons:

  1. **The assistant has no authority to adjudicate.** The BRD defines it
     as purely informational and read-only. Choosing between a weekly
     report saying "delayed" and SPM saying "on track" is a project
     judgement, and making it invisibly means the PM never learns the two
     disagree — which is itself often the most useful thing in the answer.
  2. **It is what AC06 already requires.** Answers must be grounded with
     the source cited. Two grounded, cited claims is a *fuller* answer than
     one, not a hedge.
  3. **Neither source is reliably right.** SPM is the system of record for
     status, but a weekly report can be more recent, or carry the reason
     behind a number that the field cannot express.

  **Implementation notes for whoever builds it** (`NEXT_BUILD_CHAT_BACKEND.md`
  §3, step 3):

  - Conflict detection is **not a deterministic diff**. "Status: at-risk"
    in SPM and a sentence in a weekly report are the same fact in different
    shapes. The mechanism is to hand the LLM both sources with explicit
    provenance and instruct it to *surface* disagreement rather than
    resolve it — never to compute a winner in code.
  - **Attribution must carry dates.** "SPM, updated 2026-08-01" against
    "Weekly Status Report, 2026-07-12" is often all a PM needs to decide.
    Without dates, presenting both is just confusing.
  - **Do not over-trigger.** Flagging a conflict on every wording
    difference turns a useful signal into noise. Only genuine factual
    disagreement about the same attribute.
  - Costs essentially nothing against the AC08 five-second budget: this is
    a prompting and presentation decision, not extra retrieval.

**Open questions, not yet resolved:**
- *(none currently)*

---

## 4. Design decisions and the reasoning behind them

### 4.1 Cloud models, not on-prem (this phase)

Despite the RTX 3070 GPU being available on the build machine, LLM, OCR, and
embedding models are **cloud-hosted** for this demo phase. Decided
explicitly and **reconfirmed** after reviewing a colleague's on-prem
approach on a different project (§5).

**Providers chosen — all Azure**, matching the existing Microsoft/
SharePoint/AD ecosystem this platform already lives in:

| Capability | Provider | Azure account | Status |
|---|---|---|---|
| LLM | Azure OpenAI `gpt-5.4-mini`, via an Azure AI Foundry unified endpoint (`*.services.ai.azure.com`) | **Account #2** (moved 2026-09-07) | ✅ Verified live (Arabic + English) |
| Embeddings | Azure OpenAI `text-embedding-3-small` (deployment name saved truncated as `text-embedding-3-S`, cosmetic only) | Account #2 | ✅ Verified live — **1536-dimension vectors** |
| OCR | Azure AI Document Intelligence (resource `DI-Enjaz`, East US, Standard tier) | Account #2 | ✅ Verified live (`prebuilt-read`, `prebuilt-layout` models confirmed available) |

⚠️ **The two-account split is gone as of 2026-09-07.** Account #1's
subscription key stopped authenticating (`401 — invalid subscription key`)
and `gpt-5.4-mini` was redeployed onto account #2's resource
(`sce-foundry1`), where the embeddings already lived. All three roles now
sit on one resource and one key.

That is a **reduction in isolation**, recorded rather than glossed: a single
expired or rotated key now takes down retrieval *and* generation *and*
ingestion together, where previously an LLM outage still left the corpus
searchable. Nothing in the code assumes the split — `config.py` groups model
settings by role, not by account, which is exactly why moving one role was a
two-line `.env` change — but a production deployment should weigh whether
one blast radius is what it wants.

**1536 dimensions is now a fixed parameter** — every per-project Qdrant
collection created by the ingestion pipeline must be configured with
`vector size = 1536`, distance = Cosine (the standard metric for OpenAI-
family embeddings). Changing the embedding model later means recreating
every collection.

**OCR model choice**: `prebuilt-layout`, not `prebuilt-read`, with
`outputContentFormat=markdown`. Chosen over Read because project documents
(charters, weekly reports, lessons learned) contain tables — Layout extracts
these as structured data plus headings/paragraphs/reading order, versus
Read's flat OCR text. Markdown output gives clean, chunkable structure
(headings/tables/lists) as direct input to the embedding step, and reading
order matters more than usual here given Arabic/English/mixed, RTL content.

**Verified against real documents** (not assumed), with findings that
shaped the chunker (§4.6):

- ✅ **Arabic OCR works well.** A 9-page scanned Arabic PDF with zero
  extractable text returned in ~9 seconds with correct RTL text and intact
  heading structure. Roughly 1 second per page, so the full 58-document
  corpus is only minutes of API time.
- ⚠️ **Text inside images/diagrams is mostly not extracted.** That document
  had 11 `<figure>` blocks. Counted precisely on 2026-08-08: **7 were
  completely empty, 3 held a single stray character** (`\+`, `0`, `?`,
  `C`), **and 1 produced a genuine caption** ("Tickets by Category, 156
  Open Tickets in Period"). So Document Intelligence *can* recover figure
  text — just unreliably, at roughly 1 in 11 here. An earlier version of
  this document said all 11 were empty; that was an overstatement.
  For graphics-heavy decks meaningful content will still be missed —
  worth knowing now rather than discovering it as "why can't the assistant
  answer this?"
- ⚠️ **Tables are emitted as HTML `<table>`, not markdown pipe tables.**
- ⚠️ **Headings are often absent** when the source document used visual
  formatting instead of real heading styles.
- ✅ **Arabic paragraph line-order — RESOLVED 2026-08-08, it does not
  reproduce.** This was flagged as suspected reversal in some of our
  *synthetically generated* `.docx` files. The full corpus run says
  otherwise. Checked two ways rather than by eye alone: structurally, the
  risk registers' `R-01…R-13` identifiers ascend in document order (the
  only descents are a closing **ملاحظات** section that cross-references
  earlier risks in prose, which is correct); and as prose, a
  lessons-learned document reads country → ministry → title →
  `# مقدمة` → intro → `# أولًا: ما سار بشكل جيد` → item 1 → detail →
  recommendation → owner → item 2. That is correct document order.
  Whatever prompted the original observation, Document Intelligence is not
  reversing Arabic paragraphs.

Embeddings and OCR deliberately live on a **different Azure account** than
the LLM — not a deployment-on-the-same-resource setup. Credentials live in
`infra/.env` (gitignored) under `AZURE_OPENAI_LLM_*`,
`AZURE_OPENAI_EMBEDDING_*`, `AZURE_DOCUMENT_INTELLIGENCE_*` — each capability
has its own endpoint/key/version, not shared. Note for whoever writes the
client code: the LLM endpoint is the newer Foundry unified format, not the
classic `*.openai.azure.com` resource format — confirm which SDK/client
convention matches before assuming standard Azure OpenAI SDK examples apply
as-is (the embeddings/OCR account may or may not follow the same
convention — check when those credentials arrive).

### 4.2 Per-project separate Qdrant collections, not a shared collection + RBAC filter

The alternative — one shared Qdrant collection with a per-project payload
filter applied at query time — is the standard pattern once you're
operating at large multi-tenant scale (thousands+ tenants), where
per-collection index overhead actually matters.

We rejected it for this solution because:

1. **BRD AC09 is worded as absolute** ("لا يستطيع أي مستخدم الاطلاع على
   وثائق مشروع غير مخصص له" — no user can access a project's documents
   under any circumstance, not "should not").
2. **The two patterns fail differently.** Shared-collection-plus-filter puts
   isolation in *application code correctness* — every query path (chat
   endpoint, future admin tool, debug script, an endpoint added six months
   from now) must remember to attach the filter; forgetting it once, in one
   place, is a cross-project data leak, and that kind of bug is invisible in
   testing. Separate-collection-per-project puts isolation in
   *infrastructure topology* — a session scoped to project X cannot query
   project Y's vectors because there is no shared index to misfilter.
3. **The scale argument doesn't apply here.** A government ministry's
   project count is realistically dozens-to-low-hundreds, nowhere near the
   scale where per-collection overhead costs anything meaningful. We're
   paying almost nothing for the stronger isolation property.

### 4.2b Row-level security — the same guarantee for SQL

§4.2 made *vector* retrieval structurally isolated. SQL was left
filter-based, which is the identical weakness we rejected for Qdrant:

```sql
SELECT * FROM document_links;   -- before RLS: all 58 rows, 9 projects, no error
```

Migration `013` closes it. Two roles: the pipeline keeps using `spm_admin`
(owner, bypasses RLS, legitimately needs cross-project access), while the
chat application uses a `SELECT`-only `spm_app` role whose policies scope
every read to `current_setting('app.current_project')`.

Three properties worth recording:

- **Fail-closed.** An unset session variable yields `NULL`, and `code = NULL`
  is never true — so forgetting to scope a session returns *nothing*, not
  everything.
- **`SET LOCAL`, never `SET`.** A plain `SET` persists on a pooled
  connection and leaks that scope into the next request — the isolation
  mechanism becoming the leak. `SET LOCAL` resets on commit.
- **Writes are denied at the database.** The BRD's "assistant performs no
  write operations" stops being a convention and becomes enforced.

What RLS does *not* cover: if a session is scoped to the wrong project, it
faithfully serves that project. Validating the requested project against
`project_assignments` at session creation is still application code — but
that's one reviewable gate instead of every query ever written.

Operational detail in `INFRASTRUCTURE.md` §7c.

### 4.3 Redis added for a future async job queue — ✅ now in use

**Status (2026-08-09): this bet paid off.** Redis is the Celery broker and
result backend for the ingestion worker, and the store behind the
cross-process stage locks (§4.11). It sat unused from 2026-08-04 until
2026-08-08, which was the right call — retrofitting a broker into a
finished pipeline would have been considerably more disruptive than having
it waiting.

The original reasoning, kept because it is why the decision was made:

Not yet wired to any application code (none exists yet), but added to the
infra stack proactively: the ingestion pipeline (SharePoint scan → OCR →
embed → index) and any scheduled work will need to run as background jobs
that don't block a request, same reasoning as any producer/consumer queue
pattern. Redis is already running (`mof-redis`) so this doesn't need to be
retrofitted later.

### 4.4 Docker/data hygiene

Everything Docker-related — the engine's own disk and every service's
persistent data — lives under `D:\MOF_Installations`, never C:. See
`INFRASTRUCTURE.md` §2 for exactly how.

**A consequence worth stating: the deployment is reproducible from the
repository, and the corpus is deliberately not part of it.** The Compose
file, the `Dockerfile` and all 21 migrations are tracked, so a clone
rebuilds the stack and reseeds every table — which is what makes moving to
another machine a copy of four things rather than a rebuild
(`MIGRATION.md`). The documents stay outside git, and not only for size:
`TEST-PROJECT` holds 13 real client and ministry documents, and whether
those may leave the tenancy is a decision for Enjaz, not a packaging
convenience. The same reasoning that keeps project material behind the AC09
boundary at query time keeps it out of a remote at rest.

### 4.5 Application tech stack (Python)

| Layer | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** | Async-native — matters because nearly every call the app makes is I/O-bound over the network (Azure OpenAI, Document Intelligence, Qdrant). Built-in Pydantic support, which structured LLM output needs anyway. Not asked as a question — no real alternative fit better. |
| Schema / DB access | **Raw SQL migrations, no ORM** | Considered against SQLAlchemy + Alembic. For a schema this size (projects, users, assignments, document-links — a handful of tables), Alembic's setup/ceremony doesn't pay for itself; raw `.sql` migration files (same pattern the colleague's build proved at this scale) are near-zero setup and fit the additive-only discipline (§4.2-adjacent principle: never `DROP`, only `ADD COLUMN IF NOT EXISTS`-style changes) just as naturally. Revisit if the schema's churn rate ever makes manual tracking error-prone. |
| Task queue (Redis-backed) | **Celery** | Considered against Arq (async-native, lighter, no separate Beat process). Chosen for maturity and richer workflow primitives (chains/chords/retries) — a deliberate call accepting the sync/async bridging cost and the operational gotchas documented at length in the colleague's build (duplicate worker pools, no hot-reload, `worker_process_init` prefork quirks, needs a separate Beat process for scheduling) in exchange for a much larger ecosystem and more production track record. Those known gotchas should be treated as a checklist to avoid repeating, not a reason to doubt the choice. |
| RAG orchestration | ~~**LangChain**~~ → **none; the `openai` SDK directly** | ⚠️ **Planned, then not done — and the reason it was planned stopped applying.** The case for it was that framework-native chains make Langfuse tracing nearly free. By the time the answering path was built, `observability/` already emitted OpenTelemetry spans exported to Langfuse *and* Grafana from one instrumentation, so the tracing argument was already paid for and LangChain would only have sat between us and the exact bytes sent to the model — the thing most worth being able to read when an answer is wrong. It also never covered more than the RAG share of what is now a 4-source composition. `requirements.txt` has no LangChain and no module imports it. |

Exact package versions/pins are a code-time decision, not an architecture
one — not fixed here.

---

### 4.6 Chunking strategy

Implemented in `pipeline/chunker.py`. **Its input contract is markdown, not
"Azure DI output"** — deliberately, so an on-prem OCR that emits markdown
(Docling, marker, PaddleOCR plus a layout post-processor) drops in without
rewriting it. The risk in going on-prem later is concentrated in OCR, not
here.

**The design was corrected by testing against four real OCR'd documents**
rather than reasoning about what the markdown would contain. Three
assumptions were wrong:

| Assumed | Actual |
|---|---|
| Tables are markdown pipe tables | They are **HTML `<table>`** — a pipe-detection rule would never fire |
| Documents have `##` headings to split on | **Two of four had none** — the source `.docx` used visual formatting instead of Word heading styles, which is common in real documents. Heading-only splitting produces one unusable mega-chunk |
| Keeping tables whole is sufficient | A 13-row risk register became a single **3,331-token** chunk, diluting any one risk among twelve others |

Resulting rules:

1. **Fallback ladder**, not one rule: split at headings when present →
   otherwise accumulate structural blocks → split oversized prose on
   sentence boundaries (Arabic `؟` included).
2. **Oversized tables split by rows, header repeated** in each part — the
   header row is what gives every cell beneath it meaning.
3. **Undersized chunks merged forward** (a heading plus one line carries too
   little signal to retrieve well).
4. **Repeated page headers/footers stripped** rather than embedded into
   every chunk, where they would dilute each vector.
5. **A breadcrumb prefixes each chunk's embedded text.** This matters more
   than it appears: `"SAR 4.2M was allocated"` is nearly meaningless as a
   vector, whereas `"Budget Report > Financial Summary > SAR 4.2M was
   allocated"` encodes what the text is about.

**Token counting is isolated behind one function** so swapping to a
HuggingFace tokenizer for an on-prem embedding model is a single change.
Chunk *boundaries* stay valid across embedding models — only the vector
dimension changes, meaning re-embedding but never re-chunking.

**Parameters** (defaults in `pipeline/chunker.py`, overridable per
deployment via `.env` — `CHUNK_TARGET_TOKENS`, `CHUNK_OVERLAP_TOKENS`,
`CHUNK_MIN_TOKENS`, `CHUNK_SPLIT_HEADING_LEVEL`):

| Setting | Default | Why |
|---|---|---|
| Target size | 600 tokens | Large enough for a coherent section, small enough to stay precise |
| Overlap | 80 tokens | Stops a fact straddling a boundary from being lost |
| Minimum | 120 tokens | Below this a chunk carries too little signal to retrieve well |
| Split heading level | 2 (`##`) | Deeper headings stay as breadcrumb context rather than each becoming a chunk |

### 4.7 Qdrant collection naming and chunk payload

**⚠️ Changing the payload schema after indexing means re-indexing the
entire corpus.** Recording it here so that's a deliberate decision rather
than an accident.

**Collection per project:** `project_<code>` — e.g. `project_PRJ-03`,
`project_TEST-PROJECT`. Verified that Qdrant accepts the hyphens present in
project codes. Vector size **1536**, distance **Cosine** (fixed by
`text-embedding-3-small`, §4.1). Configured in `pipeline/config.py`
(`QdrantSettings.collection_for`), not hardcoded at call sites.

**Payload carried on every chunk:**

| Field | Purpose |
|---|---|
| `project_code` | Belt-and-braces for AC09. The collection is already per-project, so this is redundant *by design* — a wrong-collection query still can't pass as correct |
| `document_link_id` | Foreign key back to Postgres for status, path, type |
| `filename`, `doc_type` | Citation display (AC06) |
| `page`, `section` | Precise citation — "Budget Report, page 3, Financial Summary" |
| `chunk_index` | Ordering and de-duplication |
| `char_start`, `char_end` | Offsets into `document_content.markdown` |
| `text` | The chunk itself, so rendering a citation needs no second lookup |
| `has_table` | Lets retrieval or ranking treat tabular chunks differently |

The `char_start`/`char_end` offsets are what enable **parent-document
retrieval** later: retrieve a small precise chunk, then expand to the
surrounding section from `document_content` before handing it to the LLM.
That option exists for free because §`document_content` stores the
markdown. (These offsets were wrong on 8 of 10 chunks in first testing —
see `STATUS_AND_ROADMAP.md` §6.)

**Status (2026-08-08): implemented and verified across the whole corpus.**
678 points across 9 collections, matching `document_links.chunk_count`
exactly per project. All 678 payloads were checked for `project_code`
against their collection: **0 cross-project contamination**. The payload
schema above is now a live contract — changing it means re-indexing
everything.

### 4.8 Stage 0 (discovery) — what the scanner decides, and why

Implemented in `pipeline/scanner.py`. `NEXT_BUILD_PIPELINE.md` §2 specifies
three outcomes per file; the built stage has **five**, and the two extra
ones are the interesting part.

| Outcome | When | Effect |
|---|---|---|
| `INSERT` | no row exists | new row, `pending`, `doc_type='Unclassified'` |
| `BACKFILL` | row exists, `file_hash IS NULL` | fill hash/size/path — the seeded-placeholder case |
| `UNCHANGED` | hash matches, path matches | **zero writes** |
| `PATH_UPDATED` | hash matches, path differs | update path only |
| `MODIFIED` | hash differs | reset to `pending` **and drop the OCR cache** |

**`PATH_UPDATED` exists because `DOCUMENT_ROOT` is a setting.** §4.1's
whole "repointing at real SharePoint is a config change" claim implies the
root will move — and when it does, the corpus arrives as identical bytes
at new paths. Calling that `UNCHANGED` strands every stored `file_path` and
Stage 1 fails to open any of them; calling it `MODIFIED` re-OCRs the entire
corpus for what was only a move, at per-page billing. Neither of the
brief's three outcomes is correct for it.

**The `document_content` deletion is the load-bearing line.** That row's
*presence* is the "OCR done" marker (`INFRASTRUCTURE.md` §8), deliberately
chosen so a failed embed never re-pays for OCR. The consequence is that a
stale row left behind on a changed file makes Stage 1 skip re-OCR — and
change detection, the entire point of migration `012`, silently does
nothing while appearing to work. So `MODIFIED` deletes it. `BACKFILL`
does **not**: a NULL hash means "never scanned", not "content changed", so
there is no evidence the cache is stale.

**A row whose file has vanished is reported, never touched.** The
alternatives were considered and rejected: marking it `failed` conflates
"deleted" with "OCR error" and would show a red badge on the AC10
knowledge-base page for a document that simply no longer exists; adding a
`missing` status means a migration and a new state for the chat backend to
handle, designed against zero observed cases. Deleting the row is the
genuinely dangerous one — one mis-set `DOCUMENT_ROOT` makes every project
look empty, and the cascade would take the paid-for OCR cache with it. The
accepted trade-off is real and worth naming: a document deleted from
SharePoint keeps showing as `indexed` and keeps its vectors until cleanup
is built. That is a visible bug we can fix deliberately, rather than data
loss noticed only afterwards.

**Classification is deferred, not guessed.** A newly discovered file gets
`doc_type='Unclassified'`, and Stage 0 never overwrites `doc_type`,
`language` or `contains_table` on a row that already exists — those were
curated from the manifest and the filesystem cannot know them. Deriving the
type from the filename would have worked on this corpus precisely because
we generated the filenames, and would fail on `client_template.pptx` or
`MOZN Project Charter-V0.1.docx`. That is the static-to-the-demo-corpus
trap this stage exists to avoid.

Verification record — including the mutation testing and the live
change-detection run — is in `STATUS_AND_ROADMAP.md` §9.

### 4.9 Stage 1 (OCR) — why every format goes to Document Intelligence

Implemented in `pipeline/ocr.py`, with `pipeline/native_extract.py` as a
fallback. The obvious objection to paying for OCR on `.docx` and `.pptx` —
their text is sitting in XML, readable for free — was taken seriously and
**measured across 11 files covering every shape in the corpus**. The
measurement reversed the initial recommendation. Numbers in
`STATUS_AND_ROADMAP.md` §10.

| Format | Finding | Decision |
|---|---|---|
| **PDF** | Native extraction is not merely worse, it is **unusable**. Our generated Arabic PDF: **36** Arabic characters natively vs **2,623** from DI, because text comes back as Arabic *presentation forms* in visual order — glyph codes, not words. A real 19-page Arabic BRD shared only **80 tokens** between methods. An English PDF came back with every letter doubled (`CChhaarrtteerr`) | **DI only.** No fallback |
| **DOCX** | Native works, but expands merged cells once per grid position — **48 duplicate cells** on one real Arabic document where DI produced **0**. DI also recovers table-of-contents entries. Decisively: **DI bills a `.docx` as ONE page** regardless of length (a 44,610-character charter = 1 page) | **DI primary** |
| **PPTX** | A genuine tie — **928 of 928 tokens shared**, zero unique to either side. Native is free, instant, and would also capture speaker notes, which DI has no concept of. But only 2 decks exist (~$0.24) | **DI primary**, native revisitable if a real corpus is deck-heavy |

**The cost hypothesis was simply wrong.** Native extraction looked like it
would avoid paying for 64% of the corpus. It wouldn't — because the 60%
that is `.docx` costs about a cent each. Whole corpus: ~307 pages, ~$3.

So: **one extractor, one markdown dialect** — the dialect the chunker was
tuned against (§4.6) — plus the best tables and by far the best Arabic.
A second primary path would have saved roughly $0.60 across the entire
corpus while introducing a merged-cell bug, a crash, and a second markdown
shape for the chunker to handle.

**The fallback, and why it is strictly a fallback.** Document Intelligence
rejected one real file — `SDD_AI_Project_Charter_Generator_OnPrem.docx`,
749 KB — as *"Content is not supported: The input content is corrupted or
format is invalid."* It is not corrupted: `testzip()` is clean,
`word/document.xml` is present, `python-docx` reads 136 paragraphs. Its
archive carries zero-byte directory entries and lacks
`word/theme/theme1.xml`; **Word never writes archives that way**, so it was
repackaged by another tool and Azure's OOXML parser refuses it.

The consequence was not degraded extraction but **none at all** — a
substantive design document contributing nothing to the knowledge base.
So `native_extract` runs **only after DI has already failed**, and only for
Office formats. `document_content.ocr_model` records which extractor
produced every row (`prebuilt-layout` vs `native:python-docx`), so
provenance is a query rather than a guess, and a rescued document bills
zero pages.

**There is deliberately no PDF fallback.** Given the numbers above, a PDF
fallback would emit confident garbage into the vector store with no error
anywhere. An honest failure is better than silent nonsense.

Known limits, recorded rather than smoothed over:

- **Vertical (row-spanning) merges still repeat** in native `.docx`
  output. Horizontal merges are collapsed correctly via `<w:tc>` element
  identity; unpicking vertical ones needs the whole grid. Only affects
  documents DI already rejected.
- **Speaker-note capture is untested against a real deck** — neither deck
  in this corpus has any notes.
- **1 of 58 documents needed the fallback.** That rate says little about a
  real SharePoint, which holds documents from many more tools.

### 4.10 Stages 2+3 — chunk, embed, index. Built as one module

Implemented in `pipeline/indexer.py`. `NEXT_BUILD_PIPELINE.md` describes
chunk+embed and index as two stages; they were built as **one**, and the
reason is what the work costs. Chunking is free and deterministic;
embedding is billed per token. A stage boundary between them ends with 678
vectors that nothing has stored — carried across a process boundary in
memory, or written to a temporary table whose only purpose is to bridge two
modules. **An embedding that isn't persisted is money spent and thrown
away**, so the paid step and the durable write belong in one per-document
transaction.

Four rules, each a *silent wrong answer* if broken — no error, no failed
row, just confidently incorrect retrieval:

| Rule | What breaks without it |
|---|---|
| Point IDs are `uuid5(namespace, "link_id:chunk_index")` | Re-running duplicates every vector instead of upserting. The namespace UUID is fixed and must never be regenerated — changing it orphans the entire index |
| **Delete this document's vectors before writing new ones** | Upserting alone overwrites matching indices but leaves surplus points behind when an edited document yields *fewer* chunks. Those orphans stay retrievable, answering current questions with deleted content |
| Embed `chunk.text`, not `chunk.body` | The breadcrumb is what makes a vector mean anything (§4.6). The payload still carries `body`, so citations render cleanly |
| Re-sort embedding results by the API's own `index` | Trusting arrival order pairs every vector with the wrong payload. Nothing anywhere reports it |

**Failed documents are retried by default — the opposite of Stage 1.** That
is cost asymmetry, not inconsistency: re-OCRing a broken file re-bills per
page, while re-embedding the entire corpus costs under a cent. When
retrying is nearly free, hiding failures serves nobody.

**Verified end to end (2026-08-08):** 58/58 documents, 678 chunks, 295,232
tokens, 9 collections, Qdrant point counts matching Postgres on every
project, 0 cross-project contamination, and a re-run reporting 0
candidates. Retrieval works: *"ما هي أعلى المخاطر المسجلة في المشروع؟"*
against `project_PRJ-03` returns that project's risk register as both top
hits.

**A hypothesis that was tested and rejected — recorded so it isn't
retried.** Arabic queries were returning raw `<table>` markup at the top,
so the obvious theory was that HTML markup dilutes the vectors. The premise
measured true: 51% of chunks contain tables, and a median **26%** of their
tokens are pure markup (max 67%). A single-query A/B even moved the correct
chunk from rank 3 to rank 2.

Testing the *mechanism* across 5 queries and 75 samples killed it. Chunks
with <5% markup lose **0.027** similarity when stripped; chunks with ≥25%
markup lose **0.029**. A 0.002 difference — markup is not selectively
inflating table chunks, stripping simply lowers everything slightly. **No
change was made.** Acting on the single-query result would have altered the
payload contract, forcing a full re-index, on the strength of an anecdote.

What *is* real: Arabic queries score lower than English (~0.40 vs ~0.60)
and the correct chunk often ranks 2nd–3rd rather than 1st. That is a
**ranking** problem for the chat backend — reranking, hybrid search, or
more retrieved candidates — not an ingestion defect.

### 4.11 The pipeline as a background worker (2026-08-08)

Until this point the three stages were commands a human typed, which is
fine for building them and useless for operating them: dropping a file into
a project folder did nothing until somebody remembered to run the scanner.
Redis had been running unused since the infrastructure was built, for
exactly this (§4.3).

**A containerised Celery worker**, not a host process. The obvious reason
is that it matches every other service — `restart: unless-stopped`, data on
D:, survives a reboot. The less obvious one decided it: **Celery's default
prefork pool does not work on Windows**, so a host worker is stuck on
`--pool=solo` with no concurrency at all. In a Linux container the problem
does not exist (verified: the worker reports `concurrency: 2 (prefork)`).

**One task per stage, not one per document.** The coarse granularity is
safe *because* the stages are idempotent — a retried OCR task over a
finished corpus calls Azure zero times, so Celery's retry costs nothing
rather than re-billing. Per-document fan-out would buy parallelism a
58-document corpus does not need, at the price of moving per-document
commit logic into the task layer.

**Locking is the genuinely new failure mode.** Idempotence protects against
running a stage again *after* it finished; it says nothing about two runs
*at the same time*, which is precisely what a schedule firing while the
previous run is still going produces. Both would see the same document as
un-OCR'd, both would call Document Intelligence, and both would be billed —
Postgres serialises the row writes afterwards, but the money is already
spent. So `pipeline/locking.py` takes a Redis lock per stage, and:

- it is taken by the **entry points** (each `main()` and each task), never
  inside `run()` — which both call, and where a non-reentrant lock would
  deadlock the worker against itself;
- a busy lock **skips** rather than queues, because by the time a waiting
  run started, its work would already have been done;
- it carries a TTL so a killed worker cannot deadlock the pipeline, and an
  owner token so a run that overran its TTL cannot delete the lock that
  legitimately replaced it;
- **the CLI takes it too.** A lock only the worker respected would not stop
  the collision most likely to happen: a developer running
  `python -m pipeline.ocr` while the schedule fires. Verified — the CLI
  exits 2 with the holder named. Dry runs are exempt: they write nothing.

`ingest` runs the stages **inline and in order**, not as a Celery chain. A
chain dispatches and returns immediately, so the caller receives a nested
graph of task ids instead of an outcome — meaning a scheduled ingest could
not report whether it worked. It now returns
`{"ok": …, "stages": [{…}, {…}, {…}]}`, and a failing stage does not stop
the ones after it.

**`document_links.file_path` is stored relative to `DOCUMENT_ROOT`.** This
was found by containerising: the host wrote `D:\MOF_DataSample\...` and the
worker wrote `/data/...`, so each rewrote all 58 rows on every run and
Stage 1 could not open a file the other environment had recorded. A
relative path is valid in both. Two consequences worth recording:

- `PATH_UPDATED` can now only fire for a row written **before** this fix,
  because `file_path` equals `<project>/<filename>` — which is the row's
  own unique key. It is a migration path, not a live case.
- The fix depended on a decision made much earlier: `PATH_UPDATED`
  deliberately does **not** reset `indexing_status`. Had a moved file been
  treated as MODIFIED, containerising would have silently triggered a full
  corpus re-OCR.

### 4.12 The conversational layer — the model is the router (2026-08-17)

**Every user message goes to the model, which detects the language,
classifies the intent, and answers — in one call.** There is no phrase
table, no script counter and no threshold deciding what a message is.

⚠️ **This reverses an earlier design, deliberately, and the trade is
recorded rather than buried.** Until 2026-08-17 language was decided by
counting Unicode scripts and intent by matching a phrase file: both free,
both instant, both structural. The requirement changed to "everything goes
to the LLM, nothing hard-coded", and the consequences were stated before
the work began: a greeting now costs a completion (~2 s and a fraction of a
cent) and language rejection stops being a guarantee and becomes a
behaviour. What is bought is that exactly one artefact decides how a
message is treated, and it is prose a non-programmer can read and amend.

**One call, not two — and that is a latency decision, not a style one.**
Retrieval runs first for every message, so the model receives the passages
at the same moment it is asked what the message is. Classify-then-answer
would be two round trips and would roughly double a turn against the AC08
five-second budget. Retrieving on a greeting costs one embedding; that is
cheaper than the second completion it avoids on every real question.

**What did NOT move into the prompt, and why.** AC06's guarantees are
structural and stayed structural: the response schema still has no field
for a filename, so a fabricated source cannot be expressed; citations are
still rebuilt from the retrieved payloads; and the relevance floor still
decides which passages are *sent*. The floor no longer decides whether to
call the model at all — it cannot, once a greeting must also be answered.

⚠️ **Rules that live in a prompt are easy to delete by accident.** The
grounding rule, the both-sides rule, the injection fence, the
no-other-project rule and the read-only rule are therefore pinned by tests
that fail if the text goes missing. That is the compensating control for
moving policy out of code.

**Identity is the other half of AC09, and it now exists.** The gate always
answered *"may this user open this project?"* and never *"is this really
that user?"*. `api/credentials.py` and `api/cookies.py` answer the second
question for the demo — email and password against seeded accounts, then an
HMAC-signed session cookie. It is a *simulation* of sign-in: real AD SSO
and OTP replace those two modules and one branch of `identity.py`, and
nothing above them changes.

### 4.13 Observability — one instrumentation, two backends (2026-08-16)

**OpenTelemetry is the only instrumentation API, and both backends receive
the same spans.** Langfuse v3 exposes an OTLP endpoint, so LLM tracing
needs no second SDK. The alternative — the Langfuse Python SDK alongside
an OTel setup — means two sets of instrumentation calls over the same code
path, and the one that drifts is always the one nobody looks at.

They are kept because they answer different questions, not out of
duplication: **Grafana** shows where the time went across a request;
**Langfuse** shows what the model was actually asked and answered, with a
conversation's turns grouped into one session.

**Tracing is fail-open, which contradicts every other rule in this
document, deliberately.** Access control fails closed because a wrong
answer about permission is a breach. Tracing fails open because a wrong
answer about *observability* is a blind spot, and a blind spot is cheaper
than an outage. An unreachable collector must never stop a project manager
getting an answer.

⚠️ **A trace store is an AC09 surface — and there are two of them.** With
content capture on, every step records its input and output: the question
and answer, the passages retrieval found, the project record, the prompt.
Neither Langfuse nor Grafana has any notion of project assignment, and the
one instrumentation sends identical attributes to both, so anyone who can
read *either* can read every project. `LANGFUSE_CAPTURE_CONTENT` governs
both despite its name, and is a deployment decision, not a preference:
acceptable against containers on the same machine holding the same corpus,
not acceptable against a shared or hosted store. Disabling it keeps timing
and token counts.

The design intent was that Grafana answers *where the time went* and
Langfuse *what the model saw*. The code does not yet enforce that split:
stripping content attributes from the Grafana exporter alone would make it
true, and is an open decision (`STATUS_AND_ROADMAP.md` §3).

**What each step records.** Every step of a turn records its input and
output when content capture is on: the question and answer on the turn
itself (trace level, so the trace list reads as questions rather than
`null`), the passages retrieval found with their scores, the embedding's
question, the project record, and the full prompt. All of it passes through
one helper that takes `capture_content` as a required argument, so a new
step cannot record content while ignoring the switch.

**AC08 is a percentile claim.** "Does not exceed 5 seconds under normal
conditions" is about the tail, so `evaluation/latency_bench.py` reports
p50/p95/max and judges on p95 — a mean hides the turns that break it, and
a max would fail a run on one slow upstream response. Measured, the LLM
call is ~80% of a turn; retrieval and SPM together are under 400 ms. The
remaining lever on latency is therefore the model, not our code.

### 4.14 Every component is a container, the app included (2026-09-14)

The chat application ran on the Windows host, started by hand, until it
stopped with the session that had started it while every container kept
running — the one component outside Docker was the one that failed. It is
now the `chat-api` service in the same Compose file, with
`restart: unless-stopped` and a `/health` healthcheck.

- **One Dockerfile, two targets on a shared base.** `pipeline` and `app`
  install the same dependencies once, but each copies only its own code, so
  rebuilding the app does not recreate the pipeline containers. Every
  service names its target — one that does not silently builds the *last*
  stage.
- **Bound to `127.0.0.1`.** The demo sign-in shares one documented password;
  publishing the port on every interface would open it to the network.
- **Dependencies by service name, not localhost.** `.env` holds localhost
  values for host runs; inside a container localhost is the container.
- **No startup dependency on tracing.** Tracing fails open (§4.13); waiting
  on Langfuse or Grafana to start would let a tracing outage stop the app.
- **Secrets never enter an image**, and neither image runs as root.

⚠️ **Containers run the code in their image, not the repository.** A code
change does nothing until the image is rebuilt — the classify stage, added
2026-09-07, was skipped by every scheduled run until 2026-09-14. The stack
also needs **8 GB** for its VM: on 4 GB it restarted itself every few
minutes while containers still reported "Up" (`INFRASTRUCTURE.md` §4.4d).

These decisions are pinned by `tests/test_deployment.py`, which reads the
real Compose file and Dockerfile.

### 4.15 The interface renders decisions; it never makes them

The UI follows the MOF design screens (DIN Next LT Arabic, `#005459`, the
ministry logo) in one static file with no build step. The principles that
survived the redesign matter more than its look:

- **Identity is the server's.** No request carries a username; a signed,
  HttpOnly cookie does.
- **Server text is text.** Every answer, citation and file name is inserted
  as text, never markup, and a test checks that `innerHTML` only ever
  receives a constant. Icons are CSS masks so no markup is built at runtime.
- **Nothing on screen is invented.** The design shows counts on unbuilt
  agents and a notification dot; with no data behind them they are omitted,
  and the agents are marked "قريباً". A failed document shows its status but
  no retry, because the app's database role is read-only.
- **Browser memory orders; the server decides.** "Recently used" projects
  and the resumed selection after a refresh live in the browser, tied to the
  signed-in username. They decide what is listed first and where the page
  resumes — never what may be opened, which the server authorises on every
  request.
- **Sources are named, once each, without noise.** Chips are built from the
  citation's own fields, deduplicated, and the SPM record's date is not
  printed beside its chip.

The model-facing wording follows the same honesty rule. The welcome states
only limits the system enforces, with the mapping written beside the text;
the overview is one paragraph; and the Arabic status is **supplied in the
record** rather than translated by the model — asked to translate, it wrote
"at-risk" as "on track".

### 4.16 The pipeline's health is judged by its runs (2026-09-14)

`pipeline.status` once judged the schedule by the newest indexed document,
so an unchanged corpus, processed correctly every day, warned that nothing
had been indexed for weeks. Each full-corpus run now records its outcome in
Redis — last attempt and last success, kept apart — and the check reports an
**error** when the latest run failed and a **warning** when nothing has
succeeded for 26 hours. The knowledge-base page shows the same fact as
"آخر مزامنة للبيانات".

A run fired while services are still starting — which beat does on purpose
when it missed a tick while Docker was off — retries a stage that cannot
*connect*, at 10, 20 and 40 seconds. Connection failures only, identified
by SQLSTATE: a cancelled query or a deadlock is an `OperationalError` too,
and must not be retried as though the server were away.

### 4.17 Streaming is one code path with a callback (2026-11-08)

Answers appear as the model writes them. The design constraint was that
this must **not** become a second way of answering a question: a streamed
turn that behaved differently from the measured one would make every
measurement a claim about something users do not use, and would give AC09
two routes to be right about instead of one.

So `answer_question` takes an optional `on_text` callback, and that is the
entire difference. The callback switches the completion to a streamed one;
the raw JSON is still accumulated, still parsed by `parse_response`, and
still produces the same `Answer`. `handle_question` and `Conversation.ask`
already passed unknown keyword arguments through, so nothing between them
changed. The HTTP route runs the same `Conversation.ask` on a worker
thread — inside a copy of the request's context, so the turn's spans stay
attached to the request's trace — and relays the pieces as Server-Sent
Events.

**Why the answer can be read before the document is complete.** The model
returns one strict-schema JSON object, and `RESPONSE_SCHEMA` lists
`language` and `intent` *before* `answer`: the classification is settled
before the first character of text, and `used_passages` — which every
citation is rebuilt from — arrives after the text is finished. So the
order on screen is text first, citations at the end, which is also the
order a reader wants. No prompt or schema change was needed, and the
router evaluation did not have to be re-run.

⚠️ **The incremental reader is a preview, never an authority.** Nothing it
produces decides anything: the complete JSON is parsed at the end, and
that parse owns the citations, the language and the intent. If the two
ever disagreed, the parse wins — it is the one that validates.

⚠️ **Streaming does not make a slow answer fast, and AC08 still budgets
the whole turn.** Time to first text is reported beside the total, never
instead of it. Judging the requirement on the first number would be
redefining it to make it pass. See `STATUS_AND_ROADMAP.md` §29b, including
why the failure this work was meant to fix did not reproduce.

### 4.18 Follow-up suggestions are bounded, not trusted (2026-11-08)

Each answer may carry up to three questions to ask next, written by the
model in the language of the reply. `follow_ups` sits **last** in the
response schema: a strict schema is generated in order, so anything before
`answer` delays the first character the reader sees, and these are the
least urgent part of the reply.

They are the first free-text field besides the answer itself, which makes
them a boundary rather than a feature. What reaches the screen is bounded
in code — at most three, de-duplicated, newlines collapsed, trimmed rather
than dropped, and a malformed field yields none instead of raising.

⚠️ **A refusal carries none**, by the same rule that strips its citations:
a suggestion under "that is outside this project" invites the user
straight back into what was just declined.

⚠️ **They are not a privileged channel.** A click sends the text as an
ordinary message, through the same gate and the same classification as
anything typed, and the label is inserted as text. So the worst a poisoned
suggestion can do is propose a question the assistant then answers
normally or refuses — it cannot widen a scope that was fixed before
retrieval ran.

Widening the response schema is the thing
`test_the_schema_makes_a_fabricated_source_structurally_impossible` exists
to prevent, and it was updated deliberately rather than worked around.
`follow_ups` is admissible because it carries no source metadata. A field
naming a filename, page or section would not be.

---

## 5. Relationship to the colleague's RiskIntel platform

A colleague (Toni) built a separate solution against the **same unified
BRD**, but for a **different use case** — UC3, project risk prediction —
on an Azure VM (WSL2/Ubuntu, no GPU), deliberately designed as a shared
platform meant to also support UC1 (this chatbot) and UC2 without new
infrastructure. Docs: `C:\Users\abood\Desktop\MOF\Risks_Prediction\`
(external to this repo; start at `START_HERE.md` there).

**Where we independently converged** (validates the choices, not copied):
Langfuse v3 self-hosted, Grafana LGTM all-in-one, Qdrant, Portainer, and
splitting the app's own Postgres from Langfuse's internal Postgres.

**Where we deliberately diverged, and why:**
- **Model hosting**: his LLM/embeddings are genuinely on-prem (Ollama
  `qwen2.5:3b` CPU-only, BGE-M3 embeddings) — his OCR is **not** actually
  built (mentioned once as unbuilt future work, not a working reference).
  We reconfirmed cloud models for this phase after seeing his approach
  (§4.1) — his VM has no GPU; ours does, but the decision stands regardless.
- **Vector KB isolation pattern**: his is one shared Qdrant collection +
  per-project RBAC payload filter (and he flags that filtered version as
  still unbuilt even in his own reference implementation). We use separate
  collections per project — see §4.2 for the full reasoning.

Do not re-litigate either of these without re-reading this section first —
both were decided with full knowledge of the alternative, not by default.
