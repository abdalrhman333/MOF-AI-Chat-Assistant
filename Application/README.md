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
    answer.py       prompt, LLM call, citation resolution (AC06)
    language.py     AC01 — verify the input language
    intent.py       AC02 — special handling vs general retrieval
    messages.py     every string a user reads that the model did not write
    conversation.py THE ENTRY POINT — Conversation (AC03/AC04), the router
                    (AC01/AC02), then delegation to answer.py
    clients.py      the embedding / vector / LLM clients, cached per
                    Settings. An AC08 fix: rebuilding them cost ~2.3s a
                    question. Call warmup() once at startup
    intent_phrases.json   tier-1 phrases, as data
    api/            (not built yet) FastAPI routes and schemas
  frontend/         (not built yet) chat UI, project switcher, KB page
```

## What is built today

**Everything except the HTTP layer and the UI.** A multi-turn conversation
can be held end to end — bilingual welcome, unsupported languages rejected,
greetings answered without touching the model, cited answers in the
question's language, and follow-ups that resolve against what was just
asked. What does not exist is a way for a browser to reach it: `api/` and
`frontend/` are named above so it is clear where they go, not because they
exist.

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
importable and skips the language check, the intent router and history.
That is deliberate — it is the grounded-answering engine, and duplicating
AC01 inside it would put one rule in two places — but it means **anything
user-facing must enter through `conversation.py`**. The rule that must
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

## Trust boundary — read before wiring anything in front of this

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
