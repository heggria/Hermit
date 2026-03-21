# Assembly

Configuration and context assembly for runtime initialization.

`Settings` is a Pydantic `BaseSettings` subclass that centralizes all runtime
configuration for Hermit. Key field groups:

- **Provider credentials** -- `claude_api_key`, `claude_auth_token`, `claude_base_url`,
  `openai_api_key`, `openai_base_url`, plus legacy alias resolution and Codex OAuth
  support
- **MCP server** -- `mcp_server_enabled`, `mcp_server_host`, `mcp_server_port` for the
  built-in MCP endpoint
- **Approval copy formatter** -- `approval_copy_formatter_enabled`,
  `approval_copy_model`, `approval_copy_formatter_timeout_ms` for LLM-generated
  approval summaries
- **Progress summary** -- `progress_summary_enabled`, `progress_summary_model`,
  `progress_summary_max_tokens`, `progress_summary_keepalive_seconds` for streaming
  progress updates
- **Kernel dispatch** -- `kernel_dispatch_worker_count` controlling the governed
  execution worker pool size
- **Tool deadlines and observation** -- `tool_soft_deadline_seconds`,
  `tool_hard_deadline_seconds`, `observation_window_seconds`,
  `observation_poll_interval_seconds` feeding into `ExecutionBudget`
- **Adapter tokens** -- `telegram_bot_token`, `slack_bot_token`, `slack_app_token`,
  `feishu_app_id`, `feishu_app_secret` for messaging adapters

`Settings.execution_budget()` constructs an `ExecutionBudget` (from
`hermit.runtime.control.lifecycle.budgets`) that replaces the earlier single
`command_timeout_seconds` with fine-grained provider connect/read timeouts, tool
soft/hard deadlines, and observation windows.

`context.py` provides locale-aware default context templates for agent sessions.

::: hermit.runtime.assembly.config

::: hermit.runtime.assembly.context
