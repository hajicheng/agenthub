from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse, Response

from app.services.attachment_service import (
    delete_attachment,
    get_attachment,
    get_attachment_absolute_path,
    list_attachments,
    upload_attachment,
)

router = APIRouter(tags=["attachments"])


@router.get("/api/conversations/{conv_id}/attachments")
async def get_conv_attachments(conv_id: str) -> JSONResponse:
    return JSONResponse({"attachments": list_attachments(conv_id)})


@router.post("/api/conversations/{conv_id}/attachments")
async def post_conv_attachment(conv_id: str, file: UploadFile = File(...)) -> JSONResponse:
    data = await file.read()
    try:
        att = upload_attachment(
            conversation_id=conv_id,
            file_name=file.filename or "upload.bin",
            content=data,
            content_type=file.content_type,
        )
        return JSONResponse({"attachment": att}, status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/api/attachments/{att_id}")
async def download_attachment(att_id: str) -> Response:
    row = get_attachment(att_id)
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    abs_path = get_attachment_absolute_path(att_id)
    if not abs_path:
        return JSONResponse({"error": "File missing on disk"}, status_code=410)
    data = Path(abs_path).read_bytes()
    disp_type = "inline" if row["kind"] == "image" else "attachment"
    disposition = f"{disp_type}; filename*=UTF-8''{quote(row['fileName'])}"
    return Response(
        content=data,
        media_type=row["mimeType"],
        headers={
            "Content-Length": str(len(data)),
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/api/attachments/{att_id}")
async def remove_attachment(att_id: str) -> JSONResponse:
    try:
        delete_attachment(att_id)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
