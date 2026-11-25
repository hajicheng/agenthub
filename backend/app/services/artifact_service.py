from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from typing import Any

from app.db.client import db_read, db_tx, row_to_artifact
from app.ids import new_artifact_id
from app.services.artifact_content import build_artifact_content, describe_artifact_content_error
from app.services.workspace_utils import is_path_within


def list_artifacts() -> list[dict[str, Any]]:
    with db_read() as conn:
        rows = conn.execute(
            """SELECT a.*, c.title AS conversation_title
               FROM artifacts a
               LEFT JOIN conversations c ON c.id = a.conversation_id
               ORDER BY a.created_at DESC"""
        ).fetchall()
    return [
        {
            "id": r["id"],
            "conversationId": r["conversation_id"],
            "conversationTitle": r["conversation_title"],
            "type": r["type"],
            "title": r["title"],
            "version": r["version"],
            "parentArtifactId": r["parent_artifact_id"],
            "createdByAgentId": r["created_by_agent_id"],
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    with db_read() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return row_to_artifact(row) if row else None


def delete_artifact(artifact_id: str) -> None:
    with db_tx() as conn:
        cur = conn.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
        if cur.rowcount == 0:
            raise ValueError(f"Artifact not found: {artifact_id}")


def get_artifact_versions(artifact_id: str) -> list[dict[str, Any]] | None:
    root = get_artifact(artifact_id)
    if not root:
        return None
    climbed = {root["id"]}
    while root.get("parentArtifactId") and root["parentArtifactId"] not in climbed:
        pid = root["parentArtifactId"]
        climbed.add(pid)
        parent = get_artifact(pid)
        if not parent:
            break
        root = parent

    collected = [root]
    visited = {root["id"]}
    queue = [root["id"]]
    with db_read() as conn:
        while queue:
            pid = queue.pop(0)
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE parent_artifact_id = ?", (pid,)
            ).fetchall()
            for r in rows:
                art = row_to_artifact(r)
                if art["id"] in visited:
                    continue
                visited.add(art["id"])
                collected.append(art)
                queue.append(art["id"])

    collected.sort(key=lambda a: a["version"])
    return collected


def create_artifact(
    *,
    conversation_id: str,
    art_type: str,
    title: str,
    content: dict[str, Any],
    created_by_agent_id: str,
    parent_artifact_id: str | None = None,
    version: int = 1,
) -> dict[str, Any]:
    art = {
        "id": new_artifact_id(),
        "conversationId": conversation_id,
        "type": art_type,
        "title": title,
        "content": content,
        "version": version,
        "parentArtifactId": parent_artifact_id,
        "createdByAgentId": created_by_agent_id,
        "createdAt": int(time.time() * 1000),
    }
    with db_tx() as conn:
        conn.execute(
            """INSERT INTO artifacts (
                id, conversation_id, type, title, content, version,
                parent_artifact_id, created_by_agent_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                art["id"],
                art["conversationId"],
                art["type"],
                art["title"],
                json.dumps(art["content"], ensure_ascii=False),
                art["version"],
                art["parentArtifactId"],
                art["createdByAgentId"],
                art["createdAt"],
            ),
        )
    return art


def create_artifact_version(
    parent_artifact_id: str, raw_content: Any, title: str | None = None
) -> tuple[dict[str, Any] | None, str | None, int]:
    parent = get_artifact(parent_artifact_id)
    if not parent:
        return None, f"Artifact not found: {parent_artifact_id}", 404
    content = build_artifact_content(parent["type"], raw_content)
    if not content:
        err = describe_artifact_content_error(parent["type"], raw_content)
        return None, err or f"Invalid content for type {parent['type']}", 400
    art = create_artifact(
        conversation_id=parent["conversationId"],
        art_type=parent["type"],
        title=(title or "").strip() or parent["title"],
        content=content,
        created_by_agent_id=parent["createdByAgentId"],
        parent_artifact_id=parent["id"],
        version=parent["version"] + 1,
    )
    return art, None, 200


def build_web_app_html(content: dict[str, Any]) -> str:
    files: dict[str, str] = content.get("files") or {}
    entry = content.get("entry") or "index.html"
    html = files.get(entry) or files.get("index.html") or next(iter(files.values()), "")
    if not html:
        return "<!doctype html><html><body></body></html>"

    # Inline linked CSS/JS files from the web_app bundle
    for name, body in files.items():
        if name == entry:
            continue
        escaped = re.escape(name)
        if name.endswith(".css"):
            pattern = re.compile(rf'<link[^>]+href=["\'](?:\./)?{escaped}["\'][^>]*/?>', re.I)
            if pattern.search(html):
                html = pattern.sub(lambda _: f"<style>\n{body}\n</style>", html)
            elif "</head>" in html:
                html = html.replace("</head>", f"<style>\n{body}\n</style>\n</head>", 1)
        elif name.endswith(".js"):
            pattern = re.compile(
                rf'<script[^>]+src=["\'](?:\./)?{escaped}["\'][^>]*>\s*</script>', re.I
            )
            if pattern.search(html):
                html = pattern.sub(lambda _: f"<script>\n{body}\n</script>", html)
            elif "</body>" in html:
                html = html.replace("</body>", f"<script>\n{body}\n</script>\n</body>", 1)
    return html


def zip_project_from_workspace(cwd: str, files: list[dict[str, Any]], title: str, exported_at: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            rel = str(f.get("path") or "").lstrip("/\\")
            if not rel:
                continue
            abs_p = os.path.abspath(os.path.join(cwd, rel))
            if not is_path_within(abs_p, cwd) or not os.path.isfile(abs_p):
                continue
            zf.write(abs_p, arcname=rel.replace("\\", "/"))
        zf.writestr("README.txt", f"Project: {title}\nExported: {exported_at}\n")
    return buf.getvalue()
