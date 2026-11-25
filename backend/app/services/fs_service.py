from __future__ import annotations

import os
import posixpath
from pathlib import Path
from typing import Any

from app.db.client import db_read, row_to_workspace
from app.services.workspace_utils import assert_path_within_workspace, get_effective_cwd

MAX_READ_BYTES = 1_048_576
MAX_READ_CHARS = 50_000
MAX_WRITE_BYTES = 100 * 1024
SANDBOX_TOTAL_BYTES = 100 * 1024 * 1024
SANDBOX_TOTAL_FILES = 1000


def get_workspace_for_conversation(conversation_id: str) -> dict[str, Any] | None:
    with db_read() as conn:
        row = conn.execute(
            "SELECT * FROM workspaces WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        return row_to_workspace(row) if row else None


def read_if_exists(workspace: dict[str, Any], target: str) -> str | None:
    try:
        abs_path = assert_path_within_workspace(workspace, target)
        if not os.path.isfile(abs_path):
            return None
        if os.path.getsize(abs_path) > MAX_READ_BYTES:
            return None
        return Path(abs_path).read_text(encoding="utf-8")
    except Exception:
        return None


def read_file_in_workspace(workspace: dict[str, Any], target: str) -> dict[str, Any]:
    abs_path = assert_path_within_workspace(workspace, target)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {target}")
    if not os.path.isfile(abs_path):
        raise ValueError(f"Not a file: {target}")
    size = os.path.getsize(abs_path)
    if size > MAX_READ_BYTES:
        raise ValueError(f"File too large ({size / 1024 / 1024:.2f} MB > 1 MB limit)")
    raw = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    truncated = len(raw) > MAX_READ_CHARS
    content = (
        raw[:MAX_READ_CHARS] + f"\n\n[TRUNCATED at {MAX_READ_CHARS} chars]"
        if truncated
        else raw
    )
    return {
        "path": target,
        "absolutePath": abs_path,
        "cwd": get_effective_cwd(workspace),
        "size": size,
        "content": content,
        "truncated": truncated,
    }


def _scan_workspace_usage(root_path: str) -> tuple[int, int]:
    total_bytes = 0
    total_files = 0
    if not os.path.isdir(root_path):
        return 0, 0
    visited: set[str] = set()
    stack = [root_path]
    while stack:
        current = stack.pop()
        try:
            real = os.path.realpath(current)
        except OSError:
            continue
        if real in visited:
            continue
        visited.add(real)
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            total_bytes += entry.stat(follow_symlinks=False).st_size
                            total_files += 1
                        except OSError:
                            pass
        except OSError:
            continue
    return total_bytes, total_files


def write_file_in_workspace(workspace: dict[str, Any], target: str, content: str) -> dict[str, Any]:
    data = content.encode("utf-8")
    size = len(data)
    if size > MAX_WRITE_BYTES:
        raise ValueError(f"Content too large ({size / 1024:.1f} KB > 100 KB limit)")
    abs_path = assert_path_within_workspace(workspace, target)

    if workspace.get("mode") == "sandbox":
        used_bytes, used_files = _scan_workspace_usage(workspace["rootPath"])
        if used_bytes + size > SANDBOX_TOTAL_BYTES:
            raise ValueError(
                f"Workspace quota exceeded ({used_bytes / 1024 / 1024:.1f} MB used + {size / 1024:.1f} KB > 100 MB cap)"
            )
        if used_files + 1 > SANDBOX_TOTAL_FILES:
            raise ValueError(f"Workspace file count exceeded ({used_files} + 1 > 1000 cap)")

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    Path(abs_path).write_bytes(data)
    return {
        "path": target,
        "absolutePath": abs_path,
        "cwd": get_effective_cwd(workspace),
        "bytes": size,
    }


def list_dir_in_workspace(workspace: dict[str, Any], target: str = "") -> dict[str, Any]:
    rel_path = "" if target == "" else target
    abs_path = (
        get_effective_cwd(workspace)
        if rel_path == ""
        else assert_path_within_workspace(workspace, target)
    )
    if not os.path.isdir(abs_path):
        raise ValueError(f"Not a directory: {target or '(root)'}")

    entries: list[dict[str, Any]] = []
    with os.scandir(abs_path) as it:
        for entry in it:
            if entry.name.startswith("."):
                continue
            is_dir = entry.is_dir()
            item: dict[str, Any] = {"name": entry.name, "isDirectory": is_dir}
            if not is_dir:
                try:
                    item["size"] = entry.stat().st_size
                except OSError:
                    pass
            entries.append(item)

    entries.sort(key=lambda e: (not e["isDirectory"], e["name"].lower()))

    if rel_path == "":
        parent = None
    else:
        p = posixpath.dirname(rel_path.replace("\\", "/"))
        parent = "" if p in (".", "") else p

    return {
        "relPath": rel_path,
        "absolutePath": abs_path,
        "parent": parent,
        "entries": entries,
    }
