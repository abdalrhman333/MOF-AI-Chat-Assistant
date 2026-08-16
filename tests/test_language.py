"""Tests for AC01 — language verification.

**The claim under test is that an unsupported language is rejected and a
supported one is named correctly**, because AC05's reply-in-kind rests on
the second half of that.

Three traps get disproportionate attention here, because each is a way the
check can look right and be wrong:

  1. **Arabic-Indic digits are inside the Arabic Unicode block.** If they
     counted as letters, `١٢٣` would be an Arabic sentence.
  2. **A Latin project code is not a second language.** `ما حالة PRJ-06؟`
     is the most common shape of question this system will receive, and
     calling it "mixed" would weaken AC05 across the board.
  3. **Hebrew is not Arabic.** Two right-to-left scripts, and a check that
     conflated them would accept an unsupported language.

The statistical discriminator is stubbed throughout, so these tests neither
depend on py3langid's accuracy nor measure it — `evaluation/language_probe`
does that separately, against real strings.
"""
from __future__ import annotations

import dataclasses

import pytest

from Application.backend import language as lang
from pipeline.config import get_settings

SETTINGS = get_settings()


def detect(text, *, discriminator=None, settings=None):
    """Default the discriminator to 'always English' so script logic is
    isolated. A test about French supplies its own."""
    return lang.detect(text, settings=settings or SETTINGS,
                       discriminator=discriminator or (lambda _t: "en"))


def with_language(**overrides):
    return dataclasses.replace(
        SETTINGS, language=dataclasses.replace(SETTINGS.language, **overrides)
    )


# ---------------------------------------------------------------------------
# The supported languages
# ---------------------------------------------------------------------------

def test_pure_arabic_is_arabic_and_supported():
    verdict = detect("ما هي الميزانية المعتمدة للمشروع؟")
    assert verdict.language == lang.ARABIC
    assert verdict.supported
    assert verdict.primary == lang.ARABIC
    assert verdict.reason == lang.REASON_OK


def test_pure_english_is_english_and_supported():
    verdict = detect("What is the approved budget for this project?")
    assert verdict.language == lang.ENGLISH
    assert verdict.supported
    assert verdict.primary == lang.ENGLISH


def test_genuinely_mixed_input_is_mixed_and_supported():
    verdict = detect("ما هي الميزانية approved budget للمشروع الحالي؟")
    assert verdict.language == lang.MIXED
    assert verdict.supported
    assert verdict.is_mixed


def test_mixed_primary_follows_the_dominant_script():
    mostly_arabic = detect("ما هي الميزانية المعتمدة والحالة budget status")
    mostly_english = detect("What is the budget and status الميزانية")
    assert mostly_arabic.primary == lang.ARABIC
    assert mostly_english.primary == lang.ENGLISH


# ---------------------------------------------------------------------------
# Trap 1 — Arabic-Indic digits are digits, not Arabic letters
# ---------------------------------------------------------------------------

def test_arabic_indic_digits_carry_no_language_signal():
    """`١٢٣` sits inside the Arabic Unicode block but is category Nd.

    If it were counted as script, a number typed on an Arabic keyboard
    would be classified as an Arabic sentence.
    """
    verdict = detect("١٢٣٤٥")
    assert verdict.language == lang.UNDETERMINED
    assert verdict.reason == lang.REASON_NO_LETTERS
    assert verdict.arabic_share == 0.0


def test_arabic_indic_digits_do_not_tip_an_english_question():
    verdict = detect("What is the budget ١٢٣٤٥٦٧٨٩٠ for this project?")
    assert verdict.language == lang.ENGLISH


# ---------------------------------------------------------------------------
# Trap 2 — a Latin project code is not a second language
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "ما حالة PRJ-06؟",
    "ما هي مخاطر المشروع PRJ-03 الحالية؟",
    "متى ينتهي مشروع PRJ-01 حسب الخطة المعتمدة؟",
])
def test_arabic_question_containing_a_latin_project_code_stays_arabic(question):
    verdict = detect(question)
    assert verdict.language == lang.ARABIC, verdict
    assert verdict.primary == lang.ARABIC


