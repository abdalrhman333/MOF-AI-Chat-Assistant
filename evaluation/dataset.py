"""Loading and validating the retrieval question set.

**The dataset is data.** A JSON file at a configurable path, so a different
corpus brings its own questions and no module changes — the same principle
that makes `DOCUMENT_ROOT` a setting.

**Validation against the live corpus is the load-bearing part.** A question
tagged with a document that no longer exists — renamed, re-typed, removed —
scores zero forever and looks exactly like a retrieval regression. Silently
measuring against a stale expectation is worse than not measuring, because
it produces a number that people will trust. So the reference set is checked
against `document_links` before anything is embedded, and a mismatch is an
error with the offending rows named, not a warning in a log.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

# Languages the set may tag a question with. Deliberately small and closed:
# the whole point of the breakdown is comparing Arabic against English, and
# a typo creating a fourth silent bucket would hide that.
LANGUAGES = ("ar", "en", "mixed")


class DatasetError(ValueError):
    """The question set is malformed, or disagrees with the corpus."""


@dataclass(frozen=True)
class Question:
    id: str
    project: str
    lang: str
    text: str
    # More than one document can legitimately answer a question — a budget
    # figure appears in both the charter and the budget report. A hit is
    # therefore "any expected document appeared", not "the one did".
    expect_documents: tuple[str, ...]
    note: str = ""
    # "retrieval" for the main set, which is tagged with the documents that
    # answer it. "register" for questions answered from SQL rows, which
    # retrieve nothing by design and so cannot be scored for retrieval
    # quality — they exist for the latency budget. See `load`.
    kind: str = "retrieval"


@dataclass
class Dataset:
    path: pathlib.Path
    questions: list[Question] = field(default_factory=list)

    @property
    def projects(self) -> list[str]:
        return sorted({q.project for q in self.questions})

    def by_language(self) -> dict[str, list[Question]]:
        out: dict[str, list[Question]] = {}
        for question in self.questions:
            out.setdefault(question.lang, []).append(question)
        return out

    def filtered(self, project: str | None = None,
                 lang: str | None = None) -> list[Question]:
        return [
            q for q in self.questions
            if (project is None or q.project == project)
            and (lang is None or q.lang == lang)
        ]


def load(path: pathlib.Path, *, require_documents: bool = True,
         kind: str = "retrieval") -> Dataset:
    """Read and structurally validate the question set.

    Every failure names the question id, because a 30-entry file with one
    bad row is otherwise debugged by bisection.

    `require_documents=False` loads a set whose questions are answered from
    the registers — SQL rows, never retrieval — so there is no document
    that "should" come back and `expect_documents` is legitimately empty.
    ⚠️ Such a set must never be scored by `retrieval_eval`: every question
    in it would count as a miss, and the baseline would fall for a reason
    that has nothing to do with retrieval. It is loaded by
    `latency_bench`, which measures time and does not care what answered.
    """
    if not path.is_file():
        raise DatasetError(
            f"question set not found at {path}. Set EVAL_QUESTIONS_PATH in "
            f"infra/.env, or pass --questions."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path} is not valid JSON: {exc}") from exc

    entries = raw.get("questions")
    if not isinstance(entries, list) or not entries:
        raise DatasetError(f"{path} has no 'questions' array")

    seen: set[str] = set()
    questions: list[Question] = []
    for index, entry in enumerate(entries):
        where = entry.get("id") or f"entry #{index}"
        required_fields = ["id", "project", "lang", "question"]
        if require_documents:
            required_fields.append("expect_documents")
        for required in required_fields:
            if not entry.get(required):
                raise DatasetError(f"{where}: missing or empty {required!r}")
        if entry["id"] in seen:
            raise DatasetError(f"duplicate question id {entry['id']!r}")
        seen.add(entry["id"])
        if entry["lang"] not in LANGUAGES:
            raise DatasetError(
                f"{where}: lang {entry['lang']!r} is not one of {LANGUAGES}"
            )
        expected = entry.get("expect_documents", [])
        if not isinstance(expected, list) or not all(isinstance(e, str) for e in expected):
            raise DatasetError(f"{where}: expect_documents must be a list of filenames")
        questions.append(Question(
            id=entry["id"], project=entry["project"], lang=entry["lang"],
            text=entry["question"], expect_documents=tuple(expected),
            note=entry.get("note", ""), kind=kind,
        ))
    return Dataset(path=path, questions=questions)


def validate_against_corpus(dataset: Dataset, connection) -> list[str]:
    """Check every referenced project and filename really exists.

    Returns a list of human-readable problems; empty means the set agrees
    with the corpus. Kept as a returned list rather than a raise so the CLI
    can print all of them at once — fixing a reference set one exception at
    a time is miserable.
    """
    problems: list[str] = []
    cursor = connection.cursor()

    known_projects = {r[0] for r in cursor.execute("SELECT code FROM projects").fetchall()}
    documents: dict[str, set[str]] = {}
    for code, filename in cursor.execute(
            "SELECT project_code, filename FROM document_links").fetchall():
        documents.setdefault(code, set()).add(filename)

    for question in dataset.questions:
        if question.project not in known_projects:
            problems.append(f"{question.id}: project {question.project!r} does not exist")
            continue
        available = documents.get(question.project, set())
        for expected in question.expect_documents:
            if expected not in available:
                problems.append(
                    f"{question.id}: {question.project} has no document named "
                    f"{expected!r}"
                )
    return problems


def indexed_documents(connection, project: str) -> int:
    """How many of a project's documents are actually searchable.

    A question set can be perfectly valid against `document_links` while the
    project has never been indexed, which would score zero for a reason that
    has nothing to do with retrieval quality.
    """
    return connection.cursor().execute(
        "SELECT count(*) FROM document_links "
        "WHERE project_code = %s AND indexing_status = 'indexed'",
        (project,),
    ).fetchone()[0]
