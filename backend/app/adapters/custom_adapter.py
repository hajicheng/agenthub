from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from app.adapters.runtime_context import with_current_time
from app.adapters.types import AdapterAttachment, AdapterInput
from app.ids import new_message_id
from app.observability import openai_usage, trace_step
from app.services.artifact_service import get_artifact
from app.services.settings_service import get_effective_api_key
from app.tools.registry import tool_registry
from app.tools.types import ToolContext

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_VOLCANO_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MAX_IMAGES_PER_MESSAGE = 5
MAX_TURNS = 8


def _resolve_client_config(
    provider: str, override_key: str | None, api_base_url: str | None
) -> tuple[str, str | None]:
    if provider == "deepseek":
        key = (override_key or "").strip() or get_effective_api_key("deepseek")
        if not key:
            raise ValueError("DEEPSEEK_API_KEY not set and agent has no apiKey")
        return key, (api_base_url or "").strip() or DEFAULT_DEEPSEEK_BASE_URL
    if provider == "volcano-ark":
        key = (override_key or "").strip() or get_effective_api_key("volcano-ark")
        if not key:
            raise ValueError("ARK_API_KEY not set and agent has no apiKey")
        return key, (api_base_url or "").strip() or DEFAULT_VOLCANO_ARK_BASE_URL
    if provider == "openai":
        key = (override_key or "").strip() or get_effective_api_key("openai")
        if not key:
            raise ValueError("OPENAI_API_KEY not set and agent has no apiKey")
        return key, (api_base_url or "").strip() or None
    if provider == "openai-compatible":
        key = (override_key or "").strip()
        base = (api_base_url or "").strip()
        if not base or not key:
            raise ValueError("openai-compatible provider requires apiBaseUrl and apiKey")
        return key, base
    raise ValueError(f'CustomAgentAdapter does not support provider "{provider}"')


