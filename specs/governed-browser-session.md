---
id: governed-browser-session
title: "Browser automation as kernel-managed governed resource"
priority: normal
trust_zone: low
---

## Goal

Make browser automation a first-class kernel-managed resource: sessions are workspace-leased, interactions produce receipts with visual evidence (screenshot artifacts), and high-risk actions (form submissions, downloads, auth flows) require policy approval.

## Steps

1. Create `src/hermit/plugins/builtin/tools/computer_use/browser_session.py`:
   - `BrowserSessionManager` class:
     - `acquire(task_id, step_attempt_id, config)` → BrowserSession
     - `release(session_id)` → bool (captures final screenshot)
     - `capture_evidence(session_id, label)` → artifact_id
     - `classify_action(url, action_type)` → BrowserActionRisk

2. Create `src/hermit/plugins/builtin/tools/computer_use/browser_models.py`:
   - `BrowserSession`, `BrowserActionRisk`, `BrowserEvidence`

3. Create governed browser tools: `governed_navigate`, `governed_fill_form`, `governed_submit`, `governed_screenshot`
4. Register via plugin.toml with action_class per tool

5. Write tests in `tests/unit/plugins/tools/test_browser_session.py` (>= 7 tests)

## Constraints

- Sessions MUST be workspace-leased
- All browser tools MUST produce receipts
- Screenshots stored as content-addressed artifacts
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/plugins/builtin/tools/computer_use/browser_session.py` exists
- [ ] `src/hermit/plugins/builtin/tools/computer_use/browser_models.py` exists
- [ ] `uv run pytest tests/unit/plugins/tools/test_browser_session.py -q` passes with >= 7 tests

## Context

- WorkspaceLeaseService: `src/hermit/kernel/authority/workspaces/workspaces.py`
- ArtifactStore: `src/hermit/kernel/artifacts/models/artifacts.py`
