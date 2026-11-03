"""The four registers — BRD v1.1 §5.3.6's risks, issues, change requests
and deliverables.

**Why this file exists.** Until migration 018 these reached the assistant
only as prose, because the corpus holds Risk Register *documents*. A
sentence has no `status` to filter on, so "which risks are still open" was
unanswerable while "what does the risk register say" was fine.

The three properties defended here, in rising order of how quietly they
would fail:

  1. **The block renders what the model needs**, and omits what it does not.
     Visible immediately if it regresses.
  2. **Counts are computed, not left to the model.** Found live: asked how
     many risks were active, the model read eleven rows and answered
     eleven — the register held seven active and four monitored. The
     heading now carries the tally.
  3. **The registers are charged against the same prompt budget as the
     passages.** Added on top of a full passage budget they pushed a
     measured turn from 3.5–4.8 s to 4.4–7.0 s, breaking AC08 on five
     questions out of seven. This one fails as a slow system, not a wrong
     one, which is why it needs a test rather than a reviewer.

RLS on the four new tables is NOT tested here — `test_rls.py` asks the
catalog which tables the app role can read without a policy, so it covers
tables that did not exist when it was written. That is the test working as
designed, and duplicating it here would be weaker, not stronger.
"""
from __future__ import annotations

import datetime as dt

import pytest

from Application.backend import registers as reg
from Application.backend.answer import MIN_PASSAGE_BUDGET, select_context
from Application.backend.errors import BackendUnavailable
from Application.backend.session import Session
from pipeline.config import get_settings

SESSION = Session(username="tester", project_code="PRJ-TEST")

RISK_COLUMNS = ("id", "status", "level", "owner", "due", "description",
                "category", "probability", "impact", "mitigation",
                "closure", "source")


def risk_row(risk_id=1, status="نشط", level="عالٍ", owner="سارة",
             due=None, description="تأخر التكامل مع جهة مشاركة"):
    return (risk_id, status, level, owner, due, description, "فني",
            "مرتفعة", "مرتفع", "متابعة أسبوعية", None, "Manual")


def register(rows, **kw):
    return reg.Register(name="Risks", columns=RISK_COLUMNS, rows=tuple(rows),
                        breakdown=reg._count_by(tuple(rows), RISK_COLUMNS),
                        **kw)


# ---------------------------------------------------------------------------
# The block the model reads
# ---------------------------------------------------------------------------

def test_an_empty_register_says_none_recorded_rather_than_vanishing():
    """A missing heading reads as "unavailable"; "none recorded" is a fact
    the assistant can report."""
    empty = reg.Register(name="Risks", columns=RISK_COLUMNS, rows=())
    assert empty.as_prompt_block() == "Risks: none recorded"


def test_the_heading_carries_the_counts_already_tallied():
    """The defect this exists for: the model counted rows and got it wrong."""
    block = register([risk_row(1), risk_row(2), risk_row(3, status="مراقَب")]
                     ).as_prompt_block()
    heading = block.splitlines()[0]
    assert "3 total" in heading
    assert "نشط: 2" in heading
    assert "مراقَب: 1" in heading


def test_the_breakdown_keeps_the_order_the_rows_are_in():
    block = register([risk_row(1, status="مراقَب"), risk_row(2)]).as_prompt_block()
    assert block.index("مراقَب: 1") < block.index("نشط: 1")


def test_a_status_nobody_anticipated_is_counted_like_any_other():
    """Statuses came out of six documents written by six people. A fixed
    vocabulary here would silently drop whatever did not match it."""
    block = register([risk_row(1, status="مُصعّد إلى لجنة التوجيه"),
                      risk_row(2, status="قيد المتابعة")]).as_prompt_block()
    assert "مُصعّد إلى لجنة التوجيه: 1" in block
    assert "قيد المتابعة: 1" in block


def test_an_empty_field_is_omitted_rather_than_printed_as_none():
    block = register([risk_row(due=None)]).as_prompt_block()
    assert "due:" not in block, "an absent due date must not be rendered"
    assert "None" not in block


def test_a_recorded_field_is_printed():
    block = register([risk_row(due=dt.date(2026, 9, 30))]).as_prompt_block()
    assert "due: 2026-09-30" in block


