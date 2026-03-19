# Zone 15: Task Services + Scheduler Engine/Tools — Test Report

## Summary

Added 287 new unit tests across 6 test files covering task controller, ingress router, control intents, topics, projections, and scheduler engine/tools. All tests pass.

## Test Files Created

| File | Tests | Target Source |
|------|-------|---------------|
| `tests/unit/kernel/task/test_controller.py` | 66 | `kernel/task/services/controller.py` (619 lines) |
| `tests/unit/kernel/task/test_ingress_router.py` | 28 | `kernel/task/services/ingress_router.py` (242 lines) |
| `tests/unit/kernel/task/test_control_intents.py` | 45 | `kernel/task/state/control_intents.py` (193 lines) |
| `tests/unit/kernel/task/test_topics.py` | 48 | `kernel/task/services/topics.py` (166 lines) |
| `tests/unit/kernel/task/test_projections.py` | 20 | `kernel/task/projections/projections.py` (134 lines) |
| `tests/unit/plugins/hooks/test_scheduler_engine.py` | 36 | `plugins/builtin/hooks/scheduler/engine.py` (236 lines) |
| `tests/unit/plugins/hooks/test_scheduler_tools.py` | 44 | `plugins/builtin/hooks/scheduler/tools.py` (136 lines) |

## Coverage Areas

### controller.py (304 missed lines)
- `IngressDecision` dataclass fields and defaults
- `source_from_session()` — all 5 channel types (webhook, scheduler, cli, feishu, chat)
- `ensure_conversation()` — explicit and auto-detected source channels
- `latest_task()` / `active_task_for_conversation()` — empty, running, completed states
- `start_task()` — auto-parent, explicit parent=None, workspace_root, policy_profile, empty goal, ingress_metadata
- `enqueue_task()` — queued status creation, source_ref parameter
- `start_followup_step()` — normal and unknown task error
- `context_for_attempt()` — normal recovery and unknown attempt error
- `finalize_result()` — completed, failed, double-finalize CAS guard, workspace lease release/error
- `mark_planning_ready()` — with and without preview/text
- `mark_blocked()` / `mark_suspended()`
- `pause_task()` / `cancel_task()` — normal and unknown task
- `focus_task()` — normal, unknown, wrong conversation
- `reprioritize_task()`
- `resume_attempt()` — normal, unknown, recovery_required context
- `enqueue_resume()` — normal, unknown, input_dirty + awaiting_approval supersession
- `resolve_text_command()` — normal text returns None
- `append_note()` — normal and unknown task
- `update_attempt_phase()` — phase change, no-op same phase, unknown attempt
- `_ingress_queue_priority()` — all priority tiers (90, 100, 10, 0)
- Static/class methods: `_normalize_ingress_text`, `_extract_artifact_refs`, `_is_chat_only_message`, `_sanitize_context_text`, `_binding_snapshot`

### ingress_router.py (161 missed lines)
- `BindingDecision` / `CandidateScore` dataclass defaults
- `_normalize()` — whitespace handling, empty, None
- `bind()` — explicit_task_ref, reply_to_task_id, no open tasks, no candidate match
- `bind()` — pending approval correlation, focus followup, branch marker (fork_child)
- `_resolve_structural_binding()` — single artifact match, no refs
- `_score_task()` — focus boost, no focus
- `_artifact_refs()` / `_receipt_refs()` / `_path_refs()` — extraction and dedup
- `_normalized_path()` — empty and normal paths
- `_workspace_targets()` — empty paths
- `_task_workspace_root()` — empty result

### control_intents.py (161 missed lines)
- `ControlIntent` dataclass defaults
- Empty and whitespace input
- Approve commands: `/task approve`, `approve`, `approve_once`, `approve_mutable_workspace`
- Deny commands: `/task deny` with and without reason
- Pending approve shortcut
- Navigation intents: help, new_session, history, task_list
- Task case, task events, task receipts, task proof, task proof export
- Plan intents: enter, confirm, exit
- Rollback: explicit and latest (Chinese locale for keyword path)
- Capability list, schedule list
- Projection rebuild / rebuild_all
- Task switch with explicit target
- Cache helpers: `_cached_re`, `_cached_set`, `_all_locale_keywords`
- Normal text returns None

### topics.py (89 missed lines)
- `_clean_topic_text()` — empty, None, whitespace, blank lines
- `_append_item()` — add, dedup, different items, phase/progress distinction
- `build_task_topic()` — empty events, initial seed, invalid progress
- All 17 event types: task.created, tool.submitted, tool.progressed, tool.status.changed, task.progress.summarized, task.note.appended, execution_contract.selected/superseded, evidence_case.recorded/invalidated, authorization_plan.recorded/invalidated, approval.requested/drifted/expired/granted/denied/consumed, reconciliation.closed, task.completed/failed/cancelled
- Items limit (max 20)
- Unknown events ignored
- Full lifecycle multi-event sequence

### projections.py (48 missed lines)
- `rebuild_task()` — full build and incremental rebuild
- `rebuild_task()` — task not found error
- `verify_projection()` — missing cache and valid cache
- `ensure_task_projection()` — creates if missing, returns cached
- `rebuild_all()` — multiple tasks and empty
- `_tool_history_from_events()` — with tool name, empty tool name, non-action events
- `_key_input()` — empty dict, first value extraction
- `_tool_input_from_event()` — no ref, missing artifact, valid artifact, bad JSON

### scheduler/engine.py (191 missed lines)
- `_build_execution_prompt()` — basic prompt wrapping and stripping
- Engine init, set_runner
- Job CRUD: add, remove (found/not found), update (found/not found), list, get (found/not found)
- History: empty, filtered by job_id, wrong filter, limit
- `_next_due_job()` — empty, due, future, disabled
- `_compute_next_run()` — interval, once future/past, disabled
- `_recalculate_all_next_run()`
- Persistence: persist and reload jobs
- `_write_log_file()` — success and error records
- wake(), stop() without thread
- `_execute()` with AgentRunner — normal, once-disables, feishu_chat_id
- `_execute()` fallback — success, failure with retries, once-disables, feishu notify
- `_catchup_missed_jobs()` — no missed, with missed, disabled skipped

### scheduler/tools.py (115 missed lines)
- `set_engine()` / `_require_engine()` — set, raises when None, returns
- `_format_time()` — None and valid timestamp
- `_handle_create()` — missing fields, invalid schedule_type, cron (missing/invalid/success), once (missing/invalid datetime/past/future), interval (too short/missing/success), feishu_chat_id, max_retries
- `_handle_list()` — empty and with multiple job types (cron/once/interval, enabled/disabled)
- `_handle_delete()` — missing id, success, not found
- `_handle_history()` — empty, with records (success/failure), filtered, with limit
- `_handle_update()` — missing id, no fields, not found, name/prompt/enabled/cron_expr/feishu_chat_id updates, invalid cron
- `register()` — verifies all 5 tools are registered

## Methodology

- All tests use real `KernelStore` with `tmp_path` for SQLite databases (no mocking of store)
- Scheduler engine tests use `MagicMock` for hooks and `SimpleNamespace` for settings
- Scheduler tools tests use a `FakeEngine` class for handler isolation
- `monkeypatch` used for IngressRouter static method overrides
- `unittest.mock.patch` used for scheduler _execute/_run_agent isolation
- Module-level `_engine` state reset via autouse fixture in scheduler tools tests

## Run Command

```bash
uv run pytest tests/unit/kernel/task/ tests/unit/plugins/hooks/test_scheduler_engine.py tests/unit/plugins/hooks/test_scheduler_tools.py -q -n0
```

Result: **287 passed in ~9s**
