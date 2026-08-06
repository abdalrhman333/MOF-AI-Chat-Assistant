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
    chunking: ChunkingSettings = field(default_factory=ChunkingSettings)
    scanner: ScannerSettings = field(default_factory=ScannerSettings)


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
        chunking=_chunking_from_env(),
        scanner=_scanner_from_env(),
    )
