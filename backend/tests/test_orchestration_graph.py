from __future__ import annotations

import asyncio
import json
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai.types import CompletionUsage

from app.adapters import custom_adapter
from app.adapters.registry import adapter_registry
from app.db.bootstrap import bootstrap_database
from app.db.client import db_tx
from app.ids import new_message_id
from app.main import app
from app.services.event_bus import event_bus
from app.services.fs_service import get_workspace_for_conversation


def assignment(agent_id, task):
    return {"agentId": agent_id, "task": task}


async def next_event(events, event_type, **fields):
    async with asyncio.timeout(5):
        while True:
            event = (await events.get()).event
            if event["type"] == event_type and all(event.get(key) == value for key, value in fields.items()):
                return event


class Model:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        response = next(self.responses)
        calls = [
            SimpleNamespace(
                index=index,
                id=f"call-{len(self.requests)}-{index}",
                function=SimpleNamespace(
                    name=args.get("toolName", "delegate_task"),
                    arguments=json.dumps(args.get("args", args)),
                ),
            )
            for index, args in enumerate(response if isinstance(response, list) else [])
        ]
        delta = SimpleNamespace(tool_calls=calls, content=response if isinstance(response, str) else None)

        async def stream():
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=delta, finish_reason="tool_calls" if calls else "stop")],
                model="trace-test-model",
                usage=CompletionUsage(
                    prompt_tokens=100, completion_tokens=20, total_tokens=120,
                    prompt_tokens_details={"cached_tokens": 25},
                ),
            )

        return stream()

    def tool_results(self, request_index):
        return [
            json.loads(message["content"])
            for message in self.requests[request_index]["messages"]
            if message["role"] == "tool"
        ]


class Worker:
    def __init__(self, execute=None):
        self.inputs = []
        self.execute = execute

    async def stream(self, inp, abort_event):
        self.inputs.append(inp)
        fields = {
            "conversationId": inp.conversation_id,
            "timestamp": int(time.time() * 1000),
            "messageId": new_message_id(),
        }
        yield {"type": "message.start", **fields, "agentId": inp.agent_id, "runId": inp.run_id}
        if self.execute:
            await self.execute(abort_event)
        yield {
            "type": "part.start", **fields, "partIndex": 0,
            "part": {"type": "thinking", "content": "private intermediate context"},
        }
        yield {
            "type": "part.start", **fields, "partIndex": 1,
            "part": {"type": "text", "content": f"Result for {inp.prompt}"},
        }
        yield {"type": "message.end", **fields}


def install_adapters(monkeypatch, responses, execute=None):
    model = Model(responses)
    worker = Worker(execute)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-value")
    monkeypatch.setattr(custom_adapter, "AsyncOpenAI", lambda **_: model)
    monkeypatch.setattr(
        adapter_registry, "get_adapter",
        lambda agent: custom_adapter.CustomAgentAdapter() if agent["isOrchestrator"] else worker,
    )
    return model, worker


@pytest.fixture
async def group(monkeypatch):
    base_tmp = Path(__file__).resolve().parent.parent / ".pytest_tmp"
    base_tmp.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base_tmp) as data_dir:
        monkeypatch.setenv("AGENTHUB_DATA_DIR", data_dir)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/conversations",
                json={"mode": "group", "agentIds": ["ag_orchestrator", "ag_pm", "ag_reviewer"]},
            )
            assert response.status_code == 201
            conversation_id = response.json()["conversation"]["id"]
            events, unsubscribe = event_bus.subscribe()
            try:
                yield client, f"/api/conversations/{conversation_id}", events
            finally:
                unsubscribe()


async def send(client, url):
    response = await client.post(
        f"{url}/messages", json={"content": "Review my project", "mentionedAgentIds": ["ag_pm"]},
    )
    assert response.status_code == 202
    return response.json()


