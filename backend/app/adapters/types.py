from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass
class AdapterAttachment:
    id: str
    file_name: str
    mime_type: str
    kind: str  # 'image' | 'file'
    abs_path: str


@dataclass
class AdapterInput:
    agent_id: str
    conversation_id: str
    run_id: str
    prompt: str
    workspace_path: str
    system_prompt: str
    api_key: str | None
    api_base_url: str | None
    model_id: str | None
    tool_names: list[str]
    attachments: list[AdapterAttachment] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    model_provider: str | None = None
    supports_vision: bool = False


class AgentPlatformAdapter(Protocol):
    name: str

    def stream(self, inp: AdapterInput, abort_event: asyncio.Event) -> AsyncIterator[dict[str, Any]]: ...
