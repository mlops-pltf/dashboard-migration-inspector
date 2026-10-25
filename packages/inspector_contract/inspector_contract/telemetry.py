"""Tracing for the Inspector team: OpenTelemetry, exported to Langfuse (or any OTLP endpoint).

One validation run is one trace across all agents: the A2A client injects the W3C trace context into every
request and the A2A server continues it, so the experts' and the data agent's work nests under the Inspector's.
Strands traces each model and tool call itself; ``span`` adds named spans for the steps code decides.

Environment (all optional; without them nothing is exported):
  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL   Langfuse project (base URL default EU cloud)
  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT (+ OTEL_EXPORTER_OTLP_HEADERS)   any other OTLP endpoint, e.g. AgentCore/CloudWatch
  INSPECTOR_TRACE_CONTENT   "false" keeps inputs/outputs (SQL, results, text) out of the custom spans
"""

from __future__ import annotations

import atexit
import base64
import json
import os
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace

_MAX_CHARS = 20_000
_tracer = trace.get_tracer("inspector")
_configured = False


def _env(name: str) -> str:
    return os.environ.get(name, "").strip().strip('"').strip("'")


def langfuse_base_url() -> str:
    return (_env("LANGFUSE_BASE_URL") or _env("LANGFUSE_HOST") or "https://cloud.langfuse.com").rstrip("/")


def langfuse_auth() -> str | None:
    public, secret = _env("LANGFUSE_PUBLIC_KEY"), _env("LANGFUSE_SECRET_KEY")
    if not (public and secret):
        return None
    return "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode()


def setup(service_name: str) -> bool:
    """Install the global tracer provider for this agent. Returns whether traces are exported."""
    global _configured
    if _configured:
        return True
    auth = langfuse_auth()
    endpoint = _env("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if not auth and not endpoint:
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)  # Strands reads it too
    if auth:
        exporter = OTLPSpanExporter(endpoint=f"{langfuse_base_url()}/api/public/otel/v1/traces",
                                    headers={"Authorization": auth})
    else:
        exporter = OTLPSpanExporter()  # endpoint and headers from the standard OTEL_* environment
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)
    _configured = True
    return True


def flush() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()


def capture_content() -> bool:
    return _env("INSPECTOR_TRACE_CONTENT").lower() not in ("false", "0", "no")


def _text(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= _MAX_CHARS else text[:_MAX_CHARS] + f"... [{len(text) - _MAX_CHARS} more chars]"


class Span:
    def __init__(self, otel_span: trace.Span):
        self.otel = otel_span

    def output(self, value: Any) -> None:
        if capture_content():
            self.otel.set_attribute("langfuse.observation.output", _text(value))

    def set(self, **attributes: Any) -> None:
        for key, value in attributes.items():
            if value is not None:
                plain = isinstance(value, (str, bool, int, float)) or \
                    (isinstance(value, list) and all(isinstance(v, str) for v in value))
                self.otel.set_attribute(key, value if plain else _text(value))

    @property
    def trace_id(self) -> str:
        return format(self.otel.get_span_context().trace_id, "032x")


@contextmanager
def span(name: str, input: Any = None, **attributes: Any) -> Iterator[Span]:
    """A named step; ``input`` and the value given to ``.output()`` show as the step's input and output."""
    with _tracer.start_as_current_span(name) as s:
        wrapped = Span(s)
        if input is not None and capture_content():
            s.set_attribute("langfuse.observation.input", _text(input))
        wrapped.set(**attributes)
        yield wrapped


def inject(headers: dict[str, str]) -> dict[str, str]:
    """Add the current trace context (traceparent) to outgoing request headers."""
    propagate.inject(headers)
    return headers


@contextmanager
def continued_from(headers: dict[str, str] | None) -> Iterator[None]:
    """Continue the caller's trace for the duration of a request."""
    token = otel_context.attach(propagate.extract(headers or {}))
    try:
        yield
    finally:
        otel_context.detach(token)
