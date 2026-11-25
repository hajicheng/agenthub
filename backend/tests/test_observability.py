import asyncio
import json
from unittest.mock import Mock

import pytest

from app import observability


def test_missing_key_disables_export(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", " ")
    constructor = Mock()
    monkeypatch.setattr(observability, "Langfuse", constructor)

    with observability.trace_step("agenthub.run"):
        pass

    constructor.assert_not_called()


def test_exporter_failure_leaves_operation_usable(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-only")
    monkeypatch.setattr(observability, "Langfuse", Mock(side_effect=RuntimeError("exporter failed")))

    with observability.trace_step("agenthub.run") as observation:
        assert observation.status == "complete"

    observability.flush_traces()


def test_content_preview_redacts_credentials_and_media(exported_spans, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-environment-key")
    with observability.trace_step(
        "chat.completions", as_type="generation", model="test-model",
        input=[
            {"role": "user", "content": "Show the answer. test-environment-key"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,SECRET_BYTES"}}]},
            {"role": "assistant", "reasoning_content": "hidden thinking"},
        ],
        secrets=("per-agent-secret",),
    ) as observation:
        observation.output = {"content": "Visible reply", "arguments": '{"api_key":"arbitrary-key","task":"per-agent-secret"}'}

    span, = exported_spans()
    preview = str(span.attributes)
    assert "Show the answer" in preview and "Visible reply" in preview
    assert json.loads(span.attributes["langfuse.observation.input"])[1]["content"] == [
        {"type": "image_url", "omitted": True},
    ]
    for private in ("test-environment-key", "per-agent-secret", "arbitrary-key", "SECRET_BYTES", "hidden thinking"):
        assert private not in preview


def test_content_capture_is_opt_in(exported_spans, monkeypatch):
    monkeypatch.delenv("LANGFUSE_CAPTURE_CONTENT", raising=False)
    with observability.trace_step("agenthub.run", input="private prompt") as observation:
        observation.output = "private reply"

    span, = exported_spans()
    assert "langfuse.observation.input" not in span.attributes
    assert "langfuse.observation.output" not in span.attributes


def test_failed_generation_excludes_provider_error_body(exported_spans):
    private_body = "private request body from provider error"
    with pytest.raises(ValueError, match=private_body):
        with observability.trace_step("agenthub.run", conversation_id="conv_test"):
            with observability.trace_step("chat.completions", as_type="generation", model="test-model"):
                raise ValueError(private_body)

    spans = exported_spans()
    assert {span.name for span in spans} == {"agenthub.run", "chat.completions"}
    for span in spans:
        assert span.attributes["langfuse.observation.level"] == "ERROR"
        assert span.attributes["langfuse.observation.metadata.error_type"] == "ValueError"
        assert not span.events
        assert private_body not in str(span.attributes)


@pytest.mark.asyncio
async def test_concurrent_conversations_keep_separate_traces(exported_spans):
    async def run(conversation_id):
        with observability.trace_step("agenthub.run", conversation_id=conversation_id):
            await asyncio.sleep(0)
            with observability.trace_step("chat.completions", as_type="generation", model="test-model"):
                await asyncio.sleep(0)

    await asyncio.gather(run("conv_first"), run("conv_second"))
    spans = exported_spans()
    sessions = {}
    for span in spans:
        sessions.setdefault(span.attributes["session.id"], set()).add(span.context.trace_id)

    assert len(spans) == 4
    assert set(sessions) == {"conv_first", "conv_second"}
    assert all(len(traces) == 1 for traces in sessions.values())
    assert sessions["conv_first"].isdisjoint(sessions["conv_second"])
