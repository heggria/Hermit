from __future__ import annotations

from typing import Any

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.capability.contracts.skills import SkillDefinition
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec, localize_tool_spec


class SkillLoader:
    """Skill tool registration and handler for the read_skill capability."""

    def __init__(
        self,
        *,
        all_skills: list[SkillDefinition],
        settings: Any,
    ) -> None:
        self._all_skills = all_skills
        self.settings = settings

    def register_skill_tool(self, registry: ToolRegistry) -> None:
        """Register the read_skill tool into the given registry if skills exist."""
        if not self._all_skills:
            return
        locale = resolve_locale(getattr(self.settings, "locale", None))
        skill_names = [s.name for s in self._all_skills]
        registry.register(
            localize_tool_spec(
                ToolSpec(
                    name="read_skill",
                    description=(
                        "Load a skill's full instructions into context. "
                        "Use when a task matches a skill's description from the catalog."
                    ),
                    description_key="prompt.available_skills.read_skill.description",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description_key": "prompt.available_skills.read_skill.name",
                                "enum": skill_names,
                            },
                        },
                        "required": ["name"],
                    },
                    handler=self._read_skill_handler,
                    readonly=True,
                    action_class="read_local",
                    idempotent=True,
                    risk_hint="low",
                    requires_receipt=False,
                    result_is_internal_context=True,
                ),
                locale=locale,
            )
        )

    def _read_skill_handler(self, payload: dict[str, Any]) -> str:
        name = str(payload.get("name", ""))
        locale = resolve_locale(getattr(self.settings, "locale", None))
        for skill in self._all_skills:
            if skill.name == name:
                return f'<skill_content name="{name}">\n{skill.content}\n</skill_content>'
        available = ", ".join(s.name for s in self._all_skills)
        return tr(
            "prompt.available_skills.read_skill.not_found",
            locale=locale,
            default=f"Skill '{name}' not found. Available: {available}",
            name=name,
            available=available,
        )
