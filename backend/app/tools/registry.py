from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.ids import new_deployment_id
from app.observability import trace_step
from app.services.artifact_content import build_artifact_content, describe_artifact_content_error
from app.services.artifact_service import create_artifact, get_artifact
from app.services.attachment_service import get_attachment, get_attachment_absolute_path
from app.services.conversation_service import get_conversation
from app.services.deployment_service import (
    create_local_static_deployment,
    create_workspace_static_deployment,
)
from app.services.fs_service import (
    get_workspace_for_conversation,
    list_dir_in_workspace,
    read_file_in_workspace,
    read_if_exists,
    write_file_in_workspace,
)
from app.services.pending_stores import pending_questions, pending_writes
from app.services.platform import IS_WINDOWS
from app.services.security import find_banned_pattern
from app.services.workspace_utils import assert_path_within_workspace, get_effective_cwd
from app.tools.types import ToolContext, ToolDefinition, ToolResult

MAX_BASH_OUTPUT_CHARS = 16_000


async def _write_artifact_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    art_type = str(args.get("type") or "").strip()
    title = str(args.get("title") or "").strip()
    raw_content = args.get("content")
    parent_id = args.get("parentArtifactId")

    if art_type not in ("web_app", "document", "image", "ppt", "diagram"):
        return ToolResult(ok=False, error=f"Unsupported artifact type: {art_type}")
    if not title:
        return ToolResult(ok=False, error="title is required")

    content = build_artifact_content(art_type, raw_content)
    if not content:
        err = describe_artifact_content_error(art_type, raw_content)
        return ToolResult(ok=False, error=err or f"Invalid content for {art_type}")

    version = 1
    if parent_id:
        parent = get_artifact(str(parent_id))
        if parent and parent["conversationId"] == ctx.conversation_id:
            version = parent["version"] + 1
        else:
            parent_id = None

    art = create_artifact(
        conversation_id=ctx.conversation_id,
        art_type=art_type,
        title=title,
        content=content,
        created_by_agent_id=ctx.agent_id,
        parent_artifact_id=parent_id,
        version=version,
    )
    res: dict[str, Any] = {
        "artifactId": art["id"],
        "type": art["type"],
        "title": art["title"],
        "version": art["version"],
    }
    if args.get("outputKey"):
        res["outputKey"] = args["outputKey"]
    return ToolResult(ok=True, value=res)


async def _read_artifact_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    art_id = str(args.get("artifactId") or "").strip()
    if not art_id:
        return ToolResult(ok=False, error="artifactId is required")
    art = get_artifact(art_id)
    if not art or art["conversationId"] != ctx.conversation_id:
        return ToolResult(ok=False, error=f"Artifact not found: {art_id}")
    return ToolResult(ok=True, value=art)


async def _deploy_artifact_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    art_id = str(args.get("artifactId") or "").strip()
    if not art_id:
        return ToolResult(ok=False, error="artifactId is required")
    art = get_artifact(art_id)
    if not art or art["conversationId"] != ctx.conversation_id:
        return ToolResult(ok=False, error=f"Artifact not found: {art_id}")
    if art["type"] != "web_app":
        return ToolResult(ok=False, error="Only web_app artifacts can be deployed")
    dep = create_local_static_deployment(
        dep_id=new_deployment_id(),
        artifact_id=art["id"],
        title=art["title"],
        version=art["version"],
        content=art["content"],
    )
    return ToolResult(ok=True, value=dep)


async def _deploy_workspace_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    ws = get_workspace_for_conversation(ctx.conversation_id)
    if not ws:
        return ToolResult(ok=False, error="Workspace not found")
    rel_dir = str(args.get("path") or "dist").strip()
    title = str(args.get("title") or rel_dir).strip()
    entry = args.get("entry")
    try:
        abs_dir = assert_path_within_workspace(ws, rel_dir)
        dep = create_workspace_static_deployment(
            dep_id=new_deployment_id(),
            title=title,
            source_dir=abs_dir,
            workspace_path=rel_dir,
            entry=str(entry) if entry else None,
        )
        return ToolResult(ok=True, value=dep)
    except Exception as exc:
        return ToolResult(ok=False, error=str(exc))


async def _read_attachment_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    att_id = str(args.get("attachmentId") or "").strip()
    if not att_id:
        return ToolResult(ok=False, error="attachmentId is required")
    att = get_attachment(att_id)
    if not att or att["conversationId"] != ctx.conversation_id:
        return ToolResult(ok=False, error=f"Attachment not found: {att_id}")
    abs_path = get_attachment_absolute_path(att_id)
    if not abs_path:
        return ToolResult(ok=False, error="Attachment file missing on disk")
    if att["mimeType"] == "application/pdf":
        try:
            reader = PdfReader(abs_path)
            text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
            return ToolResult(ok=True, value={"fileName": att["fileName"], "content": text[:50_000]})
        except Exception as exc:
            return ToolResult(ok=False, error=f"Failed to parse PDF: {exc}")
    if att["kind"] == "image":
        return ToolResult(
            ok=True,
            value={"fileName": att["fileName"], "mimeType": att["mimeType"], "note": "Image attachment"},
        )
    try:
        text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        return ToolResult(ok=True, value={"fileName": att["fileName"], "content": text[:50_000]})
    except Exception as exc:
        return ToolResult(ok=False, error=str(exc))