def test_english_question_containing_arabic_project_name_stays_english():
    verdict = detect("What is the status of the إنجاز programme this quarter?")
    assert verdict.language == lang.ENGLISH


def test_a_token_containing_a_digit_contributes_no_language_signal():
    """The rule that makes trap 2 survivable, tested directly.

    `ما حالة PRJ-06؟` is 33% Latin letters by raw count — over any sane
    mixed threshold. Dropping digit-bearing tokens is what keeps it Arabic,
    so the rule is pinned here rather than only implied by the case above.
    """
    assert lang._is_identifier("PRJ-06")
    assert lang._is_identifier("2026")
    assert lang._is_identifier("Q4")
    assert not lang._is_identifier("budget")
    assert not lang._is_identifier("الميزانية")

    with_code = detect("ما حالة PRJ-06؟")
    without_code = detect("ما حالة؟")
    assert with_code.latin_share == without_code.latin_share == 0.0


def test_every_numeric_category_is_an_identifier_not_just_decimal_digits():
    """Found by mutation testing: `Nd` alone was an arbitrary narrowing.

    Fractions are category `No` and Roman numerals are `Nl`. Left out of
    this rule they fell through to `_classify_char`, so two rules guarded
    the same thing and neither could be tested independently of the other.
    """
    assert lang._is_identifier("½"), "fraction, category No"
    assert lang._is_identifier("Ⅷ"), "Roman numeral, category Nl"
    assert detect("Budget ½ ⅓ ¼ ⅔").language == lang.ENGLISH


def test_dropping_identifiers_does_not_hide_real_code_switching():
    """The narrow rule must not become a blanket 'ignore Latin' rule."""
    verdict = detect("ما هي الميزانية approved budget لمشروع PRJ-06؟")
    assert verdict.language == lang.MIXED


# ---------------------------------------------------------------------------
# Trap 3 — and unsupported scripts generally
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question,script", [
    ("מה תקציב הפרויקט הזה", "HEBREW"),          # RTL, but not Arabic
    ("这个项目的预算是多少", "CJK"),
    ("Каков бюджет этого проекта", "CYRILLIC"),
    ("このプロジェクトの予算はいくらですか", "HIRAGANA"),
    ("इस परियोजना का बजट क्या है", "DEVANAGARI"),
    ("τι είναι ο προϋπολογισμός", "GREEK"),
])
def test_unsupported_scripts_are_rejected(question, script):
    verdict = detect(question)
    assert not verdict.supported
    assert verdict.language == lang.UNSUPPORTED
    assert verdict.reason == lang.REASON_UNSUPPORTED_SCRIPT
    assert verdict.detail.startswith(script[:4])


def test_hebrew_is_not_mistaken_for_arabic():
    """Both are right-to-left; only one is supported."""
    assert detect("מה תקציב הפרויקט").language == lang.UNSUPPORTED
    assert detect("ما ميزانية المشروع").language == lang.ARABIC


def test_unsupported_script_wins_over_an_incidental_latin_word():
    verdict = detect("这个项目的预算是多少 budget")
    assert verdict.language == lang.UNSUPPORTED
    assert verdict.reason == lang.REASON_UNSUPPORTED_SCRIPT


# ---------------------------------------------------------------------------
# No language at all is not an unsupported language
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_empty_input_is_undetermined_not_unsupported(text):
    verdict = detect(text)
    assert verdict.language == lang.UNDETERMINED
    assert verdict.reason == lang.REASON_EMPTY
    assert not verdict.supported


@pytest.mark.parametrize("text", ["12345", "?!?!...", "😀😀😀", "-- ** //"])
def test_letterless_input_is_undetermined_not_unsupported(text):
    """Telling someone who typed `?!?!` that we do not support their
    language would be simply wrong. The reason slug keeps them apart."""
    verdict = detect(text)
    assert verdict.language == lang.UNDETERMINED
    assert verdict.reason == lang.REASON_NO_LETTERS
    assert not verdict.supported


