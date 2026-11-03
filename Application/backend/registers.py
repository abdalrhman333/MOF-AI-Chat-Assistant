"""The project's registers — risks, issues, change requests, deliverables.

BRD v1.1 §5.3.6 names all four as inputs to this assistant. Until migration
018 they existed only as prose: the corpus contains Risk Register
*documents*, so "what does the risk register say" was answerable and "which
risks are high and still open" was not. A sentence has no `status` to
filter on.

⚠️ **These are rows, and they stay rows.** They are fetched with SQL and
handed to the model as a labelled block, exactly as the project record is —
never chunked and embedded. Embedding a register row destroys the field
structure that makes it answerable: "risk level عالٍ, owner فيصل العتيبي,
status نشط" retrieved as a similarity match is strictly worse than the same
row read whole. Retrieval is for documents; SQL is for registers.

⚠️ **Each register is capped** (`AnswerSettings.max_register_rows`). A real
project can carry hundreds of risks, and the whole point of AC08 is that the
prompt stays inside a time budget. When a register is truncated the block
says so, so the model can tell the user it is seeing part of a list rather
than silently answering from the first twenty rows as though they were all.

The explicit `WHERE project_code = %s` is defence in depth rather than the
boundary — unlike `projects` (see `spm.py`, migration 014), these tables
have one narrow policy each. It is kept because a policy widened later for
some other caller would otherwise turn this into a silent cross-project
read, which is exactly how §16a's leak happened.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field

import observability
from observability import tracing
from Application.backend.errors import BackendUnavailable
from Application.backend.session import Session
from pipeline.config import Settings, get_settings

# ⚠️ The discriminating fields come FIRST in every row — status, then level
# or priority, then owner — because that is the order a reader filters in.
# With `description` leading, the fields a question actually turns on sat
# behind ninety characters of Arabic prose on every line.
#
# ⚠️ Status values are NOT a controlled vocabulary here. They were extracted
# from six different Risk Register documents, and the six authors used six
# different words: نشط, مراقَب, قيد المتابعة على مستوى الفريق, مُصعّد إلى لجنة
# التوجيه. That is honest to the source and it is also why ordering cannot
# hard-code one "open" value. A real SPM supplies the list (REF lookups on
# the schema sheet); until it does, the rows are ordered by id and the
# heading reports whatever statuses are actually present.
_RISKS = """
    SELECT risk_id, status, risk_level, risk_owner, due_date, description,
           category, probability, impact, mitigation_plan, closure_notes,
           source
    FROM risks WHERE project_code = %s ORDER BY risk_id LIMIT %s
"""
_ISSUES = """
    SELECT issue_id, status, priority, owner, raised_date, resolved_date,
           issue_title, description, resolution
    FROM issues WHERE project_code = %s ORDER BY issue_id LIMIT %s
"""
_CHANGES = """
    SELECT cr_id, status, cr_type, schedule_impact_days, requested_date,
           decision_date, cr_title, justification, impact_description
    FROM change_requests WHERE project_code = %s ORDER BY cr_id LIMIT %s
"""
_DELIVERABLES = """
    SELECT deliverable_id, status, delivered, committed_date,
           actual_delivery_date, acceptance_status, deliverable_name,
           deliverable_type, weight_pct
    FROM deliverables WHERE project_code = %s
    ORDER BY committed_date NULLS LAST, deliverable_id LIMIT %s
"""


def _d(value) -> str:
    if isinstance(value, (dt.date, dt.datetime)):
        return f"{value:%Y-%m-%d}"
    return "" if value is None else str(value).strip()


# A risk's title is the opening clause of its description, so printing both
# spends tokens to say one thing twice — on this corpus it was a quarter of
# the risks block. The threshold keeps short values safe: a status of "نشط"
# appearing inside a mitigation sentence must not delete the status.
_DUPLICATE_MIN_LENGTH = 15


def _without_duplicates(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop any field whose whole content already opens a longer field."""
    kept = []
    for i, (column, value) in enumerate(values):
        redundant = (
            len(value) >= _DUPLICATE_MIN_LENGTH
            and any(other.startswith(value) and len(other) > len(value)
                    for j, (_, other) in enumerate(values) if j != i))
        if not redundant:
            kept.append((column, value))
    return kept


