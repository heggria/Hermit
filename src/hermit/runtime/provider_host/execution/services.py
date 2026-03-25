from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel import (
    ApprovalService,
    ArtifactStore,
    KernelStore,
    PolicyEngine,
    ReceiptService,
    TaskController,
    ToolExecutor,
)
from hermit.kernel.verification.assurance.recorder import TraceRecorder
from hermit.runtime.assembly.config import Settings
from hermit.runtime.assembly.context import build_base_context
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.capability.registry.tools import create_builtin_tool_registry
from hermit.runtime.control.lifecycle.budgets import ExecutionBudget, configure_runtime_budget
from hermit.runtime.provider_host.execution.approval_services import (
    LLMApprovalFormatter,  # noqa: F401  re-export
    build_approval_copy_service,
)
from hermit.runtime.provider_host.execution.progress_services import (
    LLMProgressSummarizer,  # noqa: F401  re-export
    build_progress_summarizer,
)
from hermit.runtime.provider_host.execution.runtime import AgentRuntime
from hermit.runtime.provider_host.execution.sandbox import CommandSandbox
from hermit.runtime.provider_host.execution.vision_services import (  # noqa: F401  re-export
    StructuredExtractionService,
    VisionAnalysisService,
    _parse_json_response,
)
from hermit.runtime.provider_host.llm import (
    CodexOAuthProvider,
    CodexOAuthTokenManager,
    CodexProvider,
    build_claude_provider,
)
from hermit.runtime.provider_host.shared.contracts import Provider

log = structlog.get_logger()


def _execution_budget(settings: Settings) -> ExecutionBudget:
    builder = getattr(settings, "execution_budget", None)
    if callable(builder):
        return cast(ExecutionBudget, builder())
    command_timeout = float(getattr(settings, "command_timeout_seconds", 120.0) or 120.0)
    return ExecutionBudget(
        ingress_ack_deadline=float(getattr(settings, "ingress_ack_deadline_seconds", 15.0) or 15.0),
        provider_connect_timeout=float(
            getattr(settings, "provider_connect_timeout_seconds", command_timeout)
            or command_timeout
        ),
        provider_read_timeout=float(
            getattr(settings, "provider_read_timeout_seconds", 600.0) or 600.0
        ),
        provider_stream_idle_timeout=float(
            getattr(settings, "provider_stream_idle_timeout_seconds", 600.0) or 600.0
        ),
        tool_soft_deadline=float(
            getattr(settings, "tool_soft_deadline_seconds", command_timeout) or command_timeout
        ),
        tool_hard_deadline=float(
            getattr(settings, "tool_hard_deadline_seconds", max(command_timeout, 600.0))
            or max(command_timeout, 600.0)
        ),
        observation_window=float(getattr(settings, "observation_window_seconds", 3600.0) or 3600.0),
        observation_poll_interval=float(
            getattr(settings, "observation_poll_interval_seconds", 5.0) or 5.0
        ),
    )


def build_provider(settings: Settings, *, model: str, system_prompt: str | None = None) -> Provider:
    provider_name = getattr(settings, "provider", "claude")
    budget = _execution_budget(settings)
    if provider_name == "claude":
        return build_claude_provider(settings, model=model, system_prompt=system_prompt)
    if provider_name == "codex":
        resolved_model = _resolve_codex_model(settings, model)
        api_key = getattr(settings, "resolved_openai_api_key", None)
        if not api_key:
            auth_mode = getattr(settings, "codex_auth_mode", None) or "unknown"
            if getattr(settings, "codex_auth_file_exists", False):
                raise RuntimeError(
                    "Codex provider now uses the OpenAI Responses API. "
                    f"Detected ~/.codex/auth.json auth_mode={auth_mode!r}, but no local OpenAI API key is available. "
                    "ChatGPT/Codex desktop login alone cannot call /v1/responses; "
                    "set HERMIT_OPENAI_API_KEY or log in with an API-key-backed Codex auth state."
                )
            raise RuntimeError(
                "Codex provider now uses the OpenAI Responses API and requires an OpenAI API key. "
                "Set HERMIT_OPENAI_API_KEY or OPENAI_API_KEY."
            )
        return CodexProvider(
            api_key=api_key,
            model=resolved_model,
            cwd=Path.cwd(),
            system_prompt=system_prompt,
            base_url=settings.openai_base_url or "https://api.openai.com/v1",
            default_headers=settings.parsed_openai_headers,
            connect_timeout=budget.provider_connect_timeout,
            read_timeout=budget.provider_read_timeout,
        )
    if provider_name == "codex-oauth":
        resolved_model = _resolve_codex_model(settings, model)
        if not getattr(settings, "codex_auth_file_exists", False):
            raise RuntimeError(
                "Codex OAuth provider requires a local Codex login. Expected ~/.codex/auth.json."
            )
        auth_path = Path.home() / ".codex" / "auth.json"
        try:
            token_manager = CodexOAuthTokenManager(
                auth_path=auth_path,
                timeout_seconds=budget.provider_read_timeout,
            )
        except TypeError:
            token_manager = CodexOAuthTokenManager(auth_path=auth_path)
        return CodexOAuthProvider(
            token_manager=token_manager,
            model=resolved_model,
            system_prompt=system_prompt,
            default_headers=settings.parsed_openai_headers,
            connect_timeout=budget.provider_connect_timeout,
            read_timeout=budget.provider_read_timeout,
        )
    raise RuntimeError(f"Unsupported provider: {provider_name}")


