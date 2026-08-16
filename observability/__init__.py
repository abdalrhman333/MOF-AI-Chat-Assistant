"""Instrumentation, shared by the packages that build and serve.

`pipeline/` builds the knowledge base, `evaluation/` judges it,
`Application/` serves it — and this watches whichever of them is running.
It is separate from all three because it belongs to none of them: a span
emitted from retrieval and a span emitted from an ingest run go to the same
place and must agree on the same resource, the same trace context and the
same switches.
"""
from observability.tracing import (
    configure,
    is_active,
    shutdown,
    span,
    tracer,
)

__all__ = ["configure", "is_active", "shutdown", "span", "tracer"]
