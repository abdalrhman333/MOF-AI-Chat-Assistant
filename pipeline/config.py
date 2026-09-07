"""Central configuration, loaded from infra/.env.

Everything the pipeline needs to know about *this* deployment lives here
and nowhere else. No module should read an environment variable directly
or hardcode a path, deployment name, collection prefix, or chunk size —
otherwise switching environments (or swapping a model provider) means
hunting literals through the codebase.

Two consequences worth stating:

  * Nothing here is specific to the demo corpus. DOCUMENT_ROOT is a
    setting, so pointing the pipeline at a real SharePoint export (or a
    different machine's path) is a config change, not a code change.
  * The model settings are grouped by ROLE (ocr / embedding / llm), not by
    vendor. Moving embeddings on-prem later means changing this file, not
    the chunker or the indexer.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

from pipeline import chunker

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / "infra" / ".env"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set in {ENV_PATH}. "
            f"Copy infra/.env.example to infra/.env and fill it in."
        )
    return value


def _optional(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class PostgresSettings:
    host: str
    port: int
    database: str
    user: str
    password: str

    @property
    def dsn(self) -> str:
        return (f"postgresql://{self.user}:{self.password}"
                f"@{self.host}:{self.port}/{self.database}")


@dataclass(frozen=True)
class QdrantSettings:
    url: str
    api_key: str
    # One collection per project — the isolation boundary described in
    # SOLUTION_ARCHITECTURE.md 4.2. Verified that Qdrant accepts the
    # hyphens present in project codes.
    collection_prefix: str = "project_"
    vector_size: int = 1536      # text-embedding-3-small
    distance: str = "Cosine"     # standard for OpenAI-family embeddings

    def collection_for(self, project_code: str) -> str:
        return f"{self.collection_prefix}{project_code}"


@dataclass(frozen=True)
class AzureModelSettings:
    """Grouped by role, not vendor, so a provider swap is contained here."""
    ocr_endpoint: str
    ocr_key: str
    ocr_model: str
    embedding_endpoint: str
    embedding_key: str
    embedding_api_version: str
    embedding_deployment: str
    llm_endpoint: str
    llm_key: str
    llm_api_version: str
    llm_deployment: str


@dataclass(frozen=True)
class ScannerSettings:
    """Stage 0 (discovery) parameters.

    `ignore_patterns` exists because a real SharePoint sync folder is not
    just documents. Word writes `~$name.docx` lock files while a document
    is open, Windows drops `Thumbs.db`/`desktop.ini`, and sync clients
    leave dotfiles. Without this the scanner would faithfully insert them
    as documents and Stage 1 would pay Document Intelligence to OCR a lock
    file.

    `default_doc_type` is what a NEWLY discovered file gets. The filesystem
    genuinely does not know what kind of document it is holding —
    classification belongs after OCR, where the text can actually be read.
    Guessing from the filename would work only because we generated the
    fictional filenames ourselves, which is exactly the demo-corpus
    coupling this pipeline must not have.
    """
    default_doc_type: str = "Unclassified"
    ignore_patterns: tuple[str, ...] = ("~$*", ".*", "Thumbs.db", "desktop.ini")


@dataclass(frozen=True)
class OcrSettings:
    """Stage 1 (OCR) parameters.

    `thin_result_chars` is a WARNING threshold, never a failure condition.
    A graphics-heavy deck or a scan whose content lives inside images will
    always come back thin — measured, not assumed: one real 9-page scanned
    PDF returned 11 <figure> blocks of which 10 held nothing but a stray
    character. Marking that 'failed' would re-pay Document Intelligence for
    the same document on every future run, forever, for a result that
    cannot improve. So it is recorded, flagged in the run summary, and left
    for a human to judge.

    `retry_failed_by_default` is False for the same reason pointed the
    other way: a document that genuinely errors should not silently
    re-bill on every run. Retrying is explicit (`--retry-failed`).
    """
    thin_result_chars: int = 200
    retry_failed_by_default: bool = False
    # `raw_response` holds the provider's full JSON, including word-level
    # geometry, and nothing caps it. Measured on this corpus: 177 kB
    # average, 1.3 MB largest (a 20-page document) — about 65 kB per page,
    # so a document would need roughly 4,000 pages to threaten Postgres's
    # JSONB limit. It is stored whole rather than trimmed because migration
    # 010's entire purpose is to avoid ever re-paying for OCR, and the
    # geometry is the part markdown throws away. This threshold does not
    # truncate anything; it only makes unusual growth visible in the run
    # summary instead of being discovered as a disk-space surprise.
    raw_response_warn_bytes: int = 5_000_000


@dataclass(frozen=True)
class IndexingSettings:
    """Stage 2+3 (chunk -> embed -> index) parameters.

    Embedding requests are batched two ways at once, because either limit
    can bite first: `batch_size` caps how many chunks go in one request,
    `max_batch_tokens` caps their combined size. The corpus median chunk is
    ~435 tokens, so 32 chunks is ~14k tokens — comfortably inside both.

    `retry_failed_by_default` is True here, and deliberately the OPPOSITE
    of the OCR stage's setting. The reasoning is cost asymmetry, not
    inconsistency: re-OCRing a broken file re-bills per page, whereas
    re-embedding the entire 58-document corpus costs under a cent. When
    retrying is nearly free, hiding failures serves nobody.
    """
    batch_size: int = 32
    max_batch_tokens: int = 16_000
    retry_failed_by_default: bool = True


@dataclass(frozen=True)
class CelerySettings:
    """Background-job settings for the ingestion worker.

    `lock_timeout_seconds` is a safety net, not a schedule. Idempotence
    protects against re-running a stage *sequentially* — it does nothing
    about two workers running the same stage *concurrently*, which is
    exactly what a scheduled trigger firing while the previous run is still
    going would produce: two OCR calls for the same document, billed twice,
    racing on the same row. The lock is what makes the schedule safe.

    The timeout must exceed the longest plausible run (the full corpus OCR
    took ~5 minutes) or a slow run releases its own lock mid-flight.

    **The schedule is a daily wall-clock time, and the timezone is part of
    it.** Containers run on UTC, so `hour=13` alone would fire at 13:00 UTC
    — 16:00 in a UTC+3 deployment, three hours from what anyone asking for
    "1 PM" meant. Naming the zone is what makes the setting say what it
    means, and it survives daylight-saving changes that a fixed offset
    would not.
    """
    broker_url: str
    result_backend: str
    lock_timeout_seconds: int = 3600
    schedule_hour: int = 13
    schedule_minute: int = 0
    schedule_timezone: str = "Asia/Amman"


@dataclass(frozen=True)
class ApplicationSettings:
    """Chat application settings — the connection pool behind `spm_app`.

    The pipeline needs none of this: it opens one connection per run and
    closes it. The application serves concurrent requests, so it pools —
    and pooling is exactly where `SET LOCAL` stops being a style preference
    and becomes the isolation boundary. A plain `SET` on a pooled
    connection persists onto whoever borrows it next.

    `pool_max_size` bounds concurrent database work, not concurrent users;
    requests queue for up to `pool_timeout_seconds` and then fail rather
    than hang forever, because a request that waits indefinitely for a
    connection has already blown the AC08 five-second budget.
    """
    pool_min_size: int = 1
    pool_max_size: int = 8
    pool_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class AnswerSettings:
    """Grounded answering — retrieval depth, context budget, and the LLM call.

    `top_k` is how many passages are retrieved; `max_context_tokens` is the
    ceiling on what is actually sent. Both exist because they bound
    different things: retrieving more costs nothing extra, while *sending*
    more costs tokens and seconds against the AC08 five-second budget.

    `min_score` is the floor below which the LLM is not called at all. A
    question whose best match is noise should produce an honest "not in the
    documents" rather than a paid call inviting the model to improvise.
    Measured range on this corpus is roughly 0.31–0.56, so the default sits
    below every genuine hit while still catching nothing-matched.

    `request_timeout_seconds` is a hard ceiling, not a target. AC08 asks for
    five seconds under *normal* conditions; this stops an abnormal request
    hanging indefinitely. Tightening it toward the budget belongs with the
    real latency work, once there are measurements to tighten against.
    """
    top_k: int = 8
    max_context_tokens: int = 6000
    max_completion_tokens: int = 2000
    temperature: float = 0.0
    min_score: float = 0.25
    request_timeout_seconds: float = 20.0


@dataclass(frozen=True)
class LanguageSettings:
    """AC01 — which input languages are served, and how sure we must be.

    Detection is **script-first**, because script is decisive and free: a
    question written in Han, Cyrillic or Devanagari is unsupported no matter
    what any statistical model thinks, and deciding that by counting
    codepoints costs nothing against the AC08 budget.

    `mixed_min_share` is the point at which a second script stops being
    incidental and makes the question genuinely bilingual. It is a *share of
    letters*, not a count, so it behaves the same on a five-word question
    and a paragraph. Set it too low and every Arabic question containing a
    project code becomes "mixed"; too high and a real code-switched question
    is reported as monolingual, which degrades AC05's reply-in-kind.

    `latin_discriminator_min_confidence` is the honest part. Latin script
    alone cannot separate English from French or Spanish, so a statistical
    identifier runs on Latin-dominant text — and a **non-English verdict is
    acted on only when the identifier is confident**. Below this, the text
    is accepted, because the failure we must not make is rejecting a
    legitimate English question. Rejection requires evidence; acceptance
    does not.

    **The threshold was measured, not chosen.** A live run rejected nothing
    for *"Quel est le budget?"* because an earlier version gated on string
    length instead, and at 19 characters it fell under a 20-character floor.
    Length turned out to be a poor proxy: the identifier is reliable on
    short text (10/10 non-English strings down to 11 characters), and its
    one false positive on real English — *"When does it end?"* → German —
    comes back at **0.473**, while genuine non-English sentences come back
    at **0.997–1.000**. Confidence separates what length could not, and it
    measures the thing the rule is actually about.

    Fragments below the threshold (*"le budget?"*, *"das Budget?"*) are
    still accepted. That is the asymmetry working as intended, not a gap to
    close by lowering it — the first English question it turns away costs
    more than every French fragment it lets through.
    """
    supported: tuple[str, ...] = ("ar", "en")
    mixed_min_share: float = 0.20
    latin_discriminator_enabled: bool = True
    latin_discriminator_min_confidence: float = 0.90


@dataclass(frozen=True)
class ApiSettings:
    """The HTTP layer: who the caller is, and how long a chat is kept.

    ⚠️ **`auth_mode` is the AC09 boundary.** The gate answers *"may this
    user open this project?"* and never *"is this really that user?"*, so
    whatever establishes the username becomes part of that guarantee.

    `dev_fixed` (the default) takes the identity from configuration: every
    request is the same signed-in user, which cannot be spoofed by a client
    and does not pretend to be authentication. `trusted_header` reads it
    from a request header and is correct **only** behind a proxy that
    authenticates the user and overwrites that header inbound.

    A username is never read from a request body or query string in either
    mode. That is not a setting.

    `conversation_idle_minutes` bounds the in-memory chat store. Sessions
    are held server-side, so an unbounded store is a slow memory leak and a
    growing pile of transcripts nobody will read.
    """
    host: str = "127.0.0.1"
    port: int = 8000
    auth_mode: str = "session_login"
    auth_header: str = "X-Remote-User"
    dev_username: str = ""
    # HMAC key for the session cookie. **No default on purpose**: a
    # hard-coded fallback would mean every deployment of this source shares
    # a signing key, and anyone with the code could mint a session for any
    # username. Missing means sign-in refuses rather than signs with
    # something convenient.
    session_secret: str = ""
    session_hours: int = 8
    # `Secure` keeps the cookie off plain HTTP. False by default because the
    # demo runs on http://127.0.0.1 and a Secure cookie would simply never
    # be sent — a login that silently never works. Must be true behind TLS.
    cookie_secure: bool = False
    # `redis` survives a restart and allows more than one worker; `memory`
    # is for tests and for running without Redis. Redis is the default
    # because the in-memory one silently makes the API single-process, and
    # a limitation you only meet under load is worse than one you configure.
    conversation_store: str = "redis"
    conversation_idle_minutes: int = 60
    max_conversations: int = 500
    max_question_chars: int = 2000
    cors_origins: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservabilitySettings:
    """Tracing — one instrumentation, two backends.

    **OpenTelemetry is the only instrumentation API used**, and both
    backends receive the same spans. Langfuse v3 exposes an OTLP endpoint
    (`/api/public/otel/v1/traces`, verified against this deployment before
    anything was built on it), so LLM tracing needs no second SDK and no
    second set of instrumentation calls that could drift apart. Grafana's
    LGTM container accepts OTLP directly.

    Each backend is separately switchable because they answer different
    questions: Grafana shows *where the time went* across a request, and
    Langfuse shows *what the model was actually asked and answered*.

    ⚠️ **`capture_content` controls whether prompts and completions leave
    the process.** A trace with content in it contains project document
    passages, and a trace store has no notion of AC09 — anyone who can read
    Langfuse can read every project's passages regardless of assignment.
    That is acceptable here because Langfuse is a container on the same
    machine holding the same corpus. **It would not be acceptable against a
    shared or hosted trace store**, and turning this off leaves timing and
    token counts intact, which is most of the operational value.

    Tracing is **fail-open**, the opposite of every access rule in this
    project, and deliberately: an unreachable collector must never stop a
    project manager getting an answer.
    """
    enabled: bool = False
    service_name: str = "enjaz-chat"
    environment: str = "dev"
    otlp_enabled: bool = True
    otlp_endpoint: str = "http://localhost:4318/v1/traces"
    langfuse_enabled: bool = True
    langfuse_endpoint: str = "http://localhost:3001/api/public/otel/v1/traces"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    capture_content: bool = True
    export_timeout_seconds: float = 5.0

    @property
    def langfuse_ready(self) -> bool:
        """Keys are required; a Langfuse OTLP post without them is a 401."""
        return bool(self.langfuse_enabled and self.langfuse_public_key
                    and self.langfuse_secret_key)


@dataclass(frozen=True)
class ConversationSettings:
    """AC03 — how much of the conversation is carried forward, and how.

    **The follow-up retry is a fallback, not a rewrite.** Measured on this
    corpus before it was built: a bare referential follow-up (`لماذا؟`,
    `tell me more`) scores 0.15–0.18 and clears the relevance floor with
    **nothing**, while the same question prefixed with the previous one
    scores 0.35–0.51 and lands on the right document. A follow-up that
    carries any topical noun of its own already retrieves fine, because the
    collection is a single project and the project record is always present.

    So composing every query would spend an embedding on turns that do not
    need it and risk dragging the previous topic into a genuine change of
    subject. `retry_below_floor` composes **only** when the bare question
    found no evidence at all — which is the case that would otherwise
    refuse, so the extra embedding buys an answer rather than a refinement.

    `max_history_turns` and `max_history_tokens` bound what reaches the
    prompt. Both, because they fail differently: a cap on turns says nothing
    about their size, and one long answer can outweigh five short ones —
    the same asymmetry `select_context` handles for passages.
    """
    max_history_turns: int = 6
    max_history_tokens: int = 1500
    retry_below_floor: bool = True


@dataclass(frozen=True)
class IntentSettings:
    """AC02 — how a query needing special handling is told from a normal one.

    **Two tiers, and the split is deliberate.** Tier 1 is an exact,
    whole-utterance match against a phrase set held as *data*
    (`phrases_path`), covering the openings that are unambiguous and
    frequent: greetings, thanks, and explicit meta questions. Tier 2 is
    everything else, which goes to general retrieval where the relevance
    floor already produces an honest refusal for out-of-scope questions —
    a mechanism that is built, measured and verified live (§15c).

    **Why not classify every query with the LLM.** It would be accurate and
    bilingual, and it would add a round-trip to *every* question against a
    five-second budget, to re-decide something the retrieval floor already
    decides for free. The cheap tier handles what it can prove; the rest is
    measured before anything heavier is built.

    `match_max_words` is what keeps tier 1 honest. "مرحبا" is a greeting;
    "مرحبا، ما ميزانية المشروع؟" is a question with a greeting attached, and
    routing it to a canned hello would lose the question. Tier 1 therefore
    fires only on a *whole* short utterance, never on a substring.
    """
    phrases_path: pathlib.Path
    match_max_words: int = 6


@dataclass(frozen=True)
class EvaluationSettings:
    """Retrieval evaluation parameters.

    **The question set is data, not code.** It is a path, so a different
    corpus brings its own file and nothing here changes — the same reason
    `DOCUMENT_ROOT` is a setting. A harness with 30 questions hardcoded in
    a module would be measuring this demo corpus forever.

    `recall_at` is the set of cut-offs reported. The search depth follows
    from it rather than being configured separately, because a depth
    shallower than the deepest cut-off would silently report a recall the
    query never had the chance to achieve.
    """
    questions_path: pathlib.Path
    recall_at: tuple[int, ...] = (1, 3, 5)

    @property
    def search_limit(self) -> int:
        return max(self.recall_at)


@dataclass(frozen=True)
class ChunkingSettings:
    """Chunking parameters, overridable per deployment via .env.

    Defaults are imported from chunker.py rather than repeated here: the
    chunker is a standalone library that owns its own sane defaults, and
    this layer only supplies deployment-specific overrides. Duplicating the
    numbers would guarantee they eventually drift apart.

    Tuned against real OCR output; expected to change once retrieval can
    actually be measured (SOLUTION_ARCHITECTURE.md 4.6).
    """
    target_tokens: int = chunker.DEFAULT_TARGET_TOKENS
    overlap_tokens: int = chunker.DEFAULT_OVERLAP_TOKENS
    min_chunk_tokens: int = chunker.MIN_CHUNK_TOKENS
    split_heading_level: int = chunker.SPLIT_HEADING_LEVEL


@dataclass(frozen=True)
class Settings:
    document_root: pathlib.Path
    postgres: PostgresSettings
    postgres_app: PostgresSettings      # RLS-constrained, chat backend only
    qdrant: QdrantSettings
    models: AzureModelSettings
    celery: CelerySettings
    chunking: ChunkingSettings = field(default_factory=ChunkingSettings)
    scanner: ScannerSettings = field(default_factory=ScannerSettings)
    ocr: OcrSettings = field(default_factory=OcrSettings)
    indexing: IndexingSettings = field(default_factory=IndexingSettings)
    evaluation: EvaluationSettings = field(
        default_factory=lambda: EvaluationSettings(
            questions_path=REPO_ROOT / "evaluation" / "questions.json"
        )
    )
    application: ApplicationSettings = field(default_factory=ApplicationSettings)
    answering: AnswerSettings = field(default_factory=AnswerSettings)
    language: LanguageSettings = field(default_factory=LanguageSettings)
    conversation: ConversationSettings = field(
        default_factory=ConversationSettings)
    observability: ObservabilitySettings = field(
        default_factory=ObservabilitySettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    intent: IntentSettings = field(
        default_factory=lambda: IntentSettings(
            phrases_path=REPO_ROOT / "Application" / "backend" / "intent_phrases.json"
        )
    )

    def resolve_document(self, stored_path: str) -> pathlib.Path:
        """Turn a stored `document_links.file_path` into a real path.

        **`file_path` is stored RELATIVE to `document_root`** — e.g.
        `PRJ-01/charter.docx`. It used to be absolute, and that was wrong in
        a way only containerising exposed: the host writes
        `D:\\MOF_DataSample\\...` and the worker writes `/data/...`, so each
        run rewrote all 58 rows and Stage 1 could not open a single file
        from the other environment. A relative path is valid everywhere and
        makes `PATH_UPDATED` mean "moved within the corpus" rather than
        "a different machine ran last".

        An absolute value here is therefore stale data from before that fix.
        Silently joining it would produce a nonsense path
        (`Path("D:/x") / "/data/y"` -> `D:/data/y`), so it is refused with
        an instruction instead.
        """
        candidate = pathlib.PurePosixPath(stored_path)
        if candidate.is_absolute() or pathlib.PureWindowsPath(stored_path).is_absolute():
            raise ValueError(
                f"file_path is absolute ({stored_path!r}) but must be relative to "
                f"DOCUMENT_ROOT. This row predates the relative-path fix — run "
                f"`python -m pipeline.scanner` to rewrite it."
            )
        return self.document_root.joinpath(*candidate.parts)


def _scanner_from_env() -> ScannerSettings:
    """Allow per-deployment tuning without touching code."""
    raw = _optional("SCANNER_IGNORE_PATTERNS", "")
    patterns = tuple(p.strip() for p in raw.split(",") if p.strip())
    return ScannerSettings(
        default_doc_type=_optional(
            "SCANNER_DEFAULT_DOC_TYPE", ScannerSettings.default_doc_type
        ),
        ignore_patterns=patterns or ScannerSettings.ignore_patterns,
    )


def _ocr_from_env() -> OcrSettings:
    """Allow per-deployment tuning without touching code."""
    return OcrSettings(
        thin_result_chars=int(
            _optional("OCR_THIN_RESULT_CHARS", str(OcrSettings.thin_result_chars))
        ),
        retry_failed_by_default=_optional("OCR_RETRY_FAILED", "false").lower()
        in ("1", "true", "yes"),
        raw_response_warn_bytes=int(
            _optional("OCR_RAW_RESPONSE_WARN_BYTES",
                      str(OcrSettings.raw_response_warn_bytes))
        ),
    )


def _celery_from_env() -> CelerySettings:
    """Redis connection, assembled from the same variables Compose uses.

    REDIS_HOST defaults to localhost for a developer running the stages by
    hand, and Compose overrides it to the service name `redis` inside the
    network. That is the whole reason no module reads os.environ directly:
    moving the pipeline into a container is configuration, not code.
    """
    from urllib.parse import quote

    host = _optional("REDIS_HOST", "localhost")
    port = _optional("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "").strip()
    auth = f":{quote(password)}@" if password else ""
    broker = _optional("CELERY_BROKER_URL", f"redis://{auth}{host}:{port}/0")
    backend = _optional("CELERY_RESULT_BACKEND", f"redis://{auth}{host}:{port}/1")
    return CelerySettings(
        broker_url=broker,
        result_backend=backend,
        lock_timeout_seconds=int(
            _optional("CELERY_LOCK_TIMEOUT", str(CelerySettings.lock_timeout_seconds))
        ),
        schedule_hour=int(
            _optional("INGEST_SCHEDULE_HOUR", str(CelerySettings.schedule_hour))
        ),
        schedule_minute=int(
            _optional("INGEST_SCHEDULE_MINUTE", str(CelerySettings.schedule_minute))
        ),
        schedule_timezone=_optional(
            "INGEST_SCHEDULE_TIMEZONE", CelerySettings.schedule_timezone
        ),
    )


def _indexing_from_env() -> IndexingSettings:
    """Allow per-deployment tuning without touching code."""
    return IndexingSettings(
        batch_size=int(_optional("INDEX_BATCH_SIZE", str(IndexingSettings.batch_size))),
        max_batch_tokens=int(
            _optional("INDEX_MAX_BATCH_TOKENS", str(IndexingSettings.max_batch_tokens))
        ),
        retry_failed_by_default=_optional("INDEX_RETRY_FAILED", "true").lower()
        in ("1", "true", "yes"),
    )


def _application_from_env() -> ApplicationSettings:
    """Allow per-deployment tuning without touching code."""
    return ApplicationSettings(
        pool_min_size=int(
            _optional("APP_POOL_MIN_SIZE", str(ApplicationSettings.pool_min_size))
        ),
        pool_max_size=int(
            _optional("APP_POOL_MAX_SIZE", str(ApplicationSettings.pool_max_size))
        ),
        pool_timeout_seconds=float(
            _optional("APP_POOL_TIMEOUT_SECONDS",
                      str(ApplicationSettings.pool_timeout_seconds))
        ),
    )


def _answering_from_env() -> AnswerSettings:
    """Allow per-deployment tuning without touching code."""
    d = AnswerSettings
    return AnswerSettings(
        top_k=int(_optional("ANSWER_TOP_K", str(d.top_k))),
        max_context_tokens=int(
            _optional("ANSWER_MAX_CONTEXT_TOKENS", str(d.max_context_tokens))
        ),
        max_completion_tokens=int(
            _optional("ANSWER_MAX_COMPLETION_TOKENS", str(d.max_completion_tokens))
        ),
        temperature=float(_optional("ANSWER_TEMPERATURE", str(d.temperature))),
        min_score=float(_optional("ANSWER_MIN_SCORE", str(d.min_score))),
        request_timeout_seconds=float(
            _optional("ANSWER_TIMEOUT_SECONDS", str(d.request_timeout_seconds))
        ),
    )


def _language_from_env() -> LanguageSettings:
    """Allow per-deployment tuning without touching code."""
    d = LanguageSettings
    raw = _optional("LANGUAGE_SUPPORTED", ",".join(d.supported))
    codes = tuple(c.strip().lower() for c in raw.split(",") if c.strip())
    return LanguageSettings(
        supported=codes or d.supported,
        mixed_min_share=float(
            _optional("LANGUAGE_MIXED_MIN_SHARE", str(d.mixed_min_share))
        ),
        latin_discriminator_enabled=_optional(
            "LANGUAGE_LATIN_DISCRIMINATOR", "true"
        ).lower() not in {"false", "0", "no"},
        latin_discriminator_min_confidence=float(
            _optional("LANGUAGE_LATIN_MIN_CONFIDENCE",
                      str(d.latin_discriminator_min_confidence))
        ),
    )


def _flag(name: str, default: str) -> bool:
    return _optional(name, default).lower() not in {"false", "0", "no", "off"}


def _api_from_env() -> ApiSettings:
    """Allow per-deployment tuning without touching code."""
    d = ApiSettings
    raw_origins = _optional("API_CORS_ORIGINS", "")
    return ApiSettings(
        host=_optional("API_HOST", d.host),
        port=int(_optional("API_PORT", str(d.port))),
        auth_mode=_optional("API_AUTH_MODE", d.auth_mode).strip().lower(),
        auth_header=_optional("API_AUTH_HEADER", d.auth_header),
        dev_username=_optional("API_DEV_USERNAME", ""),
        session_secret=_optional("API_SESSION_SECRET", ""),
        session_hours=int(_optional("API_SESSION_HOURS", str(d.session_hours))),
        cookie_secure=_flag("API_COOKIE_SECURE", "false"),
        conversation_store=_optional("API_CONVERSATION_STORE",
                                     d.conversation_store).strip().lower(),
        conversation_idle_minutes=int(
            _optional("API_CONVERSATION_IDLE_MINUTES",
                      str(d.conversation_idle_minutes))
        ),
        max_conversations=int(
            _optional("API_MAX_CONVERSATIONS", str(d.max_conversations))
        ),
        max_question_chars=int(
            _optional("API_MAX_QUESTION_CHARS", str(d.max_question_chars))
        ),
        cors_origins=tuple(o.strip() for o in raw_origins.split(",") if o.strip()),
    )


def _observability_from_env() -> ObservabilitySettings:
    """Allow per-deployment tuning without touching code.

    The Langfuse keys are read from the same variables the container stack
    already defines, so enabling tracing is one flag rather than a second
    copy of credentials that can drift from the ones Langfuse was given.
    """
    d = ObservabilitySettings
    return ObservabilitySettings(
        enabled=_flag("OBSERVABILITY_ENABLED", "false"),
        service_name=_optional("OTEL_SERVICE_NAME", d.service_name),
        environment=_optional("OTEL_ENVIRONMENT", d.environment),
        otlp_enabled=_flag("OTLP_TRACING_ENABLED", "true"),
        otlp_endpoint=_optional("OTLP_TRACES_ENDPOINT", d.otlp_endpoint),
        langfuse_enabled=_flag("LANGFUSE_TRACING_ENABLED", "true"),
        langfuse_endpoint=_optional("LANGFUSE_OTLP_ENDPOINT", d.langfuse_endpoint),
        langfuse_public_key=_optional("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=_optional("LANGFUSE_SECRET_KEY", ""),
        capture_content=_flag("LANGFUSE_CAPTURE_CONTENT", "true"),
        export_timeout_seconds=float(
            _optional("OTEL_EXPORT_TIMEOUT_SECONDS",
                      str(d.export_timeout_seconds))
        ),
    )


def _conversation_from_env() -> ConversationSettings:
    """Allow per-deployment tuning without touching code."""
    d = ConversationSettings
    return ConversationSettings(
        max_history_turns=int(
            _optional("CONVERSATION_MAX_HISTORY_TURNS", str(d.max_history_turns))
        ),
        max_history_tokens=int(
            _optional("CONVERSATION_MAX_HISTORY_TOKENS", str(d.max_history_tokens))
        ),
        retry_below_floor=_optional(
            "CONVERSATION_RETRY_BELOW_FLOOR", "true"
        ).lower() not in {"false", "0", "no"},
    )


def _intent_from_env() -> IntentSettings:
    """Allow per-deployment tuning without touching code.

    The phrase set is a path for the same reason the evaluation question set
    is: a different deployment brings its own vocabulary, and a list baked
    into a module would serve this demo forever.
    """
    return IntentSettings(
        phrases_path=pathlib.Path(
            _optional("INTENT_PHRASES_PATH",
                      str(REPO_ROOT / "Application" / "backend"
                          / "intent_phrases.json"))
        ),
        match_max_words=int(
            _optional("INTENT_MATCH_MAX_WORDS", str(IntentSettings.match_max_words))
        ),
    )


def _evaluation_from_env() -> EvaluationSettings:
    """Allow per-deployment tuning without touching code."""
    raw = _optional("EVAL_RECALL_AT", "1,3,5")
    cutoffs = tuple(sorted({int(p) for p in raw.split(",") if p.strip()}))
    return EvaluationSettings(
        questions_path=pathlib.Path(
            _optional("EVAL_QUESTIONS_PATH",
                      str(REPO_ROOT / "evaluation" / "questions.json"))
        ),
        recall_at=cutoffs or EvaluationSettings.recall_at,
    )


def _chunking_from_env() -> ChunkingSettings:
    """Allow per-deployment tuning without touching code."""
    return ChunkingSettings(
        target_tokens=int(_optional("CHUNK_TARGET_TOKENS", str(chunker.DEFAULT_TARGET_TOKENS))),
        overlap_tokens=int(_optional("CHUNK_OVERLAP_TOKENS", str(chunker.DEFAULT_OVERLAP_TOKENS))),
        min_chunk_tokens=int(_optional("CHUNK_MIN_TOKENS", str(chunker.MIN_CHUNK_TOKENS))),
        split_heading_level=int(_optional("CHUNK_SPLIT_HEADING_LEVEL", str(chunker.SPLIT_HEADING_LEVEL))),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    load_dotenv(ENV_PATH, override=False)

    host = _optional("POSTGRES_SPM_HOST", "localhost")
    port = int(_optional("POSTGRES_SPM_PORT", "5432"))
    database = _optional("POSTGRES_SPM_DB", "spm_data")

    return Settings(
        # A setting, not a constant: repointing at a real SharePoint export
        # is configuration, not a code change.
        document_root=pathlib.Path(
            _optional("DOCUMENT_ROOT", r"D:\MOF_DataSample")
        ),
        postgres=PostgresSettings(
            host=host, port=port, database=database,
            user=_require("POSTGRES_SPM_USER"),
            password=_require("POSTGRES_SPM_PASSWORD"),
        ),
        # Used by the chat backend only. SELECT-only and row-level-security
        # constrained; every transaction must SET LOCAL app.current_project
        # (INFRASTRUCTURE.md 7c). The pipeline uses the admin role above,
        # because it legitimately needs cross-project access.
        postgres_app=PostgresSettings(
            host=host, port=port, database=database,
            user=_optional("POSTGRES_SPM_APP_USER", "spm_app"),
            password=_optional("POSTGRES_SPM_APP_PASSWORD", ""),
        ),
        qdrant=QdrantSettings(
            url=_optional("QDRANT_URL", f"http://localhost:{_optional('QDRANT_HTTP_PORT', '6333')}"),
            api_key=_require("QDRANT_API_KEY"),
        ),
        models=AzureModelSettings(
            ocr_endpoint=_require("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"),
            ocr_key=_require("AZURE_DOCUMENT_INTELLIGENCE_KEY"),
            ocr_model=_optional("AZURE_DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout"),
            embedding_endpoint=_require("AZURE_OPENAI_EMBEDDING_ENDPOINT"),
            embedding_key=_require("AZURE_OPENAI_EMBEDDING_API_KEY"),
            embedding_api_version=_require("AZURE_OPENAI_EMBEDDING_API_VERSION"),
            embedding_deployment=_require("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
            llm_endpoint=_require("AZURE_OPENAI_LLM_ENDPOINT"),
            llm_key=_require("AZURE_OPENAI_LLM_API_KEY"),
            llm_api_version=_require("AZURE_OPENAI_LLM_API_VERSION"),
            llm_deployment=_require("AZURE_OPENAI_LLM_DEPLOYMENT"),
        ),
        celery=_celery_from_env(),
        chunking=_chunking_from_env(),
        scanner=_scanner_from_env(),
        ocr=_ocr_from_env(),
        indexing=_indexing_from_env(),
        evaluation=_evaluation_from_env(),
        application=_application_from_env(),
        answering=_answering_from_env(),
        language=_language_from_env(),
        conversation=_conversation_from_env(),
        observability=_observability_from_env(),
        api=_api_from_env(),
        intent=_intent_from_env(),
    )
