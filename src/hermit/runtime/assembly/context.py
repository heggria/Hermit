from __future__ import annotations

from pathlib import Path

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.assembly.config import Settings


def _context_template(locale: str) -> str:
    lines = [
        tr("prompt.context.template.title", locale=locale),
        "",
        tr("prompt.context.template.identity_heading", locale=locale),
        tr("prompt.context.template.identity_line_1", locale=locale),
        "",
        tr("prompt.context.template.goals_heading", locale=locale),
        tr("prompt.context.template.goals_line_1", locale=locale),
        "",
        tr("prompt.context.template.working_style_heading", locale=locale),
        tr("prompt.context.template.working_style.line_1", locale=locale),
        tr("prompt.context.template.working_style.line_2", locale=locale),
        tr("prompt.context.template.working_style.line_3", locale=locale),
        "",
        tr("prompt.context.template.self_config_heading", locale=locale),
        tr("prompt.context.template.self_config.line_1", locale=locale),
        tr("prompt.context.template.self_config.line_2", locale=locale),
        tr("prompt.context.template.self_config.line_3", locale=locale),
        tr("prompt.context.template.self_config.line_4", locale=locale),
    ]
    return "\n".join(lines).strip() + "\n"


DEFAULT_CONTEXT_TEMPLATE = _context_template("en-US")
_ZH_CONTEXT_TEMPLATE = _context_template("zh-CN")


def default_context_template(locale: str | None = None) -> str:
    return _ZH_CONTEXT_TEMPLATE if resolve_locale(locale) == "zh-CN" else DEFAULT_CONTEXT_TEMPLATE


def ensure_default_context_file(path: Path, *, locale: str | None = None) -> None:
    if not path.exists():
        path.write_text(default_context_template(locale), encoding="utf-8")


def load_context_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_base_context(settings: Settings, working_dir: Path) -> str:
    """Build the core system prompt sections (runtime info + user context).

    Plugin-contributed sections (memory, rules, skills) are appended
    separately by PluginManager.build_system_prompt().
    """
    locale = resolve_locale(getattr(settings, "locale", None))
    workspace_tmp_dir = working_dir / ".hermit" / "tmp"
    sections = [
        "<hermit_runtime>",
        f"- current_working_directory: {working_dir}",
        f"- hermit_base_dir: {settings.base_dir}",
        f"- selected_profile: {settings.resolved_profile}",
        f"- current_provider: {settings.provider}",
        f"- current_model: {settings.model}",
        f"- memory_file: {settings.memory_file}",
        f"- session_state_file: {settings.session_state_file}",
        f"- context_file: {settings.context_file}",
        f"- skills_dir: {settings.skills_dir}",
        f"- rules_dir: {settings.rules_dir}",
        f"- hooks_dir: {settings.hooks_dir}",
        f"- plugins_dir: {settings.plugins_dir}",
        f"- image_memory_dir: {settings.image_memory_dir}",
        f"- default_model: {settings.model}",
        f"- max_tokens: {settings.max_tokens}",
        f"- max_turns: {settings.max_turns}",
        f"- sandbox_mode: {settings.sandbox_mode}",
        f"- workspace_tmp_dir: {workspace_tmp_dir}",
        "</hermit_runtime>",
        "",
        "<workspace_boundary>",
        tr("prompt.context.workspace_boundary.line_1", locale=locale),
        tr("prompt.context.workspace_boundary.line_2", locale=locale),
        "</workspace_boundary>",
        "",
        "<self_configuration>",
        tr("prompt.context.self_configuration.line_1", locale=locale),
        tr("prompt.context.self_configuration.line_2", locale=locale),
        tr("prompt.context.self_configuration.line_3", locale=locale),
        tr("prompt.context.self_configuration.line_4", locale=locale),
        tr("prompt.context.self_configuration.line_5", locale=locale),
        "</self_configuration>",
    ]

    user_context = load_context_text(settings.context_file)
    if user_context:
        sections.extend(["", "<user_context>", user_context, "</user_context>"])

    return "\n".join(sections).strip()
