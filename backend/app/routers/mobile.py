from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.conversation_service import (
    edit_and_resend_latest_user_message,
    regenerate_latest_response,
    send_message,
    withdraw_latest_user_message,
)
from app.services.mobile_service import (
    get_mobile_artifact,
    get_mobile_conversation_detail,
    get_mobile_snapshot,
    require_mobile_auth,
)
from app.services.pending_stores import pending_questions, pending_writes

router = APIRouter(prefix="/api/mobile", tags=["mobile"])


@router.get("/snapshot")
async def mobile_snapshot(request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    return JSONResponse(get_mobile_snapshot())


@router.get("/conversations/{conv_id}")
async def mobile_conv_detail(conv_id: str, request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    try:
        return JSONResponse(get_mobile_conversation_detail(conv_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.post("/conversations/{conv_id}/messages")
async def mobile_send_message(conv_id: str, request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    body = await request.json()
    content = str(body.get("content") or "") if isinstance(body, dict) else ""
    if not content.strip():
        return JSONResponse({"error": "content required"}, status_code=400)
    try:
        return JSONResponse(await send_message(conversation_id=conv_id, content=content), status_code=202)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/conversations/{conv_id}/messages/{msg_id}/edit")
async def mobile_edit_message(conv_id: str, msg_id: str, request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    body = await request.json()
    content = str(body.get("content") or "") if isinstance(body, dict) else ""
    try:
        return JSONResponse(await edit_and_resend_latest_user_message(conv_id, msg_id, content))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/conversations/{conv_id}/messages/{msg_id}/withdraw")
async def mobile_withdraw_message(conv_id: str, msg_id: str, request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    try:
        return JSONResponse(await withdraw_latest_user_message(conv_id, msg_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/conversations/{conv_id}/regenerate")
async def mobile_regenerate(conv_id: str, request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    try:
        return JSONResponse(await regenerate_latest_response(conv_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/artifacts/{art_id}")
async def mobile_artifact(art_id: str, request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    try:
        return JSONResponse(get_mobile_artifact(art_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.post("/pending-writes/{pw_id}")
async def mobile_pending_write(pw_id: str, request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    body = await request.json()
    action = body.get("action") if isinstance(body, dict) else None
    if action not in ("approve", "reject"):
        return JSONResponse({"error": "Invalid action"}, status_code=400)
    if not pending_writes.get(pw_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    pending_writes.resolve(pw_id, applied=(action == "approve"))
    return JSONResponse({"ok": True})


@router.post("/pending-questions/{qid}")
async def mobile_pending_question(qid: str, request: Request) -> JSONResponse:
    err = require_mobile_auth(request)
    if err:
        return err
    body = await request.json()
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        return JSONResponse({"error": "Invalid answers"}, status_code=400)
    if not pending_questions.get(qid):
        return JSONResponse({"error": "Not found"}, status_code=404)
    pending_questions.answer(qid, answers)
    return JSONResponse({"ok": True})