def _build_multimodal_content(prompt: str, images: list[AdapterAttachment]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in images:
        try:
            b64 = base64.b64encode(Path(img.abs_path).read_bytes()).decode("ascii")
            blocks.append(
                {"type": "image_url", "image_url": {"url": f"data:{img.mime_type};base64,{b64}"}}
            )
        except Exception:
            pass
    return blocks


class CustomAgentAdapter:
    name = "custom"

    async def stream(
        self, inp: AdapterInput, abort_event: asyncio.Event
    ) -> AsyncIterator[dict[str, Any]]:
        if not inp.model_provider or not inp.model_id:
            raise ValueError("CustomAgentAdapter requires modelProvider and modelId")

        api_key, base_url = _resolve_client_config(inp.model_provider, inp.api_key, inp.api_base_url)
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=2)

        tool_defs = tool_registry.resolve(inp.tool_names, can_delegate=inp.delegate_task is not None)
        api_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tool_defs
        ]

        ctx = ToolContext(
            conversation_id=inp.conversation_id,
            workspace_path=inp.workspace_path,
            agent_id=inp.agent_id,
            run_id=inp.run_id,
            abort_event=abort_event,
            delegate_task=inp.delegate_task,
        )

        images = [a for a in inp.attachments if a.kind == "image"][:MAX_IMAGES_PER_MESSAGE]
        user_content: Any = (
            _build_multimodal_content(inp.prompt, images)
            if inp.supports_vision and images
            else inp.prompt
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": inp.system_prompt},
            *inp.history,
            {"role": "user", "content": user_content},
        ]

        def ev(body: dict[str, Any]) -> dict[str, Any]:
            return {**body, "conversationId": inp.conversation_id, "timestamp": int(time.time() * 1000)}

        run_usage = {
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 0,
            "lastInputTokens": 0,
        }

        for turn in range(MAX_TURNS):
            if abort_event.is_set():
                return
            can_use_tools = inp.delegate_task is None or turn < MAX_TURNS - 1
            message_id = new_message_id()
            yield ev(
                {
                    "type": "message.start",
                    "messageId": message_id,
                    "agentId": inp.agent_id,
                    "runId": inp.run_id,
                }
            )

            text_part_idx = -1
            text_buf = ""
            thinking_part_idx = -1
            reasoning_buf = ""
            next_part_idx = 0
            tool_call_buf: dict[int, dict[str, str]] = {}
            finish_reason: str | None = None
            msg_usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0}

            messages[0]["content"] = with_current_time(inp.system_prompt)
            kwargs: dict[str, Any] = {
                "model": inp.model_id,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if api_tools and can_use_tools:
                kwargs["tools"] = api_tools

            with trace_step(
                "chat.completions",
                as_type="generation",
                model=inp.model_id,
                metadata={"provider": inp.model_provider},
                input=messages,
                secrets=(api_key,),
                abort_event=abort_event,
            ) as generation:
                try:
                    stream = await client.chat.completions.create(**kwargs)
                except Exception:
                    yield ev({"type": "message.end", "messageId": message_id})
                    raise

                async for chunk in stream:
                    if abort_event.is_set():
                        generation.output = {"role": "assistant", "content": text_buf or None}
                        yield ev({"type": "message.end", "messageId": message_id})
                        return
                    generation.model = getattr(chunk, "model", None) or generation.model
                    usage = getattr(chunk, "usage", None)
                    if usage:
                        u_dict = usage.model_dump() if hasattr(usage, "model_dump") else {}
                        generation.usage = openai_usage(u_dict)
                        inp_t = int(u_dict.get("prompt_tokens") or 0)
                        out_t = int(u_dict.get("completion_tokens") or 0)
                        cached = int(u_dict.get("prompt_cache_hit_tokens") or u_dict.get("cached_tokens") or 0)
                        msg_usage["inputTokens"] += inp_t
                        msg_usage["outputTokens"] += out_t
                        msg_usage["cacheReadTokens"] += cached
                        run_usage["inputTokens"] += inp_t
                        run_usage["outputTokens"] += out_t
                        run_usage["cacheReadTokens"] += cached
                        run_usage["lastInputTokens"] = inp_t

                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    delta_extra = getattr(delta, "model_extra", None) or {}
                    reasoning = getattr(delta, "reasoning_content", None) or delta_extra.get("reasoning_content")

                    if isinstance(reasoning, str) and reasoning:
                        if thinking_part_idx < 0:
                            thinking_part_idx = next_part_idx
                            next_part_idx += 1
                            yield ev({"type": "part.start", "messageId": message_id, "partIndex": thinking_part_idx, "part": {"type": "thinking", "content": ""}})
                        reasoning_buf += reasoning
                        yield ev({"type": "part.delta", "messageId": message_id, "partIndex": thinking_part_idx, "delta": {"type": "thinking.append", "text": reasoning}})

                    if isinstance(delta.content, str) and delta.content:
                        if text_part_idx < 0:
                            text_part_idx = next_part_idx
                            next_part_idx += 1
                            yield ev({"type": "part.start", "messageId": message_id, "partIndex": text_part_idx, "part": {"type": "text", "content": ""}})
                        text_buf += delta.content
                        yield ev({"type": "part.delta", "messageId": message_id, "partIndex": text_part_idx, "delta": {"type": "text.append", "text": delta.content}})

                    if delta.tool_calls:
                        for tcd in delta.tool_calls:
                            idx = tcd.index
                            entry = tool_call_buf.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tcd.id:
                                entry["id"] = tcd.id
                            if tcd.function and tcd.function.name:
                                entry["name"] = tcd.function.name
                            if tcd.function and tcd.function.arguments:
                                entry["args"] += tcd.function.arguments

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                tool_calls = [tc for tc in tool_call_buf.values() if tc["id"] and tc["name"]]
                assistant_msg: dict[str, Any] = {"role": "assistant", "content": text_buf or None}
                if tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["args"] or "{}"},
                        }
                        for tc in tool_calls
                    ]
                generation.output = assistant_msg

            if thinking_part_idx >= 0:
                yield ev({"type": "part.end", "messageId": message_id, "partIndex": thinking_part_idx})
            if text_part_idx >= 0:
                yield ev({"type": "part.end", "messageId": message_id, "partIndex": text_part_idx})

            if reasoning_buf:
                assistant_msg["reasoning_content"] = reasoning_buf
            messages.append(assistant_msg)

            if not tool_calls or finish_reason == "stop" or not can_use_tools:
                if msg_usage["inputTokens"] > 0 or msg_usage["outputTokens"] > 0:
                    yield ev({"type": "message.usage", "messageId": message_id, "usage": msg_usage})
                yield ev({"type": "message.end", "messageId": message_id})
                yield ev({"type": "run.usage", "runId": inp.run_id, "usage": {**run_usage, "model": inp.model_id}})
                return

            turn_context = replace(ctx, message_id=message_id)
            for tc in tool_calls:
                if abort_event.is_set():
                    break
                try:
                    parsed_args = json.loads(tc["args"]) if tc["args"] else {}
                except Exception:
                    parsed_args = {}

                yield ev({"type": "tool.call", "messageId": message_id, "callId": tc["id"], "toolName": tc["name"], "args": parsed_args})
                result = await tool_registry.execute(tc["name"], parsed_args, turn_context)
                val = result.value if result.ok else {"error": result.error}
                yield ev({"type": "tool.result", "messageId": message_id, "callId": tc["id"], "result": val, "isError": not result.ok})

                if tc["name"] == "write_artifact" and result.ok and isinstance(val, dict) and val.get("artifactId"):
                    art = get_artifact(str(val["artifactId"]))
                    if art:
                        yield ev({"type": "artifact.create", "artifact": art})

                if tc["name"] in ("deploy_artifact", "deploy_workspace") and result.ok and isinstance(val, dict) and val.get("previewPath"):
                    yield ev({"type": "deploy.status", "messageId": message_id, "deployment": val})

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(val, ensure_ascii=False),
                    }
                )

            if msg_usage["inputTokens"] > 0 or msg_usage["outputTokens"] > 0:
                yield ev({"type": "message.usage", "messageId": message_id, "usage": msg_usage})
            yield ev({"type": "message.end", "messageId": message_id})

        yield ev({"type": "run.usage", "runId": inp.run_id, "usage": {**run_usage, "model": inp.model_id}})
