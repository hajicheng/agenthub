from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.conversation_service import revise_dispatch_plan
from app.services.pending_stores import (
    pending_bash_commands,
    pending_dispatch_plans,
    pending_questions,
    pending_writes,
)

router = APIRouter(prefix="/api/conversations/{conv_id}", tags=["pending"])


@router.get("/pending-writes")
async def list_pending_writes(conv_id: str) -> JSONResponse:
    return JSONResponse({"pendingWrites": pending_writes.list_by_conversation(conv_id)})


@router.post("/pending-writes/{pw_id}")
async def resolve_pending_write(conv_id: str, pw_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    action = body.get("action") if isinstance(body, dict) else None
    if action not in ("approve", "reject"):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not pending_writes.get(pw_id):
        return JSONResponse({"error": "Pending write not found"}, status_code=404)
    ok = pending_writes.resolve(pw_id, applied=(action == "approve"))
    if not ok:
        return JSONResponse({"error": "Failed to process pending write"}, status_code=500)
    return JSONResponse({"ok": True})


@router.get("/pending-questions")
async def list_pending_questions(conv_id: str) -> JSONResponse:
    return JSONResponse({"pendingQuestions": pending_questions.list_by_conversation(conv_id)})


@router.post("/pending-questions/{qid}")
async def resolve_pending_question(conv_id: str, qid: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not pending_questions.get(qid):
        return JSONResponse({"error": "Pending question not found"}, status_code=404)
    ok = pending_questions.answer(qid, answers)
    if not ok:
        return JSONResponse({"error": "Failed to record answer"}, status_code=500)
    return JSONResponse({"ok": True})


@router.get("/pending-bash-commands")
async def list_pending_bash(conv_id: str) -> JSONResponse:
    return JSONResponse({"pendingCommands": pending_bash_commands.list_by_conversation(conv_id)})


@router.post("/pending-bash-commands/{command_id}")
async def resolve_pending_bash(conv_id: str, command_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    action = body.get("action") if isinstance(body, dict) else None
    if action not in ("approve", "reject"):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    existing = pending_bash_commands.get(command_id)
    if not existing or existing["conversationId"] != conv_id:
        return JSONResponse({"error": "Pending command not found"}, status_code=404)
    ok = pending_bash_commands.resolve(command_id, approved=(action == "approve"))
    if not ok:
        return JSONResponse({"error": "Failed to process pending command"}, status_code=500)
    return JSONResponse({"ok": True})


@router.get("/pending-dispatch-plans")
async def list_pending_plans(conv_id: str) -> JSONResponse:
    return JSONResponse({"pendingDispatchPlans": pending_dispatch_plans.list_by_conversation(conv_id)})


@router.post("/pending-dispatch-plans/{plan_id}")
async def resolve_pending_plan(conv_id: str, plan_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    action = body.get("action") if isinstance(body, dict) else None
    if action not in ("approve", "reject", "revise"):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    existing = pending_dispatch_plans.get(plan_id)
    if not existing or existing["conversationId"] != conv_id:
        return JSONResponse({"error": "Pending dispatch plan not found"}, status_code=404)
    if action == "reject":
        ok = pending_dispatch_plans.reject(plan_id)
        return JSONResponse({"ok": True}) if ok else JSONResponse({"error": "Failed to reject"}, status_code=500)
    if action == "revise":
        fb = str(body.get("feedback") or "").strip()
        if not fb:
            return JSONResponse({"error": "feedback is required"}, status_code=400)
        ok, err = revise_dispatch_plan(conversation_id=conv_id, plan_id=plan_id, feedback=fb)
        return JSONResponse({"ok": True}) if ok else JSONResponse({"error": err}, status_code=400)
    ok, err = pending_dispatch_plans.approve(plan_id)
    return JSONResponse({"ok": True}) if ok else JSONResponse({"error": err}, status_code=400)
