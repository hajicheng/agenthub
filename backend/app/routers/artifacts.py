from __future__ import annotations

import datetime
import io
import json
import re
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.services.artifact_service import (
    build_web_app_html,
    create_artifact_version,
    delete_artifact,
    get_artifact,
    get_artifact_versions,
    list_artifacts,
    zip_project_from_workspace,
)
from app.services.fs_service import get_workspace_for_conversation
from app.services.ppt_export import slides_to_pptx_bytes
from app.services.workspace_utils import get_effective_cwd

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


def _sanitize_name(name: str) -> str:
    return re.sub(r"\s+", "_", re.sub(r'[\\/:*?"<>|]', "_", name))[:60].strip() or "artifact"


@router.get("")
async def get_artifacts_list() -> JSONResponse:
    return JSONResponse({"artifacts": list_artifacts()})


@router.get("/{artifact_id}")
async def get_artifact_by_id(artifact_id: str) -> JSONResponse:
    art = get_artifact(artifact_id)
    if not art:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"artifact": art})


@router.delete("/{artifact_id}")
async def remove_artifact(artifact_id: str) -> JSONResponse:
    try:
        delete_artifact(artifact_id)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.get("/{artifact_id}/versions")
async def get_versions(artifact_id: str) -> JSONResponse:
    versions = get_artifact_versions(artifact_id)
    if versions is None:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    return JSONResponse({"versions": versions})


@router.post("/{artifact_id}/versions")
async def post_version(artifact_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict) or "content" not in body:
        return JSONResponse({"error": "Invalid body: content is required"}, status_code=400)
    art, err, status = create_artifact_version(artifact_id, body["content"], body.get("title"))
    if err:
        return JSONResponse({"error": err}, status_code=status)
    return JSONResponse({"artifact": art})


@router.get("/{artifact_id}/preview")
async def preview_artifact(artifact_id: str) -> Response:
    art = get_artifact(artifact_id)
    if not art:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    content = art["content"]
    if content.get("type") != "web_app":
        return JSONResponse({"error": "Artifact is not a web_app"}, status_code=400)
    html = build_web_app_html(content)
    csp = "; ".join(
        [
            "sandbox allow-scripts",
            "default-src 'none'",
            "script-src 'unsafe-inline'",
            "style-src 'unsafe-inline'",
            "img-src data: blob: http: https:",
            "font-src data:",
            "connect-src 'none'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'self'",
        ]
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


@router.get("/{artifact_id}/export")
async def export_artifact(artifact_id: str, mode: str = Query(default="editable")) -> Response:
    if mode not in ("editable", "visual"):
        return JSONResponse({"error": f"Unsupported export mode: {mode}"}, status_code=400)
    art = get_artifact(artifact_id)
    if not art:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)

    base_name = f"{_sanitize_name(art['title'])}-v{art['version']}"
    content = art["content"]
    ctype = content.get("type")
    iso_now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if ctype == "web_app":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, body in (content.get("files") or {}).items():
                zf.writestr(name, body)
            zf.writestr(
                "README.txt",
                f"Artifact: {art['title']}\nVersion: v{art['version']}\nEntry: {content.get('entry', 'index.html')}\nExported: {iso_now}\n",
            )
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{quote(base_name)}.zip"'},
        )

    if ctype == "document":
        return Response(
            content=str(content.get("content") or ""),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{quote(base_name)}.md"'},
        )

    if ctype == "image":
        return RedirectResponse(url=str(content.get("url") or ""), status_code=302)

    if ctype == "diagram":
        return Response(
            content=str(content.get("source") or ""),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{quote(base_name)}.mmd"'},
        )

    if ctype == "ppt":
        if mode == "visual":
            return JSONResponse(
                {"error": "Visual-priority PPTX export is not enabled yet. Use the default editable PPTX export instead."},
                status_code=501,
            )
        pptx_bytes = slides_to_pptx_bytes(content, art["title"])
        return Response(
            content=pptx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f'attachment; filename="{quote(base_name)}.pptx"'},
        )

    if ctype == "project":
        ws = get_workspace_for_conversation(art["conversationId"])
        if not ws:
            return JSONResponse({"error": "Workspace not found"}, status_code=404)
        zbytes = zip_project_from_workspace(get_effective_cwd(ws), content.get("files") or [], art["title"], iso_now)
        return Response(
            content=zbytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{quote(base_name)}.zip"'},
        )

    return Response(
        content=json.dumps(content, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{quote(base_name)}.json"'},
    )
