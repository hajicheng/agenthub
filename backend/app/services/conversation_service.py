from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from typing import Any

from app.config import PIN_LIMIT_PER_CONVERSATION, get_workspaces_root
from app.db.client import (
    db_read,
    db_tx,
    row_to_agent,
    row_to_conversation,
    row_to_message,
    row_to_workspace,
)
from app.ids import new_conversation_id, new_message_id, new_workspace_id
from app.services.deploy_command_service import handle_deploy_command, parse_deploy_command
from app.services.event_bus import event_bus
from app.services.pending_stores import pending_dispatch_plans
from app.services.platform import IS_WINDOWS
from app.services.workspace_utils import is_path_safe


def _rm_dir_with_retry(target: str) -> None:
    if not os.path.exists(target):
        return
    for attempt in range(3):
        try:
            shutil.rmtree(target, ignore_errors=False)
            return
        except OSError:
            if attempt == 2:
                shutil.rmtree(target, ignore_errors=True)
                return
            time.sleep(0.1 * (3**attempt))


def _with_workspace_meta(conn, conv_row) -> dict[str, Any]:
    ws = conn.execute(
        "SELECT mode, bound_path FROM workspaces WHERE conversation_id = ?", (conv_row["id"],)
    ).fetchone()
    return row_to_conversation(
        conv_row,
        ws_mode=ws["mode"] if ws else "sandbox",
        ws_bound_path=ws["bound_path"] if ws else None,
    )


