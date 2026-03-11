from __future__ import annotations

from pathlib import Path

from hermit.config import Settings

DEFAULT_CONTEXT_TEMPLATE = """# Hermit Context

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


def ensure_default_context_file(path: Path) -> None:
    if not path.exists():
        path.write_text(DEFAULT_CONTEXT_TEMPLATE, encoding="utf-8")


def load_context_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_base_context(settings: Settings, working_dir: Path) -> str:
    """Build the core system prompt sections (runtime info + user context).

    Plugin-contributed sections (memory, rules, skills) are appended
    separately by PluginManager.build_system_prompt().
    """
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
        "你可以通过专用配置工具读取和修改 Hermit 自己的配置目录，从而具备自我配置能力。",
        "修改原则：先读后写、最小改动、保留用户已有内容、不要把 secrets 写入 context/memory/rules/skills。",
        "推荐落点：长期背景写 `context.md`；硬规则写 `rules/*.md`；可复用流程写 `skills/*/SKILL.md`；敏感变量写 `.env`。",
        "如果用户询问“你当前用的什么模型 / provider / profile / 默认配置”，必须严格以 <hermit_runtime> 中的 current_model / current_provider / selected_profile 为准回答。",
        "不要根据记忆、历史对话、常识或推测回答当前模型；若历史内容与 <hermit_runtime> 冲突，以 <hermit_runtime> 为准并明确说明已切换。",
        "</self_configuration>",
    ]

    user_context = load_context_text(settings.context_file)
    if user_context:
        sections.extend(["", "<user_context>", user_context, "</user_context>"])

    return "\n".join(sections).strip()
