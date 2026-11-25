from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import get_data_dir, get_db_path, get_workspaces_root
from app.db.bootstrap import bootstrap_database

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
logger = logging.getLogger(__name__)


def _recover_sandbox_paths(conn: sqlite3.Connection, workspaces_root: Path) -> None:
    rows = conn.execute("SELECT conversation_id, root_path FROM workspaces WHERE mode = 'sandbox'").fetchall()
    with conn:
        for row in rows:
            try:
                if Path(row["root_path"]).is_dir():
                    continue
                current = workspaces_root / row["conversation_id"]
                if current.parent != workspaces_root or current.resolve() != current:
                    continue
                current.mkdir(exist_ok=True)
                conn.execute(
                    "UPDATE workspaces SET root_path = ? WHERE conversation_id = ?",
                    (str(current), row["conversation_id"]),
                )
            except OSError as exc:
                logger.warning("Sandbox directory recovery failed for %s (%s)", row["conversation_id"], type(exc).__name__)


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None and db_path is None:
            return _conn
        if db_path is None:
            data_dir = get_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            get_workspaces_root().mkdir(parents=True, exist_ok=True)
            target = str(get_db_path())
        else:
            target = db_path

        conn = sqlite3.connect(target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        bootstrap_database(conn)
        if db_path is None:
            _recover_sandbox_paths(conn, get_workspaces_root().resolve())
        _conn = conn
        return conn


def get_conn() -> sqlite3.Connection:
    if _conn is None:
        return init_db()
    return _conn


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


@contextmanager
def db_tx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


@contextmanager
def db_read() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    with _lock:
        yield conn


def parse_json(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def row_to_agent(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "avatar": row["avatar"],
        "description": row["description"],
        "capabilities": parse_json(row["capabilities"], []),
        "systemPrompt": row["system_prompt"],
        "adapterName": row["adapter_name"],
        "modelProvider": row["model_provider"],
        "modelId": row["model_id"],
        "apiKey": row["api_key"],
        "apiBaseUrl": row["api_base_url"],
        "toolNames": parse_json(row["tool_names"], []),
        "isBuiltin": bool(row["is_builtin"]),
        "isOrchestrator": bool(row["is_orchestrator"]),
        "supportsVision": bool(row["supports_vision"]),
        "createdAt": row["created_at"],
    }


def row_to_conversation(row: sqlite3.Row, ws_mode: str = "sandbox", ws_bound_path: str | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "mode": row["mode"],
        "agentIds": parse_json(row["agent_ids"], []),
        "pinnedMessageIds": parse_json(row["pinned_message_ids"], []),
        "bookmarkedMessageIds": parse_json(row["bookmarked_message_ids"], []),
        "archived": bool(row["archived"]),
        "pinnedAt": row["pinned_at"],
        "fsWriteApprovalMode": row["fs_write_approval_mode"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "workspaceMode": ws_mode,
        "workspaceBoundPath": ws_bound_path,
    }


def row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "role": row["role"],
        "agentId": row["agent_id"],
        "parts": parse_json(row["parts"], []),
        "status": row["status"],
        "parentMessageId": row["parent_message_id"],
        "mentionedAgentIds": parse_json(row["mentioned_agent_ids"], []),
        "runId": row["run_id"],
        "parentRunId": row["parent_run_id"] if "parent_run_id" in row.keys() else None,
        "usage": parse_json(row["usage"], None),
        "createdAt": row["created_at"],
    }


def row_to_artifact(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "type": row["type"],
        "title": row["title"],
        "content": parse_json(row["content"], {}),
        "version": row["version"],
        "parentArtifactId": row["parent_artifact_id"],
        "createdByAgentId": row["created_by_agent_id"],
        "createdAt": row["created_at"],
    }


def row_to_workspace(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "rootPath": row["root_path"],
        "mode": row["mode"],
        "boundPath": row["bound_path"],
        "createdAt": row["created_at"],
    }


def row_to_attachment(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "kind": row["kind"],
        "fileName": row["file_name"],
        "filePath": row["file_path"],
        "size": row["size"],
        "mimeType": row["mime_type"],
        "createdAt": row["created_at"],
    }


def row_to_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "agentId": row["agent_id"],
        "triggerMessageId": row["trigger_message_id"],
        "status": row["status"],
        "error": row["error"],
        "parentRunId": row["parent_run_id"],
        "usage": parse_json(row["usage"], None),
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
    }


def row_to_settings(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "anthropicApiKey": row["anthropic_api_key"],
        "anthropicBaseUrl": row["anthropic_base_url"],
        "openaiApiKey": row["openai_api_key"],
        "deepseekApiKey": row["deepseek_api_key"],
        "arkApiKey": row["ark_api_key"],
        "companionMode": row["companion_mode"],
        "mobileDeviceToken": row["mobile_device_token"],
        "deploymentPublishEnabled": bool(row["deployment_publish_enabled"]),
        "deploymentPublishDir": row["deployment_publish_dir"],
        "deploymentPublicBaseUrl": row["deployment_public_base_url"],
        "updatedAt": row["updated_at"],
    }
