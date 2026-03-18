---
id: agent-card-publisher
title: "Auto-generate and publish A2A Agent Cards from plugin manifests"
priority: normal
trust_zone: low
---

## Goal

Automatically generate an A2A Agent Card that describes this Hermit instance's governed capabilities, derived from loaded plugin manifests and kernel configuration. The card is served at `/.well-known/agent.json` and updated on plugin reload, enabling external agents to discover what Hermit can do and how to interact with it through the governance layer.

## Steps

1. Create `src/hermit/plugins/builtin/hooks/webhook/agent_card.py`:
   - `AgentCardBuilder` class:
     - `build(plugin_manager, config)` → AgentCard
       - Extracts capabilities from loaded plugins (tools, adapters, MCP servers, subagents)
       - Includes governance metadata (supported_policy_profiles, proof_modes, approval_required_for)
       - Includes endpoint metadata (a2a_endpoint, webhook_endpoint, mcp_endpoint, auth methods)
     - `to_json()` → dict (A2A-compatible JSON)
     - `diff(old_card, new_card)` → list of changes (for logging on reload)

2. Create `src/hermit/plugins/builtin/hooks/webhook/agent_card_models.py`:
   - `AgentCard`: agent_id, agent_name, version, description, capabilities, governance, endpoints, updated_at
   - `AgentCapability`: name, description, action_class, risk_level, requires_approval, input_schema
   - `AgentGovernance`: policy_profiles, proof_modes, approval_actions, trust_scoring_enabled
   - `AgentEndpoint`: type (a2a/webhook/mcp), url, auth_methods

3. Register agent card route:
   - `GET /.well-known/agent.json` → serves current agent card
   - Card is rebuilt on SERVE_START and cached in memory
   - Card is refreshed on plugin reload (REGISTER_TOOLS hook)

4. Add card diffing on reload:
   - When plugins reload, compute diff between old and new card
   - Log changes and emit a ledger event `agent_card.updated`

5. Write tests in `tests/unit/plugins/hooks/test_agent_card.py`:
   - Test card generation from mock plugin manager
   - Test tools are mapped to capabilities with correct action classes
   - Test governance metadata includes proof modes
   - Test card diff detects added/removed capabilities
   - Test card JSON is valid and contains required A2A fields
   - Test card is updated on plugin reload

## Constraints

- Agent card MUST NOT expose internal implementation details (file paths, internal tool names)
- Card generation must be deterministic (same plugins → same card, except updated_at)
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/plugins/builtin/hooks/webhook/agent_card.py` exists
- [ ] `src/hermit/plugins/builtin/hooks/webhook/agent_card_models.py` exists
- [ ] `uv run pytest tests/unit/plugins/hooks/test_agent_card.py -q` passes with >= 6 tests
- [ ] Agent card includes capabilities derived from loaded plugins

## Context

- PluginManager: `src/hermit/runtime/capability/registry/manager.py`
- PluginManifest: `src/hermit/runtime/capability/contracts/base.py`
- ToolSpec: `src/hermit/runtime/capability/registry/tools.py`
- Webhook server: `src/hermit/plugins/builtin/hooks/webhook/server.py`
