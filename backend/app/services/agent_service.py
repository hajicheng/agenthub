from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from app.db.client import db_read, db_tx, row_to_agent
from app.ids import new_agent_id


def _validate_openai_compatible(model_provider: str | None, api_base_url: str | None, api_key: str | None) -> None:
    if model_provider != "openai-compatible":
        return
    if not api_base_url or not api_base_url.strip():
        raise ValueError("openai-compatible provider requires apiBaseUrl")
    parsed = urlparse(api_base_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("apiBaseUrl must be a valid http(s) URL")
    if not api_key or not api_key.strip():
        raise ValueError("openai-compatible provider requires apiKey")


def list_agents_ordered() -> list[dict[str, Any]]:
    with db_read() as conn:
        rows = conn.execute(
            "SELECT * FROM agents ORDER BY is_builtin DESC, created_at DESC"
        ).fetchall()
        return [row_to_agent(r) for r in rows]


def get_agent(agent_id: str) -> dict[str, Any] | None:
    with db_read() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return row_to_agent(row) if row else None


def create_custom_agent(args: dict[str, Any]) -> dict[str, Any]:
    adapter_name = args.get("adapterName") or "custom"
    model_provider = args.get("modelProvider")
    model_id = args.get("modelId")
    api_key = (args.get("apiKey") or "").strip() or None
    api_base_url = (args.get("apiBaseUrl") or "").strip() or None

    if adapter_name == "custom":
        if not model_provider or not model_id:
            raise ValueError("Custom adapter requires modelProvider and modelId")
        _validate_openai_compatible(model_provider, api_base_url, api_key)

    agent = {
        "id": new_agent_id(),
        "name": str(args["name"]).strip(),
        "avatar": (args.get("avatar") or "").strip() or "🤖",
        "description": str(args["description"]).strip(),
        "capabilities": list(args.get("capabilities") or []),
        "systemPrompt": str(args["systemPrompt"]),
        "adapterName": adapter_name,
        "modelProvider": model_provider if adapter_name == "custom" else None,
        "modelId": model_id,
        "apiKey": api_key,
        "apiBaseUrl": api_base_url,
        "toolNames": list(args.get("toolNames") or []) if adapter_name == "custom" else [],
        "isBuiltin": False,
        "isOrchestrator": False,
        "supportsVision": bool(args.get("supportsVision", False)),
        "createdAt": int(time.time() * 1000),
    }

    with db_tx() as conn:
        conn.execute(
            """INSERT INTO agents (
                id, name, avatar, description, capabilities, system_prompt,
                adapter_name, model_provider, model_id, api_key, api_base_url,
                tool_names, is_builtin, is_orchestrator, supports_vision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent["id"],
                agent["name"],
                agent["avatar"],
                agent["description"],
                json.dumps(agent["capabilities"], ensure_ascii=False),
                agent["systemPrompt"],
                agent["adapterName"],
                agent["modelProvider"],
                agent["modelId"],
                agent["apiKey"],
                agent["apiBaseUrl"],
                json.dumps(agent["toolNames"], ensure_ascii=False),
                0,
                0,
                1 if agent["supportsVision"] else 0,
                agent["createdAt"],
            ),
        )
    return agent


def update_custom_agent(agent_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"Agent not found: {agent_id}")

    next_adapter = patch.get("adapterName", agent["adapterName"])
    next_provider = patch.get("modelProvider", agent["modelProvider"])
    next_model = patch.get("modelId", agent["modelId"])
    next_base_url = (
        (patch["apiBaseUrl"] or "").strip() or None
        if "apiBaseUrl" in patch
        else agent["apiBaseUrl"]
    )
    next_api_key = (
        (patch["apiKey"] or "").strip() or None
        if "apiKey" in patch
        else agent["apiKey"]
    )

    if next_adapter == "custom":
        if not next_provider or not next_model:
            raise ValueError("Custom adapter requires modelProvider and modelId")
        _validate_openai_compatible(next_provider, next_base_url, next_api_key)

    updates: dict[str, Any] = {}
    if "name" in patch and patch["name"] is not None:
        updates["name"] = str(patch["name"]).strip()
    if "description" in patch and patch["description"] is not None:
        updates["description"] = str(patch["description"]).strip()
    if "capabilities" in patch and patch["capabilities"] is not None:
        updates["capabilities"] = json.dumps(patch["capabilities"], ensure_ascii=False)
    if "systemPrompt" in patch and patch["systemPrompt"] is not None:
        updates["system_prompt"] = str(patch["systemPrompt"])
    if "adapterName" in patch and patch["adapterName"] is not None:
        updates["adapter_name"] = patch["adapterName"]
    if "modelId" in patch:
        updates["model_id"] = (patch["modelId"] or "").strip() or None
    if "supportsVision" in patch and patch["supportsVision"] is not None:
        updates["supports_vision"] = 1 if patch["supportsVision"] else 0
    if "apiKey" in patch:
        updates["api_key"] = next_api_key
    if "apiBaseUrl" in patch:
        updates["api_base_url"] = next_base_url

    if next_adapter == "custom":
        if "modelProvider" in patch:
            updates["model_provider"] = patch["modelProvider"]
        if "toolNames" in patch and patch["toolNames"] is not None:
            updates["tool_names"] = json.dumps(patch["toolNames"], ensure_ascii=False)
    else:
        if "adapterName" in patch and "modelId" not in patch:
            updates["model_id"] = None
        if any(k in patch for k in ("adapterName", "modelProvider", "toolNames")):
            updates["model_provider"] = None
            updates["tool_names"] = "[]"

    if not updates:
        return agent

    cols = ", ".join(f"{k} = ?" for k in updates.keys())
    vals = list(updates.values()) + [agent_id]
    with db_tx() as conn:
        conn.execute(f"UPDATE agents SET {cols} WHERE id = ?", vals)

    updated = get_agent(agent_id)
    if not updated:
        raise ValueError("Update succeeded but row missing afterwards")
    return updated


def delete_custom_agent(agent_id: str) -> None:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"Agent not found: {agent_id}")
    if agent["isBuiltin"]:
        raise ValueError("Built-in agents cannot be deleted")
    with db_tx() as conn:
        cur = conn.execute(
            "DELETE FROM agents WHERE id = ? AND is_builtin = 0", (agent_id,)
        )
        if cur.rowcount == 0:
            raise ValueError(f"Failed to delete agent: {agent_id}")
