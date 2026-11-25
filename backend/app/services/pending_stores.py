from __future__ import annotations

import asyncio
import time
from typing import Any

from app.ids import (
    new_pending_bash_command_id,
    new_pending_dispatch_plan_id,
    new_pending_question_id,
    new_pending_write_id,
)
from app.services.event_bus import event_bus


class PendingWritesStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, asyncio.Future[bool]] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        path: str,
        absolute_path: str,
        old_content: str | None,
        new_content: str,
    ) -> tuple[dict[str, Any], asyncio.Future[bool]]:
        pw_id = new_pending_write_id()
        now = int(time.time() * 1000)
        rec = {
            "id": pw_id,
            "conversationId": conversation_id,
            "agentId": agent_id,
            "runId": run_id,
            "path": path,
            "absolutePath": absolute_path,
            "oldContent": old_content,
            "newContent": new_content,
            "createdAt": now,
        }
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._records[pw_id] = rec
        self._futures[pw_id] = fut
        event_bus.publish(
            {
                "type": "fs_write.pending",
                "conversationId": conversation_id,
                "timestamp": now,
                "pendingWrite": rec,
            }
        )
        return rec, fut

    def get(self, pw_id: str) -> dict[str, Any] | None:
        return self._records.get(pw_id)

    def list_by_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        return [r for r in self._records.values() if r["conversationId"] == conversation_id]

    def resolve(self, pw_id: str, applied: bool) -> bool:
        rec = self._records.pop(pw_id, None)
        fut = self._futures.pop(pw_id, None)
        if not rec:
            return False
        if fut and not fut.done():
            fut.set_result(applied)
        event_bus.publish(
            {
                "type": "fs_write.resolved",
                "conversationId": rec["conversationId"],
                "timestamp": int(time.time() * 1000),
                "pendingId": pw_id,
                "applied": applied,
            }
        )
        return True


class PendingQuestionsStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, asyncio.Future[dict[str, Any] | None]] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        questions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], asyncio.Future[dict[str, Any] | None]]:
        qid = new_pending_question_id()
        now = int(time.time() * 1000)
        rec = {
            "id": qid,
            "conversationId": conversation_id,
            "agentId": agent_id,
            "runId": run_id,
            "questions": questions,
            "createdAt": now,
        }
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any] | None] = loop.create_future()
        self._records[qid] = rec
        self._futures[qid] = fut
        event_bus.publish(
            {
                "type": "ask_user.pending",
                "conversationId": conversation_id,
                "timestamp": now,
                "pendingQuestion": rec,
            }
        )
        return rec, fut

    def get(self, qid: str) -> dict[str, Any] | None:
        return self._records.get(qid)

    def list_by_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        return [r for r in self._records.values() if r["conversationId"] == conversation_id]

    def answer(self, qid: str, answers: dict[str, Any]) -> bool:
        rec = self._records.pop(qid, None)
        fut = self._futures.pop(qid, None)
        if not rec:
            return False
        if fut and not fut.done():
            fut.set_result(answers)
        event_bus.publish(
            {
                "type": "ask_user.resolved",
                "conversationId": rec["conversationId"],
                "timestamp": int(time.time() * 1000),
                "pendingId": qid,
                "answered": True,
            }
        )
        return True

    def cancel(self, qid: str) -> bool:
        rec = self._records.pop(qid, None)
        fut = self._futures.pop(qid, None)
        if not rec:
            return False
        if fut and not fut.done():
            fut.set_result(None)
        event_bus.publish(
            {
                "type": "ask_user.resolved",
                "conversationId": rec["conversationId"],
                "timestamp": int(time.time() * 1000),
                "pendingId": qid,
                "answered": False,
            }
        )
        return True


class PendingBashCommandsStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, asyncio.Future[bool]] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        command: str,
        cwd: str,
        reason: str,
    ) -> tuple[dict[str, Any], asyncio.Future[bool]]:
        cid = new_pending_bash_command_id()
        now = int(time.time() * 1000)
        rec = {
            "id": cid,
            "conversationId": conversation_id,
            "agentId": agent_id,
            "runId": run_id,
            "command": command,
            "cwd": cwd,
            "reason": reason,
            "createdAt": now,
        }
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._records[cid] = rec
        self._futures[cid] = fut
        event_bus.publish(
            {
                "type": "bash_command.pending",
                "conversationId": conversation_id,
                "timestamp": now,
                "pendingCommand": rec,
            }
        )
        return rec, fut

    def get(self, cid: str) -> dict[str, Any] | None:
        return self._records.get(cid)

    def list_by_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        return [r for r in self._records.values() if r["conversationId"] == conversation_id]

    def resolve(self, cid: str, approved: bool) -> bool:
        rec = self._records.pop(cid, None)
        fut = self._futures.pop(cid, None)
        if not rec:
            return False
        if fut and not fut.done():
            fut.set_result(approved)
        event_bus.publish(
            {
                "type": "bash_command.resolved",
                "conversationId": rec["conversationId"],
                "timestamp": int(time.time() * 1000),
                "pendingId": cid,
                "approved": approved,
            }
        )
        return True


class PendingDispatchPlansStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        message_id: str,
        plan: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], asyncio.Future[dict[str, Any]]]:
        pid = new_pending_dispatch_plan_id()
        now = int(time.time() * 1000)
        rec = {
            "id": pid,
            "conversationId": conversation_id,
            "agentId": agent_id,
            "runId": run_id,
            "messageId": message_id,
            "plan": plan,
            "createdAt": now,
        }
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._records[pid] = rec
        self._futures[pid] = fut
        event_bus.publish(
            {
                "type": "dispatch.plan.pending",
                "conversationId": conversation_id,
                "timestamp": now,
                "pendingPlan": rec,
            }
        )
        return rec, fut

    def get(self, pid: str) -> dict[str, Any] | None:
        return self._records.get(pid)

    def list_by_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        return [r for r in self._records.values() if r["conversationId"] == conversation_id]

    def approve(self, pid: str) -> tuple[bool, str | None]:
        rec = self._records.pop(pid, None)
        fut = self._futures.pop(pid, None)
        if not rec:
            return False, "Pending dispatch plan not found"
        if fut and not fut.done():
            fut.set_result({"decision": "approve", "plan": rec["plan"]})
        event_bus.publish(
            {
                "type": "dispatch.plan.resolved",
                "conversationId": rec["conversationId"],
                "timestamp": int(time.time() * 1000),
                "pendingId": pid,
                "runId": rec["runId"],
                "messageId": rec["messageId"],
                "approved": True,
            }
        )
        return True, None

    def reject(self, pid: str) -> bool:
        rec = self._records.pop(pid, None)
        fut = self._futures.pop(pid, None)
        if not rec:
            return False
        if fut and not fut.done():
            fut.set_result({"decision": "reject"})
        event_bus.publish(
            {
                "type": "dispatch.plan.resolved",
                "conversationId": rec["conversationId"],
                "timestamp": int(time.time() * 1000),
                "pendingId": pid,
                "runId": rec["runId"],
                "messageId": rec["messageId"],
                "approved": False,
            }
        )
        return True

    def revise(self, pid: str, feedback: str) -> bool:
        rec = self._records.pop(pid, None)
        fut = self._futures.pop(pid, None)
        if not rec:
            return False
        if fut and not fut.done():
            fut.set_result({"decision": "revise", "feedback": feedback})
        event_bus.publish(
            {
                "type": "dispatch.plan.resolved",
                "conversationId": rec["conversationId"],
                "timestamp": int(time.time() * 1000),
                "pendingId": pid,
                "runId": rec["runId"],
                "messageId": rec["messageId"],
                "approved": False,
                "revising": True,
            }
        )
        return True


pending_writes = PendingWritesStore()
pending_questions = PendingQuestionsStore()
pending_bash_commands = PendingBashCommandsStore()
pending_dispatch_plans = PendingDispatchPlansStore()