def create_conversation(
    *,
    mode: str,
    agent_ids: list[str],
    title: str | None = None,
    bound_path: str | None = None,
) -> dict[str, Any]:
    if not agent_ids:
        raise ValueError("At least one agent is required")
    if mode == "single" and len(agent_ids) != 1:
        raise ValueError("Single conversation requires exactly one agent")
    if mode == "group" and len(set(agent_ids)) < 2:
        raise ValueError("Group conversation requires at least two agents")

    placeholders = ",".join("?" for _ in agent_ids)
    with db_read() as conn:
        agent_rows = conn.execute(
            f"SELECT * FROM agents WHERE id IN ({placeholders})", agent_ids
        ).fetchall()
    if len(agent_rows) != len(set(agent_ids)):
        found = {r["id"] for r in agent_rows}
        missing = [aid for aid in agent_ids if aid not in found]
        raise ValueError(f"Agents not found: {', '.join(missing)}")
    if mode == "group" and sum(bool(row["is_orchestrator"]) for row in agent_rows) != 1:
        raise ValueError("群聊需要选择一位主 Agent（Orchestrator）")

    workspace_mode = "sandbox"
    resolved_bound_path: str | None = None
    if bound_path and bound_path.strip():
        raw = bound_path.strip()
        if IS_WINDOWS and not re.match(r"^([A-Za-z]:[\\/]|\\\\)", raw):
            raise ValueError(
                f"boundPath must start with a drive letter (e.g. D:\\projects\\foo) on Windows: {raw}"
            )
        candidate = os.path.abspath(raw)
        if not os.path.isabs(candidate):
            raise ValueError("boundPath must be absolute")
        if not os.path.exists(candidate):
            raise ValueError(f"Path does not exist: {candidate}")
        if not os.path.isdir(candidate):
            raise ValueError(f"Not a directory: {candidate}")
        if not (os.access(candidate, os.R_OK) and os.access(candidate, os.W_OK)):
            raise ValueError(f"Not readable/writable: {candidate}")
        if not is_path_safe(candidate):
            raise ValueError(f"Path is not allowed (system / sensitive directory): {candidate}")
        workspace_mode = "local"
        resolved_bound_path = candidate

    now = int(time.time() * 1000)
    conv_id = new_conversation_id()
    ws_id = new_workspace_id()
    root_path = str((get_workspaces_root() / conv_id).resolve())
    os.makedirs(root_path, exist_ok=True)

    names = [r["name"] for r in agent_rows]
    final_title = title if title else (f"与 {names[0]} 的对话" if len(names) == 1 else " / ".join(names))

    with db_tx() as conn:
        conn.execute(
            """INSERT INTO conversations (
                id, title, mode, agent_ids, pinned_message_ids, bookmarked_message_ids,
                archived, pinned_at, fs_write_approval_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '[]', '[]', 0, NULL, 'review', ?, ?)""",
            (conv_id, final_title, mode, json.dumps(agent_ids), now, now),
        )
        conn.execute(
            """INSERT INTO workspaces (
                id, conversation_id, root_path, mode, bound_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (ws_id, conv_id, root_path, workspace_mode, resolved_bound_path, now),
        )

    return {
        "id": conv_id,
        "title": final_title,
        "mode": mode,
        "agentIds": agent_ids,
        "pinnedMessageIds": [],
        "bookmarkedMessageIds": [],
        "archived": False,
        "pinnedAt": None,
        "fsWriteApprovalMode": "review",
        "createdAt": now,
        "updatedAt": now,
        "workspaceMode": workspace_mode,
        "workspaceBoundPath": resolved_bound_path,
    }


def list_conversations() -> list[dict[str, Any]]:
    with db_read() as conn:
        rows = conn.execute(
            """SELECT c.*, w.mode AS ws_mode, w.bound_path AS ws_bound_path
               FROM conversations c
               LEFT JOIN workspaces w ON w.conversation_id = c.id
               ORDER BY (c.pinned_at IS NULL) ASC, c.pinned_at DESC, c.updated_at DESC"""
        ).fetchall()
    return [
        row_to_conversation(
            r,
            ws_mode=r["ws_mode"] or "sandbox",
            ws_bound_path=r["ws_bound_path"],
        )
        for r in rows
    ]


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    with db_read() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return _with_workspace_meta(conn, row) if row else None


def delete_conversation(conversation_id: str) -> None:
    with db_tx() as conn:
        ws = conn.execute(
            "SELECT root_path FROM workspaces WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if cur.rowcount == 0:
            raise ValueError(f"Conversation not found: {conversation_id}")
    if ws:
        _rm_dir_with_retry(ws["root_path"])


def rename_conversation(conversation_id: str, title: str) -> dict[str, Any]:
    trimmed = title.strip()
    if not trimmed:
        raise ValueError("Title cannot be empty")
    if len(trimmed) > 100:
        raise ValueError("Title too long (max 100)")
    now = int(time.time() * 1000)
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise ValueError(f"Conversation not found: {conversation_id}")
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (trimmed, now, conversation_id),
        )
        updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return _with_workspace_meta(conn, updated)


def add_agents_to_conversation(conversation_id: str, agent_ids: list[str]) -> dict[str, Any]:
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise ValueError(f"Conversation not found: {conversation_id}")
        placeholders = ",".join("?" for _ in agent_ids)
        found_rows = conn.execute(
            f"SELECT id FROM agents WHERE id IN ({placeholders})", agent_ids
        ).fetchall()
        found = {r["id"] for r in found_rows}
        missing = [aid for aid in agent_ids if aid not in found]
        if missing:
            raise ValueError(f"Agents not found: {', '.join(missing)}")
        existing = json.loads(row["agent_ids"] or "[]")
        merged = list(dict.fromkeys(existing + agent_ids))
        new_mode = "group" if len(merged) >= 2 else "single"
        if new_mode == "group":
            _resolve_responder({"mode": new_mode, "agentIds": merged})
        now = int(time.time() * 1000)
        conn.execute(
            "UPDATE conversations SET agent_ids = ?, mode = ?, updated_at = ? WHERE id = ?",
            (json.dumps(merged), new_mode, now, conversation_id),
        )
        updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return _with_workspace_meta(conn, updated)


def set_conversation_approval_mode(conversation_id: str, mode: str) -> dict[str, Any]:
    now = int(time.time() * 1000)
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise ValueError(f"Conversation not found: {conversation_id}")
        conn.execute(
            "UPDATE conversations SET fs_write_approval_mode = ?, updated_at = ? WHERE id = ?",
            (mode, now, conversation_id),
        )
        updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return _with_workspace_meta(conn, updated)


def toggle_pin_conversation(conversation_id: str) -> dict[str, Any]:
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise ValueError(f"Conversation not found: {conversation_id}")
        next_pinned = None if row["pinned_at"] else int(time.time() * 1000)
        conn.execute(
            "UPDATE conversations SET pinned_at = ? WHERE id = ?", (next_pinned, conversation_id)
        )
        updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return _with_workspace_meta(conn, updated)


def toggle_archive_conversation(conversation_id: str) -> dict[str, Any]:
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise ValueError(f"Conversation not found: {conversation_id}")
        next_archived = 0 if row["archived"] else 1
        conn.execute(
            "UPDATE conversations SET archived = ? WHERE id = ?", (next_archived, conversation_id)
        )
        updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return _with_workspace_meta(conn, updated)


def list_messages_snapshot(conversation_id: str) -> dict[str, Any]:
    event_id = event_bus.last_event_id()
    with db_read() as conn:
        rows = conn.execute(
            """SELECT m.*, r.parent_run_id FROM messages m
            LEFT JOIN agent_runs r ON r.id = m.run_id
            WHERE m.conversation_id = ? ORDER BY m.created_at ASC""",
            (conversation_id,),
        ).fetchall()
    return {"messages": [row_to_message(r) for r in rows], "eventId": event_id}


def list_messages(conversation_id: str) -> list[dict[str, Any]]:
    return list_messages_snapshot(conversation_id)["messages"]


def clear_conversation_history(conversation_id: str) -> dict[str, Any]:
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise ValueError(f"Conversation not found: {conversation_id}")
        active = conn.execute(
            "SELECT 1 FROM agent_runs WHERE conversation_id = ? AND status IN ('queued', 'running') LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if active:
            raise RuntimeError("Cannot clear conversation history while agent runs are active")

        msg_cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()["n"]
        run_cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM agent_runs WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()["n"]
        sum_cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM conversation_context_summaries WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["n"]

        now = int(time.time() * 1000)
        conn.execute("DELETE FROM conversation_context_summaries WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM agent_runs WHERE conversation_id = ?", (conversation_id,))
        conn.execute(
            "UPDATE conversations SET pinned_message_ids = '[]', bookmarked_message_ids = '[]', updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        conv = _with_workspace_meta(conn, updated)
    return {
        "conversation": conv,
        "deletedMessageCount": msg_cnt,
        "deletedRunCount": run_cnt,
        "deletedSummaryCount": sum_cnt,
    }


def toggle_bookmarked_message(conversation_id: str, message_id: str) -> dict[str, Any]:
    with db_tx() as conn:
        conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not conv:
            raise ValueError(f"Conversation not found: {conversation_id}")
        msg = conn.execute(
            "SELECT 1 FROM messages WHERE id = ? AND conversation_id = ?", (message_id, conversation_id)
        ).fetchone()
        if not msg:
            raise ValueError(f"Message not found in conversation: {message_id}")
        current = json.loads(conv["bookmarked_message_ids"] or "[]")
        is_bm = message_id in current
        nxt = [i for i in current if i != message_id] if is_bm else current + [message_id]
        conn.execute(
            "UPDATE conversations SET bookmarked_message_ids = ?, updated_at = ? WHERE id = ?",
            (json.dumps(nxt), int(time.time() * 1000), conversation_id),
        )
    return {"bookmarkedMessageIds": nxt, "bookmarked": not is_bm}


def toggle_pinned_message(conversation_id: str, message_id: str) -> dict[str, Any]:
    with db_tx() as conn:
        conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not conv:
            raise ValueError(f"Conversation not found: {conversation_id}")
        msg = conn.execute(
            "SELECT 1 FROM messages WHERE id = ? AND conversation_id = ?", (message_id, conversation_id)
        ).fetchone()
        if not msg:
            raise ValueError(f"Message not found in conversation: {message_id}")
        current = json.loads(conv["pinned_message_ids"] or "[]")
        is_pin = message_id in current
        if not is_pin and len(current) >= PIN_LIMIT_PER_CONVERSATION:
            raise ValueError("PIN_LIMIT_EXCEEDED")
        nxt = [i for i in current if i != message_id] if is_pin else current + [message_id]
        conn.execute(
            "UPDATE conversations SET pinned_message_ids = ? WHERE id = ?",
            (json.dumps(nxt), conversation_id),
        )
    return {"pinnedMessageIds": nxt, "pinned": not is_pin}


def _resolve_responder(conv: dict[str, Any]) -> str:
    if conv["mode"] == "single":
        return conv["agentIds"][0]
    placeholders = ",".join("?" for _ in conv["agentIds"])
    with db_read() as conn:
        leads = conn.execute(
            f"SELECT id FROM agents WHERE is_orchestrator = 1 AND id IN ({placeholders})",
            conv["agentIds"],
        ).fetchall()
    if len(leads) != 1:
        raise ValueError("群聊需要一位主 Agent，请在群成员中加入 Orchestrator")
    return leads[0]["id"]


async def send_message(
    *,
    conversation_id: str,
    content: str = "",
    parent_message_id: str | None = None,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    conv = get_conversation(conversation_id)
    if not conv:
        raise ValueError(f"Conversation not found: {conversation_id}")

    now = int(time.time() * 1000)
    message_id = new_message_id()
    parts: list[dict[str, Any]] = []
    if content and content.strip():
        parts.append({"type": "text", "content": content})

    if attachment_ids:
        placeholders = ",".join("?" for _ in attachment_ids)
        with db_read() as conn:
            att_rows = conn.execute(
                f"SELECT * FROM attachments WHERE id IN ({placeholders})", attachment_ids
            ).fetchall()
        for r in att_rows:
            if r["conversation_id"] != conversation_id:
                continue
            parts.append(
                {
                    "type": "image_attachment" if r["kind"] == "image" else "file_attachment",
                    "attachmentId": r["id"],
                    "fileName": r["file_name"],
                    "size": r["size"],
                    "mimeType": r["mime_type"],
                }
            )

    deploy_intent = (
        parse_deploy_command(content)
        if len(parts) == 1 and not parent_message_id and not attachment_ids
        else None
    )
    responder = _resolve_responder(conv) if deploy_intent is None else None
    msg_record = {
        "id": message_id,
        "conversationId": conversation_id,
        "role": "user",
        "agentId": None,
        "parts": parts,
        "status": "complete",
        "parentMessageId": parent_message_id,
        "mentionedAgentIds": [],
        "runId": None,
        "usage": None,
        "createdAt": now,
    }

    with db_tx() as conn:
        conn.execute(
            """INSERT INTO messages (
                id, conversation_id, role, agent_id, parts, status,
                parent_message_id, mentioned_agent_ids, run_id, usage, created_at
            ) VALUES (?, ?, 'user', NULL, ?, 'complete', ?, ?, NULL, NULL, ?)""",
            (
                message_id,
                conversation_id,
                json.dumps(parts, ensure_ascii=False),
                parent_message_id,
                "[]",
                now,
            ),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

    event_bus.publish(
        {
            "type": "message.added",
            "conversationId": conversation_id,
            "timestamp": now,
            "message": msg_record,
        }
    )

    if deploy_intent is not None:
        deploy = handle_deploy_command(
            conversation_id=conversation_id,
            artifact_id=deploy_intent.get("artifactId"),
            after_created_at=now,
        )
        return {"messageId": message_id, "runIds": [], "messages": [deploy["message"]], "deploy": deploy}

    from app.runner.agent_runner import agent_runner

    assert responder is not None
    run_id = agent_runner.start_run(
        agent_id=responder,
        conversation_id=conversation_id,
        trigger_message_id=message_id,
    )
    return {"messageId": message_id, "runIds": [run_id]}


def revise_dispatch_plan(*, conversation_id: str, plan_id: str, feedback: str) -> tuple[bool, str | None]:
    pending = pending_dispatch_plans.get(plan_id)
    if not pending or pending["conversationId"] != conversation_id:
        return False, "Pending dispatch plan not found"
    now = int(time.time() * 1000)
    message_id = new_message_id()
    parts = [{"type": "text", "content": feedback}]
    msg = {
        "id": message_id,
        "conversationId": conversation_id,
        "role": "user",
        "agentId": None,
        "parts": parts,
        "status": "complete",
        "parentMessageId": None,
        "mentionedAgentIds": [],
        "runId": None,
        "usage": None,
        "createdAt": now,
    }
    with db_tx() as conn:
        conn.execute(
            """INSERT INTO messages (
                id, conversation_id, role, agent_id, parts, status,
                parent_message_id, mentioned_agent_ids, run_id, usage, created_at
            ) VALUES (?, ?, 'user', NULL, ?, 'complete', NULL, '[]', NULL, NULL, ?)""",
            (message_id, conversation_id, json.dumps(parts, ensure_ascii=False), now),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    event_bus.publish(
        {
            "type": "message.added",
            "conversationId": conversation_id,
            "timestamp": now,
            "message": msg,
        }
    )
    ok = pending_dispatch_plans.revise(plan_id, feedback)
    return (True, None) if ok else (False, "Failed to revise pending dispatch plan")


async def withdraw_latest_user_message(conversation_id: str, message_id: str) -> dict[str, Any]:
    with db_read() as conn:
        msg_row = conn.execute(
            "SELECT * FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
        if not msg_row:
            raise ValueError(f"Message not found: {message_id}")
        if msg_row["role"] != "user":
            raise ValueError("Only user messages can be withdrawn")
        latest_user = conn.execute(
            "SELECT id FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if not latest_user or latest_user["id"] != message_id:
            raise ValueError("Only the latest user message can be withdrawn")
        running = conn.execute(
            "SELECT id FROM agent_runs WHERE conversation_id = ? AND started_at >= ? AND status = 'running'",
            (conversation_id, msg_row["created_at"]),
        ).fetchall()

    if running:
        from app.runner.agent_runner import agent_runner
        for r in running:
            agent_runner.abort(r["id"])
        await asyncio.sleep(0.3)

    with db_tx() as conn:
        msgs = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND created_at >= ?",
            (conversation_id, msg_row["created_at"]),
        ).fetchall()
        msg_ids = [r["id"] for r in msgs]
        art_ids: set[str] = set()
        for r in msgs:
            for p in json.loads(r["parts"] or "[]"):
                if p.get("type") == "artifact_ref" and p.get("artifactId"):
                    art_ids.add(p["artifactId"])
        runs = conn.execute(
            "SELECT id FROM agent_runs WHERE conversation_id = ? AND started_at >= ?",
            (conversation_id, msg_row["created_at"]),
        ).fetchall()
        run_ids = [r["id"] for r in runs]

        if msg_ids:
            ph = ",".join("?" for _ in msg_ids)
            conn.execute(f"DELETE FROM messages WHERE id IN ({ph})", msg_ids)
        if art_ids:
            ph = ",".join("?" for _ in art_ids)
            conn.execute(f"DELETE FROM artifacts WHERE id IN ({ph})", list(art_ids))
        if run_ids:
            ph = ",".join("?" for _ in run_ids)
            conn.execute(f"DELETE FROM agent_runs WHERE id IN ({ph})", run_ids)

    event_bus.publish(
        {
            "type": "message.removed",
            "conversationId": conversation_id,
            "timestamp": int(time.time() * 1000),
            "messageIds": msg_ids,
            "artifactIds": list(art_ids),
        }
    )
    return {"deletedMessageIds": msg_ids, "deletedArtifactIds": list(art_ids)}


async def regenerate_latest_response(conversation_id: str) -> dict[str, Any]:
    conv = get_conversation(conversation_id)
    if not conv:
        raise ValueError(f"Conversation not found: {conversation_id}")

    responder = _resolve_responder(conv)

    with db_read() as conn:
        latest_user_row = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    if not latest_user_row:
        raise ValueError("No user message to regenerate from")
    latest_user = row_to_message(latest_user_row)

    with db_read() as conn:
        running = conn.execute(
            "SELECT id FROM agent_runs WHERE conversation_id = ? AND started_at > ? AND status = 'running'",
            (conversation_id, latest_user["createdAt"]),
        ).fetchall()
    if running:
        from app.runner.agent_runner import agent_runner
        for r in running:
            agent_runner.abort(r["id"])
        await asyncio.sleep(0.3)

    with db_tx() as conn:
        msgs = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND created_at > ?",
            (conversation_id, latest_user["createdAt"]),
        ).fetchall()
        msg_ids = [r["id"] for r in msgs]
        art_ids: set[str] = set()
        for r in msgs:
            for p in json.loads(r["parts"] or "[]"):
                if p.get("type") == "artifact_ref" and p.get("artifactId"):
                    art_ids.add(p["artifactId"])
        runs = conn.execute(
            "SELECT id FROM agent_runs WHERE conversation_id = ? AND started_at > ?",
            (conversation_id, latest_user["createdAt"]),
        ).fetchall()
        run_ids = [r["id"] for r in runs]

        if msg_ids:
            ph = ",".join("?" for _ in msg_ids)
            conn.execute(f"DELETE FROM messages WHERE id IN ({ph})", msg_ids)
        if art_ids:
            ph = ",".join("?" for _ in art_ids)
            conn.execute(f"DELETE FROM artifacts WHERE id IN ({ph})", list(art_ids))
        if run_ids:
            ph = ",".join("?" for _ in run_ids)
            conn.execute(f"DELETE FROM agent_runs WHERE id IN ({ph})", run_ids)

    event_bus.publish(
        {
            "type": "message.removed",
            "conversationId": conversation_id,
            "timestamp": int(time.time() * 1000),
            "messageIds": msg_ids,
            "artifactIds": list(art_ids),
        }
    )

    from app.runner.agent_runner import agent_runner

    new_run_id = agent_runner.start_run(
        agent_id=responder,
        conversation_id=conversation_id,
        trigger_message_id=latest_user["id"],
    )
    return {
        "deletedMessageIds": msg_ids,
        "deletedArtifactIds": list(art_ids),
        "triggerMessageId": latest_user["id"],
        "runIds": [new_run_id],
    }


async def edit_and_resend_latest_user_message(
    conversation_id: str, message_id: str, new_content: str
) -> dict[str, Any]:
    trimmed = new_content.strip()
    if not trimmed:
        raise ValueError("Content cannot be empty")
    with db_read() as conn:
        orig_row = conn.execute(
            "SELECT * FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
    if not orig_row:
        raise ValueError(f"Message not found: {message_id}")
    orig = row_to_message(orig_row)
    if orig["role"] != "user":
        raise ValueError("Only user messages can be edited")

    att_ids = [
        p["attachmentId"]
        for p in orig["parts"]
        if p.get("type") in ("image_attachment", "file_attachment") and p.get("attachmentId")
    ]
    conv = get_conversation(conversation_id)
    if not conv:
        raise ValueError(f"Conversation not found: {conversation_id}")
    _resolve_responder(conv)
    withdrawn = await withdraw_latest_user_message(conversation_id, message_id)
    sent = await send_message(
        conversation_id=conversation_id,
        content=trimmed,
        parent_message_id=orig["parentMessageId"],
        attachment_ids=att_ids or None,
    )
    with db_read() as conn:
        new_row = conn.execute("SELECT * FROM messages WHERE id = ?", (sent["messageId"],)).fetchone()
    if not new_row:
        raise ValueError("New message disappeared after insert")
    return {
        **withdrawn,
        "newMessage": row_to_message(new_row),
        "runIds": sent["runIds"],
    }
