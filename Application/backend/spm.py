"""The live SPM source — structured project fields, read through the session.

The second of the three sources in `SOLUTION_ARCHITECTURE.md` §3.3. Where
RAG returns passages *written about* a project, this returns the project
record itself: status, dates, budget, sponsor — the system of record's own
answer, not a document's description of it.

⚠️ **This read MUST filter by project explicitly, and that is not
belt-and-braces — it is load-bearing.**

The instinct was the opposite: `Session.scoped()` applies row-level
security, so `SELECT ... FROM projects` should already be constrained, and
adding a `WHERE` would imply the filter is the boundary when the database
already is. That reasoning is correct for `document_links` and
`document_content`. It is **wrong for `projects`**, because migration `014`
deliberately widened that one table: a user may read *every project they
are assigned to*, so the switcher can show names before a session exists.
Permissive policies combine with OR, so inside a scoped session `projects`
returns the session's project **plus every other project the user is
assigned to**.

Found live, not in review: a session scoped to PRJ-06, opened by a user
assigned to three projects, fetched PRJ-02's record — and the answer then
reported PRJ-02's budget as this project's, flagging it as a *disagreement*
with the document's correct figure. Silently wrong, confidently presented,
and every stubbed test passed because the stub returned exactly one row.

So the filter is explicit and RLS is the second line, not the first. The
general lesson is worth carrying: **a table whose visibility rules were
widened for one caller stops being a safe implicit single-row read for
another.**

⚠️ **`projects` has no `updated_at`.** §3.3 requires attribution to carry
dates — "SPM, updated 2026-08-01" against "Weekly Status Report,
2026-07-12" is often the whole answer a project manager needs. The table
records only `created_at`, so that is what is attributed, labelled as what
it actually is. This is a *mock* SPM; inventing a last-modified timestamp
here would manufacture provenance that the real system has not supplied.
**A real SPM integration must carry its own last-updated field**, and until
it does, dated comparison between this source and a document is weaker than
§3.3 assumes.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import observability
from Application.backend.errors import BackendUnavailable
from Application.backend.session import Session
from pipeline.config import Settings

_SELECT = """
    SELECT code, name_ar, name_en, description_ar, description_en, status,
           start_date, planned_end_date, budget_sar,
           sponsoring_department_ar, sponsoring_department_en, created_at,
           owner_ar, owner_en, manager_ar, manager_en, completion_percent,
           phase_ar, phase_en, stage_ar, stage_en, updated_at
    FROM projects
    WHERE code = %s
