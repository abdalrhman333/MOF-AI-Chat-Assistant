"""Construct the Azure OpenAI client from settings. Nothing else.

**Why this is in `pipeline/` and not in `Application/`.** It used to live in
`Application/backend/answer.py`, and `pipeline/classify.py` reached across
to import it. That inverted the dependency the three packages are built
on: `pipeline/` builds the knowledge base and `Application/` serves it,
each as a separate process under different database privileges — so the
builder importing the server is backwards, and the Dockerfile says so by
only copying `pipeline/` into the pipeline image.

The result was a stage that passed every test and failed in the
container. The import is lazy, inside the branch that runs when no client
was injected, so the unit tests — which inject one — never executed it.
It surfaced on 2026-11-12, the first new document since the image was
built, as:

    "stage": "classify", "ok": false,
    "error": "ModuleNotFoundError: No module named 'Application'"

Both packages now import from here, which is the direction that was
always intended. `Application.backend.answer` re-exports the name so
nothing that referred to it there has to change.

⚠️ Keep this module free of anything the application layer owns — no
session, no gate, no prompt. The moment it needs one of those, the
dependency has inverted again.
"""
from __future__ import annotations

from pipeline.config import Settings


def build_llm(settings: Settings):
    """Constructed from settings only, so a provider swap stays contained."""
    from openai import AzureOpenAI
    return AzureOpenAI(
        azure_endpoint=settings.models.llm_endpoint,
        api_key=settings.models.llm_key,
        api_version=settings.models.llm_api_version,
        timeout=settings.answering.request_timeout_seconds,
        max_retries=1,
    )


__all__ = ["build_llm"]