async def review(client, url, pending, action, feedback=None):
    body = {"action": action}
    if feedback:
        body["feedback"] = feedback
    response = await client.post(f"{url}/pending-dispatch-plans/{pending['id']}", json=body)
    assert response.status_code == 200


@pytest.mark.parametrize(("mode", "legacy_tool"), [
    ("single", "plan_tasks"),
    ("single", "delegate_task"),
    ("group", None),
])
@pytest.mark.asyncio
async def test_lead_can_use_its_workspace_tools_in_review_mode(group, monkeypatch, mode, legacy_tool):
    client, url, events = group
    if legacy_tool:
        with db_tx() as conn:
            conn.execute(
                "UPDATE agents SET tool_names = ? WHERE id = 'ag_orchestrator'",
                (json.dumps([legacy_tool, "ask_user", "fs_list", "fs_read", "read_attachment", "read_artifact"]),),
            )
            bootstrap_database(conn)
    if mode == "single":
        created = await client.post(
            "/api/conversations", json={"mode": "single", "agentIds": ["ag_orchestrator"]},
        )
        assert created.status_code == 201
        url = f"/api/conversations/{created.json()['conversation']['id']}"
    model, worker = install_adapters(monkeypatch, [
        [{"toolName": "fs_write", "args": {"path": "demo.html", "content": "<h1>Demo</h1>"}}],
        "Created demo.html",
    ])
    run_id = (await send(client, url))["runIds"][0]
    pending = (await next_event(events, "fs_write.pending"))["pendingWrite"]
    workspace = get_workspace_for_conversation(pending["conversationId"])
    target = Path(workspace["rootPath"]) / "demo.html"
    assert pending["runId"] == run_id
    assert not target.exists()
    approved = await client.post(f"{url}/pending-writes/{pending['id']}", json={"action": "approve"})
    assert approved.status_code == 200
    assert (await next_event(events, "run.end", runId=run_id))["status"] == "complete"
    assert target.read_text(encoding="utf-8") == "<h1>Demo</h1>"
    assert worker.inputs == []
    names = {tool["function"]["name"] for tool in model.requests[0]["tools"]}
    assert {"fs_write", "bash", "write_artifact", "deploy_workspace", "deploy_artifact"} <= names
    assert ("delegate_task" in names) == (mode == "group")


@pytest.mark.asyncio
async def test_lead_keeps_custom_tool_selection_when_delegation_is_available(group, monkeypatch):
    client, url, events = group
    with db_tx() as conn:
        conn.execute(
            "UPDATE agents SET tool_names = ? WHERE id = 'ag_orchestrator'",
            (json.dumps(["fs_list"]),),
        )
        bootstrap_database(conn)
    model, _ = install_adapters(monkeypatch, ["Done"])
    run_id = (await send(client, url))["runIds"][0]
    assert (await next_event(events, "run.end", runId=run_id))["status"] == "complete"
    assert {tool["function"]["name"] for tool in model.requests[0]["tools"]} == {"fs_list", "delegate_task"}


