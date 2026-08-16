"""AC02 — tell a query needing special handling from a normal retrieval.

> **AC02** — The system can distinguish between queries needing special
> handling versus general SPM-knowledge-base retrieval.

**The BRD does not define "special handling", so this module names it.** The
categories below are derived from what this system can actually do, not
invented to fill a slot, and the ones that are *not* implemented are named
here too rather than quietly omitted.

| Intent | Handled by |
|---|---|
| `general_retrieval` | `answer.answer_question` — the built path |
| `greeting`, `courtesy` | a reply, with no retrieval and no LLM call |
| `capability` | a description of what this assistant is for |
| `session_meta` | the `Session` itself — no documents involved |
| `out_of_scope` | the relevance floor, then AC07's fallback |

**Two tiers, and the split is the whole design.**

Tier 1 is an exact, whole-utterance match against a phrase set held as data.
It fires only on things that are unambiguous alone — `hello`, `شكرا`,
`what can you do`. Its value is not classification accuracy; it is that
`hello` should not cost an embedding, a vector search and a completion only
to come back `NOT_IN_DOCUMENTS`.

Tier 2 is everything else, which goes to retrieval, where the relevance
floor already turns an out-of-scope question into an honest refusal without
calling the model (§15b). That mechanism is built, measured, and verified
live — re-deciding the same thing with an LLM classifier would add a
round-trip to *every* question against a five-second budget.

**What is deliberately left to tier 2, and why.** Two categories have a
clear special handling in principle — a request to change data
(`write_action`; the assistant is read-only by rule) and a question about
another project (`cross_project`; AC09 locks the session to one). Both are
already *safe* in tier 2: `spm_app` is `SELECT`-only so a write cannot
happen whatever the model is asked, and a question about another project
retrieves nothing from this project's collection and refuses (verified
§15c). What tier 2 gives them is a generic refusal rather than a specific
one, which is a wording problem, not a correctness one. They are left
measured rather than built, so a classifier is justified by observed
misrouting instead of by anticipation.

**Why matching is whole-utterance.** `مرحبا` is a greeting;
`مرحبا، ما ميزانية المشروع؟` is a question with a greeting attached.
Substring matching would answer the first and lose the second, which is the
obvious way this kind of router fails.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from pipeline.config import IntentSettings, Settings, get_settings

GENERAL_RETRIEVAL = "general_retrieval"
GREETING = "greeting"
COURTESY = "courtesy"
CAPABILITY = "capability"
SESSION_META = "session_meta"

# Reached through the relevance floor rather than through tier 1 — named
# here because `conversation.py` renders it, and a slug used in two modules
# should be defined in one.
OUT_OF_SCOPE = "out_of_scope"

TIER_PHRASE = "phrase"          # matched the tier-1 phrase set
TIER_RETRIEVAL = "retrieval"    # deferred to the relevance floor

# Arabic orthographic variants that carry no meaning for matching. A user
# typing `أهلا` and one typing `اهلا` mean the same thing, and a phrase set
# that had to enumerate every spelling would be unmaintainable in exactly
# the language most of this corpus is written in.
_DIACRITICS = re.compile(r"[ً-ٰٟۖ-ۭـ]")
_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي",
    "ة": "ه",
    "ؤ": "و",
})


@dataclass(frozen=True)
class IntentVerdict:
    """What to do with this query.

    `needs_retrieval` is the field callers branch on, and it is derived
    rather than stored so a new intent cannot be added without deciding
    which side of the line it falls on.
    """
    intent: str
    tier: str
    matched_phrase: str = ""

    @property
    def needs_retrieval(self) -> bool:
        return self.intent == GENERAL_RETRIEVAL


def normalise(text: str) -> str:
    """Fold a query to its comparable form.

    Lowercased, Unicode-normalised, Arabic diacritics and tatweel removed,
    orthographic variants folded, punctuation dropped and whitespace
    collapsed. Punctuation removal is what lets `hello!` and `مرحبا؟` match
    the plain phrases without the file having to list every ending.
    """
    folded = unicodedata.normalize("NFKC", text).strip().lower()
    folded = _DIACRITICS.sub("", folded).translate(_FOLD)
    stripped = "".join(
        char if (unicodedata.category(char)[0] in "LN") else " "
        for char in folded
    )
    return " ".join(stripped.split())


@lru_cache(maxsize=8)
def _load(path_str: str, mtime: float) -> dict[str, tuple[str, ...]]:
    """Read and normalise the phrase set once per file version.

    `mtime` is part of the cache key rather than being ignored: editing the
    phrase file during development should take effect, and a cache that
    silently served a stale set would be debugged as a matching bug.
    """
    import pathlib
    raw = json.loads(pathlib.Path(path_str).read_text(encoding="utf-8"))
    return {
        intent: tuple(normalise(p) for p in phrases)
        for intent, phrases in raw.items()
        if not intent.startswith("_") and isinstance(phrases, list)
    }


def load_phrases(settings: Settings | None = None) -> dict[str, tuple[str, ...]]:
    config: IntentSettings = (settings or get_settings()).intent
    path = config.phrases_path
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # A missing phrase file must not take the assistant down. Tier 1
        # simply matches nothing and every query reaches retrieval, which is
        # the behaviour that existed before this module — degraded, not
        # broken, and in the direction that still answers questions.
        return {}
    return _load(str(path), mtime)


def classify(text: str, *, settings: Settings | None = None,
             phrases: dict[str, tuple[str, ...]] | None = None) -> IntentVerdict:
    """Route one query. Never raises; an unknown query is a normal query."""
    config: IntentSettings = (settings or get_settings()).intent
    table = phrases if phrases is not None else load_phrases(settings)

    normalised = normalise(text)
    if not normalised:
        return IntentVerdict(GENERAL_RETRIEVAL, TIER_RETRIEVAL)

    # The whole-utterance rule. A long utterance is a question even if it
    # opens with a greeting, so tier 1 does not get to look at it.
    if len(normalised.split()) > config.match_max_words:
        return IntentVerdict(GENERAL_RETRIEVAL, TIER_RETRIEVAL)

    for intent, candidates in table.items():
        if normalised in candidates:
            return IntentVerdict(intent, TIER_PHRASE, matched_phrase=normalised)

    return IntentVerdict(GENERAL_RETRIEVAL, TIER_RETRIEVAL)
