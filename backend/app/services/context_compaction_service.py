from __future__ import annotations

import time
from typing import Any

from openai import AsyncOpenAI

from app.db.client import db_read, db_tx, row_to_message
from app.ids import new_context_summary_id
from app.services.conversation_context import estimate_tokens, extract_text_from_parts
from app.services.settings_service import get_effective_api_key


def get_latest_context_summary(conversation_id: str) -> dict[str, Any] | None:
    with db_read() as conn:
        row = conn.execute(
            """SELECT * FROM conversation_context_summaries
               WHERE conversation_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (conversation_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "summary": row["summary"],
        "coveredUntilMessageId": row["covered_until_message_id"],
        "coveredUntilCreatedAt": row["covered_until_created_at"],
        "sourceMessageCount": row["source_message_count"],
        "tokenEstimate": row["token_estimate"],
        "modelProvider": row["model_provider"],
        "modelId": row["model_id"],
        "createdAt": row["created_at"],
    }


def render_conversation_summary_block(summary: dict[str, Any] | None) -> str:
    if not summary or not summary.get("summary"):
        return ""
    return f"<conversation_summary>\n{summary['summary']}\n</conversation_summary>"


async def compact_conversation(conversation_id: str) -> dict[str, Any]:
    with db_read() as conn:
        conv = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not conv:
            raise ValueError(f"Conversation not found: {conversation_id}")
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()

    messages = [row_to_message(r) for r in rows if r["status"] != "streaming"]
    if len(messages) < 2:
        raise ValueError("Not enough messages to compact")

    transcript_lines: list[str] = []
    for m in messages:
        txt = extract_text_from_parts(m["parts"])
        if txt:
            transcript_lines.append(f"{m['role'].upper()}: {txt[:2000]}")
    transcript = "\n\n".join(transcript_lines)

    summary_text = ""
    used_provider: str | None = None
    used_model: str | None = None

    api_key = get_effective_api_key("deepseek")
    base_url: str | None = "https://api.deepseek.com/v1"
    model_id = "deepseek-chat"
    provider = "deepseek"
    if not api_key:
        api_key = get_effective_api_key("openai")
        base_url = None
        model_id = "gpt-4o-mini"
        provider = "openai"

    if api_key:
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            resp = await client.chat.completions.create(
                model=model_id,
                messages=[
                    {
                        "role": "system",
                        "content": "Summarize the following conversation concisely, preserving key decisions, goals, file paths, artifact IDs, and open questions.",
                    },
                    {"role": "user", "content": transcript[:24000]},
                ],
                temperature=0.2,
            )
            summary_text = (resp.choices[0].message.content or "").strip()
            used_provider = provider
            used_model = model_id
        except Exception:
            pass

    if not summary_text:
        summary_text = "对话摘要（自动截断）：\n" + "\n".join(transcript_lines[-10:])[:4000]

    last_msg = messages[-1]
    now = int(time.time() * 1000)
    record = {
        "id": new_context_summary_id(),
        "conversationId": conversation_id,
        "summary": summary_text,
        "coveredUntilMessageId": last_msg["id"],
        "coveredUntilCreatedAt": last_msg["createdAt"],
        "sourceMessageCount": len(messages),
        "tokenEstimate": estimate_tokens(summary_text),
        "modelProvider": used_provider,
        "modelId": used_model,
        "createdAt": now,
    }

    with db_tx() as conn:
        conn.execute(
            """INSERT INTO conversation_context_summaries (
                id, conversation_id, summary, covered_until_message_id,
                covered_until_created_at, source_message_count, token_estimate,
                model_provider, model_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["id"],
                record["conversationId"],
                record["summary"],
                record["coveredUntilMessageId"],
                record["coveredUntilCreatedAt"],
                record["sourceMessageCount"],
                record["tokenEstimate"],
                record["modelProvider"],
                record["modelId"],
                record["createdAt"],
            ),
        )
    return {"summary": record}
