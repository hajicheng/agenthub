## MODIFIED Requirements

### Requirement: Orchestrator SHALL be a normal Agent

The orchestrator MUST run through AgentRunner and an adapter and MUST be the sole direct responder for group messages.

#### Scenario: User starts a group task
- **WHEN** a group message is sent, edited, or regenerated
- **THEN** the lead Agent receives the turn regardless of legacy mention metadata.

## ADDED Requirements

### Requirement: Lead Agent SHALL obtain approval for each delegation

The lead MUST supply a valid individual assignment and obtain user approval before launching its child run.

#### Scenario: Lead delegates a task
- **WHEN** the lead calls `delegate_task` with a current worker and a task description
- **THEN** the system asks for review before starting the child.

#### Scenario: User requests a revision
- **WHEN** the user revises a pending task
- **THEN** the lead receives the feedback as a tool result and can submit a new assignment.

### Requirement: Lead Agent SHALL delegate sequentially

Each lead run MUST finish its current delegation before it can start another. The lead MUST receive the outcome, including errors or a child summary where available.

#### Scenario: Lead model supplies multiple calls in one response
- **WHEN** the response tries to delegate more than one task
- **THEN** at most the first call proceeds to review
- **AND** the model receives an error for subsequent delegation calls in that response.

### Requirement: Delegated tasks SHALL use isolated context and explicit artifact references

The lead MUST explicitly provide any upstream artifact references needed for an assignment. The child MUST start with the assigned task and supplied references in an isolated message context.

#### Scenario: Lead reuses a prior result
- **WHEN** the lead includes an earlier artifact ID in a delegation
- **THEN** the child can access that artifact through the conversation-scoped tools.

### Requirement: Delegation SHALL return outcomes to the lead model loop

The lead MUST use each delegation outcome as a tool result in its own model loop. The lead can then delegate again or produce its final user-facing response.

#### Scenario: A child run completes
- **WHEN** the child reaches a terminal state
- **THEN** the parent tool call returns the status, bounded summary, and available artifact IDs.

## REMOVED Requirements

### Requirement: Orchestrator SHALL plan before dispatch
**Reason**: The lead now requests approval for an individual assignment.
**Migration**: Submit one task with `delegate_task` and approve it through the existing review endpoint.

### Requirement: Child tasks SHALL respect dependency order and semantic reports
**Reason**: The lead decides the next assignment after each tool result.
**Migration**: Pass any prerequisites and acceptance instructions in the task description and have the lead assess the reported result.

### Requirement: Child task context SHALL include transitive upstream artifacts
**Reason**: Each assignment uses explicit artifact references and isolated context.
**Migration**: Include the necessary artifact IDs with each delegation.

### Requirement: Aggregation SHALL summarize child outputs
**Reason**: The lead resumes after every task result.
**Migration**: Read the final response from the continuing lead run.