def test_a_field_repeated_inside_a_longer_one_is_printed_once():
    """A risk's title opens its description, so printing both spends tokens
    to say one thing twice — a quarter of the risks block on this corpus."""
    shared = "تأخر جاهزية التكامل لدى جهة مشاركة"
    rows = [(1, "نشط", "عالٍ", "سارة", None, f"{shared} عن النافذة المتفقة",
             shared, "مرتفعة", "مرتفع", "متابعة", None, "Manual")]
    block = register(rows).as_prompt_block()
    assert block.count(shared) == 1
    assert "عن النافذة المتفقة" in block, "the longer field is the one kept"


def test_a_short_value_inside_a_longer_one_is_still_printed():
    """The dedup must not eat a status because the word also appears in a
    mitigation sentence. Without the length floor, it would."""
    rows = [(1, "نشط", "عالٍ", "سارة", None, "وصف المخاطرة الكامل هنا",
             "فني", "مرتفعة", "مرتفع", "نشط المتابعة الأسبوعية مع الفريق",
             None, "Manual")]
    block = register(rows).as_prompt_block()
    assert "status: نشط" in block


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def test_a_truncated_register_says_so():
    block = register([risk_row(i) for i in range(3)],
                     truncated=True).as_prompt_block()
    assert "list truncated" in block.splitlines()[0]


def test_a_full_register_does_not_claim_to_be_truncated():
    block = register([risk_row(i) for i in range(3)]).as_prompt_block()
    assert "truncated" not in block


def test_a_register_holding_exactly_the_cap_is_complete_not_cut_off(monkeypatch):
    """One extra row is read so truncation is DETECTED rather than assumed
    from a full page. Exactly `cap` rows is a complete register."""
    settings = get_settings()
    cap = settings.answering.max_register_rows
    captured = {}

    class Cursor:
        def execute(self, sql, params):
            captured["limit"] = params[1]
            self.rows = [risk_row(i) for i in range(cap)]
            return self

        def fetchall(self):
            return self.rows

    monkeypatch.setattr(reg.Session, "scoped", _scoped(Cursor()))
    result = reg.fetch(SESSION, settings=settings)
    assert captured["limit"] == cap + 1, "must ask for one more than the cap"
    assert not result.risks.truncated
    assert len(result.risks.rows) == cap


def test_more_rows_than_the_cap_is_reported_as_truncated(monkeypatch):
    settings = get_settings()
    cap = settings.answering.max_register_rows

    class Cursor:
        def execute(self, sql, params):
            self.rows = [risk_row(i) for i in range(cap + 1)]
            return self

        def fetchall(self):
            return self.rows

    monkeypatch.setattr(reg.Session, "scoped", _scoped(Cursor()))
    result = reg.fetch(SESSION, settings=settings)
    assert result.risks.truncated
    assert len(result.risks.rows) == cap, "the extra row is a probe, not content"


# ---------------------------------------------------------------------------
# The read itself
# ---------------------------------------------------------------------------

def _scoped(cursor):
    from contextlib import contextmanager

    @contextmanager
    def scoped(self, *, settings=None, connection=None):
        yield cursor
    return scoped


def test_every_read_filters_by_the_session_s_project(monkeypatch):
    """The project code comes from the gate's own output, never from a
    caller argument, so this filter cannot be pointed somewhere else."""
    seen = []

    class Cursor:
        def execute(self, sql, params):
            seen.append((" ".join(sql.split()), params))
            return self

        def fetchall(self):
            return []

    monkeypatch.setattr(reg.Session, "scoped", _scoped(Cursor()))
    reg.fetch(SESSION)
    assert len(seen) == 4, "one read per register"
    for sql, params in seen:
        assert "WHERE project_code = %s" in sql
        assert params[0] == "PRJ-TEST"


def test_a_project_with_nothing_recorded_returns_empty_registers_not_none(
        monkeypatch):
    class Cursor:
        def execute(self, sql, params):
            return self

        def fetchall(self):
            return []

    monkeypatch.setattr(reg.Session, "scoped", _scoped(Cursor()))
    result = reg.fetch(SESSION)
    assert result.is_empty
    assert result.counts() == {"Risks": 0, "Issues": 0,
                               "Change requests": 0, "Deliverables": 0}
    assert "none recorded" in result.as_prompt_block()


def test_a_database_that_cannot_be_reached_raises(monkeypatch):
    class Cursor:
        def execute(self, sql, params):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(reg.Session, "scoped", _scoped(Cursor()))
    with pytest.raises(BackendUnavailable):
        reg.fetch(SESSION)


