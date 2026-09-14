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
import mimetypes
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import observability
from Application.backend import clients, db
from observability import tracing
from Application.backend.api import cookies as cookie_module
from Application.backend.api import credentials
from Application.backend.api import identity as identity_module
from Application.backend.api import ratelimit
from Application.backend.api import schemas
from Application.backend.api.store import build_store
from Application.backend.conversation import Reply
from Application.backend.errors import AccessDenied, AccessError, BackendUnavailable
from Application.backend.session import list_assigned_projects, open_session
from pipeline.config import get_settings

log = logging.getLogger("enjaz.api")

SETTINGS = get_settings()
# Built lazily at startup rather than at import, so a module import in a
# test does not require a live Redis.
STORE = None
LIMITER = None

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
    global STORE
    STORE = build_store(SETTINGS.api, settings=SETTINGS)
    log.info("conversation store: %s", type(STORE).__name__)
    global LIMITER
    LIMITER = _build_limiter()
    log.info("rate limit: %s", "off" if not LIMITER.enabled else
             f"{SETTINGS.api.rate_limit_questions} questions / "
             f"{SETTINGS.api.rate_limit_window_seconds}s per user")
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


# Paths that would drown the trace view without saying anything: a liveness
# probe and the static UI. Excluded by prefix, not by exact match, so the
# docs assets go too.
UNTRACED = ("/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico",
            "/assets/")


@app.middleware("http")
async def trace_request(request: Request, call_next):
    """One span per HTTP request, wrapping everything the request does.

    This is the outermost span, so a chat turn, its retrieval and its model
    call all nest beneath the request that caused them — which is what makes
    Grafana answer "where did this user's 3 seconds go" rather than "here is
    a chat turn, unattached to anything".

    ⚠️ **The span is named from the matched ROUTE, never the raw path.**
    `/api/conversations/{token}/messages` carries a conversation handle, and
    a handle in a span name would put it in the trace store, in the span
    name index, and in every screenshot of a dashboard. It is also unbounded
    cardinality, which is how a tracing backend gets expensive.
    """
    if request.url.path.startswith(UNTRACED):
        return await call_next(request)

    with observability.span("http.request",
                            **{tracing.HTTP_METHOD: request.method}) as current:
        response = await call_next(request)
        # The matched route only exists after routing, which happens inside
        # call_next — so the name is set here rather than up front.
        route = request.scope.get("route")
        template = getattr(route, "path", None)
        if template:
            current.set_attribute(tracing.HTTP_ROUTE, template)
            current.update_name(f"{request.method} {template}")
        current.set_attribute(tracing.HTTP_STATUS, response.status_code)
        if response.status_code >= 500:
            tracing.mark_failed(current, f"HTTP {response.status_code}")
        return response


def _build_limiter():
    from Application.backend.api.redis_store import _client
    return ratelimit.RateLimiter(
        _client(SETTINGS),
        limit=SETTINGS.api.rate_limit_questions,
        window_seconds=SETTINGS.api.rate_limit_window_seconds)


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
    return identity_module.resolve(request.headers, SETTINGS.api,
                                   cookies=request.cookies)


def account(who: identity_module.Identity = Depends(caller)) -> credentials.Account:
    """The signed-in account, re-read rather than trusted from the cookie.

    The cookie carries a username and is signed, so the name itself is not
    forgeable — but an account can be removed or renamed after a session
    starts, and a display name pulled from a token is a display name that
    can go stale. Reading it back also means a deleted account stops working
    at the next request rather than at the next expiry.
    """
    found = credentials.load_account(who.username)
    if found is None:
        raise AccessDenied("unknown_account", who.username, "")
    return found


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
                as_of=c.as_of,
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

FRONTEND = (pathlib.Path(__file__).resolve().parents[2] / "frontend"
            / "index.html")
ASSETS = FRONTEND.parent / "assets"

# The logo and the brand font. Static and public — nothing here depends on
# who is signed in, and StaticFiles refuses any path outside the directory.
# ⚠️ The font files are licensed (DIN Next LT Arabic) and gitignored, so a
# fresh clone serves 404 for them and the page falls back to a system font
# rather than failing. `check_dir=False` is what lets it start without them.
# StaticFiles guesses types from the OS registry, and Windows has no entry
# for .ttf, so fonts were served as text/plain. Declared, not guessed.
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("image/svg+xml", ".svg")
app.mount("/assets", StaticFiles(directory=ASSETS, check_dir=False),
          name="assets")


@app.get("/", include_in_schema=False)
def ui():
    """Serve the chat UI from the API process.

    One thing to run, and same-origin so the browser needs no CORS grant
    and no separate dev server. The page is a single static file with no
    build step — see its own comment for why.
    """
    if not FRONTEND.is_file():
        return JSONResponse(status_code=404,
                            content={"error": "no_ui",
                                     "detail": "frontend/index.html is missing"})
    return FileResponse(FRONTEND, media_type="text/html")


