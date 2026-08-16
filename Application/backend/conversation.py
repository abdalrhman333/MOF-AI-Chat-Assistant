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
from Application.backend.retrieval import search
from Application.backend.session import Session
from pipeline.chunker import count_tokens
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


def _resolve_retrieval(session: Session, text: str, previous_question: str,
                       settings: Settings, embedder, qdrant):
    """Search for this turn, falling back to the previous question's context.

    **A fallback, not a rewrite, and the difference was measured.** On this
    corpus a bare referential follow-up — `لماذا؟`, `tell me more`, `the
    second one` — scores 0.15–0.18 and clears the relevance floor with
    nothing at all. The same question prefixed with the previous one scores
    0.35–0.51 and lands on the *right* document: `لماذا؟` after a status
    question retrieves the Weekly Status Report, and after a risks question
    retrieves the Risk Register. That is reference resolution, not noise.

    A follow-up carrying any topical noun of its own already retrieves fine,
    because the collection is one project and the project record is always
    present. So composing *every* query would spend an embedding where none
    is needed and would drag the old topic into a genuine change of subject.
    Composing only when the bare question found no evidence spends the extra
    embedding exactly where it converts a refusal into an answer.

    An LLM rewrite would be the heavier alternative: a round-trip on every
    turn against the AC08 budget, to do what one extra embedding does on the
    minority of turns that need it.
    """
    found = search(session, text, settings=settings, embedder=embedder,
                   qdrant=qdrant)
    floor = settings.answering.min_score
    if (found.above(floor) or not previous_question
            or not settings.conversation.retry_below_floor):
        return found

    composed = f"{previous_question} {text}"
    retried = search(session, composed, settings=settings, embedder=embedder,
                     qdrant=qdrant)
    # Both embeddings were paid for, so both are reported. An AC08 number
    # that hid the retry would understate the turns that are slowest.
    retried.embed_ms += found.embed_ms
    retried.search_ms += found.search_ms
    return retried if retried.above(floor) else found


