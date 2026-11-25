from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.config import get_deployments_root
from app.services.artifact_service import build_web_app_html
from app.services.workspace_utils import is_path_within

DEPLOYMENT_ID_RE = re.compile(r"^dep_[0-9A-Za-z]+$")
PRIVATE_DIR = ".agenthub"
MANIFEST_REL = f"{PRIVATE_DIR}/manifest.json"
SOURCE_ROOT_REL = f"{PRIVATE_DIR}/source"
RUNTIME_ENTRY = "index.html"
DEPLOYMENT_SUMMARY_INSTRUCTION = (
    "User-facing summaries must not invent a hostname or public URL for this deployment. "
    "The previewPath is a local relative path for the current AgentHub instance; "
    "tell the user to use the deployment card buttons, or quote previewPath exactly."
)


def _assert_deployment_id(dep_id: str) -> None:
    if not DEPLOYMENT_ID_RE.match(dep_id):
        raise ValueError(f"Invalid deployment id: {dep_id}")


def _deployment_dir(dep_id: str) -> Path:
    _assert_deployment_id(dep_id)
    return get_deployments_root() / dep_id


def deployment_preview_path(dep_id: str) -> str:
    return f"/deployments/{quote(dep_id, safe='')}"


def deployment_download_path(dep_id: str, kind: str) -> str:
    return f"/api/deployments/{quote(dep_id, safe='')}/download/{kind}"


def create_local_static_deployment(
    *,
    dep_id: str,
    artifact_id: str,
    title: str,
    version: int,
    content: dict[str, Any],
) -> dict[str, Any]:
    dep_dir = _deployment_dir(dep_id)
    if dep_dir.exists():
        raise ValueError(f"Deployment already exists: {dep_id}")

    now = int(time.time() * 1000)
    files: dict[str, str] = content.get("files") or {}
    source_entry = content.get("entry") or "index.html"
    if source_entry not in files and "index.html" in files:
        source_entry = "index.html"
    elif source_entry not in files and files:
        source_entry = next(iter(files.keys()))

    manifest = {
        "id": dep_id,
        "artifactId": artifact_id,
        "title": title,
        "version": version,
        "deploymentType": "local_static",
        "createdAt": now,
        "sourceEntry": source_entry,
        "runtimeEntry": RUNTIME_ENTRY,
        "sourceFiles": sorted(files.keys()),
        "sourceType": "artifact",
    }

    try:
        dep_dir.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            rel = name.lstrip("/\\").replace("\\", "/")
            for base in (dep_dir / SOURCE_ROOT_REL, dep_dir):
                target = (base / rel).resolve()
                if not is_path_within(str(target), str(dep_dir)):
                    raise PermissionError(f"Invalid deployment file path: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")

        runtime_html = build_web_app_html({"type": "web_app", "files": files, "entry": source_entry})
        (dep_dir / RUNTIME_ENTRY).write_text(runtime_html, encoding="utf-8")
        manifest_path = dep_dir / MANIFEST_REL
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        shutil.rmtree(dep_dir, ignore_errors=True)
        raise

    return {
        "id": dep_id,
        "artifactId": artifact_id,
        "title": title,
        "version": version,
        "previewPath": deployment_preview_path(dep_id),
        "deploymentType": "local_static",
        "deploymentPath": deployment_preview_path(dep_id),
        "sourceDownloadPath": deployment_download_path(dep_id, "source"),
        "containerDownloadPath": deployment_download_path(dep_id, "container"),
        "summaryInstruction": DEPLOYMENT_SUMMARY_INSTRUCTION,
        "status": "ready",
        "createdAt": now,
        "sourceType": "artifact",
    }


