from __future__ import annotations

from pathlib import Path


def load_rules_text(rules_dir: Path) -> str:
    if not rules_dir.exists():
        return ""

    parts: list[str] = []
    for path in sorted(rules_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        parts.append(f'<rule path="{path.name}">\n{content}\n</rule>')
    return "\n\n".join(parts)
