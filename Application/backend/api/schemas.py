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


class LoginIn(BaseModel):
    """Email and password. No username field — the account IS the identity."""
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class AccountOut(BaseModel):
    """Who is signed in. Never accepted as input, only ever returned.

    Both the full name and the shortened one are returned: the top bar shows
    the short form, and the full name stays available as the formal record.
    Shortening happens here rather than in the page so that the rules for it
    — titles, "Abd" compounds, the patronymic chain — have one implementation
    and one set of tests, not one per language of the stack.
    """
    username: str
    email: str
    full_name_ar: str
    full_name_en: str
    short_name_ar: str
    short_name_en: str


class ProjectOut(BaseModel):
    code: str
    name_ar: str | None = None
    name_en: str | None = None
    status: str | None = None
    display_name: str


class OpenConversationIn(BaseModel):
    """Only a project code. The identity comes from the request context.

    `ui_language` picks the language of the pre-input welcome, which is the
    one reply with no message to detect a language from. It cannot influence
    any later reply — those follow the language the model detects.
    """
    project_code: str = Field(min_length=1, max_length=128)
    ui_language: str = Field(default="ar", pattern="^(ar|en)$")


class CitationOut(BaseModel):
    source: str
    label: str
    filename: str = ""
    doc_type: str = ""
    page: int | None = None
    section: str = ""
    # The SPM record's provenance, e.g. "record updated 2026-08-04". §3.3
    # asks for each source WITH its date; the label carries it too, but
    # only in English, so the UI needs the fact rather than the sentence.
    as_of: str = ""


class TimingsOut(BaseModel):
    embed_ms: int
    search_ms: int
    spm_ms: int
    llm_ms: int
    total_ms: int
    # Only a streamed turn has these. `None` rather than 0, because a zero
    # would average into a flattering figure alongside real measurements.
    # ⚠️ They do not replace `total_ms`: AC08's budget is the whole turn.
    first_token_ms: int | None = None
    time_to_first_text_ms: int | None = None


class ConversationOut(BaseModel):
    conversation_id: str
    project_code: str
    project_name: str = ""
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
    # At most three, and empty after a refusal. The page renders them as
    # chips and sends a click as an ordinary message.
    follow_ups: list[str] = []
    timings: TimingsOut


class TurnOut(BaseModel):
    question: str
    reply: ReplyOut


class TranscriptOut(BaseModel):
    conversation_id: str
    project_code: str
    # The greeting the conversation opened with, so a page refresh can show
    # the exchange exactly as it was rather than without its first message.
    welcome: str = ""
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
    # When the daily sync last SUCCEEDED (ISO 8601, UTC), or null if it
    # never has or cannot be read. The same for every project — the sync runs
    # over the whole corpus — so it discloses nothing about any one project.
    # Not the newest document date: an unchanged project is still checked
    # every day, and "last changed 33 days ago" would read as stale data.
    last_sync: str | None = None


class HealthOut(BaseModel):
    status: str
    service: str
    auth_mode: str
    tracing: list[str]
    conversations: int
    conversation_store: str = ""


class ErrorOut(BaseModel):
    error: str
    detail: str
