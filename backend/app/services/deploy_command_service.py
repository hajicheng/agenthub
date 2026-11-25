from __future__ import annotations

import json
import re
import time
from typing import Any

from app.db.client import db_read, db_tx, row_to_artifact
from app.ids import new_deployment_id, new_message_id
from app.services.deployment_service import create_local_static_deployment
from app.services.event_bus import event_bus

_DEPLOY_CMD_RE = re.compile(r"^/deploy(?:\s+(art_[0-9A-Za-z]+))?\s*$", re.I)


def parse_deploy_command(content: str) -> dict[str, Any] | None:
    m = _DEPLOY_CMD_RE.match((content or "").strip())
    if not m:
        return None
    return {"artifactId": m.group(1)}


def list_deploy_candidates(conversation_id: str) -> list[dict[str, Any]]:
    with db_read() as conn:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE conversation_id = ? AND type = 'web_app' ORDER BY created_at DESC",
            (conversation_id,),
        ).fetchall()
    return [
        {
            "artifactId": r["id"],
            "title": r["title"],
            "version": r["version"],
            "createdByAgentId": r["created_by_agent_id"],
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


def _save_system_message(conversation_id: str, parts: list[dict[str, Any]], after_created_at: int | None = None) -> dict[str, Any]:
    now = max(int(time.time() * 1000), (after_created_at or 0) + 1)
    msg = {
        "id": new_message_id(),
        "conversationId": conversation_id,
        "role": "system",
        "agentId": None,
        "parts": parts,
        "status": "complete",
        "parentMessageId": None,
        "mentionedAgentIds": [],
        "runId": None,
        "usage": None,
        "createdAt": now,
    }
    with db_tx() as conn:
        conn.execute(
            """INSERT INTO messages (
                id, conversation_id, role, agent_id, parts, status,
                parent_message_id, mentioned_agent_ids, run_id, usage, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg["id"],
                msg["conversationId"],
                msg["role"],
                None,
                json.dumps(msg["parts"], ensure_ascii=False),
                msg["status"],
                None,
                "[]",
                None,
                None,
                msg["createdAt"],
            ),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    event_bus.publish(
        {
            "type": "message.added",
            "conversationId": conversation_id,
            "timestamp": now,
            "message": msg,
        }
    )
    return msg


def deploy_selected_artifact(
    *, conversation_id: str, artifact_id: str, after_created_at: int | None = None
) -> dict[str, Any]:
    with db_read() as conn:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE id = ? AND conversation_id = ?",
            (artifact_id, conversation_id),
        ).fetchone()
    if not row:
        raise ValueError(f"Web app artifact not found: {artifact_id}")
    art = row_to_artifact(row)
    if art["type"] != "web_app":
        raise ValueError(f"Artifact is not a web_app: {artifact_id}")

    deployment = create_local_static_deployment(
        dep_id=new_deployment_id(),
        artifact_id=art["id"],
        title=art["title"],
        version=art["version"],
        content=art["content"],
    )
    msg = _save_system_message(
        conversation_id,
        [{"type": "deploy_status", "deployment": deployment}],
        after_created_at=after_created_at,
    )
    return {"kind": "deployed", "deployment": deployment, "message": msg}


def handle_deploy_command(
    *, conversation_id: str, artifact_id: str | None = None, after_created_at: int | None = None
) -> dict[str, Any]:
    if artifact_id:
        return deploy_selected_artifact(
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            after_created_at=after_created_at,
        )
    candidates = list_deploy_candidates(conversation_id)
    if not candidates:
        msg = _save_system_message(
            conversation_id,
            [{"type": "text", "content": "当前会话没有可部署的 web_app 产物。"}],
            after_created_at=after_created_at,
        )
        return {"kind": "empty", "candidates": [], "message": msg}
    if len(candidates) == 1:
        return deploy_selected_artifact(
            conversation_id=conversation_id,
            artifact_id=candidates[0]["artifactId"],
            after_created_at=after_created_at,
        )
    msg = _save_system_message(
        conversation_id,
        [{"type": "deploy_candidates", "candidates": candidates}],
        after_created_at=after_created_at,
    )
    return {"kind": "candidates", "candidates": candidates, "message": msg}
