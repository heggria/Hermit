from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.config import Settings
from hermit.context import build_base_context
from hermit.core.budgets import ExecutionBudget, configure_runtime_budget
from hermit.core.sandbox import CommandSandbox
from hermit.core.tools import create_builtin_tool_registry
from hermit.i18n import resolve_locale, tr
from hermit.kernel import (
    ApprovalCopyService,
    ApprovalService,
    ArtifactStore,
    KernelStore,
    PolicyEngine,
    ReceiptService,
    TaskController,
    ToolExecutor,
)
from hermit.kernel.progress_summary import ProgressSummary, ProgressSummaryFormatter
from hermit.plugin.manager import PluginManager
from hermit.provider.contracts import Provider, ProviderRequest, ProviderResponse
from hermit.provider.messages import extract_text
from hermit.provider.providers import (
    CodexOAuthProvider,
    CodexOAuthTokenManager,
    CodexProvider,
    build_claude_provider,
)
from hermit.provider.runtime import AgentRuntime

log = structlog.get_logger()

_APPROVAL_COPY_SYSTEM_PROMPT = (
    "You rewrite approval prompts into user-friendly English product copy. "
    "You must only use the supplied JSON facts and must not invent any targets, commands, risks, or services. "
    "Return strict JSON with exactly these keys: title, summary, detail. "
    "Keep summary and detail concise, clear, and human. "
    "Explain what the tool is about to do and why approval is needed. "
    "Do not dump raw shell commands into summary or detail unless absolutely necessary."
)

_PROGRESS_SUMMARY_SYSTEM_PROMPT = (
    "You write short live progress updates for an AI agent task. "
    "You must only use the supplied JSON facts and must not invent any steps, results, blockers, or tools. "
    "Return strict JSON with exactly these keys: summary, detail, phase, progress_percent. "
    "The summary must be a single short sentence describing what the task is doing right now. "
    "The detail should be optional, compact, and explain the next likely step or blocker when useful. "
    "Keep the tone calm and operator-friendly, like a live task update. "
    "Do not mention internal IDs, JSON, or implementation details."
)


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
        builtin_dir = Path(__file__).resolve().parents[1] / "builtin"
        pm.discover_and_load(builtin_dir, settings.plugins_dir)

    workdir = (cwd or Path.cwd()).resolve()
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

    from hermit.core.runner import AgentRunner

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
    runtime.workspace_root = str(workdir)  # type: ignore[attr-defined]
    runtime.kernel_store = kernel_store  # type: ignore[attr-defined]
    runtime.artifact_store = artifact_store  # type: ignore[attr-defined]
    runtime.task_controller = TaskController(kernel_store)  # type: ignore[attr-defined]
    pm.configure_subagent_runtime(runtime)
    return runtime, pm


class StructuredExtractionService:
    def __init__(self, provider: Provider, *, model: str) -> None:
        self.provider = provider
        self.model = model

    def extract_json(
        self, *, system_prompt: str, user_content: str, max_tokens: int = 2048
    ) -> dict[str, Any] | None:
        response = self.provider.generate(
            request=self._request(
                system_prompt=system_prompt, user_content=user_content, max_tokens=max_tokens
            )
        )
        return _parse_json_response(response)

    def _request(self, *, system_prompt: str, user_content: str, max_tokens: int) -> Any:
        from hermit.provider.contracts import ProviderRequest

        return ProviderRequest(
            model=self.model,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )


class LLMProgressSummarizer:
    def __init__(
        self,
        provider: Provider,
        *,
        model: str,
        locale: str | None = None,
        max_tokens: int = 160,
    ) -> None:
        self.provider = provider
        self.model = model
        self.locale = resolve_locale(locale)
        self.max_tokens = max_tokens

    def summarize(self, *, facts: dict[str, Any]) -> ProgressSummary | None:
        response = self.provider.generate(
            ProviderRequest(
                model=self.model,
                max_tokens=self.max_tokens,
                system_prompt=self._system_prompt(),
                messages=[
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=2)}
                ],
            )
        )
        parsed = _parse_json_response(response)
        if not isinstance(parsed, dict):
            return None
        summary = str(parsed.get("summary", "") or "").strip()
        if not summary:
            return None
        return ProgressSummary.from_dict(parsed)

    def _system_prompt(self) -> str:
        language = "Simplified Chinese" if self.locale.lower().startswith("zh") else "English"
        return f"{_PROGRESS_SUMMARY_SYSTEM_PROMPT} Write the summary in {language}."


