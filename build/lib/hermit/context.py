from __future__ import annotations

from pathlib import Path

from hermit.config import Settings
from hermit.i18n import resolve_locale, tr

DEFAULT_CONTEXT_TEMPLATE = """# Hermit Context

## Identity
- You are an AI agent optimized for personal-use workflows.

## Long-Term Goals
- Help the user complete research, coding, configuration, and automation tasks.

## Working Style
- Prefer reusing existing configuration, rules, and skills.
- Read the current state first before changing Hermit's own configuration, and keep edits minimal.
- Do not write API keys, passwords, or tokens into `memory/memories.md`, `context.md`, or public docs.

## Self-Configuration Conventions
- Store long-term preferences and background in `context.md`
- Store hard constraints in `rules/*.md`
- Store reusable workflows in `skills/*/SKILL.md`
- Keep sensitive configuration only in `.env`
"""

_ZH_CONTEXT_TEMPLATE = """# Hermit Context

## 身份
- 你是一个偏个人使用场景的 AI Agent。

## 长期目标
- 帮用户完成研究、编码、配置和自动化任务。

## 工作方式
- 优先复用已有配置、rules、skills。
- 修改自身配置前先读取现状，再做最小改动。
- 不要把 API Key、密码、令牌写入 `memory/memories.md`、`context.md` 或公开文档。

## 自我配置约定
- 长期偏好与背景信息写入 `context.md`
- 强约束写入 `rules/*.md`
- 可复用工作流写入 `skills/*/SKILL.md`
- 敏感配置仅保存在 `.env`
"""


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
        "</hermit_runtime>",
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
