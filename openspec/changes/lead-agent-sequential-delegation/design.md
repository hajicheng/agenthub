## Context

The current graph executes a reviewed batch and calls the adapter again to summarize it. Both supported tool-loop adapters already feed each tool result back into the model.

## Goals

- Make the lead the sole group reply entry point and the only authority for child assignments.
- Let the lead decide the next task from each completed delegation.
- Show task review, progress, and results in the lead's message.

## Decisions

- Expose a run-scoped `delegate_task` tool only on the lead. It invokes a LangGraph review and execution graph. The adapter returns the tool result to the same model loop. Allow one delegation per model response and only one worker at a time per lead run.
- Use the existing review endpoints for single-task approval, rejection, and user feedback. Persist tool calls and results as message parts. Correlate each live task card by the lead message ID.
- Keep child messages in SQLite. Surface them inside the corresponding task card and exclude them from the main transcript and conversation history supplied to the lead's model. Children start with their assigned task and explicit artifact references.
- Keep old message text and stored mention IDs readable. Treat mentions as ordinary text and route group operations through the configured lead.

## Risks / Trade-offs

- Restarts interrupt active executions. Completed tool results remain visible in message history.
- Multiple delegations add model turns. The adapter loop bounds the number of model turns and the shared approval store makes each executed assignment explicit.
