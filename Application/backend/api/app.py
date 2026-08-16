"""The HTTP layer — FastAPI over the conversational backend.

**This adds no behaviour.** Every acceptance criterion is already
implemented and measured below it; this exposes them. That framing is the
point: a route that reaches past `Conversation` into `answer_question`
would silently skip AC01–AC04, so the routes here are deliberately thin.

Three rules it exists to hold:

  1. **Identity never comes from the request.** No body, no query string.
     See `identity.py` — this is the AC09 boundary.
  2. **A conversation id is a handle, not a credential.** Every lookup is
     owner-checked; see `store.py`.
  3. **Errors are uniform.** `AccessDenied` carries a reason for our logs
     and never for the client, or the 403 becomes a project directory.

Startup does the two things the AC08 result depends on — pre-warm the
clients and configure tracing — and shutdown flushes the spans, which are
batched and would otherwise be lost by a short-lived process.

Run it:

    .venv/Scripts/python -m Application.backend.api
    .venv/Scripts/python -m uvicorn Application.backend.api.app:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import observability
from Application.backend import clients, db
from Application.backend.api import identity as identity_module
from Application.backend.api import schemas
from Application.backend.api.store import ConversationStore
from Application.backend.conversation import Reply
from Application.backend.errors import AccessDenied, AccessError, BackendUnavailable
from Application.backend.session import list_assigned_projects, open_session
from pipeline.config import get_settings

log = logging.getLogger("enjaz.api")

SETTINGS = get_settings()
STORE = ConversationStore(SETTINGS.api)

# Shown for every refusal, whatever the underlying reason. The reason slug
# stays in the log; see `errors.AccessDenied`.
DENIED = "The requested project is not available to this user."
UNAVAILABLE = "The service is temporarily unavailable. Please try again."


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Pay setup once, here, instead of on the first user's question."""
    logging.basicConfig(level=logging.INFO)
    log.info(identity_module.describe(SETTINGS.api))
    backends = observability.configure(SETTINGS)
    log.info("tracing: %s", ", ".join(backends) if backends else "disabled")
    # AC08 depends on this: without it the first question pays ~19 s of
    # client construction and connection setup (STATUS_AND_ROADMAP.md §19a).
    log.info("warmup: %s", clients.warmup(SETTINGS))
    try:
        yield
    finally:
        # Spans are batched, so a process that exits without flushing drops
        # the traces it was running to produce.
        observability.shutdown()
        db.close_pool()


app = FastAPI(
    title="Enjaz Assistant API",
    version="0.1.0",
    summary="Chat over one assigned project's documents, with citations.",
    lifespan=lifespan,
)

if SETTINGS.api.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(SETTINGS.api.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )


# ---------------------------------------------------------------------------
# Errors — uniform, and never an oracle
# ---------------------------------------------------------------------------

@app.exception_handler(AccessDenied)
async def _denied(request: Request, exc: AccessDenied):
    # The reason is logged, not rendered. Telling "not yours" apart from
    # "does not exist" is what turns a 403 into a project directory.
    log.warning("access denied: %r path=%s", exc, request.url.path)
    return JSONResponse(status_code=403,
                        content={"error": "access_denied", "detail": DENIED})


@app.exception_handler(BackendUnavailable)
async def _unavailable(request: Request, exc: BackendUnavailable):
    log.error("backend unavailable: %s path=%s", exc, request.url.path)
    return JSONResponse(status_code=503,
                        content={"error": "unavailable", "detail": UNAVAILABLE})


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def caller(request: Request) -> identity_module.Identity:
    """The authenticated caller, or a refusal. Never reads the body."""
    return identity_module.resolve(request.headers, SETTINGS.api)


def _reply_out(reply: Reply) -> schemas.ReplyOut:
    return schemas.ReplyOut(
        text=reply.text,
        outcome=reply.outcome,
        intent=reply.intent,
        language=reply.language,
        reply_language=reply.reply_language,
        grounded=reply.grounded,
        citations=[
            schemas.CitationOut(
                source=c.source, label=c.label, filename=c.filename,
                doc_type=c.doc_type, page=c.page, section=c.section,
            ) for c in reply.citations
        ],
        timings=schemas.TimingsOut(**reply.timings.as_dict()),
    )


