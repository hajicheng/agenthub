from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.config import DEFAULT_COMPANION_PORT
from app.db.client import db_read, parse_json
from app.runner.agent_runner import agent_runner
from app.services.network_hints import get_connection_hints
from app.services.platform import current_platform, evidence_gates_enabled
from app.services.search_service import search_messages
from app.services.settings_service import (
    get_app_settings,
    regenerate_mobile_device_token,
    update_app_settings,
)

router = APIRouter(prefix="/api", tags=["misc"])

DAY_MS = 24 * 60 * 60 * 1000


@router.get("/platform")
async def get_platform() -> JSONResponse:
    return JSONResponse({"platform": current_platform(), "evidenceGates": evidence_gates_enabled()})


@router.get("/search")
async def get_search(
    q: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conversationId: str | None = Query(default=None),
    role: str | None = Query(default=None),
    fallback: str | None = Query(default=None),
) -> JSONResponse:
    if not q or len(q) > 200:
        return JSONResponse({"ok": False, "error": {"code": "INVALID_QUERY", "message": "Invalid q"}}, status_code=400)
    if role is not None and role not in ("user", "agent"):
        return JSONResponse({"ok": False, "error": {"code": "INVALID_QUERY", "message": "Invalid role"}}, status_code=400)
    res = search_messages(
        query=q,
        limit=limit,
        offset=offset,
        conversation_id=conversationId,
        role=role,
        fallback=fallback if fallback == "like" else None,
    )
    if res.get("error") == "INVALID_QUERY":
        return JSONResponse({"ok": False, "error": {"code": "INVALID_QUERY", "message": "Invalid search syntax"}}, status_code=400)
    return JSONResponse({"ok": True, "data": {"hits": res["hits"], "total": res["total"], "tookMs": res["tookMs"]}})


@router.get("/settings")
async def get_settings() -> JSONResponse:
    return JSONResponse({"settings": get_app_settings()})


@router.patch("/settings")
async def patch_settings(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    return JSONResponse({"settings": update_app_settings(body)})


@router.post("/settings/mobile-token")
async def post_mobile_token() -> JSONResponse:
    return JSONResponse({"settings": regenerate_mobile_device_token()})


@router.post("/runs/{run_id}/abort")
async def post_abort_run(run_id: str) -> JSONResponse:
    ok = agent_runner.abort(run_id)
    if not ok:
        return JSONResponse({"error": "Run not found or already finished"}, status_code=404)
    return JSONResponse({"ok": True})


@router.get("/connection-hints")
async def get_hints(request: Request) -> JSONResponse:
    settings = get_app_settings()
    local_port = str(request.url.port or os.environ.get("PORT") or "3000")
    remote_port = local_port if settings["companionMode"] == "off" else str(DEFAULT_COMPANION_PORT)
    return JSONResponse(
        {
            "hints": get_connection_hints(
                protocol=request.url.scheme,
                remote_port=remote_port,
                local_port=local_port,
            ),
            "companionMode": settings["companionMode"],
            "mobileDeviceTokenConfigured": bool(settings["mobileDeviceToken"]),
        }
    )


def _empty_bucket() -> dict[str, int]:
    return {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheCreationTokens": 0,
        "totalTokens": 0,
        "runs": 0,
    }


def _accum(b: dict[str, int], u: dict[str, Any]) -> None:
    inp = int(u.get("inputTokens") or 0)
    out = int(u.get("outputTokens") or 0)
    cr = int(u.get("cacheReadTokens") or 0)
    cc = int(u.get("cacheCreationTokens") or 0)
    b["inputTokens"] += inp
    b["outputTokens"] += out
    b["cacheReadTokens"] += cr
    b["cacheCreationTokens"] += cc
    b["totalTokens"] += inp + out + cr + cc
    b["runs"] += 1


@router.get("/usage/summary")
async def get_usage_summary() -> JSONResponse:
    with db_read() as conn:
        runs = conn.execute("SELECT * FROM agent_runs WHERE usage IS NOT NULL").fetchall()
        agents = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM agents").fetchall()}
        convs = {
            r["id"]: {"title": r["title"], "updatedAt": r["updated_at"]}
            for r in conn.execute("SELECT id, title, updated_at FROM conversations").fetchall()
        }

    now = int(time.time() * 1000)
    today_start = now - DAY_MS
    week_start = now - 7 * DAY_MS

    today = _empty_bucket()
    week = _empty_bucket()
    all_time = _empty_bucket()
    by_agent: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    by_conv: dict[str, dict[str, int]] = {}

    for r in runs:
        u = parse_json(r["usage"], None)
        if not isinstance(u, dict):
            continue
        _accum(all_time, u)
        if r["started_at"] >= week_start:
            _accum(week, u)
        if r["started_at"] >= today_start:
            _accum(today, u)
        _accum(by_agent.setdefault(r["agent_id"], _empty_bucket()), u)
        if u.get("model"):
            _accum(by_model.setdefault(str(u["model"]), _empty_bucket()), u)
        _accum(by_conv.setdefault(r["conversation_id"], _empty_bucket()), u)

    top_convs = sorted(by_conv.items(), key=lambda kv: kv[1]["totalTokens"], reverse=True)[:10]
    return JSONResponse(
        {
            "today": today,
            "week": week,
            "allTime": all_time,
            "topConversations": [
                {
                    "id": cid,
                    "title": convs[cid]["title"],
                    "totalTokens": b["totalTokens"],
                    "runs": b["runs"],
                    "updatedAt": convs[cid]["updatedAt"],
                }
                for cid, b in top_convs
                if cid in convs
            ],
            "byAgent": sorted(
                [
                    {
                        "agentId": aid,
                        "name": agents.get(aid, aid),
                        "totalTokens": b["totalTokens"],
                        "runs": b["runs"],
                    }
                    for aid, b in by_agent.items()
                ],
                key=lambda x: x["totalTokens"],
                reverse=True,
            ),
            "byModel": sorted(
                [
                    {"model": m, "totalTokens": b["totalTokens"], "runs": b["runs"]}
                    for m, b in by_model.items()
                ],
                key=lambda x: x["totalTokens"],
                reverse=True,
            ),
        }
    )


@router.post("/internal/agenthub-tools")
async def internal_tools_stub() -> JSONResponse:
    return JSONResponse({"error": "Internal Node MCP bridge is not used in pure-Python FastAPI mode"}, status_code=501)
