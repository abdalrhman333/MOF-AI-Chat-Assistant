"""Measure AC01 and AC02 against a tagged probe set, offline and free.

`retrieval_eval` measures how well retrieval *ranks*. This measures the two
decisions taken **before** retrieval: was the language identified correctly,
and was the query routed correctly. Both are cheap and deterministic, so
this costs nothing and can run on every change.

**Why it exists.** Two claims are otherwise unfalsifiable. The first is that
adding a statistical Latin discriminator buys something a pure script check
could not — measurable here as the accuracy difference with it disabled. The
second is that leaving `write_action` and `cross_project` to tier 2 is a
reasoned choice rather than an omission: those probes are tagged with what
currently happens, so if a classifier is built later, the improvement is a
number.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.conversation_probe
    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.conversation_probe --json
    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m evaluation.conversation_probe --no-discriminator

Exit 0 when every probe matches, 1 otherwise — so it works as a check
without parsing the output, the same contract as `pipeline.status`.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

from Application.backend import intent as intent_module
from Application.backend import language as language_module
from pipeline.config import Settings, get_settings

PROBES_PATH = pathlib.Path(__file__).resolve().parent / "conversation_probes.json"


def load_probes(path: pathlib.Path = PROBES_PATH) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return list(raw["probes"])


def run(settings: Settings | None = None, *, probes: list[dict] | None = None,
        use_discriminator: bool = True) -> dict:
    """Score every probe. Returns a summary dict; never raises on a miss."""
    settings = settings or get_settings()
    if not use_discriminator:
        settings = dataclasses.replace(
            settings,
            language=dataclasses.replace(settings.language,
                                         latin_discriminator_enabled=False),
        )
    probes = probes if probes is not None else load_probes()

    results = []
    for probe in probes:
        verdict = language_module.detect(probe["text"], settings=settings)
        language_ok = verdict.language == probe["language"]

        expected_route = probe.get("route")
        route = None
        route_ok = True
        if verdict.supported:
            route = intent_module.classify(probe["text"], settings=settings).intent
            route_ok = expected_route is None or route == expected_route
        elif expected_route is not None:
            # Rejected before routing. Only a miss if a route was expected.
            route_ok = False

        results.append({
            "text": probe["text"],
            "expected_language": probe["language"],
            "actual_language": verdict.language,
            "language_ok": language_ok,
            "expected_route": expected_route,
            "actual_route": route,
            "route_ok": route_ok,
            "reason": verdict.reason,
            "detail": verdict.detail,
            "note": probe.get("note", ""),
        })

    by_class: dict[str, dict[str, int]] = {}
    for row in results:
        bucket = by_class.setdefault(row["expected_language"],
                                     {"n": 0, "language_ok": 0})
        bucket["n"] += 1
        bucket["language_ok"] += int(row["language_ok"])

    routed = [r for r in results if r["expected_route"] is not None]
    return {
        "n": len(results),
        "discriminator": use_discriminator,
        "language_accuracy": (sum(r["language_ok"] for r in results) / len(results)
                              if results else 0.0),
        "route_accuracy": (sum(r["route_ok"] for r in routed) / len(routed)
                           if routed else 0.0),
        "by_language": by_class,
        "misses": [r for r in results if not (r["language_ok"] and r["route_ok"])],
        "results": results,
    }


def _print(summary: dict) -> None:
    print(f"Conversation probe — {summary['n']} probes "
          f"(latin discriminator: "
          f"{'on' if summary['discriminator'] else 'OFF'})\n")
    print(f"  language accuracy : {summary['language_accuracy']:.3f}")
    print(f"  routing accuracy  : {summary['route_accuracy']:.3f}\n")
    print("  by expected language:")
    for name, bucket in sorted(summary["by_language"].items()):
        share = bucket["language_ok"] / bucket["n"]
        print(f"      {name:<14} {bucket['language_ok']:>2}/{bucket['n']:<2}  {share:.2f}")
    if summary["misses"]:
        print(f"\n  {len(summary['misses'])} miss(es):")
        for row in summary["misses"]:
            print(f"      {row['text'][:52]!r}")
            if not row["language_ok"]:
                print(f"          language: expected {row['expected_language']}, "
                      f"got {row['actual_language']} ({row['reason']}"
                      f"{'/' + row['detail'] if row['detail'] else ''})")
            if not row["route_ok"]:
                print(f"          route   : expected {row['expected_route']}, "
                      f"got {row['actual_route']}")
    else:
        print("\n  no misses")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable")
    parser.add_argument("--no-discriminator", action="store_true",
                        help="script detection only — shows what the Latin "
                             "language identifier is actually buying")
    args = parser.parse_args(argv)

    summary = run(use_discriminator=not args.no_discriminator)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print(summary)
    return 0 if not summary["misses"] else 1


if __name__ == "__main__":
    sys.exit(main())
