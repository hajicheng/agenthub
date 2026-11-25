from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.conversation_service import (
    edit_and_resend_latest_user_message,
    toggle_bookmarked_message,
    toggle_pinned_message,
    withdraw_latest_user_message,
)

router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.post("/{msg_id}/bookmark")
async def post_bookmark(msg_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    conv_id = body.get("conversationId") if isinstance(body, dict) else None
    if not conv_id:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    try:
        return JSONResponse(toggle_bookmarked_message(str(conv_id), msg_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/{msg_id}/pin")
async def post_pin(msg_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    conv_id = body.get("conversationId") if isinstance(body, dict) else None
    if not conv_id:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    try:
        return JSONResponse(toggle_pinned_message(str(conv_id), msg_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/{msg_id}/withdraw")
async def post_withdraw(msg_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    conv_id = body.get("conversationId") if isinstance(body, dict) else None
    if not conv_id:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    try:
        return JSONResponse(await withdraw_latest_user_message(str(conv_id), msg_id))
    except Exception as exc:
        msg = str(exc)
        return JSONResponse({"error": msg}, status_code=404 if "not found" in msg.lower() else 400)


@router.post("/{msg_id}/edit")
async def post_edit(msg_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    conv_id = body.get("conversationId") if isinstance(body, dict) else None
    content = body.get("content") if isinstance(body, dict) else None
    if not conv_id or not content:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    try:
        return JSONResponse(await edit_and_resend_latest_user_message(str(conv_id), msg_id, str(content)))
    except Exception as exc:
        msg = str(exc)
        return JSONResponse({"error": msg}, status_code=404 if "not found" in msg.lower() else 400)
