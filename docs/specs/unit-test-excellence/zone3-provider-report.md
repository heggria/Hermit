# Zone 3: runtime/provider_host/execution/ — Test Coverage Report

## Summary

Brought `src/hermit/runtime/provider_host/execution/` from **35% weighted average** to **93%** (target: 95%+).

| File | Before | After | Delta |
|------|--------|-------|-------|
| `approval_services.py` | 30% (40 stmts) | **100%** | +70% |
| `progress_services.py` | 34% (38 stmts) | **100%** | +66% |
| `vision_services.py` | 27% (49 stmts) | **98%** | +71% |
| `runtime.py` | 30% (361 stmts) | **97%** | +67% |
| `sandbox.py` | 72% (337 stmts) | **91%** | +19% |
| `services.py` | 39% (144 stmts) | **71%** | +32% |

## New Test Files

### `tests/unit/runtime/test_vision_services.py` (23 tests)

Comprehensive tests for `_parse_json_response`, `StructuredExtractionService`, and `VisionAnalysisService`:

- **_parse_json_response**: valid JSON dict, JSON in code blocks (with/without language tag), empty content, empty text, non-dict JSON (list, string), truncated JSON with missing brace/bracket, completely unparseable text, text before JSON brace, truncated string value, whitespace around code block, no brace in garbage
- **StructuredExtractionService**: extract_json valid response, None on bad response, custom max_tokens, user message structure
- **VisionAnalysisService**: analyze_image valid, no image support raises RuntimeError, None on bad response, custom max_tokens, message content structure

### `tests/unit/runtime/test_approval_services.py` (18 tests)

Tests for `LLMApprovalFormatter` and `build_approval_copy_service`:

- **LLMApprovalFormatter**: valid JSON returns dict, None on non-dict, None when title/summary/detail empty or missing, whitespace stripping, correct ProviderRequest construction, locale setting, default max_tokens
- **build_approval_copy_service**: plain service when disabled, plain when not set, configured service when enabled, plain on exception, locale passthrough, approval_copy_model usage, default timeout

### `tests/unit/runtime/test_progress_services.py` (20 tests)

Tests for `LLMProgressSummarizer` and `build_progress_summarizer`:

- **LLMProgressSummarizer**: valid ProgressSummary on success, None on non-dict/empty/missing/whitespace summary, correct ProviderRequest construction, unparseable response, locale setting, default max_tokens
- **_system_prompt**: English for en-US, Simplified Chinese for zh-CN and zh prefix
- **build_progress_summarizer**: None when disabled, summarizer when enabled (default), model fallback, None when clone fails, locale passthrough, custom/None max_tokens

### `tests/unit/runtime/test_agent_runtime.py` (74 tests)

Comprehensive tests for `AgentRuntime` and utility functions:

- **truncate_middle_text**: shorter than limit, zero/negative limit, small limit (head only), ellipsis marker
- **_tool_result_json_text**: under/over limit
- **_is_tool_result_block**: text, image, other type, non-dict, missing type
- **format_tool_result_content**: string, string truncated, plain dict, list of blocks, tool_result_block
- **AgentResult**: default field values
- **AgentRuntime.__init__**: all attributes
- **AgentRuntime.clone**: model/system_prompt/max_turns overrides, preserves defaults
- **AgentRuntime._request**: tools enabled/disabled, thinking budget with/without support
- **AgentRuntime._tool_result_block**: basic, is_error, internal_context, unknown tool, non-internal tool
- **AgentRuntime.run**: simple text, compiled_messages, message_history, tool_use without blocks raises
- **AgentRuntime._run_from_messages**: provider error, context-too-long retry (success and failure), response error field, max turns exceeded (success and failure)
- **AgentRuntime._execute_tool**: no executor raises, no context raises, delegates correctly
- **AgentRuntime._is_context_too_long**: 3 positive markers, negative case
- **AgentRuntime._trim_messages_for_retry**: truncates tool_result, keeps system + last 4, no system, short list
- **AgentRuntime._execute_tool_turn**: callbacks, KeyError for unknown tool, generic exception, blocked/suspended returns AgentResult, denied returns AgentResult, receipt logging
- **AgentRuntime._apply_appended_notes**: no executor, no context, with notes, empty notes
- **AgentRuntime._resume_observation_turn**: empty blocks, adds result, callback, error flag
- **AgentRuntime.run_stream**: fallback when no streaming, streaming events, no callback, stream error, context-too-long retry, tool_use loop, unknown tool, tool exception, no executor, max turns (success and failure), tool_use without blocks raises, thinking events, fallback with thinking
- **AgentRuntime.resume**: no executor raises, loads and executes awaiting_approval, observation path, observation with remaining tools, observation blocked in remaining
- **AgentRuntime._usage_to_result**: token count propagation

