"""Measure the LLM router — AC01 language and AC02 intent.

**This exists because the component that now decides every message was the
one component with no eval.** When language detection and intent routing
moved out of code and into the system prompt (§21), the offline harness that
scored them became meaningless and was deleted. That left every prompt edit
unverifiable: you could change the brain and have no way to tell whether you
had improved it.

⚠️ **Unlike the harness it replaces, this costs money.** One completion per
probe, plus one embedding, because it drives the real path end to end. The
whole set is roughly 60 turns — a few cents. `--limit N` exists.

**It runs the real path on purpose.** A cheaper design would hand the model
a fixed prompt and skip retrieval, isolating the classifier. That would
measure the prompt; it would not measure the *system*. Whether a question is
`out_of_scope` genuinely depends on what retrieval returned, so a harness
that stubs retrieval cannot see the failure mode that matters most — a real
question refused because nothing was found.

**What it reports.** Accuracy is the headline, but the confusion matrix is
the useful part: knowing that `project_question` is being called
`out_of_scope` tells you the relevance floor or the corpus is the problem,
where a single accuracy number tells you nothing about where to look.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.router_eval
    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.router_eval --json
    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.router_eval --limit 10

Exit 0 when both accuracies clear their thresholds, 1 otherwise, so it works
as a release check without parsing the output.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time

from Application.backend import clients, db
from Application.backend.conversation import handle_question
from Application.backend.session import open_session
from pipeline.config import Settings, get_settings

PROBES_PATH = pathlib.Path(__file__).resolve().parent / "router_probes.json"

# The project every probe is asked against, and a user assigned to it.
# One project keeps the run cheap and the comparison fair; the cross-project
# probes reference a *different* code on purpose.
PROJECT = "PRJ-06"
USERNAME = "noura.alharbi"

# Thresholds. Language is the stricter of the two because it is the one the
# user notices immediately — being answered in the wrong language is more
# obviously broken than being greeted when you asked a question.
MIN_LANGUAGE_ACCURACY = 0.95
MIN_INTENT_ACCURACY = 0.85


def load_probes(path: pathlib.Path = PROBES_PATH) -> list[dict]:
    return list(json.loads(path.read_text(encoding="utf-8"))["probes"])


def run(settings: Settings | None = None, *, limit: int | None = None,
        probes: list[dict] | None = None, ask=None) -> dict:
    """Score every probe. `ask` is injectable so the harness itself is
    testable without spending anything."""
    settings = settings or get_settings()
    probes = (probes if probes is not None else load_probes())[:limit]

    if ask is None:
        clients.warmup(settings, probe=False)
        session = open_session(USERNAME, PROJECT, settings=settings)

        def ask(text):
            reply = handle_question(session, text, settings=settings,
                                    user_name=("نورة", "Noura"))
            return reply.language, reply.intent

    results = []
    for probe in probes:
        started = time.perf_counter()
        try:
            language, intent = ask(probe["text"])
            error = ""
        except Exception as exc:                              # noqa: BLE001
            language, intent, error = "", "", f"{type(exc).__name__}: {exc}"
        results.append({
            "text": probe["text"],
            "expected_language": probe["language"], "actual_language": language,
            "expected_intent": probe["intent"], "actual_intent": intent,
            "language_ok": language == probe["language"],
            "intent_ok": intent == probe["intent"],
            "ms": round((time.perf_counter() - started) * 1000),
            "error": error, "note": probe.get("note", ""),
        })

    n = len(results) or 1
    confusion = collections.Counter(
        (r["expected_intent"], r["actual_intent"]) for r in results
        if not r["intent_ok"])
    by_language: dict[str, dict] = {}
    for row in results:
        bucket = by_language.setdefault(row["expected_language"],
                                        {"n": 0, "ok": 0})
        bucket["n"] += 1
        bucket["ok"] += int(row["language_ok"])

    summary = {
        "n": len(results),
        "language_accuracy": sum(r["language_ok"] for r in results) / n,
        "intent_accuracy": sum(r["intent_ok"] for r in results) / n,
        "thresholds": {"language": MIN_LANGUAGE_ACCURACY,
                       "intent": MIN_INTENT_ACCURACY},
        "by_language": by_language,
        "confusion": [{"expected": e, "actual": a, "count": c}
                      for (e, a), c in confusion.most_common()],
        "errors": [r for r in results if r["error"]],
        "misses": [r for r in results
                   if not (r["language_ok"] and r["intent_ok"])],
        "mean_ms": round(sum(r["ms"] for r in results) / n),
        "results": results,
    }
    summary["ok"] = bool(results) and not summary["errors"] and (
        summary["language_accuracy"] >= MIN_LANGUAGE_ACCURACY
        and summary["intent_accuracy"] >= MIN_INTENT_ACCURACY)
    return summary


def _print(summary: dict) -> None:
    state = "PASS" if summary["ok"] else "BELOW THRESHOLD"
    print(f"Router eval — {summary['n']} probes — {state}\n")
    print(f"  language accuracy : {summary['language_accuracy']:.3f} "
          f"(needs {summary['thresholds']['language']:.2f})")
    print(f"  intent accuracy   : {summary['intent_accuracy']:.3f} "
          f"(needs {summary['thresholds']['intent']:.2f})")
    print(f"  mean latency      : {summary['mean_ms']} ms\n")

    print("  language, by expected value:")
    for name, bucket in sorted(summary["by_language"].items()):
        print(f"      {name:<8} {bucket['ok']:>2}/{bucket['n']:<3} "
              f"{bucket['ok'] / bucket['n']:.2f}")

    if summary["confusion"]:
        print("\n  intent confusion (expected -> actual):")
        for row in summary["confusion"]:
            print(f"      {row['expected']:<20} -> {row['actual']:<20} "
                  f"x{row['count']}")

    if summary["errors"]:
        print(f"\n  {len(summary['errors'])} probe(s) RAISED:")
        for row in summary["errors"][:5]:
            print(f"      {row['text'][:44]!r}  {row['error'][:70]}")

    misses = [m for m in summary["misses"] if not m["error"]]
    if misses:
        print(f"\n  {len(misses)} miss(es):")
        for row in misses:
            bits = []
            if not row["language_ok"]:
                bits.append(f"lang {row['expected_language']}→"
                            f"{row['actual_language'] or '?'}")
            if not row["intent_ok"]:
                bits.append(f"intent {row['expected_intent']}→"
                            f"{row['actual_intent'] or '?'}")
            print(f"      {row['text'][:46]:<48} {', '.join(bits)}")
            if row["note"]:
                print(f"          ({row['note']})")
    elif not summary["errors"]:
        print("\n  no misses")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the LLM router.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run(limit=args.limit)
    finally:
        db.close_pool()

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print(summary)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