class VisionAnalysisService:
    def __init__(self, provider: Provider, *, model: str) -> None:
        self.provider = provider
        self.model = model

    def analyze_image(
        self, *, system_prompt: str, text: str, image_block: dict[str, Any], max_tokens: int = 512
    ) -> dict[str, Any] | None:
        if not self.provider.features.supports_images:
            raise RuntimeError(f"Provider '{self.provider.name}' does not support image analysis")
        from hermit.provider.contracts import ProviderRequest

        response = self.provider.generate(
            ProviderRequest(
                model=self.model,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                messages=[
                    {"role": "user", "content": [image_block, {"type": "text", "text": text}]}
                ],
            )
        )
        return _parse_json_response(response)


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


def _parse_json_response(response: ProviderResponse) -> dict[str, Any] | None:
    raw = extract_text(response.content)
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    for candidate in (cleaned, raw):
        try:
            parsed = json.loads(candidate)
            return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            continue
    brace_start = cleaned.find("{")
    if brace_start >= 0:
        fragment = cleaned[brace_start:]
        for suffix in ("", "}", "]}", '"}', '"]}', '"]}'):
            try:
                parsed = json.loads(fragment + suffix)
                return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                continue
    log.warning("provider_json_parse_failed", preview=raw[:200])
    return None


class LLMApprovalFormatter:
    def __init__(
        self,
        provider: Provider,
        *,
        model: str,
        locale: str | None = None,
        max_tokens: int = 120,
    ) -> None:
        self.provider = provider
        self.model = model
        self.locale = resolve_locale(locale)
        self.max_tokens = max_tokens

    def format(self, facts: dict[str, Any]) -> dict[str, str] | None:
        response = self.provider.generate(
            ProviderRequest(
                model=self.model,
                max_tokens=self.max_tokens,
                system_prompt=tr(
                    "kernel.provider.approval_formatter.system_prompt",
                    locale=self.locale,
                    default=_APPROVAL_COPY_SYSTEM_PROMPT,
                ),
                messages=[
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=2)}
                ],
            )
        )
        parsed = _parse_json_response(response)
        if not isinstance(parsed, dict):
            return None
        title = str(parsed.get("title", "")).strip()
        summary = str(parsed.get("summary", "")).strip()
        detail = str(parsed.get("detail", "")).strip()
        if not title or not summary or not detail:
            return None
        return {
            "title": title,
            "summary": summary,
            "detail": detail,
        }


def build_approval_copy_service(settings: Any) -> ApprovalCopyService:
    locale = getattr(settings, "locale", None)
    if not bool(getattr(settings, "approval_copy_formatter_enabled", False)):
        return ApprovalCopyService(locale=locale)
    try:
        model = getattr(settings, "approval_copy_model", None) or getattr(settings, "model", "")
        provider = build_provider(settings, model=model, system_prompt=None)
        formatter = LLMApprovalFormatter(
            provider,
            model=getattr(provider, "model", model),
            locale=locale,
        )
        return ApprovalCopyService(
            formatter=formatter.format,
            formatter_timeout_ms=int(getattr(settings, "approval_copy_formatter_timeout_ms", 500)),
            locale=locale,
        )
    except Exception as exc:
        log.warning("approval_copy_formatter_init_failed", error=str(exc))
        return ApprovalCopyService(locale=locale)


def build_progress_summarizer(
    settings: Any,
    *,
    provider: Provider,
    model: str,
) -> ProgressSummaryFormatter | None:
    if not bool(getattr(settings, "progress_summary_enabled", True)):
        return None
    summary_model = getattr(settings, "progress_summary_model", None) or model
    try:
        summary_provider = provider.clone(model=summary_model, system_prompt=None)
        return LLMProgressSummarizer(
            summary_provider,
            model=summary_model,
            locale=getattr(settings, "locale", None),
            max_tokens=int(getattr(settings, "progress_summary_max_tokens", 160) or 160),
        )
    except Exception as exc:
        log.warning("progress_summarizer_init_failed", error=str(exc))
        return None
