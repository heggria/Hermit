from __future__ import annotations

from pathlib import Path

from hermit import __version__
from hermit.apps.companion import control, menubar


def test_profile_menu_entry_reports_title_and_availability(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    (base_dir / ".env").write_text("", encoding="utf-8")
    (base_dir / "config.toml").write_text(
        """
default_profile = "ready-profile"

[profiles.ready-profile]
provider = "claude"
claude_api_key = "sk-ant-test"

[profiles.missing-profile]
provider = "codex-oauth"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.delenv("HERMIT_PROFILE", raising=False)
    monkeypatch.delenv("HERMIT_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    ready = menubar._profile_menu_entry("ready-profile", base_dir=base_dir)
    missing = menubar._profile_menu_entry("missing-profile", base_dir=base_dir)

    assert ready == ("ready-profile", True)
    assert missing == ("missing-profile [missing auth]", False)


def test_about_message_includes_runtime_context(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    (base_dir / ".env").write_text("", encoding="utf-8")
    (base_dir / "config.toml").write_text(
        """
default_profile = "ready-profile"

[profiles.ready-profile]
provider = "codex-oauth"
model = "gpt-5.4"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.delenv("HERMIT_PROFILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    message = menubar._about_message(adapter="feishu", base_dir=base_dir)

    assert f"Version: {__version__}" in message
    assert "Adapter: feishu" in message
    assert "Profile: ready-profile" in message
    assert "Provider: codex-oauth" in message
    assert "Model: gpt-5.4" in message
    assert f"README: {control.readme_path()}" in message
    assert f"Docs: {control.docs_path()}" in message
    assert f"Repo: {control.project_repo_url()}" in message
