"""Tests for hermit.apps.companion.appbundle — macOS app bundle management."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermit.apps.companion import appbundle

# ---------------------------------------------------------------------------
# _base_dir_slug
# ---------------------------------------------------------------------------


class TestBaseDirSlug:
    def test_default_hermit_dir_returns_empty(self) -> None:
        assert appbundle._base_dir_slug(Path.home() / ".hermit") == ""

    def test_hermit_dash_suffix(self) -> None:
        assert appbundle._base_dir_slug(Path.home() / ".hermit-dev") == "dev"

    def test_hermit_plain_dot_returns_empty(self) -> None:
        # .hermit that is NOT the default home path but has the same name
        assert appbundle._base_dir_slug(Path("/other/.hermit")) == ""

    def test_dot_prefix_stripped(self) -> None:
        slug = appbundle._base_dir_slug(Path.home() / ".myapp")
        assert slug == "myapp"

    def test_special_chars_normalized(self, tmp_path: Path) -> None:
        slug = appbundle._base_dir_slug(tmp_path / "my@app!")
        # special chars replaced with dash
        assert "@" not in slug
        assert "!" not in slug

    def test_hermit_dash_complex_suffix(self) -> None:
        slug = appbundle._base_dir_slug(Path.home() / ".hermit-my-test")
        assert slug == "my-test"


# ---------------------------------------------------------------------------
# app_name / bundle_id / app_path
# ---------------------------------------------------------------------------


class TestAppName:
    def test_default_returns_hermit(self) -> None:
        result = appbundle.app_name(Path.home() / ".hermit")
        assert result == "Hermit"

    def test_custom_slug_returns_titled(self) -> None:
        result = appbundle.app_name(Path.home() / ".hermit-dev")
        assert result == "Hermit Dev"


class TestBundleId:
    def test_default_returns_base_id(self) -> None:
        result = appbundle.bundle_id(Path.home() / ".hermit")
        assert result == "com.hermit.menubar"

    def test_custom_appends_slug(self) -> None:
        result = appbundle.bundle_id(Path.home() / ".hermit-dev")
        assert result == "com.hermit.menubar.dev"


class TestAppPath:
    def test_default_path(self) -> None:
        result = appbundle.app_path(base_dir=Path.home() / ".hermit")
        assert result == Path.home() / "Applications" / "Hermit.app"

    def test_custom_target(self, tmp_path: Path) -> None:
        target = tmp_path / "Custom.app"
        result = appbundle.app_path(target)
        assert result == target


# ---------------------------------------------------------------------------
# _launcher_command
# ---------------------------------------------------------------------------


class TestLauncherCommand:
    def test_with_companion_bin_exists(self, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
        result = appbundle._launcher_command()
        assert len(result) == 1
        assert "hermit-menubar" in result[0]

    def test_fallback_to_module(self, monkeypatch) -> None:
        monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
        result = appbundle._launcher_command()
        assert result == [sys.executable, "-m", "hermit.apps.companion.menubar"]


# ---------------------------------------------------------------------------
# _bundle_python_target
# ---------------------------------------------------------------------------


class TestBundlePythonTarget:
    def test_returns_resolved_executable(self) -> None:
        result = appbundle._bundle_python_target()
        assert result == Path(sys.executable).resolve()


# ---------------------------------------------------------------------------
# _icon_source
# ---------------------------------------------------------------------------


class TestIconSource:
    def test_project_root_none(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._project_root", lambda: None)
        assert appbundle._icon_source() is None

    def test_svg_exists(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._project_root", lambda: tmp_path)
        svg = tmp_path / "docs" / "hermit-icon.svg"
        svg.parent.mkdir(parents=True, exist_ok=True)
        svg.write_text("<svg/>", encoding="utf-8")
        assert appbundle._icon_source() == svg

    def test_svg_missing(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._project_root", lambda: tmp_path)
        assert appbundle._icon_source() is None


# ---------------------------------------------------------------------------
# _install_bundle_icon
# ---------------------------------------------------------------------------


class TestInstallBundleIcon:
    def test_source_none_returns_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._icon_source", lambda: None)
        assert appbundle._install_bundle_icon(tmp_path) is None

    def test_non_darwin_returns_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle._icon_source", lambda: Path("/fake.svg")
        )
        monkeypatch.setattr(sys, "platform", "linux")
        assert appbundle._install_bundle_icon(tmp_path) is None

    def test_missing_sips_returns_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle._icon_source", lambda: Path("/fake.svg")
        )
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert appbundle._install_bundle_icon(tmp_path) is None


# ---------------------------------------------------------------------------
# install_app_bundle
# ---------------------------------------------------------------------------


class TestInstallAppBundle:
    def test_creates_bundle_structure(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._install_bundle_icon", lambda d: None)
        monkeypatch.setattr("hermit.apps.companion.appbundle._project_root", lambda: None)
        monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
        target = tmp_path / "Test.app"
        bundle = appbundle.install_app_bundle(target=target, base_dir=tmp_path / ".hermit")
        assert bundle == target
        assert (bundle / "Contents" / "MacOS" / "HermitMenu").exists()
        assert (bundle / "Contents" / "Info.plist").exists()
        # Verify launcher is executable
        launcher = bundle / "Contents" / "MacOS" / "HermitMenu"
        assert launcher.stat().st_mode & 0o111

    def test_info_plist_content(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._install_bundle_icon", lambda d: None)
        monkeypatch.setattr("hermit.apps.companion.appbundle._project_root", lambda: None)
        target = tmp_path / "Test.app"
        appbundle.install_app_bundle(target=target, base_dir=Path.home() / ".hermit")
        plist_path = target / "Contents" / "Info.plist"
        info = plistlib.loads(plist_path.read_bytes())
        assert info["CFBundleExecutable"] == "HermitMenu"
        assert info["LSUIElement"] is True
        assert info["CFBundlePackageType"] == "APPL"

    def test_with_profile(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._install_bundle_icon", lambda d: None)
        monkeypatch.setattr("hermit.apps.companion.appbundle._project_root", lambda: None)
        target = tmp_path / "Test.app"
        appbundle.install_app_bundle(
            target=target, profile="test-profile", base_dir=tmp_path / ".hermit"
        )
        launcher = target / "Contents" / "MacOS" / "HermitMenu"
        content = launcher.read_text(encoding="utf-8")
        assert "HERMIT_PROFILE" in content
        assert "test-profile" in content

    def test_with_project_root(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._install_bundle_icon", lambda d: None)
        project = tmp_path / "project"
        project.mkdir()
        (project / "scripts" / "hermit-common.sh").parent.mkdir(parents=True)
        (project / "scripts" / "hermit-common.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        monkeypatch.setattr("hermit.apps.companion.appbundle._project_root", lambda: project)
        target = tmp_path / "Test.app"
        appbundle.install_app_bundle(target=target, base_dir=tmp_path / ".hermit")
        launcher = target / "Contents" / "MacOS" / "HermitMenu"
        content = launcher.read_text(encoding="utf-8")
        assert "uv" in content.lower() or "run" in content

    def test_with_icon_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle._install_bundle_icon",
            lambda d: "HermitMenu.icns",
        )
        monkeypatch.setattr("hermit.apps.companion.appbundle._project_root", lambda: None)
        target = tmp_path / "Test.app"
        appbundle.install_app_bundle(target=target, base_dir=Path.home() / ".hermit")
        plist_path = target / "Contents" / "Info.plist"
        info = plistlib.loads(plist_path.read_bytes())
        assert info["CFBundleIconFile"] == "HermitMenu.icns"


# ---------------------------------------------------------------------------
# open_app_bundle
# ---------------------------------------------------------------------------


class TestOpenAppBundle:
    def test_calls_subprocess(self, monkeypatch) -> None:
        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        appbundle.open_app_bundle(Path("/tmp/Test.app"))
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == ["open", "/tmp/Test.app"]


# ---------------------------------------------------------------------------
# _run_osascript
# ---------------------------------------------------------------------------


class TestRunOsascript:
    def test_returns_stdout(self, monkeypatch) -> None:
        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="  result  \n")
        )
        monkeypatch.setattr("subprocess.run", mock_run)
        result = appbundle._run_osascript("tell app")
        assert result == "result"


# ---------------------------------------------------------------------------
# login_item_enabled / enable / disable
# ---------------------------------------------------------------------------


class TestLoginItemEnabled:
    def test_true(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._run_osascript", lambda s: "true")
        assert appbundle.login_item_enabled("Hermit") is True

    def test_false(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.appbundle._run_osascript", lambda s: "false")
        assert appbundle.login_item_enabled("Hermit") is False

    def test_exception_returns_false(self, monkeypatch) -> None:
        def _raise(s):
            raise OSError("no osascript")

        monkeypatch.setattr("hermit.apps.companion.appbundle._run_osascript", _raise)
        assert appbundle.login_item_enabled("Hermit") is False


class TestEnableLoginItem:
    def test_bundle_missing_raises(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "Missing.app"
        with pytest.raises(RuntimeError, match=r"not found|missing"):
            appbundle.enable_login_item(target)

    def test_success(self, tmp_path: Path, monkeypatch) -> None:
        bundle = tmp_path / "Test.app"
        bundle.mkdir()
        monkeypatch.setattr("hermit.apps.companion.appbundle._run_osascript", lambda s: "")
        result = appbundle.enable_login_item(bundle)
        assert "Test" in result or "Enabled" in result or "enabled" in result


class TestDisableLoginItem:
    def test_calls_osascript(self, monkeypatch) -> None:
        calls = []
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle._run_osascript",
            lambda s: calls.append(s) or "",
        )
        result = appbundle.disable_login_item("Hermit")
        assert len(calls) == 1
        assert "Disabled" in result or "disabled" in result or "Hermit" in result


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_default_args(self) -> None:
        args = appbundle._parse_args([])
        assert args.adapter == "feishu"
        assert args.target is None
        assert args.profile is None
        assert args.base_dir is None
        assert args.open is False
        assert args.enable_login_item is False

    def test_custom_args(self) -> None:
        args = appbundle._parse_args(
            ["--adapter", "slack", "--target", "/tmp/app", "--profile", "prod"]
        )
        assert args.adapter == "slack"
        assert args.target == "/tmp/app"
        assert args.profile == "prod"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_non_darwin_returns_1(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        result = appbundle.main([])
        assert result == 1

    def test_darwin_installs_bundle(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        target = tmp_path / "Test.app"
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle.install_app_bundle",
            lambda **kw: target,
        )
        result = appbundle.main(["--target", str(target)])
        assert result == 0

    def test_darwin_with_open(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        target = tmp_path / "Test.app"
        open_calls = []
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle.install_app_bundle",
            lambda **kw: target,
        )
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle.open_app_bundle",
            lambda t: open_calls.append(t),
        )
        result = appbundle.main(["--target", str(target), "--open"])
        assert result == 0
        assert len(open_calls) == 1

    def test_darwin_with_enable_login_item(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        target = tmp_path / "Test.app"
        enable_calls = []
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle.install_app_bundle",
            lambda **kw: target,
        )
        monkeypatch.setattr(
            "hermit.apps.companion.appbundle.enable_login_item",
            lambda t: enable_calls.append(t) or "ok",
        )
        result = appbundle.main(["--target", str(target), "--enable-login-item"])
        assert result == 0
        assert len(enable_calls) == 1
