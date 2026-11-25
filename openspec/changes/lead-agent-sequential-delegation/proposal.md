## Why

Group messages currently let an explicit mention start a worker directly. The Orchestrator also commits to a batch before seeing any result. Sequential delegation gives the lead control over each assignment and lets the next decision use the result of the previous assignment.

## What Changes

- Route group sends, edits, and regeneration to the group's lead agent.
- Give the lead a `delegate_task` tool. Run one approved delegation at a time and return its result to the lead's model before it chooses another task.
- Replace mention-based addressing with task cards inside the lead's chat. Keep legacy message text and metadata readable.

## Capabilities

### New Capabilities

### Modified Capabilities

- `orchestrator` owns sequential delegation and group routing.
- `frontend` presents lead replies and delegated task cards.

## Impact

FastAPI routing, the LangGraph execution graph, adapter tool calls, and chat rendering change. The existing task review endpoints, message storage, and workspace tool approvals support the new flow.