@dataclass(frozen=True)
class Register:
    """One register: its rows, and whether the reader is seeing all of them."""
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    truncated: bool = False
    # status -> count, computed here rather than left to the model.
    breakdown: tuple[tuple[str, int], ...] = ()

    def as_prompt_block(self) -> str:
        if not self.rows:
            return f"{self.name}: none recorded"
        # ⚠️ The heading carries the counts ALREADY TALLIED. Asked how many
        # risks were active, the model read 11 rows and answered 11; the
        # register held 7 active and 4 monitored. Counting ten fields across
        # eleven rows is work a language model does unreliably and SQL does
        # exactly, so the count is computed and handed over. Rule 4b of the
        # system prompt tells the model to trust this line over its own tally.
        parts = ", ".join(f"{s}: {n}" for s, n in self.breakdown)
        head = f"{self.name} ({len(self.rows)} total"
        head += f" — {parts}" if parts else ""
        head += ", list truncated)" if self.truncated else ")"
        lines = [head]
        for row in self.rows:
            values = [(c, _d(v)) for c, v in zip(self.columns, row) if _d(v)]
            lines.append("  - " + " | ".join(
                f"{c}: {v}" for c, v in _without_duplicates(values)))
        return "\n".join(lines)


@dataclass(frozen=True)
class Registers:
    """Everything §5.3.6 asks for beyond the project record and its documents."""
    risks: Register
    issues: Register
    change_requests: Register
    deliverables: Register

    @property
    def all(self) -> tuple[Register, ...]:
        return (self.risks, self.issues, self.change_requests,
                self.deliverables)

    @property
    def is_empty(self) -> bool:
        return not any(r.rows for r in self.all)

    def as_prompt_block(self) -> str:
        """Rendered for the model: four labelled lists, one row per line.

        An empty register is stated as "none recorded" rather than omitted.
        A missing heading invites the model to treat the register as
        unavailable; "none recorded" is a fact it can report.
        """
        return "\n\n".join(r.as_prompt_block() for r in self.all)

    def counts(self) -> dict[str, int]:
        return {r.name: len(r.rows) for r in self.all}


_SPEC = (
    ("Risks", _RISKS,
     ("id", "status", "level", "owner", "due", "description", "category",
      "probability", "impact", "mitigation", "closure", "source")),
    ("Issues", _ISSUES,
     ("id", "status", "priority", "owner", "raised", "resolved", "title",
      "description", "resolution")),
    ("Change requests", _CHANGES,
     ("id", "status", "type", "schedule impact days", "requested", "decided",
      "title", "justification", "impact")),
    ("Deliverables", _DELIVERABLES,
     ("id", "status", "delivered", "committed", "actual delivery",
      "acceptance", "name", "type", "weight")),
)
# Which column each register is counted by in its heading.
_BREAKDOWN_COLUMN = "status"


def _count_by(rows: tuple[tuple, ...],
              columns: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    """Tally the rows by status, in first-seen order.

    The counts the model would otherwise have to make by reading. Order is
    first-seen rather than alphabetical so the breakdown reads in the same
    order as the rows beneath it.
    """
    if _BREAKDOWN_COLUMN not in columns:
        return ()
    at = columns.index(_BREAKDOWN_COLUMN)
    counts: dict[str, int] = {}
    for row in rows:
        value = _d(row[at]) if at < len(row) else ""
        if value:
            counts[value] = counts.get(value, 0) + 1
    return tuple(counts.items())


def fetch(session: Session, *, settings: Settings | None = None,
          connection=None) -> Registers:
    """The session's own registers. Empty ones are empty, not missing.

    Never returns `None`: a project with no risks recorded is a fact worth
    stating, and a caller that has to distinguish "no rows" from "no object"
    will eventually get it wrong.

    A database that cannot be reached raises, like every other source.
    """
    resolved = settings or get_settings()
    cap = resolved.answering.max_register_rows
    capture = resolved.observability.capture_content
    try:
        with (
            observability.span("registers.fetch",
                               project=session.project_code) as current,
            session.scoped(settings=settings, connection=connection) as cursor,
        ):
            tracing.record_content(current, capture,
                                   input={"project": session.project_code,
                                          "limit": cap})
            built = []
            for name, sql, columns in _SPEC:
                # One extra row is read so truncation can be DETECTED rather
                # than assumed from a full page — a register holding exactly
                # `cap` rows is complete, not cut off.
                rows = cursor.execute(
                    sql, (session.project_code, cap + 1)).fetchall()
                kept = tuple(rows[:cap])
                built.append(Register(name=name, columns=columns, rows=kept,
                                      truncated=len(rows) > cap,
                                      breakdown=_count_by(kept, columns)))
            registers = Registers(*built)
            tracing.record_content(current, capture,
                                   output=registers.counts())
    except BackendUnavailable:
        raise
    except Exception as exc:                                # noqa: BLE001
        raise BackendUnavailable(f"could not read the registers: {exc}") from exc
    return registers


EMPTY = Registers(*[Register(name=n, columns=c, rows=()) for n, _, c in _SPEC])
