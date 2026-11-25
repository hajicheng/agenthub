from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI

from app.observability import openai_usage, trace_step

from app.services.settings_service import get_effective_api_key


async def create_agent_config_draft(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or payload.get("description") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")

    # Try DeepSeek or OpenAI if key is available, otherwise generate deterministic draft
    provider = "deepseek"
    api_key = get_effective_api_key("deepseek")
    base_url: str | None = "https://api.deepseek.com/v1"
    model_id = "deepseek-chat"

    if not api_key:
        api_key = get_effective_api_key("openai")
        provider = "openai"
        base_url = None
        model_id = "gpt-4o-mini"

    if api_key:
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Generate a JSON object for an AgentHub agent configuration with keys: "
                        "name (string, max 32 chars), avatar (single emoji), description (string, max 200 chars), "
                        "capabilities (array of 2-5 short strings), systemPrompt (detailed markdown string), "
                        "toolNames (subset of ['write_artifact','read_artifact','deploy_artifact','read_attachment','fs_list','fs_read','fs_write','bash','ask_user']). "
                        "Return ONLY valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            with trace_step(
                "agent.config-draft",
                as_type="generation",
                model=model_id,
                metadata={"provider": provider},
                input=messages,
                secrets=(api_key,),
            ) as generation:
                resp = await client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=0.4,
                )
                generation.model = resp.model or model_id
                if resp.choices:
                    generation.output = resp.choices[0].message.content
                if resp.usage is not None:
                    generation.usage = openai_usage(resp.usage.model_dump())
            raw = resp.choices[0].message.content or ""
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                parsed = json.loads(match.group(0))
                return {
                    "name": str(parsed.get("name") or "自定义助手")[:64],
                    "avatar": str(parsed.get("avatar") or "🤖")[:8],
                    "description": str(parsed.get("description") or prompt)[:280],
                    "capabilities": list(parsed.get("capabilities") or ["assistant"])[:8],
                    "systemPrompt": str(parsed.get("systemPrompt") or f"你是一个专业助手：{prompt}"),
                    "adapterName": "custom",
                    "modelProvider": provider,
                    "modelId": model_id,
                    "toolNames": list(parsed.get("toolNames") or ["read_artifact", "write_artifact", "fs_read"]),
                }
        except Exception:
            pass

    return {
        "name": prompt[:20].strip() or "自定义 Agent",
        "avatar": "🤖",
        "description": prompt[:280],
        "capabilities": ["general", "analysis"],
        "systemPrompt": f"你是一个专注于以下任务的智能助手：\n\n{prompt}\n\n请给出清晰、结构化、可直接落地的回答。",
        "adapterName": "custom",
        "modelProvider": "deepseek",
        "modelId": "deepseek-chat",
        "toolNames": ["write_artifact", "read_artifact", "fs_list", "fs_read", "ask_user"],
    }
