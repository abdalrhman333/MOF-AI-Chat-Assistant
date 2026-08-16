"""OpenTelemetry tracing, exported to Grafana and Langfuse at once.

**One instrumentation, two backends.** Langfuse v3 exposes an OTLP
endpoint, so LLM tracing needs no second SDK — which matters because two
SDKs mean two sets of instrumentation calls that drift apart, and the first
thing to drift is always the one nobody looks at. Both backends receive the
same spans from the same `with span(...)` blocks; they differ only in what
they are good at reading. Grafana answers *where did the time go*, Langfuse
answers *what was the model actually asked*.

Verified against this deployment before any of it was built: a POST to
`/api/public/otel/v1/traces` with the project's Basic auth returns 200.

**Tracing is fail-open, and that is the opposite of every access rule in
this project.** An unreachable collector, a bad endpoint, a missing
dependency — none of them may stop a project manager getting an answer. So
every entry point here swallows its own failures, and `span()` falls back
to a no-op that still runs the block. The cost of a lost trace is a blind
spot; the cost of a raised exception is an outage.

**Nothing here is imported at module scope from OpenTelemetry** beyond the
API, which is a no-op until a provider is installed. A deployment that
never calls `configure()` pays nothing and needs none of the packages at
runtime.

Usage:

    from observability import configure, span

    configure(settings)                       # once, at startup
    with span("chat.turn", project=code) as s:
        ...
        s.set_attribute("outcome", reply.outcome)
"""
from __future__ import annotations

import base64
import contextlib
import logging
import threading
from typing import Any

from pipeline.config import ObservabilitySettings, Settings, get_settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_provider = None
_tracer = None
_backends: tuple[str, ...] = ()

# Span attribute names. OpenTelemetry's GenAI semantic conventions, which
# Langfuse maps to its own model — using the standard rather than Langfuse's
# native field names is what keeps the Grafana view meaningful too.
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_USAGE_INPUT = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT = "gen_ai.usage.output_tokens"

# Langfuse-specific mappings. These are what turn a flat stream of spans
# into something navigable: turns grouped by conversation, attributed to a
# user, named readably in the UI.
LF_SESSION_ID = "langfuse.session.id"
LF_USER_ID = "langfuse.user.id"
LF_TRACE_NAME = "langfuse.trace.name"
LF_INPUT = "langfuse.observation.input"
LF_OUTPUT = "langfuse.observation.output"


