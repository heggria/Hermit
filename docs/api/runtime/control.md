# Control

Agent runner, session lifecycle, and execution budget management.

`AgentRunner` is the unified orchestration layer that both CLI commands and adapter
plugins call into. Heavy logic is delegated to extracted handler modules:

- **MessageCompiler** -- prompt context preparation and provider input compilation
- **SessionContextBuilder** -- session lifecycle helpers (start, resume, trim)
- **RunnerTaskExecutor** -- task execution through the governed agent loop
- **AsyncDispatcher** -- async ingress, approval resume, and dispatch result handling
- **ControlActionDispatcher** -- slash-command and control action dispatch
- **ApprovalResolver** -- approval and disambiguation resolution for governed tasks
- **utils** -- shared text helpers, regex constants, and `DispatchResult` dataclass

The lifecycle layer provides `SessionManager` for conversation state persistence and
`ExecutionBudget` / `Deadline` for fine-grained execution budget management (provider
connect/read timeouts, tool soft/hard deadlines, observation windows) -- replacing the
earlier single `command_timeout_seconds` approach.

::: hermit.runtime.control.runner.runner

::: hermit.runtime.control.runner.approval_resolver

::: hermit.runtime.control.runner.async_dispatcher

::: hermit.runtime.control.runner.control_actions

::: hermit.runtime.control.runner.message_compiler

::: hermit.runtime.control.runner.session_context_builder

::: hermit.runtime.control.runner.task_executor

::: hermit.runtime.control.runner.utils

::: hermit.runtime.control.lifecycle.session

::: hermit.runtime.control.lifecycle.budgets
