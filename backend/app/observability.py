from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from langfuse import Langfuse, is_langfuse_span, propagate_attributes
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)

_REDACTED = "[redacted]"
_SENSITIVE_FIELDS = {
    "apikey", "secretkey", "accesskey", "authorization", "clientsecret",
    "password", "privatekey", "token", "accesstoken", "refreshtoken", "cookie",
}
_PRIVATE_FIELDS = {"reasoningcontent", "reasoning", "thinking", "signature", "imageurl", "absolutepath", "cwd"}
_SECRET_TEXT = re.compile(
    r"(?i)\b(?:bearer\s+|sk-(?:lf-|ant-|proj-)?|gh[pousr]_|github_pat_)[A-Za-z0-9._~+/=-]{12,}"
    r"|\b(?:[A-Z0-9]+_)*(?:API_KEY|SECRET_KEY|ACCESS_TOKEN|PASSWORD)\s*[:=]\s*[^\s,;]+"
    r"|https?://[^/\s:@]+:[^/\s@]+@"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
_OBSERVATION_PREVIEW_LIMIT = 50_000


def _content_preview(value: object, secrets: tuple[str, ...]) -> object:
    def redact_text(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, _REDACTED)
        return _SECRET_TEXT.sub(_REDACTED, text)

    def sanitize(item: object) -> object:
        if isinstance(item, dict):
            if item.get("type") in ("image", "image_url", "base64", "thinking", "redacted_thinking"):
                return {"type": item["type"], "omitted": True}
            return {
                redact_text(str(key)): _REDACTED if field in _SENSITIVE_FIELDS else sanitize(child)
                for key, child in item.items()
                if (field := str(key).replace("_", "").replace("-", "").lower()) not in _PRIVATE_FIELDS
            }
        if isinstance(item, (list, tuple)):
            return [sanitize(child) for child in item]
        if isinstance(item, str):
            if item.lower().startswith("data:"):
                return "[embedded data omitted]"
            if item.lstrip().startswith(("{", "[")):
                try:
                    decoded = json.loads(item)
                except (ValueError, RecursionError):
                    pass
                else:
                    return json.dumps(sanitize(decoded), ensure_ascii=False)
            return redact_text(item)
        if item is None or isinstance(item, (int, float, bool)):
            return item
        return f"[{type(item).__name__} omitted]"

    preview = sanitize(value)
    serialized = json.dumps(preview, ensure_ascii=False)
    if len(serialized) <= _OBSERVATION_PREVIEW_LIMIT:
        return preview
    half = _OBSERVATION_PREVIEW_LIMIT // 2
    return {"preview": serialized[:half] + "\n[truncated]\n" + serialized[-half:]}


@lru_cache(maxsize=1)
def _get_tracing() -> tuple[Langfuse, TracerProvider] | None:
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    enabled = os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower() != "false"
    if not (public_key and secret_key and enabled):
        return None

    try:
        provider = TracerProvider(resource=Resource({"service.name": "agenthub"}))
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=os.getenv("LANGFUSE_BASE_URL", "").strip() or None,
            tracer_provider=provider,
            should_export_span=is_langfuse_span,
            additional_headers={"x-langfuse-ingestion-version": "4"},
        )
    except Exception as exc:
        logger.warning("Langfuse initialization failed (%s)", type(exc).__name__)
        return None
    return client, provider


def flush_traces() -> None:
    if not _get_tracing.cache_info().currsize:
        return
    tracing = _get_tracing()
    if tracing is None:
        return
    try:
        if not tracing[1].force_flush(timeout_millis=5000):
            logger.warning("Langfuse trace flush timed out")
    except Exception as exc:
        logger.warning("Langfuse trace flush failed (%s)", type(exc).__name__)


@dataclass
class Observation:
    status: str = "complete"
    error_type: str | None = None
    model: str | None = None
    usage: dict[str, int] | None = None
    capture_content: bool = False
    input: object = None
    output: object = None
    secrets: tuple[str, ...] = ()

    def fail(self, exc: Exception) -> None:
        self.status = "failed"
        self.error_type = type(exc).__name__


@contextmanager
def trace_step(
    name: str,
    *,
    as_type: Literal["agent", "generation", "span", "tool"] = "span",
    conversation_id: str | None = None,
    metadata: dict[str, str] | None = None,
    model: str | None = None,
    input: object = None,
    secrets: tuple[str, ...] = (),
    abort_event: asyncio.Event | None = None,
) -> Iterator[Observation]:
    observation = Observation(model=model, input=input, secrets=secrets)
    tracing = _get_tracing()
    if tracing is None:
        yield observation
        return

    client, _ = tracing
    stack = ExitStack()
    span = None
    try:
        if as_type == "generation":
            # Streaming adapters yield across this scope, so only runs and tools activate a span.
            with propagate_attributes(session_id=conversation_id):
                span = client.start_observation(
                    name=name, as_type="generation", model=model, metadata=metadata
                )
            stack.callback(span.end)
        else:
            span = stack.enter_context(
                client.start_as_current_observation(name=name, as_type=as_type, metadata=metadata)
            )
            if conversation_id:
                stack.enter_context(propagate_attributes(session_id=conversation_id))
        observation.capture_content = os.getenv("LANGFUSE_CAPTURE_CONTENT", "").strip().lower() == "true"
    except Exception as exc:
        logger.warning("Langfuse observation start failed (%s)", type(exc).__name__)

    try:
        yield observation
    except (asyncio.CancelledError, GeneratorExit):
        observation.status = "aborted"
        raise
    except Exception as exc:
        observation.fail(exc)
        raise
    finally:
        if abort_event is not None and abort_event.is_set():
            observation.status = "aborted"
        try:
            if span is not None:
                status = observation.status
                fields = {"status": status}
                if observation.error_type and status == "failed":
                    fields["error_type"] = observation.error_type
                span.update(
                    metadata=fields,
                    level="ERROR" if status == "failed" else "DEFAULT",
                    status_message=status,
                )
                if as_type == "generation":
                    span.update(model=observation.model, usage_details=observation.usage)
                if observation.capture_content:
                    secrets_to_mask = {
                        value for key, value in os.environ.items()
                        if len(value) >= 6 and key.upper().endswith(("_KEY", "_TOKEN", "_PASSWORD", "_SECRET"))
                    }
                    secrets_to_mask.update(secret for secret in observation.secrets if secret)
                    known_secrets = tuple(sorted(secrets_to_mask, key=len, reverse=True))
                    span.update(
                        input=_content_preview(observation.input, known_secrets),
                        output=_content_preview(observation.output, known_secrets),
                    )
        except Exception as exc:
            logger.warning("Langfuse observation update failed (%s)", type(exc).__name__)
        finally:
            try:
                # Keep raw business exceptions out of the OpenTelemetry span.
                stack.close()
            except Exception as exc:
                logger.warning("Langfuse observation close failed (%s)", type(exc).__name__)


def openai_usage(usage: dict[str, object]) -> dict[str, int]:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens", usage.get("cached_tokens"))
    if not isinstance(cached, int) or cached < 0 or (isinstance(prompt, int) and cached > prompt):
        cached = None
    return {
        key: count
        for key, count in (
            ("input", prompt - (cached or 0) if isinstance(prompt, int) else None),
            ("output", completion),
            ("input_cached_tokens", cached),
        )
        if isinstance(count, int) and count >= 0
    }