def handle_question(
    session: Session,
    text: str,
    *,
    settings: Settings | None = None,
    discriminator=None,
    phrases=None,
    history: list[tuple[str, str]] | None = None,
    previous_question: str = "",
    **answer_kwargs,
) -> Reply:
    """Take one user turn and produce one reply.

    Stateless by design: `history` and `previous_question` are arguments,
    not attributes, so a single turn stays testable in isolation and the
    thing that owns the conversation (`Conversation`) is the only thing that
    has to be right about what a turn remembers.

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

    # ---- AC03 + AC06 ------------------------------------------------------
    # The follow-up fallback needs the retrieval result in hand, so it is
    # resolved here and passed down. Without a previous question this is the
    # same single search `answer_question` would have run itself.
    if previous_question and "retrieval" not in answer_kwargs:
        answer_kwargs["retrieval"] = _resolve_retrieval(
            session, text, previous_question, settings,
            answer_kwargs.get("embedder"), answer_kwargs.get("qdrant"),
        )

    answer = answer_question(session, text, settings=settings,
                             reply_language=reply_language, history=history,
                             **answer_kwargs)

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


@dataclass(frozen=True)
class Turn:
    """One exchange, kept whole so a UI can render the transcript."""
    question: str
    reply: Reply

    @property
    def reached_retrieval(self) -> bool:
        """Did this turn have a topic? A greeting or a rejection did not."""
        return self.reply.outcome in (OUTCOME_ANSWERED, OUTCOME_OUT_OF_SCOPE)


class Conversation:
    """AC03 — many questions, one session, no restart. And AC04's welcome.

    > **AC03** — The system supports session continuity (a loop) — the user
    > can ask multiple consecutive questions without restarting.

    **The session is fixed at construction and never changes.** That is the
    AC09 guarantee extended through time: a `Conversation` cannot be
    repointed at another project, so a transcript cannot come to contain two
    projects' content and a follow-up cannot resolve against a project the
    user has since switched away from. Switching projects means a new
    `Session` from the gate, and therefore a new `Conversation` — which is
    also the honest model of what a project switch *is*.

    **History is bounded twice, by turns and by tokens**, because they fail
    differently: a turn cap says nothing about size, and one long answer can
    outweigh five short ones. The same asymmetry `select_context` handles
    for passages.

    **Only turns that had a topic are carried forward.** A greeting, a
    language rejection or an empty input contributes nothing a follow-up
    could refer to, and spending prompt tokens on `hello` / `Hello. I'm the
    Enjaz Platform assistant…` would crowd out the exchange that matters.
    """

    def __init__(self, session: Session, *, settings: Settings | None = None):
        self._session = session
        self._settings = settings or get_settings()
        self._turns: list[Turn] = []

    # ---- state ------------------------------------------------------------

    @property
    def session(self) -> Session:
        """Read-only on purpose — see the class docstring."""
        return self._session

    @property
    def project_code(self) -> str:
        return self._session.project_code

    @property
    def turns(self) -> tuple[Turn, ...]:
        return tuple(self._turns)

    def __len__(self) -> int:
        return len(self._turns)

    # ---- AC04 -------------------------------------------------------------

    @property
    def welcome(self) -> str:
        """The bilingual greeting, available before the user types anything.

        Bilingual is not decoration here: no question has been asked, so
        there is no language to reply in. A property rather than something
        pushed on construction, so a caller decides when it is shown.
        """
        return messages.bilingual("welcome", project=self.project_code)

    # ---- AC03 -------------------------------------------------------------

    def history_for_prompt(self) -> list[tuple[str, str]]:
        """The recent exchange, newest-last, within both budgets.

        Walks backwards so the *most recent* turns survive the cut — the
        ones a follow-up is most likely to refer to — then restores order.
        """
        config = self._settings.conversation
        chosen: list[tuple[str, str]] = []
        used = 0
        for turn in reversed(self._turns):
            if not turn.reached_retrieval:
                continue
            if len(chosen) >= config.max_history_turns:
                break
            cost = count_tokens(turn.question) + count_tokens(turn.reply.text)
            if chosen and used + cost > config.max_history_tokens:
                break
            chosen.append((turn.question, turn.reply.text))
            used += cost
        return list(reversed(chosen))

    def _previous_question(self) -> str:
        """The last question that had a topic, for the retrieval fallback."""
        for turn in reversed(self._turns):
            if turn.reached_retrieval:
                return turn.question
        return ""

    def ask(self, text: str, **kwargs) -> Reply:
        """One turn. Records it, and returns the reply.

        Every turn is recorded, including rejections and greetings — a
        transcript that silently dropped them would not be what the user saw.
        What they do not do is reach the prompt; `history_for_prompt` filters.
        """
        kwargs.setdefault("settings", self._settings)
        kwargs.setdefault("history", self.history_for_prompt())
        kwargs.setdefault("previous_question", self._previous_question())
        reply = handle_question(self._session, text, **kwargs)
        self._turns.append(Turn(question=text, reply=reply))
        return reply


def open_conversation(session: Session, *,
                      settings: Settings | None = None) -> Conversation:
    """Start a conversation on an already-authorised session.

    Takes a `Session` rather than a username and a project code, for the
    same reason `retrieval.search` does: the gate is the only place a
    project is authorised, and a function that requires its output cannot be
    handed an unvalidated code.
    """
    return Conversation(session, settings=settings)


__all__ = [
    "Reply", "Turn", "Conversation", "handle_question", "open_conversation",
    "OUTCOME_ANSWERED", "OUTCOME_REJECTED_LANGUAGE", "OUTCOME_NO_LANGUAGE",
    "OUTCOME_HANDLED", "OUTCOME_OUT_OF_SCOPE",
    "ARABIC", "ENGLISH", "MIXED",
]
