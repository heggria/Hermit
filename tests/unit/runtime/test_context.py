from __future__ import annotations

from pathlib import Path

from hermit.runtime.assembly.config import Settings
from hermit.runtime.assembly.context import (
    DEFAULT_CONTEXT_TEMPLATE,
    build_base_context,
    default_context_template,
    ensure_default_context_file,
    load_context_text,
)


def test_ensure_default_context_file_creates_template(tmp_path: Path) -> None:
    path = tmp_path / "context.md"

    ensure_default_context_file(path)

    assert path.read_text(encoding="utf-8") == DEFAULT_CONTEXT_TEMPLATE


def test_build_base_context_includes_runtime_and_user_context(tmp_path: Path) -> None:
    settings = Settings(base_dir=tmp_path)
    context_path = settings.context_file
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text("# User Context\nhello", encoding="utf-8")

    prompt = build_base_context(settings=settings, working_dir=tmp_path / "workspace")

    assert "hermit_base_dir" in prompt
    assert "context_file" in prompt
    assert "self_configuration" in prompt
    assert "plugins_dir" in prompt
    assert "image_memory_dir" in prompt
    assert "# User Context" in prompt


def test_build_base_context_without_user_context(tmp_path: Path) -> None:
    settings = Settings(base_dir=tmp_path)
    prompt = build_base_context(settings=settings, working_dir=tmp_path / "workspace")

    assert "hermit_runtime" in prompt
    assert "user_context" not in prompt


def test_load_context_text_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_context_text(tmp_path / "missing.md") == ""


def test_default_context_template_switches_by_locale() -> None:
    assert default_context_template(locale="en-US") == DEFAULT_CONTEXT_TEMPLATE
    assert "你是一个偏个人使用场景的 AI Agent" in default_context_template(locale="zh-CN")
    assert "## Identity" in default_context_template(locale="en-US")


def test_build_base_context_includes_workspace_boundary(tmp_path: Path) -> None:
    settings = Settings(base_dir=tmp_path)
    prompt = build_base_context(settings=settings, working_dir=tmp_path / "workspace")

    assert "workspace_boundary" in prompt
