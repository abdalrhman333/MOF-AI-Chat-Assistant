"""AC01 — verify the input language, and reject what cannot be served.

> **AC01** — The system verifies the input language and rejects unsupported
> languages with a clear apology message.

Two jobs, and they are not the same job. Rejecting an unsupported language
is the stated requirement; naming the *supported* one is what AC05
(reply-in-kind) then rests on, so this module returns a verdict rather than
a boolean.

**Script first, statistics second.** Script is decisive and free: a question
written in Han, Cyrillic, Hebrew or Devanagari is unsupported no matter what
any model thinks, and deciding that by counting codepoints costs nothing
against the AC08 budget and cannot be wrong. Only when the text is
*Latin-dominant* — where script alone genuinely cannot tell English from
French — is a statistical identifier consulted.

**Three traps this counts around, each of which has a test.**

  1. **Arabic-Indic digits live inside the Arabic Unicode block.** `٠١٢٣`
     are digits, not letters. Counting them as Arabic script makes a number
     typed on an Arabic keyboard look like an Arabic sentence.
  2. **A Latin project code is not a second language.** `ما حالة PRJ-06؟` is
     an Arabic question. Reporting it as mixed would weaken AC05 for the
     single most common shape of question this system will ever receive.
  3. **Hebrew is not Arabic.** Adjacent right-to-left scripts, different
     blocks, and a naive "is it RTL" test would accept one as the other.

**Rejection needs evidence; acceptance does not.** The asymmetry is
deliberate and shows up twice. Text with no letters at all is
`UNDETERMINED`, not "unsupported" — there is no language to reject. And a
non-English verdict from the Latin discriminator is acted on **only when it
is confident**, because the failure we must never make is turning away a
legitimate English question. The cost of the opposite error — accepting a
French question and answering it badly — is a poor answer, not a locked-out
user.

⚠️ **That gate is on confidence, not on length, and the difference was
found by running it.** An earlier version ignored the identifier below 20
characters; a live conversation then answered *"Quel est le budget?"* (19
characters) in English. Length is a poor proxy — the identifier is reliable
well below that — and confidence measures the thing the rule is actually
about: its one false positive on real English (*"When does it end?"* →
German) scores 0.473, while genuine non-English sentences score 0.997–1.000.

Nothing here performs I/O or calls a network service, so the whole decision
space is testable exhaustively and adds no latency to a question.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from pipeline.config import LanguageSettings, Settings, get_settings

ARABIC = "ar"
ENGLISH = "en"
MIXED = "mixed"
UNSUPPORTED = "unsupported"
UNDETERMINED = "undetermined"

# Reason slugs. Short, stable and greppable — the same discipline as
# `AccessDenied.reason`, and for the same reason: prose belongs in a
# rendered message, never in a log or a branch condition.
REASON_OK = "supported"
REASON_EMPTY = "empty_input"
REASON_NO_LETTERS = "no_letters"
REASON_UNSUPPORTED_SCRIPT = "unsupported_script"
REASON_UNSUPPORTED_LANGUAGE = "unsupported_language"

# The Unicode script blocks that carry Arabic *letters*. Arabic-Indic digits
# (U+0660–0669, U+06F0–06F9) fall inside these ranges and are excluded by
# category, not by these bounds — at token level by `_is_identifier`, and at
# character level by `_classify_char`.
_ARABIC_RANGES = (
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x0870, 0x089F),   # Arabic Extended-B
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
)

_LATIN_RANGES = (
    (0x0041, 0x005A),   # A-Z
    (0x0061, 0x007A),   # a-z
    (0x00C0, 0x024F),   # Latin-1 Supplement letters, Extended-A and -B
)

LATIN = "latin"
OTHER = "other"


@dataclass(frozen=True)
class LanguageVerdict:
    """What the language check decided, and enough detail to act on it.

    `language` is the answer AC05 needs; `supported` is the answer AC01
    needs. They are separate fields because "mixed" is supported but is not
    a language to reply in — the caller needs `primary` for that.
    """
    language: str                  # ar | en | mixed | unsupported | undetermined
    supported: bool
    reason: str
    primary: str = ""              # the language to reply in; "" if unknown
    detail: str = ""               # script or language code, for logs only
    arabic_share: float = 0.0
    latin_share: float = 0.0

    @property
    def is_mixed(self) -> bool:
        return self.language == MIXED


def _classify_char(char: str) -> str | None:
    """Which script one character belongs to, or None if it carries no signal.

    Digits, punctuation, whitespace, symbols, emoji and direction marks all
    return None. That is the whole defence against trap 1: `unicodedata`
    reports Arabic-Indic digits as category `Nd`, so they never reach the
    range check below and `١٢٣` yields no language signal at all.
    """
    if not unicodedata.category(char).startswith("L"):
        return None
    code = ord(char)
    for low, high in _ARABIC_RANGES:
        if low <= code <= high:
            return ARABIC
    for low, high in _LATIN_RANGES:
        if low <= code <= high:
            return LATIN
    return OTHER


def _is_identifier(token: str) -> bool:
    """True for a token that is a code, not a word: it contains a digit.

    **This is what makes trap 2 survivable.** `ما حالة PRJ-06؟` is seven
    Arabic letters against three Latin ones — 33% Latin, comfortably over
    any sane mixed-language threshold — so counting raw letters reports the
    single most common shape of question this system will ever receive as
    bilingual. Raising the threshold instead would trade this bug for the
    opposite one, missing genuine code-switching in short questions.

    A project code, a date, a version or a ticket number is not a language.
    Requiring a numeral keeps the rule narrow: `budget` stays a word,
    `PRJ-06`, `Q4` and `2026` do not. Dropped whole, because the letters in
    `PRJ-06` are exactly the ones that mislead.

    **Any numeric category, not just decimal digits.** Mutation testing
    found `Nd` alone to be an arbitrary narrowing that left fractions (`½`,
    category `No`) and Roman numerals (`Ⅷ`, category `Nl`) to be handled one
    level down by `_classify_char` — two rules quietly guarding the same
    thing, neither testable independently of the other. Each now has exactly
    one job: this decides whether a *token* is a code, `_classify_char`
    decides whether a *character* is a letter.
    """
    return any(unicodedata.category(char).startswith("N") for char in token)


def _script_name(char: str) -> str:
    """A readable script name for the log line, e.g. `CJK`, `CYRILLIC`.

    Taken from the character's own Unicode name rather than a table we would
    have to maintain, so a script nobody anticipated still logs usefully.
    """
    try:
        return unicodedata.name(char).split()[0]
    except ValueError:                                   # unnamed codepoint
        return f"U+{ord(char):04X}"


@lru_cache(maxsize=1)
def _identifier():
    """The statistical model, loaded once.

    `norm_probs=True` is the point: the raw model returns log-probabilities,
    and the rejection rule needs a comparable confidence, not a score whose
    scale depends on the input length.
    """
    from py3langid.langid import MODEL_FILE, LanguageIdentifier
    return LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)


def _default_discriminator(text: str) -> tuple[str, float]:
    """Identify a Latin-script language, with a confidence. Injectable.

    Returns `(code, confidence)` rather than a bare code because the policy
    — *how sure must we be to turn someone away* — belongs in `detect`
    alongside the rest of AC01, not inside the identifier. Imported lazily:
    it pulls in numpy, and every test supplies its own.
    """
    code, confidence = _identifier().classify(text)
    return str(code), float(confidence)


def detect(text: str, *, settings: Settings | None = None,
           discriminator=None) -> LanguageVerdict:
    """Decide whether this question can be served, and in which language.

    `discriminator` is injectable so the Latin branch can be tested without
    the statistical model, and so swapping it stays contained to one call
    site — the same pattern as `build_llm`.
    """
    config: LanguageSettings = (settings or get_settings()).language

    if not text or not text.strip():
        return LanguageVerdict(UNDETERMINED, False, REASON_EMPTY)

    counts = {ARABIC: 0, LATIN: 0, OTHER: 0}
    first_other = ""
    for token in text.split():
        if _is_identifier(token):
            continue                      # a code, not a language — see above
        for char in token:
            script = _classify_char(char)
            if script is None:
                continue
            counts[script] += 1
            if script == OTHER and not first_other:
                first_other = char

    total = sum(counts.values())
    if total == 0:
        # Digits, punctuation or emoji only. There is no language here to
        # reject — saying "we do not support this language" about `?????`
        # would be both wrong and unhelpful.
        return LanguageVerdict(UNDETERMINED, False, REASON_NO_LETTERS)

    arabic_share = counts[ARABIC] / total
    latin_share = counts[LATIN] / total
    other_share = counts[OTHER] / total

    # An unsupported script decides the question outright. It is checked
    # before the Arabic/Latin split so that a question mostly in Han with a
    # few Latin letters is rejected rather than read as English.
    if other_share >= config.mixed_min_share:
        return LanguageVerdict(
            UNSUPPORTED, False, REASON_UNSUPPORTED_SCRIPT,
            detail=_script_name(first_other),
            arabic_share=arabic_share, latin_share=latin_share,
        )

    arabic_supported = ARABIC in config.supported
    english_supported = ENGLISH in config.supported

    if arabic_share >= config.mixed_min_share and latin_share >= config.mixed_min_share:
        # Genuinely code-switched. `primary` is the dominant script, which
        # is how the BRD's "prefer the language of its main clause" is
        # approximated without parsing clauses.
        primary = ARABIC if arabic_share >= latin_share else ENGLISH
        supported = (ARABIC in config.supported) and (ENGLISH in config.supported)
        return LanguageVerdict(
            MIXED, supported, REASON_OK if supported else REASON_UNSUPPORTED_LANGUAGE,
            primary=primary if supported else "",
            arabic_share=arabic_share, latin_share=latin_share,
        )

    if arabic_share > latin_share:
        # Trap 2 lands here: `ما حالة PRJ-06؟` is Arabic with an incidental
        # Latin code, and the code's share sits below `mixed_min_share`.
        return LanguageVerdict(
            ARABIC, arabic_supported,
            REASON_OK if arabic_supported else REASON_UNSUPPORTED_LANGUAGE,
            primary=ARABIC if arabic_supported else "", detail=ARABIC,
            arabic_share=arabic_share, latin_share=latin_share,
        )

    # Latin-dominant. Script has run out of information: English, French and
    # Spanish are indistinguishable from here, so a statistical identifier
    # decides — but a verdict that would *reject* the user is acted on only
    # when the identifier is confident. Below the threshold the text is
    # accepted, because turning away a legitimate English question is the
    # one failure this module must not make.
    code, confidence = ENGLISH, 1.0
    if config.latin_discriminator_enabled:
        identify = discriminator or _default_discriminator
        verdict = identify(text)
        code, confidence = verdict if isinstance(verdict, tuple) else (verdict, 1.0)
        code = (code or ENGLISH).lower()
        if (code not in config.supported
                and confidence < config.latin_discriminator_min_confidence):
            code = ENGLISH          # not sure enough to refuse — see above

    if code in config.supported:
        return LanguageVerdict(
            ENGLISH, english_supported,
            REASON_OK if english_supported else REASON_UNSUPPORTED_LANGUAGE,
            primary=ENGLISH if english_supported else "", detail=code,
            arabic_share=arabic_share, latin_share=latin_share,
        )
    return LanguageVerdict(
        UNSUPPORTED, False, REASON_UNSUPPORTED_LANGUAGE, detail=code,
        arabic_share=arabic_share, latin_share=latin_share,
    )
