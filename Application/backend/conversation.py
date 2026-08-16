"""The conversational entry point — where AC01 and AC02 are actually applied.

`answer.answer_question` is the grounded-answering *engine*: given a session
and a question, it produces a cited answer. This module decides whether that
engine should be asked at all, and is where the rest of the conversational
layer (AC03 continuity, AC04 welcome) will land.

The order is fixed and matters:

    1. AC01  verify the language     -> reject before spending anything
    2. AC02  classify the intent     -> answer cheaply where possible
    3. AC06  retrieve and ground     -> answer.answer_question
    4. AC07  fallback                -> when the sources do not answer it

**Rejection happens before any paid call.** An unsupported language costs no
embedding, no vector search and no completion — which is the point of doing
AC01 here rather than inside the answering path, and is asserted by a test
that counts calls against stubs rather than trusting the reading.

⚠️ **This is an entry point, not a chokepoint.** `answer_question` remains
importable and callable directly — `START_HERE.md`'s own example does
exactly that — so AC01 is enforced for callers that come through here, not
by construction the way `Session` enforces AC09. That is a deliberate
trade: duplicating the language check inside the answering path would put
the same rule in two places, and the rule that must never be bypassable
(the project scope) is already structural. Anything user-facing must enter
here.

**AC05 gets stronger as a side effect.** Once the language is known
deterministically, the answering prompt is *told* which language to reply
in instead of inferring it from the question text. The prompt's own
reply-in-kind rule stays in place as the fallback for mixed input.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Application.backend import intent as intent_module
from Application.backend import language as language_module
from Application.backend import messages
from Application.backend.answer import NOT_FOUND, Answer, Citation, Timings
from Application.backend.answer import answer_question
from Application.backend.language import (
    ARABIC,
    ENGLISH,
    MIXED,
    REASON_EMPTY,
    REASON_NO_LETTERS,
    LanguageVerdict,
)
from Application.backend.session import Session
from pipeline.config import Settings, get_settings

# Outcome slugs, distinct from intents: an intent is what the user asked
# for, an outcome is what we did about it. They differ exactly where it
# matters — `general_retrieval` can end as `answered` or as `out_of_scope`.
OUTCOME_ANSWERED = "answered"
OUTCOME_REJECTED_LANGUAGE = "rejected_language"
OUTCOME_NO_LANGUAGE = "no_language_detected"
OUTCOME_HANDLED = "handled_without_retrieval"
OUTCOME_OUT_OF_SCOPE = "out_of_scope"


@dataclass
class Reply:
    """One turn's response, uniform whatever path produced it.

    A single shape for every outcome is what lets an HTTP layer render a
    rejection, a greeting and a cited answer without branching on type —
    and what makes "did this cost a model call?" answerable from the
    response itself.
    """
    text: str
    outcome: str
    intent: str = ""
    language: str = ""
    reply_language: str = ""
    reason: str = ""
    citations: list[Citation] = field(default_factory=list)
    timings: Timings = field(default_factory=Timings)
    answer: Answer | None = None

    @property
    def grounded(self) -> bool:
        """True only when the text rests on retrieved evidence."""
        return self.outcome == OUTCOME_ANSWERED

    @property
    def used_model(self) -> bool:
        return self.timings.llm_ms > 0


def _reply_language(verdict: LanguageVerdict) -> str:
    """Which language to answer in. Mixed follows the dominant script."""
    return verdict.primary or ENGLISH


def handle_question(
    session: Session,
    text: str,
    *,
    settings: Settings | None = None,
    discriminator=None,
    phrases=None,
    **answer_kwargs,
) -> Reply:
    """Take one user turn and produce one reply.

    `**answer_kwargs` passes straight through to `answer_question`, so the
    whole path — including the parts that cost money — stays injectable and
    testable without a paid call.
    """
    settings = settings or get_settings()

    # ---- AC01 -------------------------------------------------------------
    verdict = language_module.detect(text, settings=settings,
                                     discriminator=discriminator)
    if not verdict.supported:
        # No language identified means no language to reply in, so the
        # apology is bilingual. Distinguishing "unsupported language" from
        # "no letters at all" is not pedantry: telling someone who typed
        # `???` that we do not support their language is simply wrong.
        key = ("no_language_detected"
               if verdict.reason in (REASON_EMPTY, REASON_NO_LETTERS)
               else "unsupported_language")
        outcome = (OUTCOME_NO_LANGUAGE
                   if key == "no_language_detected"
                   else OUTCOME_REJECTED_LANGUAGE)
        return Reply(
            text=messages.bilingual(key),
            outcome=outcome,
            language=verdict.language,
            reason=verdict.reason,
        )

    reply_language = _reply_language(verdict)

    # ---- AC02 -------------------------------------------------------------
    routed = intent_module.classify(text, settings=settings, phrases=phrases)
    if not routed.needs_retrieval:
        # Handled from what we already know. No embedding, no search, no
        # completion — the reason tier 1 exists.
        fields = ({"project": session.project_code}
                  if routed.intent == intent_module.SESSION_META else {})
        return Reply(
            text=messages.text(routed.intent, reply_language, **fields),
            outcome=OUTCOME_HANDLED,
            intent=routed.intent,
            language=verdict.language,
            reply_language=reply_language,
            reason=routed.tier,
        )

    # ---- AC06 -------------------------------------------------------------
    answer = answer_question(session, text, settings=settings,
                             reply_language=reply_language, **answer_kwargs)

    # ---- AC07 -------------------------------------------------------------
    if not answer.found:
        # The sources genuinely do not answer it. `NOT_IN_DOCUMENTS` is a
        # marker for us, not a sentence for a project manager — this is
        # where it becomes one, in their language.
        return Reply(
            text=messages.text("out_of_scope", reply_language),
            outcome=OUTCOME_OUT_OF_SCOPE,
            intent=intent_module.OUT_OF_SCOPE,
            language=verdict.language,
            reply_language=reply_language,
            reason=NOT_FOUND,
            timings=answer.timings,
            answer=answer,
        )

    return Reply(
        text=answer.text,
        outcome=OUTCOME_ANSWERED,
        intent=intent_module.GENERAL_RETRIEVAL,
        language=verdict.language,
        reply_language=reply_language,
        citations=answer.citations,
        timings=answer.timings,
        answer=answer,
    )


__all__ = [
    "Reply", "handle_question",
    "OUTCOME_ANSWERED", "OUTCOME_REJECTED_LANGUAGE", "OUTCOME_NO_LANGUAGE",
    "OUTCOME_HANDLED", "OUTCOME_OUT_OF_SCOPE",
    "ARABIC", "ENGLISH", "MIXED",
]
