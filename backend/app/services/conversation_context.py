from __future__ import annotations

from typing import Any

from app.db.client import db_read, row_to_message


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def extract_text_from_parts(parts: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for p in parts:
        ptype = p.get("type")
        if ptype == "text" and p.get("content"):
            chunks.append(str(p["content"]))
        elif ptype == "code" and p.get("content"):
            lang = p.get("language") or ""
            chunks.append(f"```{lang}\n{p['content']}\n```")
        elif ptype == "artifact_ref" and p.get("artifactId"):
            chunks.append(f"[Artifact: {p['artifactId']}]")
        elif ptype in ("image_attachment", "file_attachment"):
            chunks.append(f"[Attachment {p.get('attachmentId')}: {p.get('fileName')}]")
    return "\n\n".join(chunks).strip()


def build_history_for(
    *,
    conversation_id: str,
    current_agent_id: str,
    exclude_message_id: str | None = None,
    after_created_at: int | None = None,
    max_tokens: int = 24000,
) -> list[dict[str, Any]]:
    with db_read() as conn:
        rows = conn.execute(
            """SELECT m.* FROM messages m LEFT JOIN agent_runs r ON r.id = m.run_id
            WHERE m.conversation_id = ? AND r.parent_run_id IS NULL ORDER BY m.created_at ASC""",
            (conversation_id,),
        ).fetchall()
        agent_rows = conn.execute("SELECT id, name FROM agents").fetchall()
    agent_names = {r["id"]: r["name"] for r in agent_rows}

    messages = [row_to_message(r) for r in rows]
    chat_msgs: list[dict[str, Any]] = []
    for m in messages:
        if exclude_message_id and m["id"] == exclude_message_id:
            continue
        if after_created_at is not None and m["createdAt"] <= after_created_at:
            continue
        if m["status"] == "streaming":
            continue
        text = extract_text_from_parts(m["parts"])
        if not text:
            continue
        if m["role"] == "user":
            chat_msgs.append({"role": "user", "content": text})
        elif m["role"] == "agent":
            if m["agentId"] == current_agent_id:
                chat_msgs.append({"role": "assistant", "content": text})
            else:
                name = agent_names.get(m["agentId"] or "", m["agentId"] or "Agent")
                chat_msgs.append({"role": "user", "content": f"[{name}]: {text}"})

    # Trim from oldest to stay within token budget
    total = 0
    kept: list[dict[str, Any]] = []
    for item in reversed(chat_msgs):
        t = estimate_tokens(str(item.get("content") or ""))
        if total + t > max_tokens and kept:
            break
        kept.append(item)
        total += t
    kept.reverse()
    return kept


def get_pinned_messages_block(conversation_id: str) -> str:
    with db_read() as conn:
        conv_row = conn.execute(
            "SELECT pinned_message_ids FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not conv_row:
            return ""
        import json
        pinned_ids = json.loads(conv_row["pinned_message_ids"] or "[]")
        if not pinned_ids:
            return ""
        placeholders = ",".join("?" for _ in pinned_ids)
        rows = conn.execute(
            f"""SELECT m.* FROM messages m LEFT JOIN agent_runs r ON r.id = m.run_id
            WHERE m.id IN ({placeholders}) AND r.parent_run_id IS NULL ORDER BY m.created_at ASC""",
            pinned_ids,
        ).fetchall()
    if not rows:
        return ""
    lines = ["<pinned_messages>"]
    for r in rows:
        m = row_to_message(r)
        text = extract_text_from_parts(m["parts"])
        if text:
            lines.append(f"- [{m['role']}] {text}")
    lines.append("</pinned_messages>")
    return "\n".join(lines) if len(lines) > 2 else ""
