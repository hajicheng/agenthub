from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from pydantic import ValidationError

from app.adapters.registry import adapter_registry
from app.adapters.types import AdapterAttachment, AdapterInput
from app.db.client import db_read, db_tx, row_to_agent, row_to_message, row_to_workspace
from app.ids import new_message_id, new_run_id
from app.observability import Observation, trace_step
from app.runner.orchestration_graph import DelegationContext, DelegationRequest, delegation_graph
from app.services.artifact_service import get_artifact
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
from app.services.event_bus import event_bus
from app.services.workspace_utils import get_effective_cwd
from app.tools.types import DelegationHandler, ToolResult


class AgentRunner:
    def __init__(self) -> None:
        self._active_runs: dict[str, asyncio.Event] = {}
        self._child_runs: dict[str, set[str]] = {}
        self._tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}

    def start_run(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str | None = None,
        override_prompt: str | None = None,
    ) -> str:
        run_id = new_run_id()
        abort_event = asyncio.Event()
        self._active_runs[run_id] = abort_event
        if parent_run_id:
            self._child_runs.setdefault(parent_run_id, set()).add(run_id)

        async def execute() -> dict[str, Any]:
            metadata = {"run_id": run_id, "agent_id": agent_id}
            if parent_run_id:
                metadata["parent_run_id"] = parent_run_id
            with trace_step(
                "agenthub.run",
                as_type="agent",
                conversation_id=conversation_id,
                metadata=metadata,
                abort_event=abort_event,
            ) as observation:
                outcome = await self._execute_run(
                    run_id=run_id,
                    abort_event=abort_event,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    parent_run_id=parent_run_id,
                    override_prompt=override_prompt,
                    observation=observation,
                )
                observation.status = outcome["status"]
                return outcome

        task = asyncio.create_task(execute())
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
        observation: Observation,
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
                return self._finalize_run(run_id, conversation_id, "failed", f"Agent not found: {agent_id}")
            if not ws_row:
                return self._finalize_run(run_id, conversation_id, "failed", f"Workspace not found: {conversation_id}")
            if not msg_row:
                return self._finalize_run(run_id, conversation_id, "failed", f"Trigger message not found: {trigger_message_id}")

            agent = row_to_agent(ag_row)
            workspace = row_to_workspace(ws_row)
            trigger_msg = row_to_message(msg_row)

            prompt = override_prompt if override_prompt is not None else extract_text_from_parts(trigger_msg["parts"])
            observation.input = prompt
            if agent.get("apiKey"):
                observation.secrets = (agent["apiKey"],)
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

            outcome: dict[str, Any] = {}
            if agent["isOrchestrator"] and not parent_run_id:
                await self._execute_orchestrator_run(
                    run_id=run_id,
                    abort_event=abort_event,
                    agent=agent,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    prompt=prompt,
                    attachments=attachments,
                    observation=observation,
                )
            else:
                outcome = await self._execute_simple_run(
                    run_id=run_id,
                    abort_event=abort_event,
                    agent=agent,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    prompt=prompt,
                    attachments=attachments,
                    parent_run_id=parent_run_id,
                    observation=observation,
                )

            status = "aborted" if abort_event.is_set() else "complete"
            return self._finalize_run(run_id, conversation_id, status, None, outcome)
        except Exception as exc:
            status = "aborted" if abort_event.is_set() else "failed"
            err_msg = None if status == "aborted" else str(exc)
            if err_msg:
                self._emit_error_message(conversation_id, agent_id, run_id, err_msg, parent_run_id)
            return self._finalize_run(run_id, conversation_id, status, err_msg)
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
        isolated: bool = False,
        delegate_task: DelegationHandler | None = None,
    ) -> AdapterInput:
        cwd = get_effective_cwd(workspace)
        summary = None if isolated else get_latest_context_summary(conversation_id)
        summary_block = render_conversation_summary_block(summary)
        pinned_block = "" if isolated else get_pinned_messages_block(conversation_id)

        sys_parts = [override_system_prompt or agent["systemPrompt"]]
        sys_parts.append(
            f"<workspace_info>\nmode={workspace['mode']}\ncwd={cwd}\n</workspace_info>"
        )
        if pinned_block:
            sys_parts.append(pinned_block)
        if summary_block:
            sys_parts.append(summary_block)

        history = [] if isolated else build_history_for(
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
            delegate_task=delegate_task,
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
        observation: Observation,
        override_system_prompt: str | None = None,
        parent_run_id: str | None = None,
        delegate_task: DelegationHandler | None = None,
    ) -> dict[str, Any]:
        tool_names = [
            name for name in agent["toolNames"] if name not in ("plan_tasks", "delegate_task")
        ]
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
            isolated=parent_run_id is not None,
            delegate_task=delegate_task,
        )
        return await self._consume_stream(
            adapter.stream(inp, abort_event), agent["id"], run_id, conversation_id, parent_run_id, observation
        )

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
        observation: Observation,
    ) -> None:
        with db_read() as conn:
            conv_row = conn.execute("SELECT agent_ids FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            other_ids = [aid for aid in json.loads(conv_row["agent_ids"] or "[]") if aid != agent["id"]] if conv_row else []
            other_agents = []
            if other_ids:
                ph = ",".join("?" for _ in other_ids)
                other_agents = [row_to_agent(r) for r in conn.execute(f"SELECT * FROM agents WHERE id IN ({ph})", other_ids).fetchall()]

        workers = {worker["id"]: worker for worker in other_agents if not worker["isOrchestrator"]}
        roster = "\n".join(
            f"- {worker['id']} ({worker['name']}): {worker['description']} | "
            f"capabilities={worker['capabilities']} | tools={worker['toolNames']}"
            for worker in workers.values()
        )
        system_prompt = (
            agent["systemPrompt"].replace("plan_tasks", "delegate_task")
            + "\n\nYou reply directly to the user. Use your available workspace and artifact tools "
            "to carry out the request yourself whenever you can, including when no workers are available."
        )
        if workers:
            system_prompt += (
                "\n\nWhen a worker can help, delegate one self-contained assignment at a time with delegate_task. "
                "Use each result and any approval feedback to decide what to do next. "
                "Provide artifactIds when a worker needs existing artifacts.\n\nAvailable workers:\n" + roster
            )

        async def delegate(args: dict[str, Any], message_id: str) -> ToolResult:
            try:
                request = DelegationRequest.model_validate(args)
            except ValidationError:
                return ToolResult(ok=False, error="delegate_task requires an agentId, a non-empty task and optional artifactIds.")
            if request.agentId not in workers:
                return ToolResult(ok=False, error=f"Worker is outside the available roster: {request.agentId}")
            for artifact_id in request.artifactIds:
                artifact = get_artifact(artifact_id)
                if not artifact or artifact["conversationId"] != conversation_id:
                    return ToolResult(ok=False, error=f"Artifact is unavailable in this conversation: {artifact_id}")

            child_prompt = request.task
            if request.artifactIds:
                child_prompt += "\n\nUse read_artifact for these references: " + ", ".join(request.artifactIds)

            def start_child() -> tuple[str, asyncio.Task[dict[str, Any]]]:
                child_run_id = self.start_run(
                    agent_id=request.agentId,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    parent_run_id=run_id,
                    override_prompt=child_prompt,
                )
                return child_run_id, self._tasks[child_run_id]

            state = await delegation_graph.ainvoke(
                {},
                context=DelegationContext(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    agent_id=agent["id"],
                    message_id=message_id,
                    request=request,
                    abort_event=abort_event,
                    start_child=start_child,
                ),
            )
            return ToolResult(ok=True, value=state["result"])

        await self._execute_simple_run(
            run_id=run_id,
            abort_event=abort_event,
            agent=agent,
            workspace=workspace,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            prompt=prompt,
            attachments=attachments,
            observation=observation,
            override_system_prompt=system_prompt,
            delegate_task=delegate if workers else None,
        )

    async def _consume_stream(
        self, stream, agent_id: str, run_id: str, conversation_id: str,
        parent_run_id: str | None, observation: Observation,
    ) -> dict[str, Any]:
        parts_by_msg: dict[str, list[dict[str, Any]]] = {}
        artifact_ids: list[str] = []
        summary = ""
        delegation_calls: set[tuple[str, str]] = set()
        current_msg_id: str | None = None

        async for ev in stream:
            etype = ev.get("type")
            if etype == "message.start":
                if parent_run_id:
                    ev = {**ev, "parentRunId": parent_run_id}
                mid = ev["messageId"]
                current_msg_id = mid
                parts_by_msg[mid] = []
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
                if ev["toolName"] == "delegate_task":
                    delegation_calls.add((mid, ev["callId"]))
                    self._save_streaming_parts(mid, parts)

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

                if (mid, ev["callId"]) in delegation_calls:
                    self._save_streaming_parts(mid, parts)

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
                if parent_run_id or observation.capture_content:
                    text = extract_text_from_parts(parts)
                    if text and observation.capture_content:
                        observation.output = text
                    if parent_run_id:
                        summary = text[:4000] or summary
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

        return {"artifactIds": artifact_ids, "summary": summary}

    def _save_streaming_parts(self, message_id: str, parts: list[dict[str, Any]]) -> None:
        with db_tx() as conn:
            conn.execute(
                "UPDATE messages SET parts = ? WHERE id = ?",
                (json.dumps(parts, ensure_ascii=False), message_id),
            )

    def _emit_error_message(
        self, conversation_id: str, agent_id: str, run_id: str, error: str, parent_run_id: str | None
    ) -> None:
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
            "parentRunId": parent_run_id,
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
        status: str,
        error: str | None,
        outcome: dict[str, Any] | None = None,
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
        return {"status": status, "error": error, **(outcome or {})}


agent_runner = AgentRunner()