def create_workspace_static_deployment(
    *,
    dep_id: str,
    title: str,
    source_dir: str,
    workspace_path: str,
    entry: str | None = None,
) -> dict[str, Any]:
    if not os.path.isdir(source_dir):
        raise ValueError(f"Workspace deployment source is not a directory: {workspace_path}")
    dep_dir = _deployment_dir(dep_id)
    if dep_dir.exists():
        raise ValueError(f"Deployment already exists: {dep_id}")

    now = int(time.time() * 1000)
    source_files: list[str] = []
    for root, _, filenames in os.walk(source_dir):
        for fn in filenames:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, source_dir).replace("\\", "/")
            source_files.append(rel)
    source_files.sort()
    source_entry = entry if entry and entry in source_files else ("index.html" if "index.html" in source_files else (source_files[0] if source_files else "index.html"))

    manifest = {
        "id": dep_id,
        "artifactId": f"workspace:{workspace_path}",
        "title": title,
        "version": 0,
        "deploymentType": "local_static",
        "createdAt": now,
        "sourceEntry": source_entry,
        "runtimeEntry": RUNTIME_ENTRY,
        "sourceFiles": source_files,
        "sourceType": "workspace",
        "workspacePath": workspace_path,
    }

    try:
        dep_dir.mkdir(parents=True, exist_ok=True)
        for rel in source_files:
            src = os.path.join(source_dir, *rel.split("/"))
            data = Path(src).read_bytes()
            for base in (dep_dir / SOURCE_ROOT_REL, dep_dir):
                target = (base / rel).resolve()
                if not is_path_within(str(target), str(dep_dir)):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        if source_entry != RUNTIME_ENTRY and source_entry in source_files:
            (dep_dir / RUNTIME_ENTRY).write_bytes(Path(os.path.join(source_dir, *source_entry.split("/"))).read_bytes())
        manifest_path = dep_dir / MANIFEST_REL
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        shutil.rmtree(dep_dir, ignore_errors=True)
        raise

    return {
        "id": dep_id,
        "artifactId": manifest["artifactId"],
        "title": title,
        "version": 0,
        "previewPath": deployment_preview_path(dep_id),
        "deploymentType": "local_static",
        "deploymentPath": deployment_preview_path(dep_id),
        "sourceDownloadPath": deployment_download_path(dep_id, "source"),
        "containerDownloadPath": deployment_download_path(dep_id, "container"),
        "summaryInstruction": DEPLOYMENT_SUMMARY_INSTRUCTION,
        "status": "ready",
        "createdAt": now,
        "sourceType": "workspace",
        "workspacePath": workspace_path,
    }


def read_deployment_asset(dep_id: str, rel_path: str = "") -> tuple[bytes | None, str, dict[str, str], int]:
    try:
        dep_dir = _deployment_dir(dep_id)
    except ValueError as exc:
        return None, str(exc), {}, 400
    if not dep_dir.is_dir():
        return None, "Deployment not found", {}, 404

    cleaned = rel_path.strip("/") or RUNTIME_ENTRY
    if cleaned.startswith(PRIVATE_DIR):
        return None, "Not found", {}, 404
    target = (dep_dir / cleaned).resolve()
    if not is_path_within(str(target), str(dep_dir)):
        return None, "Invalid path", {}, 400
    if target.is_dir():
        target = target / RUNTIME_ENTRY
    if not target.is_file():
        return None, "Asset not found", {}, 404

    mime, _ = mimetypes.guess_type(str(target))
    content_type = mime or "application/octet-stream"
    headers = {
        "Content-Type": f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return target.read_bytes(), "", headers, 200


def build_deployment_zip(dep_id: str, kind: str) -> tuple[bytes, str] | None:
    if kind not in ("source", "container"):
        return None
    try:
        dep_dir = _deployment_dir(dep_id)
    except ValueError:
        return None
    if not dep_dir.is_dir():
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        src_root = dep_dir / SOURCE_ROOT_REL
        walk_root = src_root if src_root.is_dir() else dep_dir
        for root, dirs, files in os.walk(walk_root):
            dirs[:] = [d for d in dirs if d != PRIVATE_DIR]
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, walk_root).replace("\\", "/")
                arc = f"site/{rel}" if kind == "container" else rel
                zf.write(full, arcname=arc)
        if kind == "container":
            zf.writestr(
                "Dockerfile",
                "FROM nginx:alpine\nCOPY site/ /usr/share/nginx/html/\nEXPOSE 80\n",
            )
    return buf.getvalue(), f"{dep_id}-{kind}.zip"
