from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import APP_VERSION
from app.db.client import db_read, row_to_artifact, row_to_run
from app.services.agent_service import list_agents_ordered
from app.services.conversation_service import list_conversations, list_messages
from app.services.pending_stores import pending_questions, pending_writes
from app.services.settings_service import get_app_settings


def require_mobile_auth(request: Request) -> JSONResponse | None:
    settings = get_app_settings()
    expected = (
        os.environ.get("AGENTHUB_MOBILE_TOKEN")
        or os.environ.get("AGENTHUB_MOBILE_DEV_TOKEN")
        or settings.get("mobileDeviceToken")
        or ""
    ).strip()
    if not expected:
        return JSONResponse(
            {"error": "Mobile companion is not configured on the desktop host"}, status_code=503
        )
    auth = request.headers.get("authorization") or ""
    parts = auth.strip().split(None, 1)
    if len(parts) != 2 or parts[0] != "Bearer" or not hmac.compare_digest(parts[1], expected):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return None


def _list_active_runs(conversation_ids: list[str]) -> list[dict[str, Any]]:
    if not conversation_ids:
        return []
    ph = ",".join("?" for _ in conversation_ids)
    with db_read() as conn:
        rows = conn.execute(
            f"SELECT * FROM agent_runs WHERE conversation_id IN ({ph}) AND status IN ('queued', 'running') ORDER BY started_at DESC",
            conversation_ids,
        ).fetchall()
    return [row_to_run(r) for r in rows]


def _to_mobile_part(part: dict[str, Any]) -> dict[str, Any]:
    ptype = part.get("type")
    if ptype in ("text", "thinking"):
        return {"type": ptype, "content": part.get("content", "")}
    if ptype == "code":
        return {"type": "code", "language": part.get("language", ""), "content": part.get("content", "")}
    if ptype == "tool_use":
        return {"type": "tool_use", "callId": part.get("callId", ""), "toolName": part.get("toolName", "")}
    if ptype == "tool_result":
        return {"type": "tool_result", "callId": part.get("callId", ""), "isError": bool(part.get("isError"))}
    if ptype == "artifact_ref":
        return {"type": "artifact_ref", "artifactId": part.get("artifactId", "")}
    if ptype == "deploy_status":
        d = part.get("deployment") or {}
        return {
            "type": "deploy_status",
            "title": d.get("title", ""),
            "version": d.get("version", 0),
            "sourceType": d.get("sourceType"),
            "workspacePath": d.get("workspacePath"),
            "previewPath": d.get("previewPath", ""),
            "status": d.get("status", "ready"),
            "error": d.get("error"),
        }
    if ptype == "deploy_candidates":
        return {"type": "deploy_candidates", "candidates": part.get("candidates", [])}
    if ptype in ("image_attachment", "file_attachment"):
        return {
            "type": "attachment",
            "fileName": part.get("fileName", ""),
            "kind": "image" if ptype == "image_attachment" else "file",
        }
    return {"type": "text", "content": ""}


def get_mobile_snapshot() -> dict[str, Any]:
    convs = list_conversations()
    agents = list_agents_ordered()
    running_runs = _list_active_runs([c["id"] for c in convs])

    all_writes: list[dict[str, Any]] = []
    all_questions: list[dict[str, Any]] = []
    conv_summaries: list[dict[str, Any]] = []

    for c in convs:
        ws = pending_writes.list_by_conversation(c["id"])
        qs = pending_questions.list_by_conversation(c["id"])
        all_writes.extend(ws)
        all_questions.extend(qs)
        conv_summaries.append(
            {
                "id": c["id"],
                "title": c["title"],
                "mode": c["mode"],
                "updatedAt": c["updatedAt"],
                "runningRunCount": sum(1 for r in running_runs if r["conversationId"] == c["id"]),
                "pendingWriteCount": len(ws),
                "pendingQuestionCount": len(qs),
            }
        )

    return {
        "conversations": conv_summaries,
        "agents": [
            {
                "id": a["id"],
                "name": a["name"],
                "avatar": a["avatar"],
                "description": a["description"],
                "isOrchestrator": a["isOrchestrator"],
            }
            for a in agents
        ],
        "runningRuns": [
            {
                "id": r["id"],
                "conversationId": r["conversationId"],
                "agentId": r["agentId"],
                "status": r["status"],
                "startedAt": r["startedAt"],
            }
            for r in running_runs
        ],
        "pendingWrites": all_writes,
        "pendingQuestions": all_questions,
        "server": {
            "version": APP_VERSION,
            "companionMode": "tailnet" if os.environ.get("AGENTHUB_COMPANION_MODE") == "tailnet" else "lan",
        },
    }


def get_mobile_conversation_detail(conversation_id: str) -> dict[str, Any]:
    convs = list_conversations()
    conv = next((c for c in convs if c["id"] == conversation_id), None)
    if not conv:
        raise ValueError(f"Conversation not found: {conversation_id}")
    msgs = list_messages(conversation_id)
    running_runs = _list_active_runs([conversation_id])

    art_ids = list(
        dict.fromkeys(
            p["artifactId"]
            for m in msgs
            for p in m["parts"]
            if p.get("type") == "artifact_ref" and p.get("artifactId")
        )
    )
    artifacts: list[dict[str, Any]] = []
    if art_ids:
        ph = ",".join("?" for _ in art_ids)
        with db_read() as conn:
            rows = conn.execute(f"SELECT * FROM artifacts WHERE id IN ({ph})", art_ids).fetchall()
        by_id = {r["id"]: row_to_artifact(r) for r in rows}
        for aid in art_ids:
            if aid in by_id:
                a = by_id[aid]
                artifacts.append(
                    {
                        "id": a["id"],
                        "type": a["type"],
                        "title": a["title"],
                        "version": a["version"],
                        "createdAt": a["createdAt"],
                    }
                )

    return {
        "conversation": {
            "id": conv["id"],
            "title": conv["title"],
            "mode": conv["mode"],
            "agentIds": conv["agentIds"],
            "updatedAt": conv["updatedAt"],
        },
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "agentId": m["agentId"],
                "parts": [_to_mobile_part(p) for p in m["parts"]],
                "status": m["status"],
                "createdAt": m["createdAt"],
            }
            for m in msgs
        ],
        "artifacts": artifacts,
        "runningRuns": [
            {
                "id": r["id"],
                "conversationId": r["conversationId"],
                "agentId": r["agentId"],
                "status": r["status"],
                "startedAt": r["startedAt"],
            }
            for r in running_runs
        ],
        "pendingWrites": pending_writes.list_by_conversation(conversation_id),
        "pendingQuestions": pending_questions.list_by_conversation(conversation_id),
    }


def get_mobile_artifact(artifact_id: str) -> dict[str, Any]:
    with db_read() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if not row:
        raise ValueError(f"Artifact not found: {artifact_id}")
    return row_to_artifact(row)
