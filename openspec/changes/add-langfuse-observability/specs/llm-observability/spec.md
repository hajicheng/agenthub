# LLM Observability

## Purpose

Provide opt-in Langfuse timelines for AgentHub runs and model requests so operators can diagnose latency, failures, and token usage.

## ADDED Requirements

### Requirement: Langfuse export SHALL require project credentials

The FastAPI backend MUST enable Langfuse export only when both project keys are configured and tracing has not been explicitly disabled. It MUST use the configured Langfuse base URL when one is provided.

#### Scenario: Local install has no Langfuse credentials

- **WHEN** either project key is missing or blank
- **THEN** AgentHub runs normally and sends no telemetry.

#### Scenario: Langfuse export fails

- **WHEN** Langfuse initialization or export fails
- **THEN** chat, tool approvals, and local usage accounting continue.

### Requirement: Tracing SHALL link agent runs and model calls

Each AgentRunner run MUST record its duration and final outcome. Delegated runs MUST appear under the lead run in the same trace. All observations associated with a conversation MUST share its Langfuse session.

#### Scenario: Lead agent delegates work

- **WHEN** a group lead delegates a task to another agent
- **THEN** the lead and worker have separate observations in the same trace
- **AND** each agent's model calls appear under its own run.

#### Scenario: User cancels a run

- **WHEN** the run is aborted
- **THEN** its observation finishes with an aborted outcome.

#### Scenario: Delegation awaits review

- **WHEN** a group lead invokes the delegation tool
- **THEN** the timeline records tool execution and the review outcome under the lead run.

### Requirement: Generation observations SHALL record provider outcomes

Each implemented model request MUST record the effective model, duration, outcome, and available provider-reported token counts. This applies to agent replies, configuration drafts, and conversation compaction.

#### Scenario: Provider returns token counts

- **WHEN** the provider reports input, output, or cached token counts
- **THEN** the generation records those counts without double counting cache tokens
- **AND** AgentHub continues to use the existing local usage flow.

#### Scenario: Provider fails or omits usage

- **WHEN** the request errors or finishes with no usage data
- **THEN** the generation reports the actual outcome and omits unknown token counts.

### Requirement: Telemetry SHALL support opt-in input and output previews

With `LANGFUSE_CAPTURE_CONTENT=true`, run, generation, and tool observations MUST record bounded text previews of inputs and outputs. The previews MUST redact known credentials and omit binary attachments, private reasoning, and raw provider error bodies. Other observations and installations without content capture enabled MUST export operational metadata only.

#### Scenario: A model uses a tool to answer a message

- **WHEN** content capture is enabled and a run calls a model and a tool
- **THEN** Langfuse displays the run prompt and answer, the model request and generated reply or tool call, and the tool arguments and result.

#### Scenario: Error includes user content

- **WHEN** a provider includes a prompt or a credential in an exception message
- **THEN** Langfuse receives only the error type and the failure outcome.
