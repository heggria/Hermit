---
id: a2a-governed-endpoint
title: "Agent-to-Agent protocol endpoint with governed message handling"
priority: high
trust_zone: low
---

## Goal

Add an A2A (Agent-to-Agent) protocol endpoint to the webhook server, enabling external agents to send governed task requests to Hermit. Each incoming A2A message creates a governed task with full policy evaluation, approval flow, and receipted execution — making Hermit a trust-anchored participant in multi-agent networks.

## Steps

1. Create `src/hermit/plugins/builtin/hooks/webhook/a2a.py`:
   - A2A message models (aligned with Google's A2A spec concepts):
     - `A2ATaskRequest`: sender_agent_id, sender_agent_url, task_description, required_capabilities, priority, context_artifacts, reply_to_url
     - `A2ATaskResponse`: task_id, status, result_summary, proof_ref, receipts_summary
     - `A2ACapabilityCard`: agent_id, agent_name, capabilities, supported_actions, trust_level
   - `A2AHandler` class:
     - `handle_task_request(request, runner)` → validates sender, creates governed task, returns task_id
     - `build_capability_card()` → returns this Hermit instance's capability advertisement
     - `send_result(reply_to_url, response)` → POST result back to sender agent

2. Register A2A routes on the webhook server:
   - `POST /a2a/tasks` — receive task request, return task_id + status
   - `GET /a2a/tasks/{task_id}/status` — poll task status + proof summary
   - `GET /a2a/.well-known/agent.json` — capability card (agent discovery)
   - All routes require HMAC signature verification (reuse webhook's existing mechanism)

3. Create `src/hermit/plugins/builtin/hooks/webhook/a2a_hooks.py`:
   - Hook into SERVE_START to register A2A routes
   - Hook into DISPATCH_RESULT to auto-send results back to reply_to_url when task completes

4. Governance integration:
   - Incoming A2A tasks get policy_profile based on sender's trust_level
   - Unknown senders default to "supervised" policy (requires approval)
   - Known/trusted senders can use "autonomous" policy
   - Sender trust is stored in memory_records with memory_kind="a2a_trust"

5. Write tests in `tests/unit/plugins/hooks/test_a2a_endpoint.py`:
   - Test capability card endpoint returns valid JSON
   - Test task request creates a governed task
   - Test unknown sender gets supervised policy
   - Test HMAC signature verification on A2A routes
   - Test task status endpoint returns proof summary
   - Test result callback fires on task completion

## Constraints

- Do NOT modify existing webhook routes — A2A routes are additive under /a2a/ prefix
- A2A requests MUST go through the full governed execution path (no shortcuts)
- Sender identity must be verified before task creation
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/plugins/builtin/hooks/webhook/a2a.py` exists with A2A models and handler
- [ ] `src/hermit/plugins/builtin/hooks/webhook/a2a_hooks.py` exists with hook registration
- [ ] `uv run pytest tests/unit/plugins/hooks/test_a2a_endpoint.py -q` passes with >= 6 tests
- [ ] GET /a2a/.well-known/agent.json returns a valid capability card

## Context

- Webhook server: `src/hermit/plugins/builtin/hooks/webhook/server.py`
- Webhook hooks: `src/hermit/plugins/builtin/hooks/webhook/hooks.py`
- HMAC verification: `WebhookServer._verify_signature()`
- Task creation: `TaskController.start_task()` in `src/hermit/kernel/task/services/controller.py`
- Google A2A spec concepts: agent cards, task lifecycle, capability advertisement