@pytest.mark.asyncio
async def test_lead_receives_each_result_before_it_can_delegate_again(group, monkeypatch, exported_spans):
    client, url, events = group
    model, worker = install_adapters(monkeypatch, [
        [assignment("ag_pm", "Inspect"), assignment("ag_reviewer", "Premature review")],
        [assignment("ag_reviewer", "Review the inspection")],
        "Final answer",
        "New turn",
    ])
    sent = await send(client, url)
    run_id, = sent["runIds"]
    root = await next_event(events, "run.start", runId=run_id)
    assert root["agentId"] == "ag_orchestrator"
    first = (await next_event(events, "dispatch.plan.pending"))["pendingPlan"]
    assert len(first["plan"]) == 1
    assert worker.inputs == []
    snapshot = (await client.get(f"{url}/messages")).json()["messages"]
    origin = next(message for message in snapshot if message["id"] == first["messageId"])
    assert origin["parts"][0]["toolName"] == "delegate_task"

    await review(client, url, first, "approve")
    first_end = await next_event(events, "dispatch.end", parentRunId=run_id)
    assert first_end["status"] == "complete"
    second = (await next_event(events, "dispatch.plan.pending"))["pendingPlan"]
    assert first["messageId"] != second["messageId"]
    assert [inp.prompt for inp in worker.inputs] == ["Inspect"]
    result, denied = model.tool_results(1)
    assert result["summary"] == "Result for Inspect"
    assert "once per model response" in denied["error"]

    await review(client, url, second, "approve")
    await next_event(events, "dispatch.end", parentRunId=run_id, messageId=second["messageId"])
    assert (await next_event(events, "run.end", runId=run_id))["status"] == "complete"
    assert [inp.prompt for inp in worker.inputs] == ["Inspect", "Review the inspection"]
    assert all(inp.history == [] and "delegate_task" not in inp.tool_names for inp in worker.inputs)
    assert "private intermediate context" not in str(model.requests)
    assert model.tool_results(2)[-1]["summary"] == "Result for Review the inspection"

    snapshot = (await client.get(f"{url}/messages")).json()["messages"]
    first_message = next(message for message in snapshot if message["id"] == first["messageId"])
    outcome = next(part["result"] for part in first_message["parts"] if part["type"] == "tool_result")
    assert outcome["childRunId"] == first_end["childRunId"]
    assert any(
        message.get("parentRunId") == run_id and message["runId"] == outcome["childRunId"]
        for message in snapshot
    )

    sent = await send(client, url)
    assert (await next_event(events, "run.end", runId=sent["runIds"][0]))["status"] == "complete"
    assert "Result for Inspect" not in str(model.requests[3]["messages"])

    spans = exported_spans()
    runs = {
        span.attributes["langfuse.observation.metadata.run_id"]: span
        for span in spans if span.name == "agenthub.run"
    }
    lead = runs[run_id]
    worker = runs[first_end["childRunId"]]
    assert lead.context.trace_id == worker.context.trace_id
    assert lead.context.trace_id != runs[sent["runIds"][0]].context.trace_id
    lead_flow = [span for span in spans if span.context.trace_id == lead.context.trace_id]
    assert all(span.attributes["session.id"] == url.rsplit("/", 1)[-1] for span in spans)
    assert {span.name for span in lead_flow} == {
        "agenthub.run", "tool.delegate_task", "delegation.review", "chat.completions",
    }
    tool = next(span for span in lead_flow if span.context.span_id == worker.parent.span_id)
    assert tool.name == "tool.delegate_task" and tool.parent.span_id == lead.context.span_id
    generation = min((span for span in lead_flow if span.name == "chat.completions"), key=lambda span: span.start_time)
    assert generation.parent.span_id == lead.context.span_id
    assert json.loads(generation.attributes["langfuse.observation.usage_details"]) == {
        "input": 75, "output": 20, "input_cached_tokens": 25,
    }
    assert lead.attributes["langfuse.observation.input"] == "Review my project"
    assert lead.attributes["langfuse.observation.output"] == "Final answer"
    for request in model.requests:
        assert "Current local time:" in request["messages"][0]["content"]
    assert "Review my project" in generation.attributes["langfuse.observation.input"]
    assert "delegate_task" in generation.attributes["langfuse.observation.output"]
    assert "Inspect" in tool.attributes["langfuse.observation.input"]
    assert "Result for Inspect" in tool.attributes["langfuse.observation.output"]


