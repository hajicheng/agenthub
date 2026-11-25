from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class ToolContext:
    conversation_id: str
    workspace_path: str
    agent_id: str
    run_id: str
    abort_event: asyncio.Event


@dataclass
class ToolResult:
    ok: bool
    value: Any = None
    error: str | None = None


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
