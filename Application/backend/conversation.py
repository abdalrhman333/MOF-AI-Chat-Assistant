"""The conversational entry point — one retrieval, one model call.

`answer.answer_question` is the grounded-answering engine. This module owns
the conversation around it: who is speaking, what was said before, and what
to do with the reply.

**The model decides what each message is.** Language and intent are not
determined here and are not determined by any table, phrase list or
threshold — they come back from the model along with the answer, governed by
the system prompt in `answer.py`. That is a deliberate reversal of the
earlier design, which detected the language by counting scripts and routed
greetings through a phrase file. The trade is explicit: a greeting now costs
a model call instead of nothing, and in exchange there is exactly one place
that decides how to treat a message, and it is a prompt rather than code.

    retrieve  ->  ask the model once  ->  render what it decided

**Retrieval runs first, for every message.** That ordering is what keeps a
turn to a single completion: the model gets the passages it might need at
the same moment it is asked what the message is, so a real question is
answered in one round trip. Classifying first and answering second would be
two, and roughly double the latency against the AC08 budget.

⚠️ **This is an entry point, not a chokepoint.** `answer_question` remains
importable and skips the conversation around it. Anything user-facing must
enter here. The rule that must never be bypassable — the project scope — is
structural already: retrieval derives its collection from the `Session`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from Application.backend import messages
from Application.backend.answer import (
    INTENT_CAPABILITY,
    INTENT_GREETING,
    INTENT_OUT_OF_SCOPE,
    INTENT_OVERVIEW,
    INTENT_QUESTION,
    INTENT_UNSUPPORTED_LANGUAGE,
    Answer,
    Citation,
    Timings,
    answer_question,
)
from Application.backend.retrieval import search
from Application.backend.session import Session
from pipeline.chunker import count_tokens
from pipeline.config import Settings, get_settings

import observability
from observability import tracing

# Outcome slugs, distinct from intents: an intent is what the user asked
# for, an outcome is what we did about it. They differ exactly where it
# matters — `general_retrieval` can end as `answered` or as `out_of_scope`.
OUTCOME_ANSWERED = "answered"
OUTCOME_CONVERSATIONAL = "conversational"
OUTCOME_OUT_OF_SCOPE = "out_of_scope"
OUTCOME_REJECTED_LANGUAGE = "rejected_language"

# Intent is the model's word for what the message *was*; outcome is our word
# for how a caller should render it. Kept as a table rather than a chain of
# conditions so adding an intent without deciding how it renders is a
# visible omission rather than a silent fall-through.
OUTCOME_BY_INTENT = {
    INTENT_GREETING: OUTCOME_CONVERSATIONAL,
    INTENT_CAPABILITY: OUTCOME_CONVERSATIONAL,
    INTENT_OVERVIEW: OUTCOME_ANSWERED,
    INTENT_QUESTION: OUTCOME_ANSWERED,
    INTENT_OUT_OF_SCOPE: OUTCOME_OUT_OF_SCOPE,
    INTENT_UNSUPPORTED_LANGUAGE: OUTCOME_REJECTED_LANGUAGE,
}

# Which intents leave something a later follow-up could refer to. A greeting
# does not: "why?" after "hello" must not anchor on "hello".
TOPICAL_INTENTS = (INTENT_OVERVIEW, INTENT_QUESTION, INTENT_OUT_OF_SCOPE)


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
    history: list[tuple[str, str]] | None = None,
    previous_question: str = "",
    user_name: str = "",
    conversation_id: str = "",
    turn_index: int = 0,
    **answer_kwargs,
) -> Reply:
    """Take one user turn and produce one reply.

    Stateless by design: `history` and `previous_question` are arguments,
    not attributes, so a single turn stays testable in isolation and the
    thing that owns the conversation (`Conversation`) is the only thing that
    has to be right about what a turn remembers.

    `conversation_id` and `turn_index` are for tracing only and change no
    behaviour — they are what group a multi-turn conversation into one
    session in Langfuse instead of a scatter of unrelated traces.

    `**answer_kwargs` passes straight through to `answer_question`, so the
    whole path — including the parts that cost money — stays injectable and
    testable without a paid call.
    """
    settings = settings or get_settings()
    with observability.span(
        "chat.turn",
        **{
            tracing.LF_TRACE_NAME: "chat.turn",
            tracing.LF_SESSION_ID: conversation_id or session.project_code,
            tracing.LF_USER_ID: session.username,
            "enjaz.project": session.project_code,
            "enjaz.turn_index": turn_index,
        },
    ) as turn_span:
        reply = _handle(session, text, settings=settings,
                        history=history, previous_question=previous_question,
                        user_name=user_name, **answer_kwargs)
        # Recorded after the fact, because the interesting attributes are
        # outcomes rather than inputs: what a turn *did* is what makes a
        # trace searchable.
        turn_span.set_attributes({
            "enjaz.outcome": reply.outcome,
            "enjaz.intent": reply.intent or "",
            "enjaz.language": reply.language,
            "enjaz.reply_language": reply.reply_language,
            "enjaz.grounded": reply.grounded,
            "enjaz.citations": len(reply.citations),
            "enjaz.used_model": reply.used_model,
            "enjaz.total_ms": reply.timings.total_ms,
        })
        return reply


def _handle(
    session: Session,
    text: str,
    *,
    settings: Settings,
    history: list[tuple[str, str]] | None = None,
    previous_question: str = "",
    user_name: str = "",
    **answer_kwargs,
) -> Reply:
    """One turn: retrieve, then ask the model once. No routing here.

    **Retrieval runs before the model, for every message.** That ordering is
    what keeps this to a single completion: the model receives the passages
    it might need at the same time as it is asked to decide what the message
    even is, so a question is answered in one round trip rather than two.
    The cost of running it on a greeting is one embedding — a few hundred
    milliseconds and a fraction of a cent — against the second model call it
    saves on every real question.
    """
    # A follow-up whose own words retrieve nothing gets a second search with
    # the previous question in front of it. This is retrieval quality, not
    # intent classification: it changes what evidence the model sees, never
    # what the model is allowed to decide.
    if previous_question and "retrieval" not in answer_kwargs:
        answer_kwargs["retrieval"] = _resolve_retrieval(
            session, text, previous_question, settings,
            answer_kwargs.get("embedder"), answer_kwargs.get("qdrant"),
        )

    answer = answer_question(session, text, settings=settings,
                             user_name=user_name, history=history,
                             **answer_kwargs)

    # The model classified; we only translate its category into the one word
    # a caller renders on. Nothing here re-decides what the message was.
    return Reply(
        text=answer.text,
        outcome=OUTCOME_BY_INTENT.get(answer.intent, OUTCOME_ANSWERED),
        intent=answer.intent,
        language=answer.language,
        reply_language=answer.language,
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
    def carries_topic(self) -> bool:
        """Is there something here a later follow-up could refer to?

        Every turn now reaches retrieval, so the old name lied. What still
        matters is whether the turn had a *subject*: a greeting or a
        language refusal leaves nothing for "why?" to attach to.
        """
        return self.reply.intent in TOPICAL_INTENTS


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

    def __init__(self, session: Session, *, settings: Settings | None = None,
                 conversation_id: str = "",
                 user_name: str | tuple[str, str] = "",
                 ui_language: str = "ar"):
        self._session = session
        self._settings = settings or get_settings()
        self._turns: list[Turn] = []
        # The signed-in user's display name, from their authenticated
        # account. It reaches the model so it can greet by name, and it never
        # comes from anything the user typed.
        self._user_name = user_name
        self._ui_language = ui_language
        # Tracing only. A stable id per conversation is what makes Langfuse
        # show one session with N turns rather than N unrelated traces.
        self._id = conversation_id or uuid.uuid4().hex

    # ---- state ------------------------------------------------------------

    @property
    def session(self) -> Session:
        """Read-only on purpose — see the class docstring."""
        return self._session

    @property
    def _names(self) -> tuple[str, str]:
        """(Arabic, English) display names, each falling back to the other."""
        if isinstance(self._user_name, tuple):
            ar, en = self._user_name
        else:
            ar = en = self._user_name
        fallback = self._session.username
        return (ar or en or fallback, en or ar or fallback)

    @property
    def id(self) -> str:
        """Stable per conversation. Tracing only; carries no authority."""
        return self._id

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
        # Built half by half rather than through `bilingual()`, because
        # each half needs its *own* language's name — "Welcome, نورة" reads
        # as a bug, not as bilingual.
        ar, en = self._names
        return "\n\n".join([
            messages.text("welcome", messages.ARABIC,
                          project=self.project_code, name=ar),
            messages.text("welcome", messages.ENGLISH,
                          project=self.project_code, name=en),
        ])

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
            if not turn.carries_topic:
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
            if turn.carries_topic:
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
        kwargs.setdefault("user_name", self._user_name)
        kwargs.setdefault("conversation_id", self._id)
        kwargs.setdefault("turn_index", len(self._turns) + 1)
        reply = handle_question(self._session, text, **kwargs)
        self._turns.append(Turn(question=text, reply=reply))
        return reply


def open_conversation(session: Session, *,
                      settings: Settings | None = None,
                      user_name: str | tuple[str, str] = "",
                      ui_language: str = "ar") -> Conversation:
    """Start a conversation on an already-authorised session.

    Takes a `Session` rather than a username and a project code, for the
    same reason `retrieval.search` does: the gate is the only place a
    project is authorised, and a function that requires its output cannot be
    handed an unvalidated code.
    """
    return Conversation(session, settings=settings, user_name=user_name,
                        ui_language=ui_language)


__all__ = [
    "Reply", "Turn", "Conversation", "handle_question", "open_conversation",
    "OUTCOME_ANSWERED", "OUTCOME_CONVERSATIONAL", "OUTCOME_OUT_OF_SCOPE",
    "OUTCOME_REJECTED_LANGUAGE", "OUTCOME_BY_INTENT", "TOPICAL_INTENTS",
]
