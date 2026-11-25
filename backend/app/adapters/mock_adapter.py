from __future__ import annotations

import asyncio
import re
import time
from typing import Any, AsyncIterator

from app.adapters.types import AdapterInput
from app.ids import new_message_id, new_tool_call_id

_GREETING_SCRIPT = [
    {"kind": "thinking", "content": "用户在问候，我应该礼貌回应并介绍自己。"},
    {
        "kind": "text",
        "content": (
            "你好！我是 Mock Agent，目前用于验证 AgentHub（FastAPI 后端）的端到端骨架。"
            "我会按预设脚本流式回复，不消耗任何 LLM token。\n\n"
            "你可以试试输入「写代码」或「执行任务」看其他场景。"
        ),
    },
]

_CODE_SCRIPT = [
    {"kind": "thinking", "content": "用户希望看到代码示例，我演示一段 React 组件代码。"},
    {"kind": "text", "content": "好的，这是一个简单的 React 计数器组件："},
    {
        "kind": "code",
        "language": "tsx",
        "content": (
            "import { useState } from 'react'\n\n"
            "export function Counter() {\n"
            "  const [count, setCount] = useState(0)\n"
            "  return (\n"
            "    <div className=\"flex items-center gap-2\">\n"
            "      <button onClick={() => setCount(c => c - 1)}>-</button>\n"
            "      <span>{count}</span>\n"
            "      <button onClick={() => setCount(c => c + 1)}>+</button>\n"
            "    </div>\n"
            "  )\n"
            "}"
        ),
    },
    {"kind": "text", "content": "这是最朴素的实现。需要扩展可以告诉我（持久化、键盘快捷键等）。"},
]

_TOOL_SCRIPT = [
    {"kind": "thinking", "content": "我需要演示工具调用流程。"},
    {"kind": "text", "content": "我先调用工具收集信息："},
    {
        "kind": "tool",
        "toolName": "read_artifact",
        "args": {"artifactId": "art_demo"},
        "result": {"title": "示例产物", "size": 1024},
    },
    {"kind": "text", "content": "已读取产物信息。这只是脚本演示。"},
]

_DEFAULT_SCRIPT = [
    {"kind": "thinking", "content": "收到了用户消息，按通用模板回应。"},
    {
        "kind": "text",
        "content": (
            "我收到了你的消息。这是 MockAdapter 的默认响应，用于验证消息流式渲染、part 切换、tool 调用等链路。\n\n"
            '试试输入 "你好"、"写代码"、"执行任务" 触发不同脚本。'
        ),
    },
]


def _pick_script(prompt: str) -> list[dict[str, Any]]:
    p = prompt.lower()
    if re.search(r"(你好|hello|hi|您好)", p):
        return _GREETING_SCRIPT
    if re.search(r"(写代码|代码|code|component|组件)", p):
        return _CODE_SCRIPT
    if re.search(r"(执行|工具|tool|run|跑)", p):
        return _TOOL_SCRIPT
    return _DEFAULT_SCRIPT


def _chunk(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class MockAdapter:
    name = "mock"

    async def stream(
        self, inp: AdapterInput, abort_event: asyncio.Event
    ) -> AsyncIterator[dict[str, Any]]:
        script = _pick_script(inp.prompt)
        message_id = new_message_id()

        def ev(body: dict[str, Any]) -> dict[str, Any]:
            return {**body, "conversationId": inp.conversation_id, "timestamp": int(time.time() * 1000)}

        yield ev(
            {
                "type": "message.start",
                "messageId": message_id,
                "agentId": inp.agent_id,
                "runId": inp.run_id,
            }
        )

        part_index = -1
        for step in script:
            if abort_event.is_set():
                break
            part_index += 1
            kind = step["kind"]
            if kind == "text":
                yield ev({"type": "part.start", "messageId": message_id, "partIndex": part_index, "part": {"type": "text", "content": ""}})
                for ch in _chunk(step["content"], 6):
                    if abort_event.is_set():
                        break
                    await asyncio.sleep(0.015)
                    yield ev({"type": "part.delta", "messageId": message_id, "partIndex": part_index, "delta": {"type": "text.append", "text": ch}})
                yield ev({"type": "part.end", "messageId": message_id, "partIndex": part_index})
            elif kind == "thinking":
                yield ev({"type": "part.start", "messageId": message_id, "partIndex": part_index, "part": {"type": "thinking", "content": ""}})
                for ch in _chunk(step["content"], 10):
                    if abort_event.is_set():
                        break
                    await asyncio.sleep(0.01)
                    yield ev({"type": "part.delta", "messageId": message_id, "partIndex": part_index, "delta": {"type": "thinking.append", "text": ch}})
                yield ev({"type": "part.end", "messageId": message_id, "partIndex": part_index})
            elif kind == "code":
                yield ev({"type": "part.start", "messageId": message_id, "partIndex": part_index, "part": {"type": "code", "language": step["language"], "content": ""}})
                for ch in _chunk(step["content"], 12):
                    if abort_event.is_set():
                        break
                    await asyncio.sleep(0.01)
                    yield ev({"type": "part.delta", "messageId": message_id, "partIndex": part_index, "delta": {"type": "code.append", "text": ch}})
                yield ev({"type": "part.end", "messageId": message_id, "partIndex": part_index})
            elif kind == "tool":
                call_id = new_tool_call_id()
                yield ev({"type": "tool.call", "messageId": message_id, "callId": call_id, "toolName": step["toolName"], "args": step["args"]})
                await asyncio.sleep(0.15)
                yield ev({"type": "tool.result", "messageId": message_id, "callId": call_id, "result": step.get("result", {"ok": True}), "isError": False})

        yield ev({"type": "message.end", "messageId": message_id})
