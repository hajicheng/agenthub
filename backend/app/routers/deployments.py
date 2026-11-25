from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from app.services.deployment_service import build_deployment_zip, read_deployment_asset

router = APIRouter(tags=["deployments"])


@router.get("/api/deployments/{dep_id}/download/{kind}")
async def download_deployment(dep_id: str, kind: str) -> Response:
    if kind not in ("source", "container"):
        return JSONResponse({"error": "Invalid download kind"}, status_code=400)
    res = build_deployment_zip(dep_id, kind)
    if not res:
        return JSONResponse({"error": "Deployment not found"}, status_code=404)
    data, file_name = res
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{quote(file_name)}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/deployments/{dep_id}")
@router.get("/deployments/{dep_id}/{rel_path:path}")
async def serve_deployment(dep_id: str, rel_path: str = "") -> Response:
    body, err, headers, status = read_deployment_asset(dep_id, rel_path)
    if body is None:
        return JSONResponse({"error": err}, status_code=status)
    return Response(content=body, headers=headers)
