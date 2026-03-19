from __future__ import annotations

from typing import Any

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.capability.contracts.base import HookEvent
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.contracts.skills import SkillDefinition


class SystemPromptBuilder:
    """Builds the system prompt including rules, skills catalog, and hook fragments."""

    def __init__(
        self,
        *,
        all_skills: list[SkillDefinition],
        all_rules_parts: list[str],
        hooks: HooksEngine,
        settings: Any,
    ) -> None:
        self._all_skills = all_skills
        self._all_rules_parts = all_rules_parts
        self._hooks = hooks
        self._settings = settings

    def build_system_prompt(
        self,
        base_prompt: str,
        preloaded_skills: list[str] | None = None,
    ) -> str:
        locale = resolve_locale(getattr(self._settings, "locale", None))
        parts: list[str] = [base_prompt]

        if self._all_rules_parts:
            combined = "\n\n".join(self._all_rules_parts)
            parts.append(f"<rules_context>\n{combined}\n</rules_context>")

        preloaded = set(preloaded_skills or [])
        catalog_skills = [s for s in self._all_skills if s.name not in preloaded]

        for skill in self._all_skills:
            if skill.name in preloaded:
                parts.append(
                    f'<skill_content name="{skill.name}">\n{skill.content}\n</skill_content>'
                )

        if catalog_skills:
            lines = [
                "<available_skills>",
                tr("prompt.available_skills.intro", locale=locale),
                tr("prompt.available_skills.guidance", locale=locale),
                "",
            ]
            for skill in catalog_skills:
                lines.append(f'  <skill name="{skill.name}">{skill.description}</skill>')
            lines.append("</available_skills>")
            parts.append("\n".join(lines))

        for fragment in self._hooks.fire(HookEvent.SYSTEM_PROMPT):
            if fragment:
                parts.append(str(fragment))

        return "\n\n".join(p for p in parts if p)
