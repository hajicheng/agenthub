from __future__ import annotations

from typing import Any

from app.adapters.claude_code_adapter import ClaudeCodeAdapter
from app.adapters.codex_adapter import CodexAdapter
from app.adapters.custom_adapter import CustomAgentAdapter
from app.adapters.mock_adapter import MockAdapter
from app.adapters.types import AgentPlatformAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AgentPlatformAdapter] = {
            "custom": CustomAgentAdapter(),
            "claude-code": ClaudeCodeAdapter(),
            "codex": CodexAdapter(),
            "mock": MockAdapter(),
        }

    def get_adapter(self, agent: dict[str, Any]) -> AgentPlatformAdapter:
        name = str(agent.get("adapterName") or "custom")
        adapter = self._adapters.get(name)
        if not adapter:
            raise ValueError(f"Unknown adapter: {name}")
        return adapter


adapter_registry = AdapterRegistry()