"""


@dataclass(frozen=True)
class ProjectRecord:
    """One project as the system of record holds it."""
    code: str
    name_ar: str
    name_en: str
    description_ar: str | None
    description_en: str | None
    status: str
    start_date: dt.date | None
    planned_end_date: dt.date | None
    budget_sar: float | None
    sponsor_ar: str | None
    sponsor_en: str | None
    recorded_at: dt.datetime | None
    updated_at: dt.datetime | None = None
    # The five fields migration 016 added, so the overview request (QA1.1)
    # can be answered from the system of record rather than assembled out of
    # whatever the documents happen to mention.
    owner_ar: str | None = None
    owner_en: str | None = None
    manager_ar: str | None = None
    manager_en: str | None = None
    completion_percent: int | None = None
    phase_ar: str | None = None
    phase_en: str | None = None
    stage_ar: str | None = None
    stage_en: str | None = None

    @property
    def as_of(self) -> str:
        """What this record can honestly claim about its own currency.

        §3.3 wants a date on every source so a project manager comparing two
        claims can see which is newer. Migration 017 finally gives the table
        a maintained `updated_at`, so the record can say when it last
        *changed* rather than when it was first written — which is the fact
        the comparison actually needs.

        A row nobody has edited reports its creation date, and says so.
        Labelling an untouched row "updated" would be the invented
        provenance §16b refused.
        """
        if self.updated_at and self.recorded_at and self.updated_at > self.recorded_at:
            return f"record updated {self.updated_at:%Y-%m-%d}"
        if self.recorded_at is not None:
            return f"record created {self.recorded_at:%Y-%m-%d}"
        return "date unknown"

    def as_prompt_block(self) -> str:
        """Rendered for the model — labelled, dated, and clearly not a document.

        Empty fields are omitted rather than shown as "None": a blank line
        invites the model to report an absence as a fact.
        """
        rows: list[tuple[str, str]] = [
            ("Project code", self.code),
            ("Name (Arabic)", self.name_ar),
            ("Name (English)", self.name_en),
            ("Status", self.status),
        ]
        if self.start_date:
            rows.append(("Start date", f"{self.start_date:%Y-%m-%d}"))
        if self.planned_end_date:
            rows.append(("Planned end date", f"{self.planned_end_date:%Y-%m-%d}"))
        if self.budget_sar is not None:
            rows.append(("Approved budget (SAR)", f"{self.budget_sar:,.0f}"))
        if self.completion_percent is not None:
            rows.append(("Completion", f"{self.completion_percent}%"))
        for label, value in (
            ("Project owner", self.owner_en), ("Project owner (Arabic)", self.owner_ar),
            ("Project manager", self.manager_en),
            ("Project manager (Arabic)", self.manager_ar),
            ("Phase", self.phase_en), ("Phase (Arabic)", self.phase_ar),
            ("Stage", self.stage_en), ("Stage (Arabic)", self.stage_ar),
            ("Sponsoring department", self.sponsor_en),
            ("Sponsoring department (Arabic)", self.sponsor_ar),
            ("Description", self.description_en),
        ):
            if value:
                rows.append((label, value))
        return "\n".join(f"{label}: {value}" for label, value in rows)

    def missing_overview_fields(self) -> list[str]:
        """Which of the ten overview fields this record cannot supply.

        Returned so the model can say *which* are unrecorded rather than
        quietly producing a nine-field answer to a ten-field question. An
        absence stated is information; an absence omitted looks like an
        oversight.
        """
        present = {
            "name": bool(self.name_ar or self.name_en),
            "status": bool(self.status),
            "owner": bool(self.owner_ar or self.owner_en),
            "manager": bool(self.manager_ar or self.manager_en),
            "completion": self.completion_percent is not None,
            "budget": self.budget_sar is not None,
            "start date": self.start_date is not None,
            "end date": self.planned_end_date is not None,
            "phase": bool(self.phase_ar or self.phase_en),
            "stage": bool(self.stage_ar or self.stage_en),
        }
        return [field for field, ok in present.items() if not ok]


def fetch(session: Session, *, settings: Settings | None = None,
          connection=None) -> ProjectRecord | None:
    """The session's own project record, or `None` if there isn't one.

    `None` is a legitimate answer, not a failure: row-level security
    returning nothing means this session has no project record to read, and
    the caller should carry on with documents alone.

    A database that cannot be reached is different, and raises.
    """
    try:
        with (
            observability.span("spm.fetch", project=session.project_code),
            session.scoped(settings=settings, connection=connection) as cursor,
        ):
            # The project code comes from the Session — the gate's own
            # output — never from a caller argument, so this filter cannot
            # be pointed at a project the user was not granted.
            row = cursor.execute(_SELECT, (session.project_code,)).fetchone()
    except BackendUnavailable:
        raise
    except Exception as exc:                                # noqa: BLE001
        raise BackendUnavailable(f"could not read the project record: {exc}") from exc

    if row is None:
        return None
    (code, name_ar, name_en, desc_ar, desc_en, status, start, end,
     budget, sponsor_ar, sponsor_en, created,
     owner_ar, owner_en, manager_ar, manager_en, completion,
     phase_ar, phase_en, stage_ar, stage_en, updated) = row
    return ProjectRecord(
        code=code, name_ar=name_ar, name_en=name_en,
        description_ar=desc_ar, description_en=desc_en, status=status,
        start_date=start, planned_end_date=end,
        budget_sar=float(budget) if budget is not None else None,
        sponsor_ar=sponsor_ar, sponsor_en=sponsor_en, recorded_at=created,
        owner_ar=owner_ar, owner_en=owner_en,
        manager_ar=manager_ar, manager_en=manager_en,
        completion_percent=completion,
        phase_ar=phase_ar, phase_en=phase_en,
        stage_ar=stage_ar, stage_en=stage_en, updated_at=updated,
    )
