---
id: live-execution-streaming
title: "Real-time SSE execution visibility for governance dashboards"
priority: normal
trust_zone: low
---

## Goal

Add real-time execution streaming via Server-Sent Events (SSE): external dashboards can subscribe to a live feed of kernel events with governance metadata (policy verdicts, approval status, grant status, receipt summaries).

## Steps

1. Create `src/hermit/plugins/builtin/hooks/webhook/streaming.py`:
   - `ExecutionStreamService` class:
     - `subscribe(filter_config)` → StreamSubscription
     - `publish(event)` → None
     - `unsubscribe(subscription_id)` → None

2. Create `src/hermit/plugins/builtin/hooks/webhook/streaming_models.py`:
   - `StreamEvent`, `StreamSubscription`, `StreamFilter`

3. Register SSE endpoint `GET /stream` on webhook server (requires control_secret)
4. Hook into PRE_RUN/POST_RUN and ToolExecutor events

5. Write tests in `tests/unit/plugins/hooks/test_streaming.py` (>= 8 tests)

## Constraints

- SSE endpoint MUST require authentication
- Async publish — must not block execution pipeline
- Event payloads must NOT include raw tool inputs/outputs
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/plugins/builtin/hooks/webhook/streaming.py` exists
- [ ] `src/hermit/plugins/builtin/hooks/webhook/streaming_models.py` exists
- [ ] `uv run pytest tests/unit/plugins/hooks/test_streaming.py -q` passes with >= 8 tests

## Context

- Webhook server: `src/hermit/plugins/builtin/hooks/webhook/server.py`
- HookEvents: PRE_RUN, POST_RUN, DISPATCH_RESULT
