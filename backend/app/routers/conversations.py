from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.context_compaction_service import compact_conversation
from app.services.conversation_service import (
    add_agents_to_conversation,
    clear_conversation_history,
    create_conversation,
    delete_conversation,
    list_conversations,
    list_messages_snapshot,
    regenerate_latest_response,
    rename_conversation,
    send_message,
    set_conversation_approval_mode,
    toggle_archive_conversation,
    toggle_pin_conversation,
)
from app.services.deploy_command_service import (
    deploy_selected_artifact,
    handle_deploy_command,
    list_deploy_candidates,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("")
async def get_conversations() -> JSONResponse:
    return JSONResponse({"conversations": list_conversations()})


@router.post("")
async def post_conversation(request: Request) -> JSONResponse:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict) or body.get("mode") not in ("single", "group") or not isinstance(body.get("agentIds"), list):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    try:
        conv = create_conversation(
            mode=body["mode"],
            agent_ids=body["agentIds"],
            title=body.get("title"),
            bound_path=body.get("boundPath"),
        )
        return JSONResponse({"conversation": conv}, status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.delete("/{conv_id}")
async def remove_conversation(conv_id: str) -> JSONResponse:
    try:
        delete_conversation(conv_id)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.patch("/{conv_id}")
async def patch_conversation(conv_id: str, request: Request) -> JSONResponse:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict) or not any(
        k in body for k in ("addAgentIds", "title", "fsWriteApprovalMode", "togglePin", "toggleArchive")
    ):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    try:
        conv = None
        if body.get("title") is not None:
            conv = rename_conversation(conv_id, str(body["title"]))
        if body.get("addAgentIds") is not None:
            conv = add_agents_to_conversation(conv_id, list(body["addAgentIds"]))
        if body.get("fsWriteApprovalMode") is not None:
            conv = set_conversation_approval_mode(conv_id, str(body["fsWriteApprovalMode"]))
        if body.get("togglePin") is True:
            conv = toggle_pin_conversation(conv_id)
        if body.get("toggleArchive") is True:
            conv = toggle_archive_conversation(conv_id)
        return JSONResponse({"conversation": conv})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/{conv_id}/messages")
async def get_messages(conv_id: str) -> JSONResponse:
    return JSONResponse(list_messages_snapshot(conv_id))


@router.post("/{conv_id}/messages")
async def post_message(conv_id: str, request: Request) -> JSONResponse:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    content = str(body.get("content") or "") if isinstance(body, dict) else ""
    att_ids = body.get("attachmentIds") if isinstance(body, dict) else None
    if not content.strip() and not att_ids:
        return JSONResponse({"error": "必须提供 content 或 attachmentIds 之一"}, status_code=400)
    try:
        res = await send_message(
            conversation_id=conv_id,
            content=content,
            mentioned_agent_ids=body.get("mentionedAgentIds"),
            parent_message_id=body.get("parentMessageId"),
            attachment_ids=att_ids,
        )
        return JSONResponse(res, status_code=202)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.delete("/{conv_id}/messages")
async def delete_messages(conv_id: str) -> JSONResponse:
    try:
        return JSONResponse(clear_conversation_history(conv_id))
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ValueError as exc:
        status = 404 if str(exc).startswith("Conversation not found") else 400
        return JSONResponse({"error": str(exc)}, status_code=status)


@router.post("/{conv_id}/regenerate")
async def post_regenerate(conv_id: str) -> JSONResponse:
    try:
        return JSONResponse(await regenerate_latest_response(conv_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/{conv_id}/compact")
async def post_compact(conv_id: str) -> JSONResponse:
    try:
        return JSONResponse(await compact_conversation(conv_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/{conv_id}/deploy")
async def get_deploy_candidates(conv_id: str) -> JSONResponse:
    try:
        return JSONResponse({"candidates": list_deploy_candidates(conv_id)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/{conv_id}/deploy")
async def post_deploy(conv_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    artifact_id = body.get("artifactId") if isinstance(body, dict) else None
    try:
        res = (
            deploy_selected_artifact(conversation_id=conv_id, artifact_id=str(artifact_id))
            if artifact_id
            else handle_deploy_command(conversation_id=conv_id)
        )
        return JSONResponse(res)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
