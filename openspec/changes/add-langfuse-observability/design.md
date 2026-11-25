# Design

## Context

FastAPI owns the active AgentRunner, Anthropic and OpenAI-compatible streaming loops, and the model calls for agent drafts and conversation compaction. The runner creates an asyncio task for each run, and delegated tasks are spawned within the lead's run. Python already loads the repository's gitignored `.env.local`.

## Goals

- Capture provider timings at the model request boundary so tool approval and tool execution time appear only in the enclosing run.
- Record the final run state from the existing runner lifecycle, including handled errors and explicit aborts.
- Verify the first trace with Mock so the cloud check can run without a paid model request or a real conversation.

## Decisions

- Use the official Langfuse Python SDK with OpenTelemetry export and real-time ingestion support. A supported SDK owns batching, retry, and export lifecycle.
- Use a scoped run observation and Python's asyncio context propagation for delegated runs. Attach the conversation ID as the session ID and verify nesting with the actual runner.
- Open generation observations around streaming requests and non-streaming service calls. Use explicit fields for model and usage, and send a fixed status plus the error class on failure. Suppress automatic exception recording at the telemetry boundary to keep provider response bodies local.
- Gate input and output previews on `LANGFUSE_CAPTURE_CONTENT`. Capture text at the existing run, model, and tool boundaries and sanitize it before assigning it to Langfuse.
- Initialize the client lazily when configured. Use a dedicated tracer provider so the integration exports AgentHub's explicitly selected observations. Attempt a bounded shutdown flush outside the FastAPI event loop.

## Risks and trade-offs

- Compatible providers vary in usage fields. Record known counts and account for the different Anthropic and OpenAI cache accounting conventions.
- Run duration can include time spent awaiting approval. The child generation duration shows the provider timing.
- Credentials shown in a screenshot can be replaced in the Langfuse project settings. Keep active credentials in the backend environment or `.env.local`.

## Migration plan

Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and the project's `LANGFUSE_BASE_URL`, then restart FastAPI. Remove either key or set `LANGFUSE_TRACING_ENABLED=false` and restart to disable export.