# ---------------------------------------------------------------------------
# The Latin discriminator — the honest half of AC01
# ---------------------------------------------------------------------------

def test_a_latin_script_language_that_is_not_english_is_rejected():
    """Script alone cannot do this; the discriminator is why AC01 is not
    merely a script check."""
    verdict = detect("Quel est le budget approuve pour ce projet cette annee",
                     discriminator=lambda _t: "fr")
    assert not verdict.supported
    assert verdict.language == lang.UNSUPPORTED
    assert verdict.reason == lang.REASON_UNSUPPORTED_LANGUAGE
    assert verdict.detail == "fr"


def test_short_latin_text_is_accepted_without_trusting_the_discriminator():
    """Rejection needs evidence; acceptance does not.

    The identifier answers `en` for almost any short string, so below the
    threshold its verdict is ignored — the failure we must never make is
    turning away a legitimate English question.
    """
    calls = []

    def spy(text):
        calls.append(text)
        return "fr"

    verdict = detect("budget?", discriminator=spy)
    assert verdict.language == lang.ENGLISH
    assert verdict.supported
    assert calls == [], "the discriminator must not be consulted on short text"


def test_the_discriminator_is_consulted_once_text_is_long_enough():
    calls = []

    def spy(text):
        calls.append(text)
        return "en"

    long_enough = "What is the approved budget for this project this year"
    assert len(long_enough) >= SETTINGS.language.latin_discriminator_min_chars
    detect(long_enough, discriminator=spy)
    assert len(calls) == 1


def test_arabic_never_reaches_the_discriminator():
    """It identifies Latin-script languages; consulting it on Arabic would
    be both pointless and a latency cost on the commonest path."""
    def explode(_text):
        raise AssertionError("the discriminator was called on Arabic text")

    assert detect("ما هي الميزانية المعتمدة للمشروع الحالي؟",
                  discriminator=explode).language == lang.ARABIC


def test_the_discriminator_can_be_disabled_by_configuration():
    settings = with_language(latin_discriminator_enabled=False)
    verdict = lang.detect("Quel est le budget approuve pour ce projet",
                          settings=settings,
                          discriminator=lambda _t: "fr")
    assert verdict.language == lang.ENGLISH, "disabled means script-only"


def test_a_discriminator_returning_nothing_falls_back_to_english():
    """Acceptance is the safe direction when identification fails."""
    verdict = detect("What is the approved budget for this project",
                     discriminator=lambda _t: "")
    assert verdict.language == lang.ENGLISH
    assert verdict.supported


# ---------------------------------------------------------------------------
# Nothing is hardcoded
# ---------------------------------------------------------------------------

def test_the_supported_set_is_configuration_not_a_literal():
    """A deployment that serves English only must reject Arabic — proving
    the supported list is read rather than assumed."""
    english_only = with_language(supported=("en",))
    verdict = lang.detect("ما هي الميزانية المعتمدة للمشروع؟",
                          settings=english_only,
                          discriminator=lambda _t: "en")
    assert not verdict.supported
    assert verdict.reason == lang.REASON_UNSUPPORTED_LANGUAGE
    assert verdict.primary == "", "an unsupported language has no reply language"


def test_the_mixed_threshold_is_configuration_not_a_literal():
    question = "ما هي الميزانية approved للمشروع"
    assert detect(question).language == lang.MIXED
    tolerant = with_language(mixed_min_share=0.95)
    assert lang.detect(question, settings=tolerant,
                       discriminator=lambda _t: "en").language == lang.ARABIC


def test_shares_are_reported_for_diagnosis():
    verdict = detect("ما هي الميزانية approved للمشروع")
    assert 0.0 < verdict.latin_share < verdict.arabic_share
    assert verdict.arabic_share + verdict.latin_share == pytest.approx(1.0)
