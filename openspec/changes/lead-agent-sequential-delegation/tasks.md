## 1. Lead and child runs

- [x] 1.1 Route group sends, edits, and regeneration to the lead and verify legacy mentions cannot start a worker.
- [x] 1.2 Wire the lead-only tool through the adapter loop and the single-task LangGraph. Verify result continuation, context isolation, revision, rejection, and cancellation.
- [x] 1.3 Remove the runner's read-only override and upgrade the old built-in tool defaults. Verify that the lead can write after approval in both single and group chats.

## 2. Chat

- [x] 2.1 Remove mention controls and render live and reloaded task cards with child details. Verify typecheck, lint, and frontend tests.
- [x] 2.2 Update the architecture documentation and validate the OpenSpec change.
