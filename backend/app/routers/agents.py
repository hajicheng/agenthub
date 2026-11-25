from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.agent_draft_service import create_agent_config_draft
from app.services.agent_service import (
    create_custom_agent,
    delete_custom_agent,
    list_agents_ordered,
    update_custom_agent,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def get_agents() -> JSONResponse:
    return JSONResponse({"agents": list_agents_ordered()})


@router.post("")
async def post_agent(request: Request) -> JSONResponse:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict) or not body.get("name") or not body.get("description") or not body.get("systemPrompt"):
        return JSONResponse({"error": "Invalid body: name, description, systemPrompt are required"}, status_code=400)
    try:
        agent = create_custom_agent(body)
        return JSONResponse({"agent": agent}, status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/draft")
async def post_agent_draft(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    try:
        draft = await create_agent_config_draft(body if isinstance(body, dict) else {})
        return JSONResponse({"draft": draft})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.patch("/{agent_id}")
async def patch_agent(agent_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    try:
        agent = update_custom_agent(agent_id, body)
        return JSONResponse({"agent": agent})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str) -> JSONResponse:
    try:
        delete_custom_agent(agent_id)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
