from uuid import uuid4

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import observability


@pytest.fixture(autouse=True)
def disable_cloud_tracing(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    observability._get_tracing.cache_clear()
    yield
    observability._get_tracing.cache_clear()


@pytest.fixture
def exported_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"test-{uuid4()}")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-only")
    monkeypatch.setattr(observability, "Langfuse", lambda **kwargs: Langfuse(span_exporter=exporter, **kwargs))

    def spans():
        observability.flush_traces()
        return exporter.get_finished_spans()

    yield spans

    tracing = observability._get_tracing()
    if tracing is not None:
        client, provider = tracing
        client.shutdown()
        provider.shutdown()
