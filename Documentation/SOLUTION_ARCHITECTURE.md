# Solution Architecture — MOF Enjaz Chatbot

**Last verified:** 2026-08-04

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

## 3. The retrieval architecture (three sources per query)

Designed by the user before any implementation existed; this is the
governing shape for the whole backend.

### 3.1 Ingestion pipeline (offline / background)

Each SharePoint project folder (project charter, weekly reports, lessons
learned, etc.) is OCR'd, embedded, and indexed. **Each project gets its own
separate Qdrant collection** — not one shared collection with a
project-ID metadata filter. See §4.2 for why.

### 3.2 Session flow

```
AD login (SSO + OTP)
  → system fetches the user's project list from SPM
  → user must select exactly one project
  → session is locked to that project for its duration
  → user converses (multi-turn, no restart needed — BRD AC03)
```

### 3.3 Three-way answer composition per query

| # | Source | What it is | Persisted? |
|---|---|---|---|
| 1 | **RAG** | Vector search against the selected project's dedicated Qdrant collection (the ingested SharePoint files) | Yes — the collection itself |
| 2 | **Live SPM entity data** | Structured project fields pulled directly by Project ID from SPM (status, dates, metadata — not files). Demo: filled directly into `postgres-spm`, no live SPM API yet | Yes — Postgres |
| 3 | **CAG** | Documents uploaded mid-chat through the UI get OCR'd and injected directly into the current session's LLM context | **No** — session-scoped only, matches BRD scope item 5 ("supplementary upload," not permanent ingestion) |

**Resolved:**
- ~~What triggers re-ingestion / index refresh when files change~~ —
  **answered 2026-08-05 by content hashing.** `document_links.file_hash`
  (SHA-256, plus `file_size_bytes` as a cheap pre-check) lets the
  scanner distinguish new / unchanged / modified files, so re-ingestion
  is triggered by a hash mismatch. Chosen over filesystem modified-time,
  which is only a hint — copying resets it and SharePoint sync may not
  preserve it, and stale content producing confidently wrong answers is
  worse than a slower scan. Migration `012`.

**Open questions, not yet resolved:**
- Source arbitration when RAG-indexed files and live SPM data disagree or
  overlap (e.g. project status appears in both a weekly report and SPM's
  status field).

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
| LLM | Azure OpenAI `gpt-5.4-mini`, via an Azure AI Foundry unified endpoint (`*.services.ai.azure.com`) | Account #1 | ✅ Provisioned, credentials verified live (Arabic + English) |
| Embeddings | Azure OpenAI `text-embedding-3-small` (deployment name saved truncated as `text-embedding-3-S`, cosmetic only) | Account #2 (separate from LLM) | ✅ Provisioned, verified live — **1536-dimension vectors** |
| OCR | Azure AI Document Intelligence (resource `DI-Enjaz`, East US, Standard tier) | Account #2 (separate from LLM) | ✅ Provisioned, verified live (`prebuilt-read`, `prebuilt-layout` models confirmed available) |

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
  had 11 `<figure>` blocks. Counted precisely on 2026-08-06: **7 were
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
- ✅ **Arabic paragraph line-order — RESOLVED 2026-08-06, it does not
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

### 4.3 Redis added for a future async job queue

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

### 4.5 Application tech stack (Python)

| Layer | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** | Async-native — matters because nearly every call the app makes is I/O-bound over the network (Azure OpenAI, Document Intelligence, Qdrant). Built-in Pydantic support, which structured LLM output needs anyway. Not asked as a question — no real alternative fit better. |
| Schema / DB access | **Raw SQL migrations, no ORM** | Considered against SQLAlchemy + Alembic. For a schema this size (projects, users, assignments, document-links — a handful of tables), Alembic's setup/ceremony doesn't pay for itself; raw `.sql` migration files (same pattern the colleague's build proved at this scale) are near-zero setup and fit the additive-only discipline (§4.2-adjacent principle: never `DROP`, only `ADD COLUMN IF NOT EXISTS`-style changes) just as naturally. Revisit if the schema's churn rate ever makes manual tracking error-prone. |
| Task queue (Redis-backed) | **Celery** | Considered against Arq (async-native, lighter, no separate Beat process). Chosen for maturity and richer workflow primitives (chains/chords/retries) — a deliberate call accepting the sync/async bridging cost and the operational gotchas documented at length in the colleague's build (duplicate worker pools, no hot-reload, `worker_process_init` prefork quirks, needs a separate Beat process for scheduling) in exchange for a much larger ecosystem and more production track record. Those known gotchas should be treated as a checklist to avoid repeating, not a reason to doubt the choice. |
| RAG orchestration | **LangChain** | The colleague's own conclusion after building both ways: framework-native chains make Langfuse tracing integration nearly free (`config=` kwarg per call vs. hand-written spans), and we already have Langfuse running and want traces from day one. Accepted trade-off: LangChain only really covers the RAG third of our 3-source composition (SPM lookups and CAG context-stuffing are hand-written regardless), and the framework has real version-churn history (the colleague hit a breaking v3→v4 callback API rename) to watch for. |

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

**Known-open, deliberately:** only 4 of 58 documents have been exercised
(`.pptx` and the real English charters are untested), there are no unit
tests yet, and the true measure — whether the right chunk comes back for a
real question — cannot be assessed until retrieval runs. Chunking quality
is empirical; storing the markdown (§ `document_content`) is what makes
iterating on it free.

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
