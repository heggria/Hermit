from __future__ import annotations

from functools import cached_property
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

    @cached_property
    def _static_prompt_parts(self) -> list[str]:
        """Cache the static portions of the system prompt (rules + skills catalog).

        Rules never change after construction. The skills catalog (listing all
        non-preloaded skills) is also static when no skills are preloaded, which
        is the common case. Preloaded-skill content is handled dynamically in
        build_system_prompt since the set varies per call.
        """
        parts: list[str] = []
        if self._all_rules_parts:
            combined = "\n\n".join(self._all_rules_parts)
            parts.append(f"<rules_context>\n{combined}\n</rules_context>")

        locale = resolve_locale(getattr(self._settings, "locale", None))
        if self._all_skills:
            lines = [
                "<available_skills>",
                tr("prompt.available_skills.intro", locale=locale),
                tr("prompt.available_skills.guidance", locale=locale),
                "",
            ]
            for skill in self._all_skills:
                lines.append(f'  <skill name="{skill.name}">{skill.description}</skill>')
            lines.append("</available_skills>")
            parts.append("\n".join(lines))

        return parts

    def build_system_prompt(
        self,
        base_prompt: str,
        preloaded_skills: list[str] | None = None,
    ) -> str:
        preloaded = set(preloaded_skills or [])

        parts: list[str] = [base_prompt]

        if not preloaded:
            # Fast path: no preloaded skills, use fully cached static parts.
            parts.extend(self._static_prompt_parts)
        else:
            # Slow path: preloaded skills alter the catalog, rebuild skills portion.
            if self._all_rules_parts:
                combined = "\n\n".join(self._all_rules_parts)
                parts.append(f"<rules_context>\n{combined}\n</rules_context>")

            for skill in self._all_skills:
                if skill.name in preloaded:
                    parts.append(
                        f'<skill_content name="{skill.name}">\n{skill.content}\n</skill_content>'
                    )

            catalog_skills = [s for s in self._all_skills if s.name not in preloaded]
            if catalog_skills:
                locale = resolve_locale(getattr(self._settings, "locale", None))
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

        # Dynamic: hook fragments are never cached.
        for fragment in self._hooks.fire(HookEvent.SYSTEM_PROMPT):
            if fragment:
                parts.append(str(fragment))

        return "\n\n".join(p for p in parts if p)
