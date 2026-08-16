"""Every string a user reads that the model did not write.

Kept in one module for a reason that is about correctness, not tidiness:
these are the *only* replies this system produces without evidence behind
them, so they must be easy to enumerate and audit. Anything else a user
sees came from a document or the project record and carries a citation.

**Rejections are bilingual; replies are in the question's language.** If the
language check could not identify the language, we cannot reply in kind —
so an AC01 rejection is shown in Arabic *and* English, which is the same
shape AC04's welcome will need. Once the language is known, replying in one
language is what AC05 asks for.

This is a placeholder for a real localisation layer, and the seam is
deliberate: `text(key, language)` is the only entry point, so moving these
into resource files the frontend also reads changes this module and nothing
else.
"""
from __future__ import annotations

from Application.backend.language import ARABIC, ENGLISH

# key -> {language -> text}
MESSAGES: dict[str, dict[str, str]] = {
    # AC04 — shown immediately on session start, before the user has typed
    # anything. Bilingual is not a nicety here: there is no question yet, so
    # there is no language to reply in. `{project}` comes from the Session.
    "welcome": {
        ARABIC: "أهلاً بك في مساعد منصة إنجاز. جلستك مرتبطة بالمشروع "
                "{project}. يمكنك طرح أسئلتك بالعربية أو الإنجليزية، "
                "وسأجيب اعتماداً على وثائق المشروع المعتمدة وسجله في نظام "
                "SPM، مع ذكر المصدر في كل إجابة.",
        ENGLISH: "Welcome to the Enjaz Platform assistant. This session is "
                 "for project {project}. Ask in Arabic or English, and I'll "
                 "answer from the project's approved documents and its SPM "
                 "record, citing the source each time.",
    },
    # AC01 — the clear apology. Says what is supported, because a rejection
    # that does not tell the user what to do instead is not "clear".
    "unsupported_language": {
        ARABIC: "نعتذر، لا يمكننا حالياً معالجة الأسئلة بهذه اللغة. "
                "يُرجى إعادة طرح سؤالك بالعربية أو الإنجليزية.",
        ENGLISH: "We're sorry — we can't process questions in that language "
                 "yet. Please ask again in Arabic or English.",
    },
    # No letters at all: digits, punctuation or emoji. Not a language
    # rejection, because there is no language to reject.
    "no_language_detected": {
        ARABIC: "نعتذر، لم نتمكن من فهم سؤالك. يُرجى كتابته بالعربية أو الإنجليزية.",
        ENGLISH: "Sorry — we couldn't read a question there. Please write it "
                 "in Arabic or English.",
    },
    "greeting": {
        ARABIC: "أهلاً بك. أنا مساعد منصة إنجاز، ويمكنني الإجابة عن أسئلتك "
                "حول مشروعك اعتماداً على وثائقه المعتمدة. كيف يمكنني مساعدتك؟",
        ENGLISH: "Hello. I'm the Enjaz Platform assistant. I can answer "
                 "questions about your project from its approved documents. "
                 "How can I help?",
    },
    "courtesy": {
        ARABIC: "على الرحب والسعة. هل لديك سؤال آخر عن المشروع؟",
        ENGLISH: "You're welcome. Is there anything else about the project?",
    },
    "capability": {
        ARABIC: "يمكنني الإجابة عن الأسئلة المتعلقة بمشروعك الحالي فقط، "
                "اعتماداً على وثائقه المفهرسة وسجل المشروع في نظام SPM — "
                "مثل الميزانية والحالة والمواعيد والمخاطر والمسؤوليات. "
                "أذكر دائماً مصدر كل إجابة، ولا أطّلع على أي مشروع آخر، "
                "ولا أُجري أي تعديل على البيانات.",
        ENGLISH: "I answer questions about your current project only, using "
                 "its indexed documents and the project record held in SPM — "
                 "budget, status, dates, risks, responsibilities and the "
                 "like. I always cite the source, I cannot see any other "
                 "project, and I never change any data.",
    },
    # `{project}` is filled from the Session, never from user input.
    "session_meta": {
        ARABIC: "جلستك الحالية مرتبطة بالمشروع {project} فقط. "
                "لعرض مشاريعك الأخرى أو التبديل بينها، استخدم مُبدِّل "
                "المشاريع في أعلى الصفحة.",
        ENGLISH: "This session is locked to project {project}. To see your "
                 "other projects or switch, use the project switcher at the "
                 "top of the page.",
    },
    # AC07 — the fallback when the sources genuinely do not answer it.
    # Says why, and does not invite the user to rephrase into an answer that
    # does not exist in the documents.
    "out_of_scope": {
        ARABIC: "لم أجد في وثائق هذا المشروع أو في سجله ما يجيب عن سؤالك. "
                "لا أستطيع الإجابة من معرفة خارج المصادر المعتمدة للمشروع.",
        ENGLISH: "I couldn't find anything in this project's documents or its "
                 "project record that answers that. I can't answer from "
                 "anything outside the project's approved sources.",
    },
}

# The order a bilingual message is shown in. Arabic first: the platform is
# Arabic-first and the primary user population writes Arabic.
BILINGUAL_ORDER = (ARABIC, ENGLISH)


def text(key: str, language: str, **fields) -> str:
    """One message, in one language. Falls back to English, then to any.

    A missing key raises — a user-facing string that silently renders empty
    is worse than a loud failure during development.
    """
    variants = MESSAGES[key]
    body = variants.get(language) or variants.get(ENGLISH) or next(iter(variants.values()))
    return body.format(**fields) if fields else body


def bilingual(key: str, **fields) -> str:
    """Both languages, for when we could not identify the user's."""
    return "\n\n".join(text(key, lang, **fields) for lang in BILINGUAL_ORDER)