@app.post("/api/auth/login", response_model=schemas.AccountOut, tags=["auth"])
def login(body: schemas.LoginIn, response: Response):
    """Verify a demo credential and set a signed session cookie.

    ⚠️ **Simulated sign-in.** Real AD SSO and OTP replace this route and
    `credentials.py`; nothing above them changes. See migration 015.

    A wrong password and an unknown address produce the same refusal and
    roughly the same timing — a login form that answers faster for an
    unknown address is an account directory.
    """
    # ⚠️ The attempted address is NOT recorded on failure. A trace store has
    # no access control, and a stream of failed sign-ins with addresses in
    # it is an account directory plus a list of what an attacker guessed.
    # The username is recorded only once it is known to be genuine.
    with observability.span("auth.login") as current:
        try:
            found = credentials.authenticate(body.email, body.password)
        except AccessDenied:
            current.set_attribute("enjaz.outcome", "refused")
            raise
        current.set_attributes({"enjaz.outcome": "signed_in",
                                tracing.LF_USER_ID: found.username})
    config = SETTINGS.api
    try:
        token = cookie_module.sign(found.username, config.session_secret,
                                   max_age_seconds=config.session_hours * 3600)
    except cookie_module.CookieError as exc:
        # No signing secret configured. Refusing is the only safe answer:
        # an unsigned cookie is a username the browser can edit.
        log.error("cannot mint a session: %s", exc)
        raise BackendUnavailable(str(exc)) from exc

    response.set_cookie(
        cookie_module.COOKIE_NAME, token,
        max_age=config.session_hours * 3600,
        httponly=True,          # a script must not be able to read it
        samesite="lax",         # not sent on cross-site POSTs
        secure=config.cookie_secure,
        path="/",
    )
    log.info("signed in user=%s", found.username)
    return schemas.AccountOut(username=found.username, email=found.email,
                              full_name_ar=found.full_name_ar,
                              full_name_en=found.full_name_en)


@app.post("/api/auth/logout", status_code=204, tags=["auth"])
def logout(response: Response):
    """Clear the session cookie. Deliberately needs no valid session."""
    response.delete_cookie(cookie_module.COOKIE_NAME, path="/")
    return None


@app.get("/api/auth/me", response_model=schemas.AccountOut, tags=["auth"])
def me(found: credentials.Account = Depends(account)):
    """Who is signed in — what the UI calls on load to decide the screen."""
    return schemas.AccountOut(username=found.username, email=found.email,
                              full_name_ar=found.full_name_ar,
                              full_name_en=found.full_name_en)


@app.get("/health", response_model=schemas.HealthOut, tags=["ops"])
def health():
    """Liveness. Deliberately requires no identity and touches no project."""
    return schemas.HealthOut(
        status="ok",
        service=SETTINGS.observability.service_name,
        auth_mode=SETTINGS.api.auth_mode,
        tracing=list(observability.tracing.backends()),
        conversations=len(STORE) if STORE is not None else 0,
        conversation_store=(type(STORE).__name__ if STORE is not None
                            else "not started"),
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
                      found: credentials.Account = Depends(account)):
    """Open a chat on one project. **This is the AC09 gate.**

    `open_session` raises `AccessDenied` for a project this user is not
    assigned to — and identically for one that does not exist.

    Switching project means calling this again: a `Conversation` is bound to
    one session for its lifetime, so a new project is a new conversation
    rather than a mutation of the old one.
    """
    session = open_session(found.username, body.project_code, settings=SETTINGS)
    token, conversation = STORE.open(
        # First names: the welcome and every reply address the user the way a
        # colleague would. The full name stays on the account, and on screen.
        session, settings=SETTINGS, user_name=found.first_names,
        ui_language=body.ui_language)
    log.info("conversation opened user=%s project=%s",
             found.username, session.project_code)
    name = next((p.display_name for p in
                 list_assigned_projects(found.username, settings=SETTINGS)
                 if p.code == session.project_code), "")
    return schemas.ConversationOut(
        conversation_id=token,
        project_code=conversation.project_code,
        project_name=name,
        welcome=conversation.welcome,     # AC04 — before any turn exists
    )


@app.post("/api/conversations/{token}/messages", response_model=schemas.ReplyOut,
          tags=["chat"])
def ask(token: str, body: schemas.AskIn,
        who: identity_module.Identity = Depends(caller)):
    """One turn. AC01–AC07 all happen below this line, not in it."""
    conversation = _conversation_or_denied(token, who)

    # Checked AFTER the conversation is authorised, so a rate-limit answer
    # cannot tell a stranger whether a conversation id is real — it is only
    # ever reached by someone who already owns it.
    if LIMITER is not None:
        decision = LIMITER.check(who.username)
        if not decision.allowed:
            log.warning("rate limited user=%s", who.username)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(decision.retry_after)},
                content={"error": "rate_limited",
                         "detail": "Too many questions just now. "
                                   "Please wait a moment and try again."})

    text = body.text[:SETTINGS.api.max_question_chars]
    reply = conversation.ask(text)
    # The in-memory store held the live object, so a new turn was already
    # stored. A durable store holds a snapshot and must be written back.
    saver = getattr(STORE, "save", None)
    if saver is not None:
        saver(token, conversation)
    return _reply_out(reply)


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
                   found: credentials.Account = Depends(account)):
    """AC10 — the knowledge-base page: document type and indexing status.

    Read through a `Session`, so the gate authorises the project before a
    single row is fetched and row-level security scopes the read. View-only
    by construction: `spm_app` is `SELECT`-only.
    """
    session = open_session(found.username, project_code, settings=SETTINGS)
    with (
        observability.span("knowledge_base.read", project=session.project_code),
        session.scoped(settings=SETTINGS) as cursor,
    ):
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
