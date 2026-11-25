## Context

The active backend is FastAPI. AgentRunner owns run IDs, adapter calls, persistence and cancellation. The plan review API and SSE events already share an in-memory pending store.

## Goals

Give planning, review, dispatch and aggregation explicit transitions. Keep the graph inside the existing Orchestrator run.

## Decisions

- Compile one Python LangGraph StateGraph and supply per-run dependencies through runtime context.
- Route revision feedback back to planning and wait on the existing pending review store. Keep the same parent run through revisions.
- Compile the validated plan into dependency waves and fan out each wave with LangGraph Send. Merge outcomes by task ID and use the merged statuses to skip blocked descendants.
- Leave run records and the event bus as the sources for persisted history and live updates. Pending review state has process lifetime.

## Risks / Trade-offs

- Parent abort can race with review approval or worker startup. Check the abort signal at graph transitions, clear pending review on abort, and retain AgentRunner's existing child cancellation.
- Workers may finish in a different order each time. Build the aggregation prompt in plan order.
