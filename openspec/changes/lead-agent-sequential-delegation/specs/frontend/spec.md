## ADDED Requirements

### Requirement: Group chat SHALL present the lead conversation and delegated tasks

The group composer MUST accept ordinary message text without a mention picker. Delegations MUST appear with the lead message that requested them. Child messages MUST be visible in task details.

#### Scenario: User reviews a task
- **WHEN** a review event arrives
- **THEN** the associated lead message displays the task, assignee, and approve and reject controls.

#### Scenario: User reloads completed work
- **WHEN** the conversation is reloaded
- **THEN** delegated results remain visible on their originating lead messages
- **AND** child messages do not appear as top-level chat replies.
