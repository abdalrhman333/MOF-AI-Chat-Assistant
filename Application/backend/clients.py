"""One embedding client, one vector client, one LLM client — per process.

**This is an AC08 module, not a tidiness one.** Before it existed, every
question constructed all three clients from scratch, and that is most of
where the five-second budget was going. Measured on this machine:

| | first call | steady state |
|---|---|---|
| construct the embedding client | 4196 ms | **515 ms** |
| construct the Qdrant client | 4064 ms | **1035 ms** |
| construct the LLM client | 847 ms | **800 ms** |
| embed a question, fresh client each time | 3734 ms | **~1000 ms** |
| embed a question, reusing one client | 628 ms | **~290 ms** |

So a question was paying roughly **2.3 seconds** to rebuild clients it had
already built, and then a further ~700 ms because a brand-new HTTPS client
cannot reuse a connection and must repeat the TLS handshake. None of that
work is retrieval or generation; all of it is setup.

**Cached on `Settings`, not globally.** The cache key is the settings
object, so a test or a second deployment configuration gets its own client
rather than silently inheriting one built for a different endpoint. That
matters more than it sounds: a cached client keyed on nothing would make
`dataclasses.replace(settings, ...)` a no-op in exactly the tests that
exist to prove settings are honoured.

**The clients are safe to share.** `AzureOpenAI` wraps an `httpx` client
with a connection pool, and `QdrantClient` holds an HTTP session; both are
documented as thread-safe for concurrent requests, which is what a pooled
web layer will do to them.

`reset()` exists for tests, and for a caller that has changed credentials
and wants the next call to reconnect.
"""
from __future__ import annotations

import time
from functools import lru_cache

from pipeline.config import Settings, get_settings


@lru_cache(maxsize=4)
def embedder(settings: Settings):
    """The embedding client. Same model the corpus was indexed with."""
    from pipeline.indexer import build_embedder
    return build_embedder(settings)


@lru_cache(maxsize=4)
def vector_store(settings: Settings):
    from pipeline.indexer import build_qdrant
    return build_qdrant(settings)


@lru_cache(maxsize=4)
def llm(settings: Settings):
    from Application.backend.answer import build_llm
    return build_llm(settings)


def reset() -> None:
    """Drop every cached client. Tests, and credential changes."""
    embedder.cache_clear()
    vector_store.cache_clear()
    llm.cache_clear()


def warmup(settings: Settings | None = None, *, probe: bool = True) -> dict:
    """Build the clients, and optionally open the connections, up front.

    **Constructing a client is not the same as connecting one.** Caching
    alone removes the ~2.3 s of repeated construction, but the *first*
    request through a fresh client still pays a TLS handshake — which is
    why the first embedding costs ~630 ms against ~290 ms afterwards. A
    `probe` embeds a single short string so that cost lands here, at
    startup, instead of on whoever asks the first question.

    Returns the per-step timings, so a startup log can show where the time
    went rather than merely that it was spent.

    Never raises. A warmup is an optimisation, and an assistant that
    refuses to start because a pre-warm failed is worse than one that pays
    the cost on the first question. Failures are reported in the result.
    """
    settings = settings or get_settings()
    timings: dict[str, object] = {}

    for name, build in (("embedder", embedder), ("vector_store", vector_store),
                        ("llm", llm)):
        started = time.perf_counter()
        try:
            build(settings)
            timings[name] = int((time.perf_counter() - started) * 1000)
        except Exception as exc:                              # noqa: BLE001
            timings[name] = f"failed: {exc}"

    if probe:
        started = time.perf_counter()
        try:
            embedder(settings).embeddings.create(
                model=settings.models.embedding_deployment, input=["warmup"],
            )
            timings["probe"] = int((time.perf_counter() - started) * 1000)
        except Exception as exc:                              # noqa: BLE001
            timings["probe"] = f"failed: {exc}"

    timings["total_ms"] = sum(v for v in timings.values() if isinstance(v, int))
    return timings