def _exporters(config: ObservabilitySettings) -> list[tuple[str, Any]]:
    """Build one exporter per enabled, usable backend.

    A backend that is enabled but unusable — Langfuse with no keys — is
    skipped with a warning rather than configured to fail on every export.
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    built: list[tuple[str, Any]] = []
    timeout = int(config.export_timeout_seconds)

    if config.otlp_enabled and config.otlp_endpoint:
        built.append(("grafana", OTLPSpanExporter(
            endpoint=config.otlp_endpoint, timeout=timeout)))

    if config.langfuse_enabled:
        if not config.langfuse_ready:
            log.warning("Langfuse tracing is enabled but the keys are not set; "
                        "skipping that exporter. Traces still go to any other "
                        "configured backend.")
        else:
            token = base64.b64encode(
                f"{config.langfuse_public_key}:{config.langfuse_secret_key}"
                .encode()
            ).decode()
            built.append(("langfuse", OTLPSpanExporter(
                endpoint=config.langfuse_endpoint,
                headers={"Authorization": f"Basic {token}"},
                timeout=timeout,
            )))
    return built


def configure(settings: Settings | None = None, *, force: bool = False) -> tuple[str, ...]:
    """Install a tracer provider. Idempotent, and never raises.

    Returns the names of the backends that were wired up — empty when
    tracing is disabled or could not be set up, which is a normal state and
    not an error. Call once at startup.
    """
    global _provider, _tracer, _backends

    config = (settings or get_settings()).observability
    with _lock:
        if _provider is not None and not force:
            return _backends
        if not config.enabled:
            _backends = ()
            return _backends

        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({
                "service.name": config.service_name,
                "deployment.environment.name": config.environment,
            })
            provider = TracerProvider(resource=resource)

            built = _exporters(config)
            for _name, exporter in built:
                # Batched, so an export never happens on the request thread.
                # A synchronous exporter would put the collector's latency
                # directly into the AC08 budget.
                provider.add_span_processor(BatchSpanProcessor(exporter))

            if not built:
                log.warning("Tracing is enabled but no backend is usable.")
                _backends = ()
                return _backends

            # **Our tracer comes from our own provider, not from the global
            # one.** OpenTelemetry permits the global provider to be set
            # exactly once per process and warns-and-ignores afterwards, so
            # a module that read its tracer back from the global would be
            # impossible to reconfigure — and every test after the first
            # would silently emit into the first test's exporter. Setting
            # the global is still attempted, so any auto-instrumentation
            # added later finds it, but nothing here depends on it working.
            _provider = provider
            _tracer = provider.get_tracer(config.service_name)
            try:
                trace.set_tracer_provider(provider)
            except Exception:                                 # noqa: BLE001
                pass
            _backends = tuple(name for name, _ in built)
            log.info("Tracing enabled -> %s", ", ".join(_backends))
            return _backends
        except Exception as exc:                              # noqa: BLE001
            # Fail open. See the module docstring: a blind spot beats an
            # outage, and this is the one place in the repo where that is
            # the right trade.
            log.warning("Tracing could not be configured (%s); continuing "
                        "without it.", exc)
            _provider, _tracer, _backends = None, None, ()
            return _backends


def is_active() -> bool:
    return _tracer is not None


def backends() -> tuple[str, ...]:
    return _backends


def tracer():
    return _tracer


class _NoopSpan:
    """Stands in when tracing is off, so callers need no `if` around it."""

    def set_attribute(self, *_args, **_kwargs) -> None:
        return None

    def set_attributes(self, *_args, **_kwargs) -> None:
        return None

    def record_exception(self, *_args, **_kwargs) -> None:
        return None

    def set_status(self, *_args, **_kwargs) -> None:
        return None

    def add_event(self, *_args, **_kwargs) -> None:
        return None


@contextlib.contextmanager
def span(name: str, **attributes):
    """A span, or a no-op that still runs the block.

    Attributes with a value of `None` are dropped rather than exported as
    the string "None", which is otherwise how half a trace ends up
    unreadable.
    """
    if _tracer is None:
        yield _NoopSpan()
        return

    clean = {k: v for k, v in attributes.items() if v is not None}
    try:
        with _tracer.start_as_current_span(name) as current:
            try:
                current.set_attributes(clean)
            except Exception:                                 # noqa: BLE001
                pass
            yield current
    except Exception:                                         # noqa: BLE001
        # A failure in the tracing machinery itself, not in the block --
        # the block's own exceptions propagate from inside the `with`.
        yield _NoopSpan()


def record_exception(current, exc: BaseException) -> None:
    """Mark a span failed without letting the tracer raise in an error path."""
    try:
        from opentelemetry.trace import Status, StatusCode
        current.record_exception(exc)
        current.set_status(Status(StatusCode.ERROR, str(exc)))
    except Exception:                                         # noqa: BLE001
        pass


def shutdown() -> None:
    """Flush pending spans. Safe to call when tracing was never configured.

    Worth calling from a short-lived process: `BatchSpanProcessor` exports
    on a timer, so a script that exits promptly would otherwise drop the
    very traces it was run to produce.
    """
    global _provider, _tracer, _backends
    with _lock:
        if _provider is None:
            return
        try:
            _provider.shutdown()
        except Exception as exc:                              # noqa: BLE001
            log.warning("Tracing shutdown failed: %s", exc)
        finally:
            _provider, _tracer, _backends = None, None, ()
