from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.adapters.registry import adapter_registry
from app.adapters.types import AdapterAttachment, AdapterInput
from app.db.client import db_read, db_tx, row_to_agent, row_to_message, row_to_workspace
from app.ids import new_message_id, new_run_id
from app.services.attachment_service import get_attachment_absolute_path
from app.services.context_compaction_service import (
    get_latest_context_summary,
    render_conversation_summary_block,
)
from app.services.conversation_context import (
    build_history_for,
    extract_text_from_parts,
    get_pinned_messages_block,
)
from app.services.dispatch_plan import compile_dispatch_waves, validate_dispatch_plan
from app.services.event_bus import event_bus
from app.services.pending_stores import pending_dispatch_plans
from app.services.workspace_utils import get_effective_cwd


class AgentRunner:
    def __init__(self) -> None:
        self._active_runs: dict[str, asyncio.Event] = {}
        self._child_runs: dict[str, set[str]] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def start_run(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str | None = None,
        override_prompt: str | None = None,
        override_system_prompt: str | None = None,
        override_tool_names: list[str] | None = None,
    ) -> str:
        run_id = new_run_id()
        abort_event = asyncio.Event()
        self._active_runs[run_id] = abort_event
        if parent_run_id:
            self._child_runs.setdefault(parent_run_id, set()).add(run_id)

        task = asyncio.create_task(
            self._execute_run(
                run_id=run_id,
                abort_event=abort_event,
                agent_id=agent_id,
                conversation_id=conversation_id,
                trigger_message_id=trigger_message_id,
                parent_run_id=parent_run_id,
                override_prompt=override_prompt,
                override_system_prompt=override_system_prompt,
                override_tool_names=override_tool_names,
            )
        )
        self._tasks[run_id] = task
        return run_id

    def abort(self, run_id: str) -> bool:
        ev = self._active_runs.get(run_id)
        if not ev:
            return False
        ev.set()
        for child_id in list(self._child_runs.get(run_id, set())):
            self.abort(child_id)
        return True

    async def _execute_run(
        self,
        *,
        run_id: str,
        abort_event: asyncio.Event,
        agent_id: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str | None,
        override_prompt: str | None,
        override_system_prompt: str | None,
        override_tool_names: list[str] | None,
    ) -> dict[str, Any]:
        try:
            with db_read() as conn:
                ag_row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
                ws_row = conn.execute("SELECT * FROM workspaces WHERE conversation_id = ?", (conversation_id,)).fetchone()
                msg_row = conn.execute(
                    "SELECT * FROM messages WHERE id = ? AND conversation_id = ?",
                    (trigger_message_id, conversation_id),
                ).fetchone()

            if not ag_row:
                return self._finalize_run(run_id, conversation_id, agent_id, trigger_message_id, parent_run_id, "failed", f"Agent not found: {agent_id}")
            if not ws_row:
                return self._finalize_run(run_id, conversation_id, agent_id, trigger_message_id, parent_run_id, "failed", f"Workspace not found: {conversation_id}")
            if not msg_row:
                return self._finalize_run(run_id, conversation_id, agent_id, trigger_message_id, parent_run_id, "failed", f"Trigger message not found: {trigger_message_id}")

            agent = row_to_agent(ag_row)
            workspace = row_to_workspace(ws_row)
            trigger_msg = row_to_message(msg_row)

            prompt = override_prompt if override_prompt is not None else extract_text_from_parts(trigger_msg["parts"])
            attachments: list[AdapterAttachment] = []
            if override_prompt is None:
                for p in trigger_msg["parts"]:
                    if p.get("type") in ("image_attachment", "file_attachment"):
                        abs_p = get_attachment_absolute_path(p["attachmentId"])
                        if abs_p:
                            attachments.append(
                                AdapterAttachment(
                                    id=p["attachmentId"],
                                    file_name=p["fileName"],
                                    mime_type=p["mimeType"],
                                    kind="image" if p["type"] == "image_attachment" else "file",
                                    abs_path=abs_p,
                                )
                            )

            now = int(time.time() * 1000)
            with db_tx() as conn:
                conn.execute(
                    """INSERT INTO agent_runs (
                        id, conversation_id, agent_id, trigger_message_id,
                        status, error, parent_run_id, usage, started_at, finished_at
                    ) VALUES (?, ?, ?, ?, 'running', NULL, ?, NULL, ?, NULL)""",
                    (run_id, conversation_id, agent_id, trigger_message_id, parent_run_id, now),
                )

            event_bus.publish(
                {
                    "type": "run.start",
                    "conversationId": conversation_id,
                    "timestamp": now,
                    "runId": run_id,
                    "agentId": agent_id,
                    "triggerMessageId": trigger_message_id,
                    **({"parentRunId": parent_run_id} if parent_run_id else {}),
                }
            )

            if agent["isOrchestrator"] and not parent_run_id:
                result = await self._execute_orchestrator_run(
                    run_id=run_id,
                    abort_event=abort_event,
                    agent=agent,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    prompt=prompt,
                    attachments=attachments,
                )
            else:
                result = await self._execute_simple_run(
                    run_id=run_id,
                    abort_event=abort_event,
                    agent=agent,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    prompt=prompt,
                    attachments=attachments,
                    override_system_prompt=override_system_prompt,
                    override_tool_names=override_tool_names,
                )

            status = "aborted" if abort_event.is_set() else "complete"
            return self._finalize_run(
                run_id, conversation_id, agent_id, trigger_message_id, parent_run_id, status, None, result
            )
        except Exception as exc:
            status = "aborted" if abort_event.is_set() else "failed"
            err_msg = None if status == "aborted" else str(exc)
            if err_msg:
                self._emit_error_message(conversation_id, agent_id, run_id, err_msg)
            return self._finalize_run(
                run_id, conversation_id, agent_id, trigger_message_id, parent_run_id, status, err_msg
            )
        finally:
            self._active_runs.pop(run_id, None)
            self._tasks.pop(run_id, None)
            if parent_run_id and parent_run_id in self._child_runs:
                self._child_runs[parent_run_id].discard(run_id)
            self._child_runs.pop(run_id, None)

    def _build_adapter_input(
        self,
        *,
        run_id: str,
        agent: dict[str, Any],
        workspace: dict[str, Any],
        conversation_id: str,
        trigger_message_id: str,
        prompt: str,
        attachments: list[AdapterAttachment],
        tool_names: list[str],
        override_system_prompt: str | None = None,
    ) -> AdapterInput:
        cwd = get_effective_cwd(workspace)
        summary = get_latest_context_summary(conversation_id)
        summary_block = render_conversation_summary_block(summary)
        pinned_block = get_pinned_messages_block(conversation_id)

        sys_parts = [override_system_prompt or agent["systemPrompt"]]
        sys_parts.append(
            f"<workspace_info>\nmode={workspace['mode']}\ncwd={cwd}\n</workspace_info>"
        )
        if pinned_block:
            sys_parts.append(pinned_block)
        if summary_block:
            sys_parts.append(summary_block)

        history = build_history_for(
            conversation_id=conversation_id,
            current_agent_id=agent["id"],
            exclude_message_id=trigger_message_id,
            after_created_at=summary["coveredUntilCreatedAt"] if summary else None,
        )

        return AdapterInput(
            agent_id=agent["id"],
            conversation_id=conversation_id,
            run_id=run_id,
            prompt=prompt,
            workspace_path=cwd,
            system_prompt="\n\n".join(sys_parts),
            api_key=agent.get("apiKey"),
            api_base_url=agent.get("apiBaseUrl"),
            model_id=agent.get("modelId"),
            tool_names=tool_names,
            attachments=attachments,
            history=history,
            model_provider=agent.get("modelProvider"),
            supports_vision=bool(agent.get("supportsVision")),
        )

    async def _execute_simple_run(
        self,
        *,
        run_id: str,
        abort_event: asyncio.Event,
        agent: dict[str, Any],
        workspace: dict[str, Any],
        conversation_id: str,
        trigger_message_id: str,
        prompt: str,
        attachments: list[AdapterAttachment],
        override_system_prompt: str | None = None,
        override_tool_names: list[str] | None = None,
    ) -> dict[str, Any]:
        tool_names = override_tool_names if override_tool_names is not None else list(agent["toolNames"])
        adapter = adapter_registry.get_adapter(agent)
        inp = self._build_adapter_input(
            run_id=run_id,
            agent=agent,
            workspace=workspace,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            prompt=prompt,
            attachments=attachments,
            tool_names=tool_names,
            override_system_prompt=override_system_prompt,
        )
        return await self._consume_stream(adapter.stream(inp, abort_event), agent["id"], run_id, conversation_id)

    async def _execute_orchestrator_run(
        self,
        *,
        run_id: str,
        abort_event: asyncio.Event,
        agent: dict[str, Any],
        workspace: dict[str, Any],
        conversation_id: str,
        trigger_message_id: str,
        prompt: str,
        attachments: list[AdapterAttachment],
    ) -> dict[str, Any]:
        with db_read() as conn:
            conv_row = conn.execute("SELECT agent_ids FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            other_ids = [aid for aid in json.loads(conv_row["agent_ids"] or "[]") if aid != agent["id"]] if conv_row else []
            other_agents = []
            if other_ids:
                ph = ",".join("?" for _ in other_ids)
                other_agents = [row_to_agent(r) for r in conn.execute(f"SELECT * FROM agents WHERE id IN ({ph})", other_ids).fetchall()]

        roster_lines = [
            f"- {a['id']} ({a['name']}): {a['description']} | capabilities={a['capabilities']}"
            for a in other_agents
        ]
        plan_sys = (
            agent["systemPrompt"]
            + "\n\n当前群聊可用 Agent 列表：\n"
            + ("\n".join(roster_lines) if roster_lines else "(无其他 Agent)")
        )

        # Stage 1: PLAN
        plan_res = await self._execute_simple_run(
            run_id=run_id,
            abort_event=abort_event,
            agent=agent,
            workspace=workspace,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            prompt=prompt,
            attachments=attachments,
            override_system_prompt=plan_sys,
            override_tool_names=["plan_tasks", "ask_user", "fs_list", "fs_read", "read_artifact", "read_attachment"],
        )

        plan_tasks = plan_res.get("plannedTasks")
        if not plan_tasks or abort_event.is_set():
            return plan_res

        valid_ids = {a["id"] for a in other_agents}
        ok, err = validate_dispatch_plan(plan_tasks, valid_ids)
        if not ok:
            self._emit_error_message(conversation_id, agent["id"], run_id, f"Invalid dispatch plan: {err}")
            return plan_res

        # Publish plan and await user approval if needed
        event_bus.publish(
            {
                "type": "dispatch.plan",
                "conversationId": conversation_id,
                "timestamp": int(time.time() * 1000),
                "runId": run_id,
                "plan": plan_tasks,
            }
        )

        # Stage 2: EXECUTE DAG waves
        waves = compile_dispatch_waves(plan_tasks)
        task_summaries: list[str] = []
        for wave in waves:
            if abort_event.is_set():
                break

            async def _run_child(item: dict[str, Any]) -> None:
                child_run_id = self.start_run(
                    agent_id=item["agentId"],
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    parent_run_id=run_id,
                    override_prompt=item["task"],
                )
                event_bus.publish(
                    {
                        "type": "dispatch.start",
                        "conversationId": conversation_id,
                        "timestamp": int(time.time() * 1000),
                        "parentRunId": run_id,
                        "childRunId": child_run_id,
                        "taskId": item["id"],
                        "agentId": item["agentId"],
                    }
                )
                child_task = self._tasks.get(child_run_id)
                child_res = await child_task if child_task else {"status": "complete"}
                end_status = child_res.get("status", "complete")
                event_bus.publish(
                    {
                        "type": "dispatch.end",
                        "conversationId": conversation_id,
                        "timestamp": int(time.time() * 1000),
                        "parentRunId": run_id,
                        "childRunId": child_run_id,
                        "taskId": item["id"],
                        "status": end_status,
                        **({"error": child_res["error"]} if child_res.get("error") else {}),
                    }
                )
                task_summaries.append(f"- Task {item['id']} ({item['agentId']}): {end_status}")

            await asyncio.gather(*(_run_child(item) for item in wave))

        if abort_event.is_set():
            return plan_res

        # Stage 3: AGGREGATE
        agg_prompt = (
            f"原始需求：{prompt}\n\n子任务执行结果：\n"
            + "\n".join(task_summaries)
            + "\n\n请简洁总结关键结论、产物位置与下一步建议。"
        )
        return await self._execute_simple_run(
            run_id=run_id,
            abort_event=abort_event,
            agent=agent,
            workspace=workspace,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            prompt=agg_prompt,
            attachments=[],
            override_tool_names=["read_artifact", "fs_read"],
        )

    async def _consume_stream(
        self, stream, agent_id: str, run_id: str, conversation_id: str
    ) -> dict[str, Any]:
        parts_by_msg: dict[str, list[dict[str, Any]]] = {}
        artifact_ids: list[str] = []
        output_msg_ids: list[str] = []
        planned_tasks: list[dict[str, Any]] | None = None
        current_msg_id: str | None = None

        async for ev in stream:
            etype = ev.get("type")
            if etype == "message.start":
                mid = ev["messageId"]
                current_msg_id = mid
                parts_by_msg[mid] = []
                output_msg_ids.append(mid)
                now = ev["timestamp"]
                with db_tx() as conn:
                    conn.execute(
                        """INSERT INTO messages (
                            id, conversation_id, role, agent_id, parts, status,
                            parent_message_id, mentioned_agent_ids, run_id, usage, created_at
                        ) VALUES (?, ?, 'agent', ?, '[]', 'streaming', NULL, '[]', ?, NULL, ?)""",
                        (mid, conversation_id, agent_id, run_id, now),
                    )
                    conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

            elif etype == "part.start":
                mid = ev["messageId"]
                idx = ev["partIndex"]
                parts = parts_by_msg.setdefault(mid, [])
                while len(parts) <= idx:
                    parts.append({"type": "text", "content": ""})
                parts[idx] = dict(ev["part"])

            elif etype == "part.delta":
                mid = ev["messageId"]
                idx = ev["partIndex"]
                delta = ev["delta"]
                parts = parts_by_msg.get(mid)
                if parts and idx < len(parts):
                    parts[idx]["content"] = str(parts[idx].get("content", "")) + str(delta.get("text", ""))

            elif etype == "tool.call":
                mid = ev["messageId"]
                parts = parts_by_msg.setdefault(mid, [])
                parts.append(
                    {
                        "type": "tool_use",
                        "callId": ev["callId"],
                        "toolName": ev["toolName"],
                        "args": ev.get("args"),
                    }
                )
                if ev["toolName"] == "plan_tasks" and isinstance(ev.get("args"), dict):
                    tasks = ev["args"].get("tasks")
                    if isinstance(tasks, list):
                        planned_tasks = tasks

            elif etype == "tool.result":
                mid = ev["messageId"]
                parts = parts_by_msg.setdefault(mid, [])
                parts.append(
                    {
                        "type": "tool_result",
                        "callId": ev["callId"],
                        "result": ev.get("result"),
                        "isError": bool(ev.get("isError")),
                    }
                )

            elif etype == "artifact.create":
                art = ev.get("artifact") or {}
                if art.get("id"):
                    artifact_ids.append(art["id"])
                    if current_msg_id and current_msg_id in parts_by_msg:
                        ref_part = {"type": "artifact_ref", "artifactId": art["id"]}
                        parts_by_msg[current_msg_id].append(ref_part)
                        event_bus.publish(
                            {
                                "type": "part.start",
                                "conversationId": conversation_id,
                                "timestamp": int(time.time() * 1000),
                                "messageId": current_msg_id,
                                "partIndex": len(parts_by_msg[current_msg_id]) - 1,
                                "part": ref_part,
                            }
                        )

            elif etype == "deploy.status":
                mid = ev["messageId"]
                parts = parts_by_msg.setdefault(mid, [])
                parts.append({"type": "deploy_status", "deployment": ev["deployment"]})

            elif etype == "message.usage":
                mid = ev["messageId"]
                with db_tx() as conn:
                    conn.execute(
                        "UPDATE messages SET usage = ? WHERE id = ?",
                        (json.dumps(ev["usage"]), mid),
                    )

            elif etype == "message.end":
                mid = ev["messageId"]
                parts = parts_by_msg.get(mid, [])
                with db_tx() as conn:
                    conn.execute(
                        "UPDATE messages SET parts = ?, status = 'complete' WHERE id = ?",
                        (json.dumps(parts, ensure_ascii=False), mid),
                    )

            elif etype == "run.usage":
                with db_tx() as conn:
                    conn.execute(
                        "UPDATE agent_runs SET usage = ? WHERE id = ?",
                        (json.dumps(ev["usage"]), run_id),
                    )

            event_bus.publish(ev)

        return {
            "artifactIds": artifact_ids,
            "outputMessageIds": output_msg_ids,
            "plannedTasks": planned_tasks,
        }

    def _emit_error_message(self, conversation_id: str, agent_id: str, run_id: str, error: str) -> None:
        now = int(time.time() * 1000)
        mid = new_message_id()
        parts = [{"type": "text", "content": f"❌ 运行失败：{error}"}]
        msg = {
            "id": mid,
            "conversationId": conversation_id,
            "role": "agent",
            "agentId": agent_id,
            "parts": parts,
            "status": "error",
            "parentMessageId": None,
            "mentionedAgentIds": [],
            "runId": run_id,
            "usage": None,
            "createdAt": now,
        }
        with db_tx() as conn:
            conn.execute(
                """INSERT INTO messages (
                    id, conversation_id, role, agent_id, parts, status,
                    parent_message_id, mentioned_agent_ids, run_id, usage, created_at
                ) VALUES (?, ?, 'agent', ?, ?, 'error', NULL, '[]', ?, NULL, ?)""",
                (mid, conversation_id, agent_id, json.dumps(parts, ensure_ascii=False), run_id, now),
            )
        event_bus.publish(
            {
                "type": "message.added",
                "conversationId": conversation_id,
                "timestamp": now,
                "message": msg,
            }
        )

    def _finalize_run(
        self,
        run_id: str,
        conversation_id: str,
        agent_id: str,
        trigger_message_id: str,
        parent_run_id: str | None,
        status: str,
        error: str | None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with db_tx() as conn:
            conn.execute(
                "UPDATE agent_runs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, error, now, run_id),
            )
            if status in ("aborted", "failed"):
                conn.execute(
                    "UPDATE messages SET status = ? WHERE run_id = ? AND status = 'streaming'",
                    ("aborted" if status == "aborted" else "error", run_id),
                )
        event_bus.publish(
            {
                "type": "run.end",
                "conversationId": conversation_id,
                "timestamp": now,
                "runId": run_id,
                "status": status,
                **({"error": error} if error else {}),
            }
        )
        return {"runId": run_id, "status": status, "error": error, **(result or {})}


agent_runner = AgentRunner()
