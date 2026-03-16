from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_YAML_FIELD_RE = re.compile(r'^(\w[\w-]*):\s*"?(.*?)"?\s*$')


@dataclass
class SkillDefinition:
    name: str
    description: str
    path: Path
    content: str


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Extract YAML frontmatter fields and remaining body from a SKILL.md.

    Uses simple regex parsing to avoid a PyYAML dependency.
    Handles unquoted colons in values (common in skill descriptions).
    """
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw

    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        fm = _YAML_FIELD_RE.match(line)
        if fm:
            fields[fm.group(1)] = fm.group(2)

    body = raw[m.end() :]
    return fields, body


def load_skills(skills_dir: Path) -> list[SkillDefinition]:
    if not skills_dir.exists():
        return []

    skills: list[SkillDefinition] = []
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        raw = path.read_text(encoding="utf-8").strip()
        fields, body = _parse_frontmatter(raw)

        name = fields.get("name", path.parent.name)
        description = fields.get("description", "")
        if not description:
            description = next(
                (line.strip("# ").strip() for line in body.splitlines() if line.strip()),
                "No description",
            )

        skills.append(
            SkillDefinition(
                name=name,
                description=description,
                path=path,
                content=body.strip(),
            )
        )
    return skills
