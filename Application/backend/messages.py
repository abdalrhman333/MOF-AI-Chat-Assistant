"""The only user-facing strings the model does not write.

There are two, and the list is short on purpose. Every reply to something a
user typed comes from the model, governed by the system prompt in
`answer.py` — greetings, apologies, refusals and out-of-scope answers
included. Nothing here classifies a message or decides how to respond to one.

**Why the welcome survives.** AC04 requires a greeting *immediately on
session start*, before the user has typed anything. There is no message to
detect a language from and no question to answer, so there is nothing for
the model to do — asking it would be a paid call to render a constant. It
is bilingual for the same reason: with no input, there is no language to
reply in.

**Why the outage message survives.** If the backend is unreachable, the
model is exactly what we cannot call.

`text(key, language)` is the only entry point, so moving these into resource
files the frontend also reads changes this module and nothing else.
"""
from __future__ import annotations

ARABIC = "ar"
ENGLISH = "en"

# key -> {language -> text}
MESSAGES: dict[str, dict[str, str]] = {
    # AC04. `{name}` is the signed-in user's own name, from their
    # authenticated account; `{project}` comes from the Session.
    "welcome": {
        ARABIC: "أهلاً بك يا {name}. أنا المساعد الذكي، وجلستك مرتبطة "
                "بالمشروع {project}. اسألني بالعربية أو الإنجليزية، وسأجيب "
                "اعتماداً على وثائق المشروع المعتمدة وسجله في نظام SPM، مع "
                "ذكر المصدر في كل إجابة.",
        ENGLISH: "Welcome, {name}. I'm the Smart Assistant, and this session "
                 "is for project {project}. Ask me in Arabic or English and "
                 "I'll answer from the project's approved documents and its "
                 "SPM record, citing the source each time.",
    },
    "unavailable": {
        ARABIC: "تعذّر الوصول إلى الخدمة مؤقتاً. يُرجى المحاولة مرة أخرى.",
        ENGLISH: "The service is temporarily unavailable. Please try again.",
    },
}

# Arabic first: the platform is Arabic-first and the primary users write it.
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
    """Both languages, for when there is no input to infer one from."""
    return "\n\n".join(text(key, lang, **fields) for lang in BILINGUAL_ORDER)
