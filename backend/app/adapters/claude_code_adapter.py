from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from app.adapters.types import AdapterInput
from app.ids import new_message_id
from app.services.artifact_service import get_artifact
from app.services.settings_service import get_effective_anthropic_base_url, get_effective_api_key
from app.tools.registry import tool_registry
from app.tools.types import ToolContext

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TURNS = 10


class ClaudeCodeAdapter:
    """Pure-Python Anthropic adapter with full AgentHub tool loop & approval integration."""

    name = "claude-code"

    async def stream(
        self, inp: AdapterInput, abort_event: asyncio.Event
    ) -> AsyncIterator[dict[str, Any]]:
        api_key = (inp.api_key or "").strip() or get_effective_api_key("anthropic")
        base_url = (inp.api_base_url or "").strip() or get_effective_anthropic_base_url()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set and agent has no apiKey")

        model_id = (inp.model_id or "").strip() or DEFAULT_CLAUDE_MODEL
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = AsyncAnthropic(**client_kwargs)

        # Default tool set for claude-code agents
        requested_tools = inp.tool_names or [
            "write_artifact",
            "read_artifact",
            "deploy_artifact",
            "deploy_workspace",
            "read_attachment",
            "fs_list",
            "fs_read",
            "fs_write",
            "bash",
            "ask_user",
        ]
        tool_defs = tool_registry.resolve(requested_tools)
        anthropic_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tool_defs
        ]

        ctx = ToolContext(
            conversation_id=inp.conversation_id,
            workspace_path=inp.workspace_path,
            agent_id=inp.agent_id,
            run_id=inp.run_id,
            abort_event=abort_event,
        )

        messages: list[dict[str, Any]] = []
        for h in inp.history:
            role = h.get("role")
            if role in ("user", "assistant") and h.get("content"):
                messages.append({"role": role, "content": str(h["content"])})
        messages.append({"role": "user", "content": inp.prompt})

        def ev(body: dict[str, Any]) -> dict[str, Any]:
            return {**body, "conversationId": inp.conversation_id, "timestamp": int(time.time() * 1000)}

        run_usage = {
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 0,
            "lastInputTokens": 0,
        }

        for _ in range(MAX_TURNS):
            if abort_event.is_set():
                return
            message_id = new_message_id()
            yield ev({"type": "message.start", "messageId": message_id, "agentId": inp.agent_id, "runId": inp.run_id})

            text_part_idx = -1
            next_part_idx = 0
            text_buf = ""
            tool_uses: list[dict[str, Any]] = []

            req_kwargs: dict[str, Any] = {
                "model": model_id,
                "max_tokens": 8192,
                "system": inp.system_prompt,
                "messages": messages,
            }
            if anthropic_tools:
                req_kwargs["tools"] = anthropic_tools

            async with client.messages.stream(**req_kwargs) as stream:
                async for text_delta in stream.text_stream:
                    if abort_event.is_set():
                        yield ev({"type": "message.end", "messageId": message_id})
                        return
                    if text_part_idx < 0:
                        text_part_idx = next_part_idx
                        next_part_idx += 1
                        yield ev({"type": "part.start", "messageId": message_id, "partIndex": text_part_idx, "part": {"type": "text", "content": ""}})
                    text_buf += text_delta
                    yield ev({"type": "part.delta", "messageId": message_id, "partIndex": text_part_idx, "delta": {"type": "text.append", "text": text_delta}})

                final_msg = await stream.get_final_message()

            if text_part_idx >= 0:
                yield ev({"type": "part.end", "messageId": message_id, "partIndex": text_part_idx})

            usage = getattr(final_msg, "usage", None)
            msg_usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0}
            if usage:
                inp_t = int(getattr(usage, "input_tokens", 0) or 0)
                out_t = int(getattr(usage, "output_tokens", 0) or 0)
                cc = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
                cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                msg_usage = {"inputTokens": inp_t, "outputTokens": out_t, "cacheReadTokens": cr}
                run_usage["inputTokens"] += inp_t
                run_usage["outputTokens"] += out_t
                run_usage["cacheCreationTokens"] += cc
                run_usage["cacheReadTokens"] += cr
                run_usage["lastInputTokens"] = inp_t

            assistant_blocks: list[dict[str, Any]] = []
            for blk in final_msg.content:
                if blk.type == "text":
                    assistant_blocks.append({"type": "text", "text": blk.text})
                elif blk.type == "tool_use":
                    tu = {"type": "tool_use", "id": blk.id, "name": blk.name, "input": blk.input}
                    assistant_blocks.append(tu)
                    tool_uses.append(tu)

            messages.append({"role": "assistant", "content": assistant_blocks})

            if not tool_uses:
                if msg_usage["inputTokens"] > 0 or msg_usage["outputTokens"] > 0:
                    yield ev({"type": "message.usage", "messageId": message_id, "usage": msg_usage})
                yield ev({"type": "message.end", "messageId": message_id})
                yield ev({"type": "run.usage", "runId": inp.run_id, "usage": {**run_usage, "model": model_id}})
                return

            tool_results_blocks: list[dict[str, Any]] = []
            for tu in tool_uses:
                yield ev({"type": "tool.call", "messageId": message_id, "callId": tu["id"], "toolName": tu["name"], "args": tu["input"]})
                res = await tool_registry.execute(tu["name"], tu["input"], ctx)
                val = res.value if res.ok else {"error": res.error}
                yield ev({"type": "tool.result", "messageId": message_id, "callId": tu["id"], "result": val, "isError": not res.ok})

                if tu["name"] == "write_artifact" and res.ok and isinstance(val, dict) and val.get("artifactId"):
                    art = get_artifact(str(val["artifactId"]))
                    if art:
                        yield ev({"type": "artifact.create", "artifact": art})
                if tu["name"] in ("deploy_artifact", "deploy_workspace") and res.ok and isinstance(val, dict) and val.get("previewPath"):
                    yield ev({"type": "deploy.status", "messageId": message_id, "deployment": val})

                tool_results_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": json.dumps(val, ensure_ascii=False),
                        "is_error": not res.ok,
                    }
                )

            messages.append({"role": "user", "content": tool_results_blocks})
            if msg_usage["inputTokens"] > 0 or msg_usage["outputTokens"] > 0:
                yield ev({"type": "message.usage", "messageId": message_id, "usage": msg_usage})
            yield ev({"type": "message.end", "messageId": message_id})

        yield ev({"type": "run.usage", "runId": inp.run_id, "usage": {**run_usage, "model": model_id}})
