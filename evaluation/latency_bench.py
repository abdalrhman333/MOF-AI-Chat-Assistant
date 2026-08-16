"""AC08 — measure the five-second budget instead of asserting it.

> **AC08** — Response time does not exceed 5 seconds under normal
> conditions.

`retrieval_eval` measures *quality*; `conversation_probe` measures AC01 and
AC02 *offline*. This measures the thing AC08 is actually about: how long a
real turn takes, end to end, against the real corpus and the real models.

**It reports percentiles, not an average.** "Does not exceed 5 seconds" is
a statement about the tail, and a mean hides exactly the turns that break
it. p95 and max are the numbers that answer the requirement; the mean is
reported only because it is what people ask for.

**It separates the phases, because they have different fixes.** Embedding
is a client and connection problem, search is Qdrant, and the LLM call is
the model — and knowing which one moved is the difference between tuning
and guessing. The breakdown already exists on every `Reply`; this
aggregates it.

**`--cold` is the honest comparison.** By default the clients are warmed
first, because that is what a long-lived HTTP process does and what the
deployed system will be. `--cold` resets them and measures a fresh
process's first question, which is a genuinely different number and the one
that used to be quoted as a failure.

The question set is `evaluation/questions.json` — the same real questions
the retrieval evaluation uses, so this measures work the system actually
does. Projects with no assignment cannot have a session opened at all
(AC09), so their questions are skipped and reported as skipped rather than
silently dropped.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.latency_bench
    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.latency_bench --cold
    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.latency_bench --json

⚠️ **This costs money** — one embedding and one completion per question.
The whole 29-question set is a few cents. `--limit N` exists.

Exit 0 when p95 is inside the budget, 1 when it is not, so it works as a
regression check without parsing the output.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

from Application.backend import clients, db
from Application.backend.conversation import handle_question
from Application.backend.errors import AccessError
from Application.backend.session import open_session
from evaluation.dataset import load
from pipeline.config import Settings, get_settings
from pipeline.db import admin_connection

BUDGET_SECONDS = 5.0


def assigned_users(connection) -> dict[str, str]:
    """One username per project that actually has an assignment.

    Read as the admin role because this is a harness, not the application:
    it is choosing *whose* session to open, which is a question the app is
    never allowed to ask. The session it then opens goes through the gate
    like any other.
    """
    rows = connection.execute(
        "SELECT project_code, min(username) FROM project_assignments "
        "GROUP BY project_code"
    ).fetchall()
    return {code: user for code, user in rows}


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round(fraction * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


def run(settings: Settings | None = None, *, limit: int | None = None,
        cold: bool = False, warm: bool = True) -> dict:
    settings = settings or get_settings()
    dataset = load(settings.evaluation.questions_path)

    with admin_connection() as connection:
        users = assigned_users(connection)

    if cold:
        clients.reset()
        warmup = None
    elif warm:
        warmup = clients.warmup(settings)
    else:
        warmup = None

    results, skipped = [], []
    questions = dataset.questions[:limit] if limit else dataset.questions

    for question in questions:
        username = users.get(question.project)
        if not username:
            skipped.append({"id": question.id, "project": question.project,
                            "why": "no assignment — AC09 forbids a session"})
            continue
        try:
            session = open_session(username, question.project)
        except AccessError as exc:
            skipped.append({"id": question.id, "project": question.project,
                            "why": f"{type(exc).__name__}"})
            continue

        started = time.perf_counter()
        reply = handle_question(session, question.text, settings=settings)
        elapsed = (time.perf_counter() - started) * 1000

        results.append({
            "id": question.id, "project": question.project,
            "lang": question.lang, "outcome": reply.outcome,
            "total_ms": round(elapsed), "embed_ms": reply.timings.embed_ms,
            "search_ms": reply.timings.search_ms,
            "spm_ms": reply.timings.spm_ms, "llm_ms": reply.timings.llm_ms,
            "within_budget": elapsed <= BUDGET_SECONDS * 1000,
        })

    totals = [r["total_ms"] for r in results]
    summary = {
        "n": len(results),
        "skipped": skipped,
        "cold": cold,
        "warmup": warmup,
        "budget_ms": int(BUDGET_SECONDS * 1000),
        "within_budget": sum(r["within_budget"] for r in results),
        "mean_ms": round(statistics.fmean(totals)) if totals else 0,
        "p50_ms": round(percentile(totals, 0.50)),
        "p95_ms": round(percentile(totals, 0.95)),
        "max_ms": max(totals) if totals else 0,
        "phases": {
            phase: round(statistics.fmean([r[phase] for r in results]))
            for phase in ("embed_ms", "search_ms", "spm_ms", "llm_ms")
        } if results else {},
        "by_language": {},
        "results": results,
    }
    for lang in sorted({r["lang"] for r in results}):
        subset = [r["total_ms"] for r in results if r["lang"] == lang]
        summary["by_language"][lang] = {
            "n": len(subset), "p50_ms": round(percentile(subset, 0.50)),
            "p95_ms": round(percentile(subset, 0.95)),
        }
    summary["ok"] = bool(totals) and summary["p95_ms"] <= summary["budget_ms"]
    return summary


def _print(summary: dict) -> None:
    state = "WITHIN BUDGET" if summary["ok"] else "OVER BUDGET"
    mode = "cold (clients reset)" if summary["cold"] else "warm (clients pre-built)"
    print(f"AC08 latency — {summary['n']} turns, {mode} — {state}\n")
    if summary.get("warmup"):
        print(f"  warmup      : {summary['warmup']}\n")
    print(f"  budget      : {summary['budget_ms']} ms")
    print(f"  within      : {summary['within_budget']}/{summary['n']}")
    print(f"  p50 / p95   : {summary['p50_ms']} / {summary['p95_ms']} ms")
    print(f"  mean / max  : {summary['mean_ms']} / {summary['max_ms']} ms\n")
    if summary["phases"]:
        print("  mean per phase:")
        for phase, value in summary["phases"].items():
            print(f"      {phase:<12} {value:>6} ms")
    if summary["by_language"]:
        print("\n  by language:")
        for lang, bucket in summary["by_language"].items():
            print(f"      {lang:<6} n={bucket['n']:<3} "
                  f"p50={bucket['p50_ms']:>5} p95={bucket['p95_ms']:>5}")
    over = [r for r in summary["results"] if not r["within_budget"]]
    if over:
        print(f"\n  {len(over)} turn(s) over budget:")
        for row in over:
            print(f"      {row['id']:<18} {row['total_ms']:>6} ms "
                  f"(llm {row['llm_ms']}, embed {row['embed_ms']})")
    if summary["skipped"]:
        print(f"\n  {len(summary['skipped'])} skipped "
              f"(no assignment — a session cannot be opened at all):")
        for row in summary["skipped"][:5]:
            print(f"      {row['id']:<18} {row['project']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure AC08 end to end.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cold", action="store_true",
                        help="reset the client cache first — a fresh process")
    parser.add_argument("--no-warmup", action="store_true",
                        help="warm nothing; pay setup on the first question")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run(limit=args.limit, cold=args.cold,
                      warm=not args.no_warmup)
    finally:
        db.close_pool()

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print(summary)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