def build_provider_client_kwargs(settings: Settings, provider: str | None = None) -> dict[str, Any]:
    selected = provider or getattr(settings, "provider", "claude")
    budget = _execution_budget(settings)
    if selected == "claude":
        kwargs: dict[str, Any] = {}
        if settings.claude_api_key:
            kwargs["api_key"] = settings.claude_api_key
        if settings.claude_auth_token:
            kwargs["auth_token"] = settings.claude_auth_token
        if settings.claude_base_url:
            kwargs["base_url"] = settings.claude_base_url
        if settings.parsed_claude_headers:
            kwargs["default_headers"] = settings.parsed_claude_headers
        import httpx

        kwargs["timeout"] = httpx.Timeout(
            budget.provider_read_timeout,
            connect=budget.provider_connect_timeout,
            read=budget.provider_stream_idle_timeout,
            write=budget.provider_read_timeout,
            pool=budget.provider_read_timeout,
        )
        return kwargs
    if selected == "codex":
        kwargs = {}
        if settings.resolved_openai_api_key:
            kwargs["api_key"] = settings.resolved_openai_api_key
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        if settings.parsed_openai_headers:
            kwargs["default_headers"] = settings.parsed_openai_headers
        return kwargs
    if selected == "codex-oauth":
        kwargs = {}
        if settings.codex_access_token:
            kwargs["access_token"] = settings.codex_access_token
        if settings.parsed_openai_headers:
            kwargs["default_headers"] = settings.parsed_openai_headers
        return kwargs
    return {}


