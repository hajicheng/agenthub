from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.services.fs_service import (
    get_workspace_for_conversation,
    list_dir_in_workspace,
    read_file_in_workspace,
    write_file_in_workspace,
)
from app.services.platform import IS_WINDOWS
from app.services.workspace_utils import get_available_drives, is_path_safe

router = APIRouter(tags=["fs"])

DRIVES_SENTINEL = "__drives__"
WINDOWS_HIDDEN_NAMES = {
    "appdata",
    "$recycle.bin",
    "system volume information",
    "recovery",
    "perflogs",
    "config.msi",
    "msocache",
    "onedrivetemp",
    "programdata",
}


@router.get("/api/fs/listdir")
async def global_listdir(path: str | None = Query(default=None)) -> JSONResponse:
    target = (path or "").strip() or str(Path.home())

    if target == DRIVES_SENTINEL:
        drives = get_available_drives()
        return JSONResponse(
            {
                "path": DRIVES_SENTINEL,
                "parent": None,
                "entries": [
                    {"name": d.rstrip("\\/") or d, "isDirectory": True, "path": d}
                    for d in drives
                ],
            }
        )

    if not os.path.isabs(target):
        return JSONResponse({"error": "path must be absolute"}, status_code=400)

    resolved = os.path.abspath(target)
    home = os.path.abspath(str(Path.home()))
    if resolved != home and not is_path_safe(resolved):
        return JSONResponse({"error": "Path not allowed"}, status_code=403)
    if not os.path.exists(resolved):
        return JSONResponse({"error": "Path does not exist"}, status_code=404)
    if not os.path.isdir(resolved):
        return JSONResponse({"error": "Not a directory"}, status_code=400)

    try:
        entries = []
        with os.scandir(resolved) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                if IS_WINDOWS and e.name.lower() in WINDOWS_HIDDEN_NAMES:
                    continue
                if e.is_dir():
                    entries.append({"name": e.name, "isDirectory": True})
        entries.sort(key=lambda x: x["name"].lower())
    except OSError as exc:
        return JSONResponse({"error": f"Cannot read directory: {exc}"}, status_code=403)

    parent_dir = os.path.dirname(resolved)
    parent = parent_dir if parent_dir != resolved else (DRIVES_SENTINEL if IS_WINDOWS else None)
    return JSONResponse({"path": resolved, "parent": parent, "entries": entries})


@router.get("/api/conversations/{conv_id}/fs/listdir")
async def conv_listdir(conv_id: str, path: str = Query(default="")) -> JSONResponse:
    ws = get_workspace_for_conversation(conv_id)
    if not ws:
        return JSONResponse({"error": "Workspace not found"}, status_code=404)
    try:
        return JSONResponse(list_dir_in_workspace(ws, path))
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/conversations/{conv_id}/fs/read")
async def conv_fs_read(conv_id: str, path: str | None = Query(default=None)) -> JSONResponse:
    if not path:
        return JSONResponse({"error": "path required"}, status_code=400)
    ws = get_workspace_for_conversation(conv_id)
    if not ws:
        return JSONResponse({"error": "Workspace not found"}, status_code=404)
    try:
        return JSONResponse(read_file_in_workspace(ws, path))
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        msg = str(exc)
        status = 413 if "too large" in msg else 400
        return JSONResponse({"error": msg}, status_code=status)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/conversations/{conv_id}/fs/write")
async def conv_fs_write(conv_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict) or not body.get("path") or not isinstance(body.get("content"), str):
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    ws = get_workspace_for_conversation(conv_id)
    if not ws:
        return JSONResponse({"error": "Workspace not found"}, status_code=404)
    try:
        return JSONResponse(write_file_in_workspace(ws, str(body["path"]), body["content"]))
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        msg = str(exc)
        status = 413 if ("too large" in msg or "quota" in msg or "exceeded" in msg) else 400
        return JSONResponse({"error": msg}, status_code=status)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
