## Why

FastAPI currently sequences the Orchestrator stages inside AgentRunner. LangGraph will make the workflow transitions and parallel dispatch explicit while continuing to use the existing AgentRunner lifecycle.

## What Changes

- Run planning, plan review, dependency-ordered dispatch, and aggregation through a Python LangGraph StateGraph.
- Connect the existing plan review endpoints and events to the workflow so approval gates execution and revision returns to planning.
- Keep the existing adapter, run, and SSE interfaces.

## Capabilities

### New Capabilities

### Modified Capabilities

## Impact

The change affects the FastAPI runner, plan validation, and Python dependencies. The workflow follows the existing Orchestrator and plan review specifications.
