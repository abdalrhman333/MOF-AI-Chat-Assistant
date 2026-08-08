"""Measure retrieval quality against a tagged question set.

Right now nobody can answer *"did that change make retrieval better?"*.
What exists is anecdote: Arabic queries score around 0.40 where English
scores 0.60, and the correct passage often lands 2nd or 3rd rather than
1st. That is a fine observation and a useless baseline — every candidate
improvement (reranking, hybrid search, more candidates, expanding to the
parent section) buys grounding by spending seconds against the five-second
response budget, and that trade cannot be made by feel.

**MRR matters more than recall here**, which is why it leads the report.
The known problem is *ranking*, not retrieval: the right passage is in the
results almost every time, just not first. Recall@5 can sit unchanged at
1.00 while the answer moves from 3rd to 1st — a large real improvement that
recall alone cannot see. Mean reciprocal rank moves from 0.33 to 1.00 for
exactly that change.

**Scoring rule.** A question is a hit at rank *k* if any of its expected
documents appears in the top *k* results. More than one document may
legitimately answer a question — a budget figure lives in both the charter
and the budget report — so requiring a specific one would be measuring
tie-breaks rather than retrieval.

**What this costs.** One embedding call per question, batched. Roughly 25
tokens each, so a 30-question run is well under a hundredth of a cent. It
is still a paid call, so `--dry-run` validates the whole set, reports what
would run, and sends nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field

from evaluation.dataset import Dataset, Question, load, validate_against_corpus
from pipeline.config import Settings, get_settings
from pipeline.db import admin_connection


@dataclass(frozen=True)
class Hit:
    rank: int
    filename: str
    score: float
    section: str


@dataclass
class Outcome:
    question: Question
    results: list[Hit] = field(default_factory=list)
    error: str = ""

    @property
    def rank(self) -> int | None:
        """1-based rank of the first expected document, if it was found."""
        for hit in self.results:
            if hit.filename in self.question.expect_documents:
                return hit.rank
        return None

    @property
    def reciprocal_rank(self) -> float:
        rank = self.rank
        return 1.0 / rank if rank else 0.0

    @property
    def top_score(self) -> float:
        return self.results[0].score if self.results else 0.0

    def hit_at(self, k: int) -> bool:
        rank = self.rank
        return rank is not None and rank <= k


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    recall_at: tuple[int, ...] = (1, 3, 5)
    search_limit: int = 5
    seconds: float = 0.0
    dry_run: bool = False
    questions_planned: int = 0

    @property
    def scored(self) -> list[Outcome]:
        return [o for o in self.outcomes if not o.error]

    @property
    def errors(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.error]

    @property
    def misses(self) -> list[Outcome]:
        return [o for o in self.scored if o.rank is None]

    def recall(self, k: int, subset: list[Outcome] | None = None) -> float:
        pool = self.scored if subset is None else subset
        return sum(o.hit_at(k) for o in pool) / len(pool) if pool else 0.0

    def mrr(self, subset: list[Outcome] | None = None) -> float:
        pool = self.scored if subset is None else subset
        return sum(o.reciprocal_rank for o in pool) / len(pool) if pool else 0.0

    def mean_top_score(self, subset: list[Outcome] | None = None) -> float:
        pool = self.scored if subset is None else subset
        return sum(o.top_score for o in pool) / len(pool) if pool else 0.0

    def by_language(self) -> dict[str, list[Outcome]]:
        out: dict[str, list[Outcome]] = {}
        for outcome in self.scored:
            out.setdefault(outcome.question.lang, []).append(outcome)
        return dict(sorted(out.items()))

    def by_project(self) -> dict[str, list[Outcome]]:
        out: dict[str, list[Outcome]] = {}
        for outcome in self.scored:
            out.setdefault(outcome.question.project, []).append(outcome)
        return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def embed_questions(questions: list[Question], settings: Settings,
                    embedder) -> list[list[float]]:
    """One batched call. Re-sorted by the API's own index, never by arrival.

    The indexer learned this the hard way: trusting arrival order pairs
    every vector with the wrong text, and nothing anywhere reports it. The
    consequence here is subtler than in ingestion but the same shape — each
    question would be scored against another question's results.
    """
    response = embedder.embeddings.create(
        model=settings.models.embedding_deployment,
        input=[q.text for q in questions],
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    if len(ordered) != len(questions):
        raise RuntimeError(
            f"embedding count mismatch: sent {len(questions)}, got {len(ordered)}"
        )
    return [item.embedding for item in ordered]


def search(qdrant, settings: Settings, project: str,
           vector: list[float], limit: int) -> list[Hit]:
    """Search exactly one project's collection.

    The collection name comes from settings, so this cannot accidentally
    query a shared index — there isn't one. That is the AC09 isolation
    property, and it applies to the evaluation harness as much as to the
    application.
    """
    collection = settings.qdrant.collection_for(project)
    response = qdrant.query_points(
        collection_name=collection, query=vector, limit=limit, with_payload=True,
    )
    return [
        Hit(
            rank=index + 1,
            filename=(point.payload or {}).get("filename", ""),
            score=float(point.score),
            section=(point.payload or {}).get("section", ""),
        )
        for index, point in enumerate(response.points)
    ]


def run(
    project: str | None = None,
    lang: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    questions_path=None,
    settings: Settings | None = None,
    connection=None,
    embedder=None,
    qdrant=None,
    dataset: Dataset | None = None,
) -> Report:
    """Score the question set.

    Everything external is injectable, so the tests exercise the whole
    scoring path against stubs — no paid call, no live vector store.
    """
    settings = settings or get_settings()
    dataset = dataset or load(questions_path or settings.evaluation.questions_path)
    search_limit = limit or settings.evaluation.search_limit

    if connection is not None:
        _check_corpus(dataset, connection)
    else:
        with admin_connection() as conn:
            _check_corpus(dataset, conn)

    selected = dataset.filtered(project=project, lang=lang)
    if not selected:
        raise ValueError(
            f"no questions match project={project!r} lang={lang!r}; "
            f"the set covers {dataset.projects}"
        )

    report = Report(recall_at=settings.evaluation.recall_at,
                    search_limit=search_limit, dry_run=dry_run,
                    questions_planned=len(selected))
    if dry_run:
        return report

    started = time.time()
    embedder = embedder or _build_embedder(settings)
    qdrant = qdrant or _build_qdrant(settings)

    vectors = embed_questions(selected, settings, embedder)
    for question, vector in zip(selected, vectors):
        outcome = Outcome(question=question)
        try:
            outcome.results = search(qdrant, settings, question.project,
                                     vector, search_limit)
        except Exception as exc:                          # noqa: BLE001
            outcome.error = f"{type(exc).__name__}: {exc}"
        report.outcomes.append(outcome)

    report.seconds = round(time.time() - started, 2)
    return report


def _check_corpus(dataset: Dataset, connection) -> None:
    problems = validate_against_corpus(dataset, connection)
    if problems:
        raise ValueError(
            "the question set disagrees with the corpus — every one of these "
            "would score zero for a reason unrelated to retrieval quality:\n  "
            + "\n  ".join(problems)
        )


def _build_embedder(settings: Settings):
    from pipeline.indexer import build_embedder
    return build_embedder(settings)


def _build_qdrant(settings: Settings):
    from pipeline.indexer import build_qdrant
    return build_qdrant(settings)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def format_report(report: Report, show_misses: bool = True) -> str:
    if report.dry_run:
        return (f"Retrieval evaluation — DRY RUN — nothing embedded\n"
                f"  questions  : {report.questions_planned}\n"
                f"  search depth: {report.search_limit}\n"
                f"  the set validates against the corpus")

    ks = report.recall_at
    lines = [
        "Retrieval evaluation",
        "",
        f"  questions   : {len(report.scored)}"
        f"{f' ({len(report.errors)} errored)' if report.errors else ''}",
        f"  search depth: top {report.search_limit}",
        f"  elapsed     : {report.seconds}s",
        "",
        f"  MRR         : {report.mrr():.3f}   "
        f"(1.000 = always first; 0.500 = typically second)",
        "  " + "   ".join(f"recall@{k}: {report.recall(k):.2f}" for k in ks),
        f"  mean top score: {report.mean_top_score():.3f}",
    ]

    for label, groups in (("by language", report.by_language()),
                          ("by project", report.by_project())):
        lines += ["", f"  {label}:"]
        header = "  ".join(f"r@{k}".rjust(5) for k in ks)
        lines.append(f"      {'':<16} {'n':>3}  {'MRR':>5}  {header}  {'score':>6}")
        for name, pool in groups.items():
            cells = "  ".join(f"{report.recall(k, pool):.2f}".rjust(5) for k in ks)
            lines.append(
                f"      {name:<16} {len(pool):>3}  {report.mrr(pool):.3f}  "
                f"{cells}  {report.mean_top_score(pool):>6.3f}"
            )

    if show_misses and report.misses:
        lines += ["", f"  not found in the top {report.search_limit} "
                      f"({len(report.misses)}):"]
        for outcome in report.misses:
            got = ", ".join(dict.fromkeys(h.filename for h in outcome.results[:3]))
            lines.append(f"      {outcome.question.id} [{outcome.question.lang}] "
                         f"{outcome.question.project}")
            lines.append(f"          asked   : {outcome.question.text}")
            lines.append(f"          wanted  : {', '.join(outcome.question.expect_documents)}")
            lines.append(f"          got     : {got or '(nothing)'}")

    ranked = [o for o in report.scored if o.rank and o.rank > 1]
    if ranked:
        lines += ["", f"  found but not first ({len(ranked)}) — the known gap, "
                      f"and what reranking would move:"]
        for outcome in sorted(ranked, key=lambda o: -o.rank)[:8]:
            lines.append(f"      rank {outcome.rank}  {outcome.question.id} "
                         f"[{outcome.question.lang}]  {outcome.question.text[:58]}")

    if report.errors:
        lines += ["", "  ERRORED:"]
        for outcome in report.errors:
            lines.append(f"      {outcome.question.id}: {outcome.error}")

    return "\n".join(lines)


def to_dict(report: Report) -> dict:
    return {
        "dry_run": report.dry_run,
        "questions": len(report.scored),
        "search_limit": report.search_limit,
        "seconds": report.seconds,
        "mrr": round(report.mrr(), 4),
        "recall": {str(k): round(report.recall(k), 4) for k in report.recall_at},
        "mean_top_score": round(report.mean_top_score(), 4),
        "by_language": {
            lang: {
                "n": len(pool),
                "mrr": round(report.mrr(pool), 4),
                "recall": {str(k): round(report.recall(k, pool), 4)
                           for k in report.recall_at},
                "mean_top_score": round(report.mean_top_score(pool), 4),
            }
            for lang, pool in report.by_language().items()
        },
        "results": [
            {"id": o.question.id, "project": o.question.project,
             "lang": o.question.lang, "rank": o.rank,
             "top_score": round(o.top_score, 4),
             "expected": list(o.question.expect_documents),
             "returned": [h.filename for h in o.results],
             "error": o.error or None}
            for o in report.outcomes
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Measure retrieval quality against a tagged question set."
    )
    parser.add_argument("--project", help="Only questions for this project code.")
    parser.add_argument("--lang", help="Only questions in this language (ar/en/mixed).")
    parser.add_argument("--questions", help="Path to a question set (overrides settings).")
    parser.add_argument("--limit", type=int,
                        help="Search depth. Defaults to the deepest recall cut-off.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate the set and report the plan. Embeds nothing.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument("--quiet-misses", action="store_true",
                        help="Suppress the per-question miss listing.")
    args = parser.parse_args(argv)

    import pathlib
    report = run(
        project=args.project, lang=args.lang, dry_run=args.dry_run,
        limit=args.limit,
        questions_path=pathlib.Path(args.questions) if args.questions else None,
    )
    print(json.dumps(to_dict(report), ensure_ascii=False, indent=2) if args.json
          else format_report(report, show_misses=not args.quiet_misses))
    # A run that could not score every question is not a usable baseline.
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
