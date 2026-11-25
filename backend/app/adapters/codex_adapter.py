from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from app.adapters.types import AdapterInput


class CodexAdapter:
    """Stub adapter for Codex (no official Python SDK for @openai/codex-sdk)."""

    name = "codex"

    async def stream(
        self, inp: AdapterInput, abort_event: asyncio.Event
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError(
            "CodexAdapter is currently unavailable in the pure-Python FastAPI backend "
            "(no official Python SDK for @openai/codex-sdk). "
            "Please switch this agent to the 'custom' (OpenAI / DeepSeek / OpenAI-compatible) "
            "or 'claude-code' adapter."
        )
        yield {}  # pragma: no cover
