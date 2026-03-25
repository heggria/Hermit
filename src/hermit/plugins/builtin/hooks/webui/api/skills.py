"""WebUI API router for user skill definition management."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermit.plugins.builtin.hooks.webui.api.deps import get_runner

_log = structlog.get_logger()

router = APIRouter(tags=["skills"])

# Valid skill name: alphanumeric, hyphens, underscores
_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class SkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    max_tokens: int | None = None


class SkillUpdateRequest(BaseModel):
    description: str | None = None
    content: str | None = None
    max_tokens: int | None = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _user_skills_dir() -> Path:
    """Get the user skills directory for the current environment."""
    runner = get_runner()
    settings = getattr(runner.pm, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Settings not available")
    return settings.skills_dir


def _skill_path(name: str) -> Path:
    return _user_skills_dir() / name / "SKILL.md"


def _build_skill_md(name: str, description: str, content: str, max_tokens: int | None) -> str:
    """Build a SKILL.md file with frontmatter."""
    lines = ["---"]
    lines.append(f"name: {name}")
    if description:
        lines.append(f"description: {description}")
    if max_tokens is not None:
        lines.append(f"max_tokens: {max_tokens}")
    lines.append("---")
    lines.append("")
    if content:
        lines.append(content)
    return "\n".join(lines) + "\n"


def _parse_skill_file(path: Path) -> dict[str, Any]:
    """Read a SKILL.md and return parsed fields."""
    from hermit.runtime.capability.contracts.skills import _parse_frontmatter

    raw = path.read_text(encoding="utf-8").strip()
    fields, body = _parse_frontmatter(raw)
    return {
        "name": fields.get("name", path.parent.name),
        "description": fields.get("description", ""),
        "content": body.strip(),
        "max_tokens": int(fields["max_tokens"]) if fields.get("max_tokens", "").isdigit() else None,
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("/skills")
def create_skill(body: SkillCreateRequest) -> dict[str, Any]:
    """Create a user skill definition."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    if not _VALID_NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="Name must start with alphanumeric and contain only letters, digits, hyphens, underscores",
        )

    skill_dir = _user_skills_dir() / name
    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{name}' already exists")

    md_content = _build_skill_md(name, body.description, body.content, body.max_tokens)

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(md_content, encoding="utf-8")
    except Exception as exc:
        _log.exception("skill_create_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to create skill: {exc}") from exc

    return {"name": name, "needs_reload": True}


@router.patch("/skills/{name}")
def update_skill(name: str, body: SkillUpdateRequest) -> dict[str, Any]:
    """Update a user skill definition."""
    path = _skill_path(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found in user skills")

    current = _parse_skill_file(path)

    description = body.description if body.description is not None else current["description"]
    content = body.content if body.content is not None else current["content"]
    max_tokens = body.max_tokens if body.max_tokens is not None else current["max_tokens"]

    md_content = _build_skill_md(name, description, content, max_tokens)

    try:
        path.write_text(md_content, encoding="utf-8")
    except Exception as exc:
        _log.exception("skill_update_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to update skill: {exc}") from exc

    return {"name": name, "needs_reload": True}


@router.delete("/skills/{name}")
def delete_skill(name: str) -> dict[str, Any]:
    """Delete a user skill definition."""
    skill_dir = _user_skills_dir() / name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found in user skills")

    try:
        shutil.rmtree(skill_dir)
    except Exception as exc:
        _log.exception("skill_delete_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to delete skill: {exc}") from exc

    return {"name": name, "status": "deleted", "needs_reload": True}
