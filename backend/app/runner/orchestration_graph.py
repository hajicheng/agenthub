from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.observability import trace_step
from app.services.event_bus import event_bus
from app.services.pending_stores import pending_dispatch_plans


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DelegationRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    agentId: NonEmptyText
    task: NonEmptyText
    artifactIds: list[NonEmptyText] = Field(default_factory=list)


class DelegationState(TypedDict, total=False):
    approved: bool
    result: dict[str, Any]


@dataclass(frozen=True)
class DelegationContext:
    run_id: str
    conversation_id: str
    agent_id: str
    message_id: str
    request: DelegationRequest
    abort_event: asyncio.Event
    start_child: Callable[[], tuple[str, asyncio.Task[dict[str, Any]]]]

    def publish(self, event_type: str, **fields: Any) -> None:
        event_bus.publish(
            {
                "type": event_type,
                "conversationId": self.conversation_id,
                "timestamp": int(time.time() * 1000),
                "messageId": self.message_id,
                **fields,
            }
        )

    def plan(self) -> list[dict[str, Any]]:
        return [{"id": self.message_id, "agentId": self.request.agentId, "task": self.request.task}]


async def review(_state: DelegationState, runtime: Runtime[DelegationContext]) -> DelegationState:
    ctx = runtime.context
    if ctx.abort_event.is_set():
        return {"result": {"status": "aborted"}}
    pending, decision = pending_dispatch_plans.register(
        conversation_id=ctx.conversation_id,
        agent_id=ctx.agent_id,
        run_id=ctx.run_id,
        message_id=ctx.message_id,
        plan=ctx.plan(),
    )
    with trace_step("delegation.review", input=ctx.request.model_dump(), abort_event=ctx.abort_event) as observation:
        abort_wait = asyncio.create_task(ctx.abort_event.wait())
        try:
            await asyncio.wait((decision, abort_wait), return_when=asyncio.FIRST_COMPLETED)
            if ctx.abort_event.is_set():
                return {"result": {"status": "aborted"}}
            response = decision.result()
            observation.output = response
            if response["decision"] == "approve":
                observation.status = "approved"
                return {"approved": True}
            if response["decision"] == "revise":
                observation.status = "revision_requested"
                return {"result": {"status": "revision_requested", "feedback": response["feedback"]}}
            observation.status = "rejected"
            return {"result": {"status": "rejected"}}
        finally:
            pending_dispatch_plans.reject(pending["id"])
            abort_wait.cancel()
            await asyncio.gather(abort_wait, return_exceptions=True)


def after_review(state: DelegationState) -> Literal["execute", "__end__"]:
    return "execute" if state.get("approved") else END


async def execute(_state: DelegationState, runtime: Runtime[DelegationContext]) -> DelegationState:
    ctx = runtime.context
    if ctx.abort_event.is_set():
        return {"result": {"status": "aborted"}}

    ctx.publish("dispatch.plan", runId=ctx.run_id, plan=ctx.plan())
    child_run_id, child_task = ctx.start_child()
    fields = {"parentRunId": ctx.run_id, "childRunId": child_run_id, "taskId": ctx.message_id}
    ctx.publish("dispatch.start", **fields, agentId=ctx.request.agentId)
    result = await child_task
    ctx.publish(
        "dispatch.end",
        **fields,
        status=result["status"],
        **({"error": result["error"]} if result.get("error") else {}),
    )
    return {"result": {**result, "childRunId": child_run_id}}


delegation_graph = (
    StateGraph(DelegationState, context_schema=DelegationContext)
    .add_node("review", review)
    .add_node("execute", execute)
    .add_edge(START, "review")
    .add_conditional_edges("review", after_review)
    .add_edge("execute", END)
    .compile()
)
