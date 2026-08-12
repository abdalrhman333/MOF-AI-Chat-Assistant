# `Application/` — the chat application

Everything a user eventually touches lives here. The ingestion pipeline
(`pipeline/`) and the retrieval evaluation harness (`evaluation/`) stay
where they are: the pipeline *produces* the knowledge base and this
*serves* it, and they run as different processes under different database
roles with deliberately different privileges.

```
Application/
  backend/
    errors.py     access/session failures, as types rather than strings
    db.py         the spm_app connection pool + the scoped transaction
    session.py    the AC09 session gate — the security boundary
    api/          (not built yet) FastAPI routes and schemas
  frontend/       (not built yet) chat UI, project switcher, KB page
```

## What is built today

**The session gate and the scoped data-access layer, and nothing else.**
There is no HTTP layer, no retrieval composition, no LLM call and no UI.
`api/` and `frontend/` are named above so it is clear where they go, not
because they exist.

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