@pytest.mark.asyncio
async def test_revision_and_rejection_return_to_lead_without_starting_a_worker(group, monkeypatch):
    client, url, events = group
    model, worker = install_adapters(monkeypatch, [
        [assignment("ag_pm", "Original")],
        [assignment("ag_reviewer", "Revised")],
        "Reply after rejection",
    ])
    run_id = (await send(client, url))["runIds"][0]
    old = (await next_event(events, "dispatch.plan.pending"))["pendingPlan"]
    await review(client, url, old, "revise", "Change the assignment")
    new = (await next_event(events, "dispatch.plan.pending"))["pendingPlan"]
    assert new["runId"] == run_id
    assert model.tool_results(1)[0] == {"status": "revision_requested", "feedback": "Change the assignment"}
    await review(client, url, new, "reject")
    assert (await next_event(events, "run.end", runId=run_id))["status"] == "complete"
    assert model.tool_results(2)[-1] == {"status": "rejected"}
    assert worker.inputs == []


@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.asyncio
async def test_abort_cleans_up_review_or_stops_running_child(group, monkeypatch, approved, exported_spans):
    client, url, events = group
    worker_started = asyncio.Event()

    async def execute(abort_event):
        worker_started.set()
        await asyncio.wait_for(abort_event.wait(), timeout=5)

    _, worker = install_adapters(monkeypatch, [[assignment("ag_pm", "Run")]], execute)
    run_id = (await send(client, url))["runIds"][0]
    pending = (await next_event(events, "dispatch.plan.pending"))["pendingPlan"]
    if approved:
        await review(client, url, pending, "approve")
        await asyncio.wait_for(worker_started.wait(), timeout=5)
    assert (await client.post(f"/api/runs/{run_id}/abort")).status_code == 200
    if approved:
        assert (await next_event(events, "dispatch.end", parentRunId=run_id))["status"] == "aborted"
    assert (await next_event(events, "run.end", runId=run_id))["status"] == "aborted"
    assert len(worker.inputs) == int(approved)
    assert (await client.get(f"{url}/pending-dispatch-plans")).json()["pendingDispatchPlans"] == []
    runs = [span for span in exported_spans() if span.name == "agenthub.run"]
    assert len(runs) == 1 + int(approved)
    assert all(span.attributes["langfuse.observation.status_message"] == "aborted" for span in runs)


@pytest.mark.asyncio
async def test_worker_outside_roster_is_rejected_without_review(group, monkeypatch):
    client, url, events = group
    model, worker = install_adapters(monkeypatch, [[assignment("ag_frontend", "Outside")], "No worker"])
    run_id = (await send(client, url))["runIds"][0]
    assert (await next_event(events, "run.end", runId=run_id))["status"] == "complete"
    assert "outside the available roster" in model.tool_results(1)[0]["error"]
    assert (await client.get(f"{url}/pending-dispatch-plans")).json()["pendingDispatchPlans"] == []
    assert worker.inputs == []


@pytest.mark.asyncio
async def test_regeneration_ignores_saved_legacy_worker_mentions(group, monkeypatch):
    client, url, events = group
    _, worker = install_adapters(monkeypatch, ["Initial reply", "Regenerated reply", "Edited reply"])
    sent = await send(client, url)
    await next_event(events, "run.end", runId=sent["runIds"][0])
    with db_tx() as conn:
        conn.execute(
            "UPDATE messages SET mentioned_agent_ids = ? WHERE id = ?", ('["ag_pm"]', sent["messageId"]),
        )
    response = await client.post(f"{url}/regenerate")
    assert response.status_code == 200
    new_run_id, = response.json()["runIds"]
    assert (await next_event(events, "run.start", runId=new_run_id))["agentId"] == "ag_orchestrator"
    assert (await next_event(events, "run.end", runId=new_run_id))["status"] == "complete"
    edited = await client.post(
        f"/api/messages/{sent['messageId']}/edit",
        json={"conversationId": url.rsplit("/", 1)[-1], "content": "Updated request"},
    )
    assert edited.status_code == 200
    edited_run_id, = edited.json()["runIds"]
    assert (await next_event(events, "run.start", runId=edited_run_id))["agentId"] == "ag_orchestrator"
    assert (await next_event(events, "run.end", runId=edited_run_id))["status"] == "complete"
    assert worker.inputs == []
