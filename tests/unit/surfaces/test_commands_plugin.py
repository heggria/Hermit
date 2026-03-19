"""Tests for src/hermit/surfaces/cli/_commands_plugin.py"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import typer.testing

from hermit.surfaces.cli.main import app

runner = typer.testing.CliRunner()


def _fake_settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        base_dir=tmp_path,
        memory_dir=tmp_path / "memory",
        skills_dir=tmp_path / "skills",
        rules_dir=tmp_path / "rules",
        hooks_dir=tmp_path / "hooks",
        plugins_dir=tmp_path / "plugins",
        sessions_dir=tmp_path / "sessions",
        image_memory_dir=tmp_path / "image-memory",
        kernel_dir=tmp_path / "kernel",
        kernel_artifacts_dir=tmp_path / "kernel" / "artifacts",
        context_file=tmp_path / "context.md",
        memory_file=tmp_path / "memory" / "memories.md",
        kernel_db_path=Path(":memory:"),
        locale="en-US",
    )


# ---------------------------------------------------------------------------
# plugin list
# ---------------------------------------------------------------------------
class TestPluginList:
    def test_with_plugins(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_pm = MagicMock()
        mock_pm.manifests = [
            SimpleNamespace(
                builtin=True, name="memory", version="1.0", description="Memory plugin"
            ),
            SimpleNamespace(
                builtin=False, name="custom", version="0.1", description="Custom plugin"
            ),
        ]
        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_plugin.PluginManager", return_value=mock_pm),
        ):
            result = runner.invoke(app, ["plugin", "list"])
        assert result.exit_code == 0
        assert "memory" in result.output
        assert "custom" in result.output
        assert "builtin" in result.output
        assert "installed" in result.output

    def test_no_plugins(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_pm = MagicMock()
        mock_pm.manifests = []
        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_plugin.PluginManager", return_value=mock_pm),
        ):
            result = runner.invoke(app, ["plugin", "list"])
        assert result.exit_code == 0
        assert "No plugins" in result.output


# ---------------------------------------------------------------------------
# plugin install
# ---------------------------------------------------------------------------
class TestPluginInstall:
    def test_successful_clone(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        settings.plugins_dir.mkdir(parents=True, exist_ok=True)
        toml_path = settings.plugins_dir / "my-plugin" / "plugin.toml"

        def mock_run_fn(*args, **kwargs):
            # Simulate git clone creating the directory
            (settings.plugins_dir / "my-plugin").mkdir(parents=True, exist_ok=True)
            toml_path.write_text("[plugin]\nname='test'\n")
            return SimpleNamespace(returncode=0, stderr="")

        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_plugin.subprocess.run", side_effect=mock_run_fn),
        ):
            result = runner.invoke(
                app, ["plugin", "install", "https://github.com/user/my-plugin.git"]
            )
        assert result.exit_code == 0
        assert "Installed" in result.output

    def test_clone_fails(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        settings.plugins_dir.mkdir(parents=True, exist_ok=True)
        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
            patch(
                "hermit.surfaces.cli._commands_plugin.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stderr="fatal: not found"),
            ),
        ):
            result = runner.invoke(app, ["plugin", "install", "https://github.com/user/bad-plugin"])
        assert result.exit_code != 0
        assert "failed" in result.output.lower()

    def test_already_exists(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        settings.plugins_dir.mkdir(parents=True, exist_ok=True)
        (settings.plugins_dir / "existing-plugin").mkdir()
        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
        ):
            result = runner.invoke(
                app, ["plugin", "install", "https://github.com/user/existing-plugin"]
            )
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_missing_manifest_warning(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        settings.plugins_dir.mkdir(parents=True, exist_ok=True)

        def mock_run_fn(*args, **kwargs):
            (settings.plugins_dir / "no-toml").mkdir(parents=True, exist_ok=True)
            # Don't create plugin.toml
            return SimpleNamespace(returncode=0, stderr="")

        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_plugin.subprocess.run", side_effect=mock_run_fn),
        ):
            result = runner.invoke(app, ["plugin", "install", "https://github.com/user/no-toml"])
        assert result.exit_code == 0
        assert "Warning" in result.output or "plugin.toml" in result.output


# ---------------------------------------------------------------------------
# plugin remove
# ---------------------------------------------------------------------------
class TestPluginRemove:
    def test_remove_existing(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        plugin_dir = settings.plugins_dir / "removeme"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("[plugin]\nname='removeme'\n")

        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
        ):
            result = runner.invoke(app, ["plugin", "remove", "removeme"])
        assert result.exit_code == 0
        assert "Removed" in result.output
        assert not plugin_dir.exists()

    def test_remove_not_found(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        settings.plugins_dir.mkdir(parents=True, exist_ok=True)
        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
        ):
            result = runner.invoke(app, ["plugin", "remove", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# plugin info
# ---------------------------------------------------------------------------
class TestPluginInfo:
    def test_found_builtin(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        # Create the plugin dir so is_dir() returns True
        (settings.plugins_dir / "memory").mkdir(parents=True)
        manifest = SimpleNamespace(
            name="memory",
            version="1.0.0",
            description="Memory hooks",
            author="Hermit",
            builtin=True,
            entry={"hooks": "hooks:register"},
            dependencies=["dep1"],
        )
        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
            patch(
                "hermit.runtime.capability.loader.loader.parse_manifest",
                return_value=manifest,
            ),
        ):
            result = runner.invoke(app, ["plugin", "info", "memory"])
        assert result.exit_code == 0
        assert "memory" in result.output
        assert "1.0.0" in result.output
        assert "Memory hooks" in result.output

    def test_not_found(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        settings.plugins_dir.mkdir(parents=True, exist_ok=True)
        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
            patch(
                "hermit.runtime.capability.loader.loader.parse_manifest",
                return_value=None,
            ),
        ):
            result = runner.invoke(app, ["plugin", "info", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_no_author(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        # Create the plugin dir so is_dir() returns True
        (settings.plugins_dir / "test").mkdir(parents=True)
        manifest = SimpleNamespace(
            name="test",
            version="0.1",
            description="Test",
            author=None,
            builtin=False,
            entry=None,
            dependencies=None,
        )
        with (
            patch("hermit.surfaces.cli._commands_plugin.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_plugin.ensure_workspace"),
            patch(
                "hermit.runtime.capability.loader.loader.parse_manifest",
                return_value=manifest,
            ),
        ):
            result = runner.invoke(app, ["plugin", "info", "test"])
        assert result.exit_code == 0
        assert "none" in result.output.lower() or "(none)" in result.output