async def _fs_list_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    ws = get_workspace_for_conversation(ctx.conversation_id)
    if not ws:
        return ToolResult(ok=False, error="Workspace not found")
    target = str(args.get("path") or "")
    try:
        return ToolResult(ok=True, value=list_dir_in_workspace(ws, target))
    except Exception as exc:
        return ToolResult(ok=False, error=str(exc))


async def _fs_read_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    ws = get_workspace_for_conversation(ctx.conversation_id)
    if not ws:
        return ToolResult(ok=False, error="Workspace not found")
    target = str(args.get("path") or "").strip()
    if not target:
        return ToolResult(ok=False, error="path is required")
    try:
        return ToolResult(ok=True, value=read_file_in_workspace(ws, target))
    except Exception as exc:
        return ToolResult(ok=False, error=str(exc))


async def _fs_write_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    ws = get_workspace_for_conversation(ctx.conversation_id)
    if not ws:
        return ToolResult(ok=False, error="Workspace not found")
    target = str(args.get("path") or "").strip()
    content = args.get("content")
    if not target or not isinstance(content, str):
        return ToolResult(ok=False, error="path and content are required")
    try:
        abs_path = assert_path_within_workspace(ws, target)
    except Exception as exc:
        return ToolResult(ok=False, error=str(exc))

    conv = get_conversation(ctx.conversation_id)
    approval_mode = (conv or {}).get("fsWriteApprovalMode", "review")
    if approval_mode == "review":
        old_content = read_if_exists(ws, target)
        _, fut = pending_writes.register(
            conversation_id=ctx.conversation_id,
            agent_id=ctx.agent_id,
            run_id=ctx.run_id,
            path=target,
            absolute_path=abs_path,
            old_content=old_content,
            new_content=content,
        )
        approved = await fut
        if not approved:
            return ToolResult(ok=False, error="User rejected the file write")

    try:
        res = write_file_in_workspace(ws, target, content)
        return ToolResult(ok=True, value=res)
    except Exception as exc:
        return ToolResult(ok=False, error=str(exc))


async def _bash_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolResult(ok=False, error="command is required")
    banned = find_banned_pattern(command)
    if banned is not None:
        return ToolResult(ok=False, error=f"Command blocked by security policy: {banned.pattern}")

    ws = get_workspace_for_conversation(ctx.conversation_id)
    if not ws:
        return ToolResult(ok=False, error="Workspace not found")
    cwd = get_effective_cwd(ws)
    timeout_s = min(max(int(args.get("timeoutMs") or 30_000) / 1000.0, 1.0), 300.0)
    child_env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"}
    }

    try:
        if IS_WINDOWS:
            proc = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
                cwd=cwd,
                env=child_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                env=child_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(ok=False, error=f"Command timed out after {int(timeout_s)}s")
        out_str = stdout.decode("utf-8", errors="replace")[:MAX_BASH_OUTPUT_CHARS]
        err_str = stderr.decode("utf-8", errors="replace")[:MAX_BASH_OUTPUT_CHARS]
        return ToolResult(
            ok=proc.returncode == 0,
            value={"exitCode": proc.returncode, "stdout": out_str, "stderr": err_str, "cwd": cwd},
            error=None if proc.returncode == 0 else (err_str or f"Exited with code {proc.returncode}"),
        )
    except Exception as exc:
        return ToolResult(ok=False, error=str(exc))


async def _delegate_task_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    delegate = ctx.delegate_task
    if delegate is None or ctx.message_id is None:
        return ToolResult(ok=False, error="Only the lead can delegate, once per model response.")
    ctx.delegate_task = None
    return await delegate(args, ctx.message_id)


async def _ask_user_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    questions = args.get("questions")
    if not isinstance(questions, list) or not questions:
        return ToolResult(ok=False, error="questions must be a non-empty list")
    _, fut = pending_questions.register(
        conversation_id=ctx.conversation_id,
        agent_id=ctx.agent_id,
        run_id=ctx.run_id,
        questions=questions,
    )
    answers = await fut
    if answers is None:
        return ToolResult(ok=False, error="User cancelled the question")
    return ToolResult(ok=True, value={"answers": answers})