def build_runtime(
    settings: Settings,
    *,
    preloaded_skills: list[str] | None = None,
    pm: PluginManager | None = None,
    serve_mode: bool = False,
    cwd: Path | None = None,
    store: KernelStore | None = None,
) -> tuple[AgentRuntime, PluginManager]:
    if pm is None:
        pm = PluginManager(settings=settings)
        builtin_dir = Path(__file__).resolve().parents[3] / "plugins" / "builtin"
        pm.discover_and_load(builtin_dir, settings.plugins_dir)

    workdir = (cwd or Path.cwd()).resolve()
    # Ensure workspace-local tmp directory exists so agents never need /tmp/
    workspace_tmp = workdir / ".hermit" / "tmp"
    try:
        workspace_tmp.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        log.warning(
            "Cannot create workspace tmp directory — permission denied, skipping",
            path=str(workspace_tmp),
        )
    budget = _execution_budget(settings)
    configure_runtime_budget(budget)
    sandbox = CommandSandbox(
        mode=settings.sandbox_mode,
        budget=budget,
        cwd=workdir,
    )
    kernel_store = store or KernelStore(settings.kernel_db_path)
    try:
        registry = create_builtin_tool_registry(
            workdir,
            sandbox,
            config_root_dir=settings.base_dir,
            locale=getattr(settings, "locale", None),
        )
    except TypeError:
        registry = create_builtin_tool_registry(
            workdir,
            sandbox,
            config_root_dir=settings.base_dir,
        )
    pm.setup_tools(registry)
    pm.start_mcp_servers(registry)

    from hermit.runtime.control.runner.runner import AgentRunner

    base_prompt = build_base_context(settings, workdir)
    locale = resolve_locale(getattr(settings, "locale", None))
    visible_commands: list[tuple[str, str]] = [
        (cmd, tr(help_text, locale=locale, default=help_text))
        for cmd, (_fn, help_text, cli_only) in sorted(AgentRunner.core_command_specs().items())
        if not (serve_mode and cli_only)
    ]
    for spec in pm.all_commands:
        if not (serve_mode and spec.cli_only):
            visible_commands.append(
                (spec.name, tr(spec.help_text, locale=locale, default=spec.help_text))
            )
    visible_commands.sort()
    if visible_commands:
        cmd_lines = ["<available_commands>"]
        cmd_lines.append(tr("kernel.provider.available_commands.intro", locale=locale))
        for cmd, help_text in visible_commands:
            cmd_lines.append(f"- `{cmd}` — {help_text}")
        cmd_lines.append("</available_commands>")
        base_prompt = base_prompt + "\n\n" + "\n".join(cmd_lines)

    system_prompt = pm.build_system_prompt(base_prompt, preloaded_skills=preloaded_skills)
    provider = build_provider(settings, model=settings.model, system_prompt=system_prompt)
    runtime_model = getattr(provider, "model", settings.model)
    artifact_store = ArtifactStore(settings.kernel_artifacts_dir)
    approval_copy_service = build_approval_copy_service(settings)
    progress_summarizer = build_progress_summarizer(
        settings,
        provider=provider,
        model=runtime_model,
    )

    # -- Competition / deliberation services --
    from hermit.kernel.execution.competition.deliberation_integration import (
        DeliberationIntegration,
    )
    from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
    from hermit.kernel.execution.competition.llm_critic import CritiqueGenerator
    from hermit.kernel.execution.competition.llm_proposer import ProposalGenerator

    deliberation_model = getattr(settings, "deliberation_model", None) or "claude-sonnet-4-6"

    def _deliberation_provider_factory():
        return build_provider(settings, model=deliberation_model, system_prompt=None)

    proposer = ProposalGenerator(_deliberation_provider_factory, default_model=deliberation_model)
    critic = CritiqueGenerator(_deliberation_provider_factory, default_model=deliberation_model)
    arbitrator = ArbitrationEngine(_deliberation_provider_factory, default_model=deliberation_model)
    deliberation = DeliberationIntegration(
        store=kernel_store,
        artifact_store=artifact_store,
        proposer=proposer,
        critic=critic,
        arbitrator=arbitrator,
    )

    trace_recorder = TraceRecorder(store=kernel_store)

    from hermit.kernel.signals.protocol import SignalProtocol

    signal_protocol = SignalProtocol(kernel_store)

    tool_executor = ToolExecutor(
        registry=registry,
        store=kernel_store,
        artifact_store=artifact_store,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(kernel_store),
        approval_copy_service=approval_copy_service,
        receipt_service=ReceiptService(kernel_store, artifact_store),
        progress_summarizer=progress_summarizer,
        progress_summary_keepalive_seconds=float(
            getattr(settings, "progress_summary_keepalive_seconds", 15.0) or 15.0
        ),
        tool_output_limit=settings.tool_output_limit,
        deliberation=deliberation,
        trace_recorder=trace_recorder,
        signal_protocol=signal_protocol,
    )
    runtime = AgentRuntime(
        provider=provider,
        registry=registry,
        model=runtime_model,
        max_tokens=settings.effective_max_tokens(),
        max_turns=settings.max_turns,
        tool_output_limit=settings.tool_output_limit,
        thinking_budget=settings.thinking_budget,
        system_prompt=system_prompt,
        tool_executor=tool_executor,
        locale=getattr(settings, "locale", None),
    )
    runtime.workspace_root = str(workdir)
    runtime.kernel_store = kernel_store
    runtime.artifact_store = artifact_store
    runtime.task_controller = TaskController(kernel_store)
    runtime.deliberation = deliberation

    from hermit.kernel.context.injection.provider_input import ProviderInputCompiler

    runtime.provider_input_compiler = ProviderInputCompiler(kernel_store, artifact_store)

    pm.configure_subagent_runtime(runtime)
    return runtime, pm


def build_background_runtime(settings: Any, *, cwd: Path) -> tuple[AgentRuntime, PluginManager]:
    return build_runtime(settings, cwd=cwd)


def _resolve_codex_model(settings: Any, requested_model: str) -> str:
    if requested_model and not requested_model.startswith("claude"):
        return requested_model
    config_path = Path.home() / ".codex" / "config.toml"
    if config_path.exists():
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            configured = str(data.get("model", "")).strip()
            if configured:
                return configured
        except Exception:
            log.debug("codex_config_model_read_failed", path=str(config_path))
    return "gpt-5.4"
