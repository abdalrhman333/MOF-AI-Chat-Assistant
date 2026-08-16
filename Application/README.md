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
    conversation.py THE ENTRY POINT — applies AC01/AC02, then delegates
    intent_phrases.json   tier-1 phrases, as data
    api/            (not built yet) FastAPI routes and schemas
  frontend/         (not built yet) chat UI, project switcher, KB page
```

## What is built today

**Everything except the HTTP layer and the UI.** A question can go in and a
cited answer can come out, in the question's language, with unsupported
languages rejected and greetings answered without touching the model. What
does not exist is a way for a browser to reach it: `api/` and `frontend/`
are named above so it is clear where they go, not because they exist.
AC03 (multi-turn continuity) and AC04 (bilingual welcome) are also not
built — `conversation.py` and `messages.py` are where they belong.

## Call `handle_question`, not `answer_question`

```python
from Application.backend.session import open_session
from Application.backend.conversation import handle_question

session = open_session(username, project_code)   # AC09 — raises AccessDenied
reply   = handle_question(session, user_text)    # AC01, AC02, AC06, AC07

reply.text            # what to show, always populated
reply.outcome         # answered | rejected_language | no_language_detected
                      # | handled_without_retrieval | out_of_scope
reply.citations       # resolved from retrieved payloads, never model-invented
reply.reply_language  # ar | en — AC05
reply.timings         # embed / search / spm / llm, in ms — AC08
```

⚠️ **`handle_question` is an entry point, not a chokepoint.**
`answer_question` remains importable and skips the language check and the
intent router. That is deliberate — it is the grounded-answering engine,
and duplicating AC01 inside it would put one rule in two places — but it
means **anything user-facing must enter through `conversation.py`**. The
rule that must never be bypassable is project scope, and that one *is*
structural: `retrieval.search` takes a `Session` and derives the collection
from it, so it cannot be called with a project the gate did not grant.

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
