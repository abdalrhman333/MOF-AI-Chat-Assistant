# `Application/` — the chat application

Everything a user eventually touches lives here. The ingestion pipeline
(`pipeline/`) and the retrieval evaluation harness (`evaluation/`) stay
where they are: the pipeline *produces* the knowledge base and this
*serves* it, and they run as different processes under different database
roles with deliberately different privileges.

```
Application/
  backend/
    errors.py       access/session failures, as types rather than strings
    db.py           the spm_app connection pool + the scoped transaction
    session.py      the AC09 session gate — the security boundary
    retrieval.py    vector search, scoped by Session, never by project code
    spm.py          the live project record — the second grounding source
    answer.py       THE SYSTEM PROMPT — the whole router — plus the LLM
                    call and citation resolution (AC01, AC02, AC05-AC07)
    messages.py     the only two strings the model does not write
    answer.py also bounds `follow_ups`: up to three suggested next
                    questions, empty after a refusal. The model proposes;
                    what reaches the screen is decided here
    streaming.py    reads the answer out of the strict-schema JSON while
                    it is still arriving. A PREVIEW, never the authority:
                    the complete document is parsed at the end and that
                    parse owns the citations, language and intent
    conversation.py THE ENTRY POINT — Conversation (AC03/AC04), history,
                    the follow-up retry, then delegation to answer.py
    clients.py      the embedding / vector / LLM clients, cached per
                    Settings. An AC08 fix: rebuilding them cost ~2.3s a
                    question. Call warmup() once at startup
    api/
      identity.py    WHERE THE USERNAME COMES FROM — the AC09 boundary
      credentials.py demo sign-in: PBKDF2, uniform refusals
      cookies.py     the SIGNED session cookie
      store.py       server-side conversations; an id is not a credential
      schemas.py     request/response shapes — no field holds a username
      app.py         the routes, deliberately thin. `/messages` and
                     `/messages/stream` are the SAME turn: both call
                     Conversation.ask, under the same owner check and the
                     same rate limit. The streamed one runs it on a worker
                     thread and relays Server-Sent Events
  frontend/
    index.html      the whole UI: chat, switcher, KB page, RTL
```

## What is built today

**All of it, except SSO/OTP.** A project manager can open the page, pick an
assigned project, and hold a bilingual multi-turn conversation whose
answers are grounded and cited — with unsupported languages refused, and a
knowledge-base page showing what is indexed. Sign-in is simulated: email and
password against seeded accounts, then a signed session cookie.

```bash
cd infra && docker compose up -d                   # runs as container mof-chat-api → http://localhost:8000
docker compose up -d --build chat-api              # after changing code here
.venv/Scripts/python -m Application.backend.api    # host run, for development — stop the container first
```

## Call `open_conversation`, not `answer_question`

```python
from Application.backend.session import open_session
from Application.backend.conversation import open_conversation

session = open_session(username, project_code)   # AC09 — raises AccessDenied
talk    = open_conversation(session)

talk.welcome            # AC04 — bilingual, before anything is typed
reply = talk.ask(text)  # AC01, AC02, AC03, AC05, AC06, AC07
talk.turns              # the transcript, every turn the user saw

reply.text            # what to show, always populated
reply.outcome         # answered | rejected_language | no_language_detected
                      # | handled_without_retrieval | out_of_scope
reply.citations       # resolved from retrieved payloads, never model-invented
reply.reply_language  # ar | en — AC05
reply.timings         # embed / search / spm / llm, in ms — AC08
```

`handle_question(session, text, ...)` is the single-turn form underneath,
for a caller that owns its own history.

## What a long-lived process must do at startup

```python
from Application.backend import clients
import observability

clients.warmup()            # AC08 — without this the first turn is ~19s
observability.configure()   # tracing, if OBSERVABILITY_ENABLED
...
observability.shutdown()    # on exit — spans are batched and lost otherwise
```

⚠️ **These are entry points, not chokepoints.** `answer_question` remains
importable and skips the conversation around it — history, the follow-up
retry, the transcript. It does *not* skip the language or intent decision,
because since 2026-08-17 those live in its own system prompt. Anything
user-facing must still enter through `conversation.py`. The rule that must
never be bypassable is project scope, and that one *is* structural:
`retrieval.search` takes a `Session` and derives the collection from it, so
it cannot be called with a project the gate did not grant.

**A `Conversation` extends that guarantee through time.** Its session is
fixed at construction and cannot be replaced, so a transcript cannot come
to hold two projects' content and a follow-up cannot resolve against a
project the user has left. Switching project means a new `Session` from the
gate, and therefore a new `Conversation` — which is also the honest model
of what a project switch *is*.

## The one rule everything here depends on

The application connects as **`spm_app`** — `SELECT`-only, and constrained
by row-level security to a single project per transaction. It never
connects as `spm_admin`; that role belongs to the pipeline, which
legitimately needs to reach across every project.

Row-level security is not, by itself, the AC09 guarantee. **RLS faithfully
serves whatever project the session declares.** Verified live on
2026-08-09: a user assigned to PRJ-02/05/06, scoping a session to PRJ-01,
was handed PRJ-01's row without complaint. Deciding whether a project may
be scoped *at all* is `session.py`, and it is the most security-sensitive
code in the repository.

## Trust boundary — where it now actually lives

**`api/identity.py` is the other half of this section, and it is the second
most security-sensitive file here.** The gate answers *"may this user open
this project?"*; identity answers *"who is this?"*, and until the HTTP
layer existed nothing did.

Two rules it holds, both mutation-tested:

- **A username never comes from a request body or query string**, enforced
  structurally — no schema has a field for one. Same argument as the LLM
  response schema having no field for a filename.
- **A conversation id is a handle, not a credential.** Every lookup is
  owner-checked; the gate validated the *owner's* assignment, so a leaked
  id would otherwise bypass AC09 completely.

`session_login` (the default) verifies an email and password against the
seeded accounts and mints an **HMAC-signed** cookie — an unsigned one would
be a username the browser can edit. `dev_fixed` takes the identity from
configuration, for development and tests. `trusted_header` is
production-*shaped*, not production-*safe*: correct only behind a proxy that
overwrites the header on every inbound request.

**Real AD SSO + OTP replaces `credentials.py` and one branch of
`identity.py`, and nothing above them changes.** That seam is the whole
reason the AC09 gate never had to know how identity was established.

`open_session()` **trusts the username it is given.** Real Active Directory
SSO and OTP are external dependencies that do not exist in this
environment, and the BRD's own practical note says to simulate what can be
simulated honestly and flag the rest rather than build something that
merely looks like authentication.

So the gate answers *"may this user open this project?"* and never
*"is this really that user?"*. Whatever eventually supplies the username —
an AD token, a session cookie — becomes part of the security boundary, and
this gate's guarantee is only as good as that. Nothing downstream should
accept a username from a request body.
