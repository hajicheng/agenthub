from __future__ import annotations

import sqlite3
import time
from typing import Any

from app.db.client import db_read


def _maybe_quote(q: str) -> str:
    if q.endswith("*") or "(" in q:
        return q
    if "-" in q:
        escaped = q.replace('"', '""')
        return f'"{escaped}"'
    return q


def search_messages(
    *,
    query: str,
    limit: int = 20,
    offset: int = 0,
    conversation_id: str | None = None,
    role: str | None = None,
    fallback: str | None = None,
) -> dict[str, Any]:
    trimmed = query.strip()
    if not trimmed:
        return {"hits": [], "total": 0, "tookMs": 0}
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    if fallback == "like":
        return _run_like(trimmed, limit, offset, conversation_id, role)
    return _run_fts(trimmed, limit, offset, conversation_id, role)


def _run_fts(
    q: str, limit: int, offset: int, conversation_id: str | None, role: str | None
) -> dict[str, Any]:
    start = int(time.time() * 1000)
    fts_query = _maybe_quote(q)
    sql = """
        SELECT
            m.id AS messageId,
            m.conversation_id AS conversationId,
            m.role AS role,
            m.agent_id AS agentId,
            m.created_at AS createdAt,
            snippet(messages_fts, 0, '<mark>', '</mark>', '…', 12) AS snippetHtml,
            c.title AS conversationTitle,
            a.name AS agentName,
            a.avatar AS agentAvatar
        FROM messages_fts
        JOIN messages m ON m.rowid = messages_fts.rowid
        JOIN conversations c ON c.id = m.conversation_id
        LEFT JOIN agents a ON a.id = m.agent_id
        WHERE messages_fts MATCH ?
          AND (? IS NULL OR m.conversation_id = ?)
          AND (? IS NULL OR m.role = ?)
        ORDER BY bm25(messages_fts)
        LIMIT ? OFFSET ?
    """
    with db_read() as conn:
        try:
            rows = conn.execute(
                sql, (fts_query, conversation_id, conversation_id, role, role, limit, offset)
            ).fetchall()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "fts5" in msg or "syntax" in msg or "no such column" in msg:
                return {"hits": [], "total": 0, "tookMs": 0, "error": "INVALID_QUERY"}
            if "no such table" in msg:
                return _run_like(q, limit, offset, conversation_id, role)
            raise

        total = len(rows)
        if len(rows) == limit:
            count_row = conn.execute(
                """SELECT COUNT(*) AS n FROM messages_fts
                   JOIN messages m ON m.rowid = messages_fts.rowid
                   WHERE messages_fts MATCH ?
                     AND (? IS NULL OR m.conversation_id = ?)
                     AND (? IS NULL OR m.role = ?)""",
                (fts_query, conversation_id, conversation_id, role, role),
            ).fetchone()
            total = int(count_row["n"]) if count_row else len(rows)

    hits = [dict(r) for r in rows]
    return {"hits": hits, "total": total, "tookMs": int(time.time() * 1000) - start}


def _run_like(
    q: str, limit: int, offset: int, conversation_id: str | None, role: str | None
) -> dict[str, Any]:
    start = int(time.time() * 1000)
    sql = """
        SELECT
            m.id AS messageId,
            m.conversation_id AS conversationId,
            m.role AS role,
            m.agent_id AS agentId,
            m.created_at AS createdAt,
            substr(m.parts, max(1, instr(m.parts, ?) - 30), 80) AS snippetHtml,
            c.title AS conversationTitle,
            a.name AS agentName,
            a.avatar AS agentAvatar
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        LEFT JOIN agents a ON a.id = m.agent_id
        WHERE m.parts LIKE '%' || ? || '%'
          AND (? IS NULL OR m.conversation_id = ?)
          AND (? IS NULL OR m.role = ?)
        ORDER BY m.created_at DESC
        LIMIT ? OFFSET ?
    """
    with db_read() as conn:
        rows = conn.execute(
            sql, (q, q, conversation_id, conversation_id, role, role, limit, offset)
        ).fetchall()
    hits = [dict(r) for r in rows]
    return {"hits": hits, "total": len(hits), "tookMs": int(time.time() * 1000) - start}