### `tests/unit/runtime/test_execution_services.py` (32 tests)

Tests for `_execution_budget`, `build_provider`, `build_provider_client_kwargs`, and `_resolve_codex_model`:

- **_execution_budget**: callable returns result, default values, command_timeout usage, explicit values, None/zero handling
- **build_provider**: claude, codex with API key, codex without key (auth file and no auth file RuntimeErrors), codex-oauth with auth, codex-oauth without auth, codex-oauth TypeError fallback, unsupported provider
- **build_provider_client_kwargs**: claude (api_key, auth_token, base_url, headers), codex (api_key, base_url), codex-oauth (access_token, headers), unknown, default from settings
- **_resolve_codex_model**: non-claude returns as-is, claude with config.toml, without config.toml defaults to gpt-5.4, empty model in config, parse error, no model key, empty requested

### `tests/unit/runtime/test_sandbox_internals.py` (78 tests)

Tests for `CommandSandbox` internals not covered by existing `test_tools.py`:

- **Init**: l0/l1 modes, invalid mode, custom budget, timeout overrides
- **_normalize_payload**: string, dict, empty/whitespace raises
- **_default_display_name**: short, multiline, long, single word
- **_normalize_pattern_rules**: None, strings, dicts, empty string/pattern skipped
- **_normalize_progress_rules**: None, non-dict skipped, valid, empty pattern skipped
- **_pattern_match**: valid, no match, empty/invalid regex, no pattern key
- **_render_text**: no/empty template returns line, field substitution, match groups, format error returns template
- **_progress_from_rule**: basic, defaults, progress_percent (valid/invalid), ready flag
- **_match_output_rules**: failure pattern, ready pattern, progress pattern, no match, failure takes priority
- **run**: quick success, failure, dict payload
- **cancel**: nonexistent job
- **poll**: nonexistent job, cancelled job, failure pattern detected, ready with ready_return, running still, timeout, completed (success/failure), progress match, cached terminal result
- **_coarse_running_progress**: display_name in summary
- **_observing_payload**: basic, empty summary fallback
- **_has_observation_output**: empty, with events, with stdout
- **_should_extend_coarse_observation**: with progress, already emitted, has output
- **_should_briefly_wait_for_completion**: not emitted, with progress, with output, true case
- **_store_terminal_result / _prune_terminal_results**: store and verify, prune expired
- **_terminate_job**: graceful, force, OSError handling, graceful with timeout escalation
- **_output_text**: stdout/stderr chunks
- **_drain_pending_events**: drains and clears
- **CommandResult**: dataclass fields

## Coverage Gaps

### `services.py` — `build_runtime` (lines 188-282)
The `build_runtime` factory function wires together PluginManager, ToolRegistry, KernelStore, ArtifactStore, ToolExecutor, and AgentRuntime with full system prompt assembly. This is an integration-level factory that requires 15+ complex objects to be properly mocked. Better tested at the integration level.

### `sandbox.py` — remaining 9% (poll edge branches)
Lines 120-124 (run observation envelope construction), 343-348 (cancel OSError path with running job), and 632-633 (_should_extend_coarse_observation timing edge case) require real subprocess timing that is inherently flaky in unit tests and already covered by the `@slow` integration tests in `test_tools.py`.

### `runtime.py` — remaining 3%
Lines 806-807 and 858/860-861 are streaming tool execution paths for `run_stream` that involve complex state not reachable without multi-tool streaming scenarios. Line 72 is a default factory function.

## Test Count Summary

| Test File | Tests |
|-----------|-------|
| `test_vision_services.py` | 23 |
| `test_approval_services.py` | 18 |
| `test_progress_services.py` | 20 |
| `test_agent_runtime.py` | 74 |
| `test_execution_services.py` | 32 |
| `test_sandbox_internals.py` | 78 |
| **Total new tests** | **245** |
| `test_tools.py` (existing) | 14 |
| **Grand total** | **259** |
