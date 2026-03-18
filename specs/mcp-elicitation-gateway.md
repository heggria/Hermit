---
id: mcp-elicitation-gateway
title: "Route MCP elicitation requests through Hermit's governance layer"
priority: high
trust_zone: low
---

## Goal

When MCP servers connected to Hermit use the Elicitation capability (form mode or URL mode) to request user input, route those requests through Hermit's operator approval surface instead of bypassing governance. This ensures that all user-facing interactions — including credential prompts, OAuth redirects, and data collection forms — are policy-evaluated, receipted, and auditable.

## Steps

1. Create `src/hermit/runtime/capability/resolver/elicitation.py`:
   - `ElicitationGateway` class:
     - `handle_elicitation(server_name, request)` → ElicitationResult
       - Classifies the elicitation type (form vs URL)
       - Creates an ActionRequest with action_class="elicitation" for policy evaluation
       - For URL-mode: checks URL against allowlist, flags external OAuth redirects
       - For form-mode: validates JSON Schema, strips sensitive field hints
       - Routes to operator approval if policy requires it
       - Returns collected data (or denial) to the MCP server
     - `classify_sensitivity(request)` → SensitivityLevel (low/medium/high/critical)
       - URL-mode with external domain → high
       - Form with password/secret fields → critical
       - Form with text-only fields → low
   - `ElicitationRecord`: server_name, elicitation_type, sensitivity, schema_hash, approved_by, responded_at

2. Create `src/hermit/runtime/capability/resolver/elicitation_models.py`:
   - `ElicitationRequest`: server_name, type (form/url), message, schema (for form), url (for url), requested_at
   - `ElicitationResult`: status (completed/denied/timeout), data (for form), redirect_completed (for url)
   - `SensitivityLevel` enum: low, medium, high, critical

3. Integrate into MCP client resolver:
   - In `McpClientResolver`, intercept elicitation callbacks
   - Route through `ElicitationGateway.handle_elicitation()` instead of direct user prompting
   - Emit receipt after each elicitation (action_type="elicitation", result_code based on completion)

4. Add policy rules for elicitation:
   - `elicitation` action_class in policy guards
   - low sensitivity → auto-approve
   - medium → log + allow
   - high → require operator approval
   - critical → require approval + evidence case

5. Write tests in `tests/unit/runtime/test_elicitation_gateway.py`:
   - Test form-mode elicitation creates correct ActionRequest
   - Test URL-mode with external domain gets high sensitivity
   - Test critical sensitivity requires approval
   - Test low sensitivity auto-approves
   - Test elicitation produces receipt
   - Test schema validation rejects invalid form data
   - Test URL allowlist filtering

## Constraints

- Do NOT modify the MCP protocol implementation itself — intercept at the resolver layer
- All elicitations MUST produce receipts regardless of sensitivity level
- URL-mode must NEVER auto-redirect without at least logging
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/runtime/capability/resolver/elicitation.py` exists
- [ ] `src/hermit/runtime/capability/resolver/elicitation_models.py` exists
- [ ] `uv run pytest tests/unit/runtime/test_elicitation_gateway.py -q` passes with >= 7 tests
- [ ] Elicitation sensitivity classification works correctly for form and URL modes

## Context

- MCP client resolver: `src/hermit/runtime/capability/resolver/mcp_client.py`
- Policy guards: `src/hermit/kernel/policy/guards/rules.py`
- Action classes: defined in `src/hermit/kernel/execution/controller/contracts.py`
- MCP Elicitation spec: form mode uses JSON Schema, URL mode redirects to external URLs
