"""Request and response shapes.

**No request model carries a username.** That is the one thing the HTTP
layer must not accept from a client, and the surest way to enforce it is
that there is no field to put it in — the same structural argument as
`answer.py`'s response schema, which gives the model no field for a
filename and so cannot be handed a fabricated citation.

Responses deliberately expose *outcomes* and never internal reason slugs.
`AccessDenied.reason` distinguishes "not yours" from "does not exist" for
our logs; rendering that distinction to a client turns the error into a
project directory.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectOut(BaseModel):
    code: str
    name_ar: str | None = None
    name_en: str | None = None
    status: str | None = None
    display_name: str


class OpenConversationIn(BaseModel):
    """Only a project code. The identity comes from the request context."""
    project_code: str = Field(min_length=1, max_length=128)


class CitationOut(BaseModel):
    source: str
    label: str
    filename: str = ""
    doc_type: str = ""
    page: int | None = None
    section: str = ""


class TimingsOut(BaseModel):
    embed_ms: int
    search_ms: int
    spm_ms: int
    llm_ms: int
    total_ms: int


class ConversationOut(BaseModel):
    conversation_id: str
    project_code: str
    welcome: str
    turns: int = 0


class AskIn(BaseModel):
    """A question. Length-bounded so one request cannot spend the budget."""
    text: str = Field(min_length=1)


class ReplyOut(BaseModel):
    text: str
    outcome: str
    intent: str = ""
    language: str = ""
    reply_language: str = ""
    grounded: bool = False
    citations: list[CitationOut] = []
    timings: TimingsOut


class TurnOut(BaseModel):
    question: str
    reply: ReplyOut


class TranscriptOut(BaseModel):
    conversation_id: str
    project_code: str
    turns: list[TurnOut]


class DocumentOut(BaseModel):
    """AC10 — the knowledge-base page. Type and indexing status."""
    filename: str
    doc_type: str
    indexing_status: str
    language: str | None = None
    chunk_count: int | None = None
    indexed_at: str | None = None
    size_bytes: int | None = None


class KnowledgeBaseOut(BaseModel):
    project_code: str
    documents: list[DocumentOut]
    indexed: int
    total: int


class HealthOut(BaseModel):
    status: str
    service: str
    auth_mode: str
    tracing: list[str]
    conversations: int


class ErrorOut(BaseModel):
    error: str
    detail: str
