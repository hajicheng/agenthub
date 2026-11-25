# Proposal

## Why

AgentHub records usage locally. Langfuse adds a timeline for identifying slow or failed model calls and following a lead agent's delegation across runs.

## What Changes

- Enable Langfuse export when the FastAPI process has both project keys. Configure the endpoint with `LANGFUSE_BASE_URL`.
- Trace AgentRunner runs, group delegated runs under the lead run, and group runs by conversation in Langfuse Sessions.
- Show tool execution and delegation review outcomes in the run timeline.
- Record generation latency, model, outcome, and provider-reported token usage for Anthropic and OpenAI-compatible calls, including agent drafts and conversation compaction.
- Support opt-in text previews of inputs and outputs, with credential redaction. Keep telemetry failure isolated from chat, tools, approvals, and existing usage accounting.

## Capabilities

### New Capabilities

- `llm-observability` covers opt-in tracing of agent runs and model requests.

### Modified Capabilities

## Impact

The FastAPI runner, implemented providers, app lifecycle, and environment configuration are affected. The integration uses the official Python SDK to batch and export OpenTelemetry observations. The [Langfuse compatibility guide](https://langfuse.com/docs/compatibility) describes its ingestion support.