# ---------------------------------------------------------------------------
# The shared prompt budget — AC08
# ---------------------------------------------------------------------------

class Passage:
    def __init__(self, text):
        self.text = text


def test_registers_are_charged_against_the_passage_budget():
    """Measured, not argued: on top of a full passage budget the registers
    pushed a turn to 7.0 s against AC08's five seconds."""
    settings = get_settings()
    passages = [Passage("نص " * 400) for _ in range(12)]

    without = select_context(passages, settings)
    with_registers = select_context(passages, settings, reserved_tokens=3000)
    assert len(with_registers) < len(without), (
        "a large register must cost passages, not seconds")


def test_the_passage_budget_has_a_floor_however_large_the_register():
    """A register question still deserves the documents behind the rows.

    ⚠️ Asserting only that SOMETHING came back proves nothing: the selector
    always takes the first passage whatever the budget, so that assertion
    passes with the floor deleted. Found by mutation. The floor has to be
    shown to fit SEVERAL short passages.
    """
    from pipeline.chunker import count_tokens
    settings = get_settings()
    passages = [Passage("نص قصير ") for _ in range(40)]
    one = count_tokens(passages[0].text)
    assert one * 3 < MIN_PASSAGE_BUDGET, "fixture too big to test the floor"

    chosen = select_context(passages, settings, reserved_tokens=10 ** 6)
    assert len(chosen) > 3, (
        "the floor must keep room for several passages, not just the one the "
        "selector always takes")
    assert sum(count_tokens(p.text) for p in chosen) <= MIN_PASSAGE_BUDGET + one


def test_no_registers_means_the_full_passage_budget_is_available():
    settings = get_settings()
    passages = [Passage("نص " * 400) for _ in range(12)]
    assert (len(select_context(passages, settings, reserved_tokens=0))
            == len(select_context(passages, settings)))


# ---------------------------------------------------------------------------
# Through the real call paths
#
# Everything above tests a part. These four exist because mutation showed
# the parts can all pass while the wiring between them is cut: the tally
# computed but never attached, the budget shared but not at the call site,
# the block built but never put in the prompt.
# ---------------------------------------------------------------------------

def test_fetch_attaches_the_tally_to_what_it_returns(monkeypatch):
    """`_count_by` being correct is not the same as `fetch` using it."""
    class Cursor:
        def execute(self, sql, params):
            self.rows = ([risk_row(1), risk_row(2, status="مراقَب")]
                         if "FROM risks" in sql else [])
            return self

        def fetchall(self):
            return self.rows

    monkeypatch.setattr(reg.Session, "scoped", _scoped(Cursor()))
    risks = reg.fetch(SESSION).risks
    assert risks.breakdown, "the tally never reached the register"
    assert dict(risks.breakdown) == {"نشط": 1, "مراقَب": 1}
    assert "نشط: 1" in risks.as_prompt_block()


SECTION = "PROJECT REGISTERS (SPM system of record"


def _messages(registers):
    from Application.backend.answer import build_messages
    return build_messages("سؤال", [], None, "", None, registers)[0]["content"]


def test_the_prompt_carries_the_registers():
    block = _messages(reg.Registers(
        risks=register([risk_row(1)]),
        issues=reg.Register("Issues", ("id", "status"), ()),
        change_requests=reg.Register("Change requests", ("id", "status"), ()),
        deliverables=reg.Register("Deliverables", ("id", "status"), ())))
    assert SECTION in block, "the block was built and discarded"
    assert "تأخر التكامل مع جهة مشاركة" in block


def test_a_project_with_no_registers_adds_no_empty_section():
    """An empty heading in the prompt is noise the model has to read past."""
    assert SECTION not in _messages(reg.EMPTY)
    assert SECTION not in _messages(None)


def test_answer_question_charges_the_registers_to_the_passage_budget():
    """The sharing has to happen at the CALL SITE. select_context honouring
    `reserved_tokens` is useless if answer_question never passes it."""
    import inspect
    from Application.backend import answer

    source = inspect.getsource(answer.answer_question)
    assert "reserved_tokens=reserved" in source, (
        "answer_question must charge the registers against the budget")
    assert "count_tokens(register_rows.as_prompt_block())" in source, (
        "the reservation must be measured from the real block")