def _conversation_or_denied(token: str, who: identity_module.Identity):
    conversation = STORE.get(token, who.username)
    if conversation is None:
        # Same answer for "no such conversation" and "not yours".
        raise AccessDenied("no_such_conversation", who.username, "")
    return conversation


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=schemas.HealthOut, tags=["ops"])
def health():
    """Liveness. Deliberately requires no identity and touches no project."""
    return schemas.HealthOut(
        status="ok",
        service=SETTINGS.observability.service_name,
        auth_mode=SETTINGS.api.auth_mode,
        tracing=list(observability.tracing.backends()),
        conversations=len(STORE),
    )


@app.get("/api/projects", response_model=list[schemas.ProjectOut], tags=["projects"])
def projects(who: identity_module.Identity = Depends(caller)):
    """The project switcher — only what this user is assigned to.

    Filtered by the database, not by a condition written here: migration
    `014` exists so `projects` is readable by an assigned user before any
    project is scoped.
    """
    return [
        schemas.ProjectOut(
            code=p.code, name_ar=p.name_ar, name_en=p.name_en,
            status=p.status, display_name=p.display_name,
        )
        for p in list_assigned_projects(who.username, settings=SETTINGS)
    ]


@app.post("/api/conversations", response_model=schemas.ConversationOut,
          status_code=201, tags=["chat"])
def open_conversation(body: schemas.OpenConversationIn,
                      who: identity_module.Identity = Depends(caller)):
    """Open a chat on one project. **This is the AC09 gate.**

    `open_session` raises `AccessDenied` for a project this user is not
    assigned to — and identically for one that does not exist.
    """
    session = open_session(who.username, body.project_code, settings=SETTINGS)
    token, conversation = STORE.open(session, settings=SETTINGS)
    log.info("conversation opened user=%s project=%s via=%s",
             who.username, session.project_code, who.source)
    return schemas.ConversationOut(
        conversation_id=token,
        project_code=conversation.project_code,
        welcome=conversation.welcome,     # AC04 — bilingual, before any turn
    )


@app.post("/api/conversations/{token}/messages", response_model=schemas.ReplyOut,
          tags=["chat"])
def ask(token: str, body: schemas.AskIn,
        who: identity_module.Identity = Depends(caller)):
    """One turn. AC01–AC07 all happen below this line, not in it."""
    conversation = _conversation_or_denied(token, who)
    text = body.text[:SETTINGS.api.max_question_chars]
    return _reply_out(conversation.ask(text))


@app.get("/api/conversations/{token}", response_model=schemas.TranscriptOut,
         tags=["chat"])
def transcript(token: str, who: identity_module.Identity = Depends(caller)):
    """The whole exchange — AC03's continuity, as the user saw it."""
    conversation = _conversation_or_denied(token, who)
    return schemas.TranscriptOut(
        conversation_id=token,
        project_code=conversation.project_code,
        turns=[
            schemas.TurnOut(question=t.question, reply=_reply_out(t.reply))
            for t in conversation.turns
        ],
    )


@app.delete("/api/conversations/{token}", status_code=204, tags=["chat"])
def close_conversation(token: str, who: identity_module.Identity = Depends(caller)):
    STORE.close(token, who.username)
    return None


@app.get("/api/projects/{project_code}/documents",
         response_model=schemas.KnowledgeBaseOut, tags=["projects"])
def knowledge_base(project_code: str,
                   who: identity_module.Identity = Depends(caller)):
    """AC10 — the knowledge-base page: document type and indexing status.

    Read through a `Session`, so the gate authorises the project before a
    single row is fetched and row-level security scopes the read. View-only
    by construction: `spm_app` is `SELECT`-only.
    """
    session = open_session(who.username, project_code, settings=SETTINGS)
    with session.scoped(settings=SETTINGS) as cursor:
        rows = cursor.execute(
            "SELECT filename, doc_type, indexing_status, language, "
            "       chunk_count, indexed_at, file_size_bytes "
            "FROM document_links ORDER BY doc_type, filename"
        ).fetchall()

    documents = [
        schemas.DocumentOut(
            filename=r[0], doc_type=r[1], indexing_status=r[2], language=r[3],
            chunk_count=r[4], indexed_at=r[5].isoformat() if r[5] else None,
            size_bytes=r[6],
        )
        for r in rows
    ]
    return schemas.KnowledgeBaseOut(
        project_code=session.project_code,
        documents=documents,
        indexed=sum(1 for d in documents if d.indexing_status == "indexed"),
        total=len(documents),
    )
