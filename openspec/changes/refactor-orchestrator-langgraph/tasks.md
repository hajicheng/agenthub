## 1. Graph runtime

- [x] 1.1 Lock and install the Python LangGraph dependency with uv.
- [x] 1.2 Route Orchestrator runs through StateGraph and verify plan approval, revision, and rejection through the existing API.
- [x] 1.3 Dispatch dependency waves with Send and verify parallel siblings, blocked descendants, cancellation, and aggregation through the normal run entry point.

## 2. Integration

- [x] 2.1 Document the workflow in the project architecture and verify the OpenSpec change, backend tests, TypeScript checks, and lint.
- [x] 2.2 Remove unused runner fields, legacy dispatch event lookup, the unused Claude SDK extra, and stale orchestration references. Verify backend and frontend tests.
