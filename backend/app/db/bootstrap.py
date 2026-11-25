from __future__ import annotations

import json
import sqlite3

from app.db.builtin_agents import get_builtin_agents

DDL: list[str] = [
    """CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        avatar TEXT NOT NULL,
        description TEXT NOT NULL,
        capabilities TEXT NOT NULL,
        system_prompt TEXT NOT NULL,
        adapter_name TEXT NOT NULL,
        model_provider TEXT,
        model_id TEXT,
        api_key TEXT,
        api_base_url TEXT,
        tool_names TEXT NOT NULL,
        is_builtin INTEGER NOT NULL DEFAULT 0,
        is_orchestrator INTEGER NOT NULL DEFAULT 0,
        supports_vision INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        mode TEXT NOT NULL,
        agent_ids TEXT NOT NULL,
        pinned_message_ids TEXT NOT NULL DEFAULT '[]',
        bookmarked_message_ids TEXT NOT NULL DEFAULT '[]',
        archived INTEGER NOT NULL DEFAULT 0,
        pinned_at INTEGER,
        fs_write_approval_mode TEXT NOT NULL DEFAULT 'review',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at)",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        agent_id TEXT REFERENCES agents(id),
        parts TEXT NOT NULL,
        status TEXT NOT NULL,
        parent_message_id TEXT,
        mentioned_agent_ids TEXT NOT NULL DEFAULT '[]',
        run_id TEXT,
        usage TEXT,
        created_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_messages_conv_created ON messages(conversation_id, created_at)",
    """CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        parent_artifact_id TEXT,
        created_by_agent_id TEXT NOT NULL REFERENCES agents(id),
        created_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_conv ON artifacts(conversation_id)",
    """CREATE TABLE IF NOT EXISTS workspaces (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
        root_path TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'sandbox',
        bound_path TEXT,
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS attachments (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        file_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        size INTEGER NOT NULL,
        mime_type TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_attachments_conv ON attachments(conversation_id)",
    """CREATE TABLE IF NOT EXISTS agent_runs (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        agent_id TEXT NOT NULL REFERENCES agents(id),
        trigger_message_id TEXT,
        status TEXT NOT NULL,
        error TEXT,
        parent_run_id TEXT,
        usage TEXT,
        started_at INTEGER NOT NULL,
        finished_at INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_runs_parent ON agent_runs(parent_run_id)",
    """CREATE TABLE IF NOT EXISTS conversation_context_summaries (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        summary TEXT NOT NULL,
        covered_until_message_id TEXT NOT NULL,
        covered_until_created_at INTEGER NOT NULL,
        source_message_count INTEGER NOT NULL,
        token_estimate INTEGER NOT NULL,
        model_provider TEXT,
        model_id TEXT,
        created_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_context_summaries_conv_created ON conversation_context_summaries(conversation_id, created_at)",
    """CREATE TABLE IF NOT EXISTS app_settings (
        id TEXT PRIMARY KEY,
        anthropic_api_key TEXT,
        anthropic_base_url TEXT,
        openai_api_key TEXT,
        deepseek_api_key TEXT,
        ark_api_key TEXT,
        companion_mode TEXT NOT NULL DEFAULT 'off',
        mobile_device_token TEXT,
        deployment_publish_enabled INTEGER NOT NULL DEFAULT 0,
        deployment_publish_dir TEXT,
        deployment_public_base_url TEXT,
        updated_at INTEGER NOT NULL
    )""",
]

FTS_STATEMENTS: list[str] = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, tokenize='trigram')",
    """CREATE TRIGGER IF NOT EXISTS messages_fts_ai
       AFTER INSERT ON messages
       WHEN new.status != 'streaming'
       BEGIN
         INSERT INTO messages_fts(rowid, content)
         SELECT new.rowid, (
           SELECT GROUP_CONCAT(json_extract(value, '$.content'), ' ')
           FROM json_each(new.parts)
           WHERE json_extract(value, '$.type') = 'text'
         );
       END""",
    """CREATE TRIGGER IF NOT EXISTS messages_fts_au
       AFTER UPDATE ON messages
       WHEN new.status != 'streaming'
       BEGIN
         DELETE FROM messages_fts WHERE rowid = old.rowid;
         INSERT INTO messages_fts(rowid, content)
         SELECT new.rowid, (
           SELECT GROUP_CONCAT(json_extract(value, '$.content'), ' ')
           FROM json_each(new.parts)
           WHERE json_extract(value, '$.type') = 'text'
         );
       END""",
    """CREATE TRIGGER IF NOT EXISTS messages_fts_ad
       AFTER DELETE ON messages
       BEGIN
         DELETE FROM messages_fts WHERE rowid = old.rowid;
       END""",
    """INSERT OR IGNORE INTO messages_fts(rowid, content)
       SELECT m.rowid, (
         SELECT GROUP_CONCAT(json_extract(value, '$.content'), ' ')
         FROM json_each(m.parts)
         WHERE json_extract(value, '$.type') = 'text'
       )
       FROM messages m
       WHERE m.status != 'streaming'""",
]


def _safe_alter(conn: sqlite3.Connection, stmt: str) -> None:
    try:
        conn.execute(stmt)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


def bootstrap_database(conn: sqlite3.Connection) -> None:
    for stmt in DDL:
        conn.execute(stmt)

    _safe_alter(conn, "ALTER TABLE app_settings ADD COLUMN companion_mode TEXT NOT NULL DEFAULT 'off'")
    _safe_alter(conn, "ALTER TABLE app_settings ADD COLUMN mobile_device_token TEXT")
    _safe_alter(conn, "ALTER TABLE app_settings ADD COLUMN deployment_publish_enabled INTEGER NOT NULL DEFAULT 0")
    _safe_alter(conn, "ALTER TABLE app_settings ADD COLUMN deployment_publish_dir TEXT")
    _safe_alter(conn, "ALTER TABLE app_settings ADD COLUMN deployment_public_base_url TEXT")

    for stmt in FTS_STATEMENTS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            # FTS5 may be unavailable in some custom SQLite builds; LIKE fallback still works
            pass

    existing = conn.execute("SELECT 1 FROM agents WHERE is_builtin = 1 LIMIT 1").fetchone()
    if not existing:
        builtins = get_builtin_agents()
        conn.executemany(
            """INSERT INTO agents (
                id, name, avatar, description, capabilities, system_prompt,
                adapter_name, model_provider, model_id, api_key, api_base_url,
                tool_names, is_builtin, is_orchestrator, supports_vision, created_at
            ) VALUES (
                :id, :name, :avatar, :description, :capabilities, :system_prompt,
                :adapter_name, :model_provider, :model_id, :api_key, :api_base_url,
                :tool_names, :is_builtin, :is_orchestrator, :supports_vision, :created_at
            )""",
            [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "avatar": a["avatar"],
                    "description": a["description"],
                    "capabilities": json.dumps(a["capabilities"], ensure_ascii=False),
                    "system_prompt": a["systemPrompt"],
                    "adapter_name": a["adapterName"],
                    "model_provider": a["modelProvider"],
                    "model_id": a["modelId"],
                    "api_key": a["apiKey"],
                    "api_base_url": a["apiBaseUrl"],
                    "tool_names": json.dumps(a["toolNames"], ensure_ascii=False),
                    "is_builtin": 1 if a["isBuiltin"] else 0,
                    "is_orchestrator": 1 if a["isOrchestrator"] else 0,
                    "supports_vision": 1 if a["supportsVision"] else 0,
                    "created_at": a["createdAt"],
                }
                for a in builtins
            ],
        )
    conn.commit()