async def _report_task_result_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    status = args.get("status")
    summary = args.get("summary")
    if status not in ("complete", "failed", "blocked") or not isinstance(summary, str):
        return ToolResult(ok=False, error="status (complete|failed|blocked) and summary are required")
    return ToolResult(ok=True, value={"report": args})


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        defs = [
            ToolDefinition(
                name="write_artifact",
                description="Create a previewable artifact (web_app, document, image, ppt, diagram) in the current conversation.",
                parameters={
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["web_app", "document", "image", "ppt", "diagram"]},
                        "title": {"type": "string"},
                        "content": {},
                        "parentArtifactId": {"type": "string"},
                        "outputKey": {"type": "string"},
                    },
                    "required": ["type", "title", "content"],
                },
                handler=_write_artifact_handler,
            ),
            ToolDefinition(
                name="read_artifact",
                description="Read an existing artifact in the current conversation by artifactId.",
                parameters={
                    "type": "object",
                    "properties": {"artifactId": {"type": "string"}},
                    "required": ["artifactId"],
                },
                handler=_read_artifact_handler,
            ),
            ToolDefinition(
                name="deploy_artifact",
                description="Deploy a web_app artifact to a local static preview path.",
                parameters={
                    "type": "object",
                    "properties": {"artifactId": {"type": "string"}},
                    "required": ["artifactId"],
                },
                handler=_deploy_artifact_handler,
            ),
            ToolDefinition(
                name="deploy_workspace",
                description="Deploy a built static directory (e.g. dist, build, out) from the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "title": {"type": "string"},
                        "entry": {"type": "string"},
                    },
                    "required": ["path"],
                },
                handler=_deploy_workspace_handler,
            ),
            ToolDefinition(
                name="read_attachment",
                description="Read a user-uploaded attachment in the current conversation by attachmentId.",
                parameters={
                    "type": "object",
                    "properties": {"attachmentId": {"type": "string"}},
                    "required": ["attachmentId"],
                },
                handler=_read_attachment_handler,
            ),
            ToolDefinition(
                name="fs_list",
                description="List files and directories in the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
                handler=_fs_list_handler,
            ),
            ToolDefinition(
                name="fs_read",
                description="Read a file inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                handler=_fs_read_handler,
            ),
            ToolDefinition(
                name="fs_write",
                description="Write a file inside the workspace (subject to user approval in review mode).",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                handler=_fs_write_handler,
            ),
            ToolDefinition(
                name="bash",
                description="Run a shell command inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeoutMs": {"type": "integer"},
                    },
                    "required": ["command"],
                },
                handler=_bash_handler,
            ),
            ToolDefinition(
                name="delegate_task",
                description="Request approval and run one worker task. Wait for its result before deciding the next assignment.",
                parameters={
                    "type": "object",
                    "properties": {
                        "agentId": {"type": "string", "description": "ID of an available worker in this conversation"},
                        "task": {"type": "string", "description": "Self-contained assignment, including expected outcomes"},
                        "artifactIds": {"type": "array", "items": {"type": "string"}, "description": "Conversation artifacts the worker should use"},
                    },
                    "required": ["agentId", "task"],
                    "additionalProperties": False,
                },
                handler=_delegate_task_handler,
            ),
            ToolDefinition(
                name="ask_user",
                description="Ask the user structured multiple-choice questions.",
                parameters={
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "question": {"type": "string"},
                                    "header": {"type": "string"},
                                    "options": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": {"type": "string"},
                                                "description": {"type": "string"},
                                            },
                                            "required": ["label"],
                                        },
                                    },
                                    "multiSelect": {"type": "boolean"},
                                },
                                "required": ["question", "header", "options"],
                            },
                        }
                    },
                    "required": ["questions"],
                },
                handler=_ask_user_handler,
            ),
            ToolDefinition(
                name="report_task_result",
                description="Report the final structured result of a dispatched task.",
                parameters={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["complete", "failed", "blocked"]},
                        "summary": {"type": "string"},
                    },
                    "required": ["status", "summary"],
                },
                handler=_report_task_result_handler,
            ),
        ]
        for d in defs:
            self._tools[d.name] = d

    def resolve(self, names: list[str], *, can_delegate: bool = False) -> list[ToolDefinition]:
        tools = [self._tools[name] for name in names if name in self._tools and name != "delegate_task"]
        if can_delegate:
            tools.append(self._tools["delegate_task"])
        return tools

    async def execute(self, name: str, args: Any, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(ok=False, error=f"Unknown tool: {name}")
        if not isinstance(args, dict):
            args = {}
        with trace_step(
            f"tool.{tool.name}", as_type="tool", input=args, abort_event=ctx.abort_event,
        ) as observation:
            try:
                result = await tool.handler(args, ctx)
            except Exception as exc:
                observation.fail(exc)
                observation.output = {"error": type(exc).__name__}
                return ToolResult(ok=False, error=str(exc))
            if not result.ok:
                observation.status = "failed"
            observation.output = result.value if result.ok else {"error": result.error}
            return result


tool_registry = ToolRegistry()
