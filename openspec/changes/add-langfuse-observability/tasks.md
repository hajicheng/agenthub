# Tasks

## 1. Export configuration

- [x] 1.1 Add and lock the Langfuse Python SDK. Verify that the FastAPI environment installs from the lockfile.
- [x] 1.2 Configure optional export and shutdown handling. Verify that missing credentials and exporter failures leave the application operational.
- [x] 1.3 Document configuration and add empty environment placeholders. Verify that the documented variables are read by FastAPI.

## 2. Agent and model observations

- [x] 2.1 Instrument AgentRunner. Verify that delegated runs share the lead trace, separate conversations stay isolated, and run statuses match local outcomes.
- [x] 2.2 Instrument streaming and non-streaming model calls. Verify usage mapping, cancellation, and exclusion of chat content and provider error bodies.

## 3. End-to-end check

- [x] 3.1 Run the backend tests and repository checks. Send a Mock agent trace using configured credentials and verify that Langfuse can retrieve it.

## 4. Input and output previews

- [x] 4.1 Capture opt-in run, generation, and tool inputs and outputs. Verify redaction, default behavior, and the Langfuse preview.
