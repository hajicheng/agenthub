from __future__ import annotations

import os
import posixpath
import re
import time
from pathlib import Path
from typing import Any

from app.db.client import db_read, db_tx, row_to_attachment, row_to_workspace
from app.ids import new_attachment_id
from app.services.workspace_utils import is_path_within

MAX_FILE_SIZE = 20 * 1024 * 1024

_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
}


def _sanitize_ext(file_name: str) -> str:
    _, ext = os.path.splitext(file_name)
    ext = ext.lower()
    if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
        return ""
    return ext


def upload_attachment(
    *,
    conversation_id: str,
    file_name: str,
    content: bytes,
    content_type: str | None = None,
) -> dict[str, Any]:
    size = len(content)
    if size == 0:
        raise ValueError("Empty file")
    if size > MAX_FILE_SIZE:
        raise ValueError(f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)")

    with db_read() as conn:
        ws_row = conn.execute(
            "SELECT * FROM workspaces WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
    if not ws_row:
        raise ValueError(f"Workspace not found for conversation: {conversation_id}")
    workspace = row_to_workspace(ws_row)

    upload_dir = os.path.join(workspace["rootPath"], "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    att_id = new_attachment_id()
    ext = _sanitize_ext(file_name)
    stored_name = f"{att_id}{ext}"
    abs_path = os.path.abspath(os.path.join(upload_dir, stored_name))
    if not is_path_within(abs_path, workspace["rootPath"]):
        raise PermissionError("Path traversal detected")

    Path(abs_path).write_bytes(content)

    mime_type = content_type or _MIME_MAP.get(ext, "application/octet-stream")
    kind = "image" if mime_type.startswith("image/") else "file"
    now = int(time.time() * 1000)
    row = {
        "id": att_id,
        "conversationId": conversation_id,
        "kind": kind,
        "fileName": file_name,
        "filePath": posixpath.join("uploads", stored_name),
        "size": size,
        "mimeType": mime_type,
        "createdAt": now,
    }
    with db_tx() as conn:
        conn.execute(
            """INSERT INTO attachments (
                id, conversation_id, kind, file_name, file_path, size, mime_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["conversationId"],
                row["kind"],
                row["fileName"],
                row["filePath"],
                row["size"],
                row["mimeType"],
                row["createdAt"],
            ),
        )
    return row


def list_attachments(conversation_id: str) -> list[dict[str, Any]]:
    with db_read() as conn:
        rows = conn.execute(
            "SELECT * FROM attachments WHERE conversation_id = ? ORDER BY created_at DESC",
            (conversation_id,),
        ).fetchall()
        return [row_to_attachment(r) for r in rows]


def get_attachment(attachment_id: str) -> dict[str, Any] | None:
    with db_read() as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        return row_to_attachment(row) if row else None


def get_attachment_absolute_path(attachment_id: str) -> str | None:
    row = get_attachment(attachment_id)
    if not row:
        return None
    with db_read() as conn:
        ws_row = conn.execute(
            "SELECT * FROM workspaces WHERE conversation_id = ?", (row["conversationId"],)
        ).fetchone()
    if not ws_row:
        return None
    workspace = row_to_workspace(ws_row)
    abs_path = os.path.abspath(os.path.join(workspace["rootPath"], row["filePath"]))
    if not is_path_within(abs_path, workspace["rootPath"]):
        return None
    return abs_path if os.path.isfile(abs_path) else None


def delete_attachment(attachment_id: str) -> None:
    row = get_attachment(attachment_id)
    if not row:
        raise ValueError(f"Attachment not found: {attachment_id}")
    abs_path = get_attachment_absolute_path(attachment_id)
    with db_tx() as conn:
        cur = conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        if cur.rowcount == 0:
            raise ValueError(f"Failed to delete attachment: {attachment_id}")
    if abs_path and os.path.isfile(abs_path):
        try:
            os.remove(abs_path)
        except OSError:
            pass
