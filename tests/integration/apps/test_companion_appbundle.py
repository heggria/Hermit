from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from hermit.apps.companion import appbundle


def test_install_app_bundle_creates_expected_structure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(appbundle, "_bundle_python_target", lambda: Path("/usr/local/bin/python3"))
    monkeypatch.setattr(appbundle, "_project_root", lambda: None)
    monkeypatch.setattr(appbundle, "_install_bundle_icon", lambda resources_dir: None)
    bundle = appbundle.install_app_bundle(
        target=tmp_path / "Hermit.app",
        adapter="feishu",
        profile="codex-local",
        base_dir=tmp_path / ".hermit",
    )

    launcher = bundle / "Contents" / "MacOS" / "HermitMenu"
    info_plist = bundle / "Contents" / "Info.plist"

    assert launcher.exists()
    assert info_plist.exists()
    assert (bundle / "Contents" / "MacOS" / "python3").is_symlink()
    launcher_text = launcher.read_text(encoding="utf-8")
    assert 'export HERMIT_PROFILE="codex-local"' in launcher_text
    assert (
        'exec "$APP_ROOT/python3" -m hermit.apps.companion.menubar --adapter "feishu"'
        in launcher_text
    )


def test_install_app_bundle_uses_environment_specific_name_and_bundle_id(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(appbundle, "_bundle_python_target", lambda: Path("/usr/local/bin/python3"))
    monkeypatch.setattr(appbundle, "_project_root", lambda: None)
    monkeypatch.setattr(appbundle, "_install_bundle_icon", lambda resources_dir: None)
    bundle = appbundle.install_app_bundle(
        target=tmp_path / "Hermit Dev.app",
        adapter="feishu",
        base_dir=tmp_path / ".hermit-dev",
    )

    assert bundle.name == "Hermit Dev.app"
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleDisplayName"] == "Hermit Dev"
    assert info["CFBundleIdentifier"] == "com.hermit.menubar.dev"


def test_install_app_bundle_uses_uv_project_launcher_when_repo_available(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(appbundle, "_bundle_python_target", lambda: Path("/usr/local/bin/python3"))
    monkeypatch.setattr(appbundle, "_project_root", lambda: Path("/Users/beta/work/Hermit"))
    monkeypatch.setattr(appbundle, "_install_bundle_icon", lambda resources_dir: None)
    bundle = appbundle.install_app_bundle(
        target=tmp_path / "Hermit Dev.app",
        adapter="feishu",
        base_dir=tmp_path / ".hermit-dev",
    )

    launcher_text = (bundle / "Contents" / "MacOS" / "HermitMenu").read_text(encoding="utf-8")
    assert 'export HERMIT_BASE_DIR="' in launcher_text
    assert not (bundle / "Contents" / "MacOS" / "python3").exists()
    assert 'source "/Users/beta/work/Hermit/scripts/hermit-common.sh"' in launcher_text
    assert 'UV_BIN="$(resolve_uv_bin)"' in launcher_text
    assert (
        'exec "${UV_BIN}" run --project "/Users/beta/work/Hermit" --python 3.13 python -m hermit.apps.companion.menubar --adapter "feishu"'
        in launcher_text
    )
    assert "/opt/homebrew/bin/uv" not in launcher_text


def test_install_app_bundle_sets_icon_when_generated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(appbundle, "_bundle_python_target", lambda: Path("/usr/local/bin/python3"))
    monkeypatch.setattr(appbundle, "_project_root", lambda: None)

    def fake_install_bundle_icon(resources_dir: Path) -> str:
        icon_path = resources_dir / "HermitMenu.icns"
        icon_path.write_text("fake icns", encoding="utf-8")
        return icon_path.name

    monkeypatch.setattr(appbundle, "_install_bundle_icon", fake_install_bundle_icon)
    bundle = appbundle.install_app_bundle(
        target=tmp_path / "Hermit.app",
        adapter="feishu",
        base_dir=tmp_path / ".hermit",
    )

    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIconFile"] == "HermitMenu.icns"
    assert (bundle / "Contents" / "Resources" / "HermitMenu.icns").exists()


def test_base_dir_helpers_normalize_names(monkeypatch) -> None:
    monkeypatch.setattr(appbundle, "hermit_base_dir", lambda: Path("/tmp/.hermit-dev"))

    assert appbundle._base_dir_slug(Path.home() / ".hermit") == ""
    assert appbundle._base_dir_slug(Path("/tmp/.hermit-dev")) == "dev"
    assert appbundle._base_dir_slug(Path("/tmp/.workspace sandbox")) == "workspace-sandbox"
    assert appbundle._base_dir_slug(Path("/tmp/!!!")) == "custom"
    assert appbundle.app_name() == "Hermit Dev"
    assert (
        appbundle.bundle_id(Path("/tmp/.workspace sandbox"))
        == "com.hermit.menubar.workspace-sandbox"
    )
    assert appbundle.app_path(base_dir=Path("/tmp/.hermit-dev")).name == "Hermit Dev.app"


def test_launcher_command_prefers_companion_binary(tmp_path: Path, monkeypatch) -> None:
    python_bin = tmp_path / "bin" / "python3"
    companion_bin = tmp_path / "bin" / "hermit-menubar"
    companion_bin.parent.mkdir(parents=True)
    python_bin.write_text("", encoding="utf-8")
    companion_bin.write_text("", encoding="utf-8")
    monkeypatch.setattr(appbundle.sys, "executable", str(python_bin))

    assert appbundle._launcher_command() == [str(companion_bin)]

    companion_bin.unlink()

    assert appbundle._launcher_command() == [str(python_bin), "-m", "hermit.apps.companion.menubar"]


def test_project_root_and_icon_source_follow_repo_layout(tmp_path: Path, monkeypatch) -> None:
    fake_file = tmp_path / "pkg" / "companion" / "appbundle.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='hermit'\n", encoding="utf-8")
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    icon = docs_dir / "hermit-icon.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr(appbundle, "__file__", str(fake_file))

    assert appbundle._project_root() == tmp_path
    assert appbundle._icon_source() == icon

    (tmp_path / "pyproject.toml").unlink()

    assert appbundle._project_root() is None
    assert appbundle._icon_source() is None


def test_install_bundle_icon_handles_missing_tools(tmp_path: Path, monkeypatch) -> None:
    resources_dir = tmp_path / "Resources"
    resources_dir.mkdir()
    source = tmp_path / "docs" / "hermit-icon.svg"
    source.parent.mkdir()
    source.write_text("<svg/>", encoding="utf-8")

    monkeypatch.setattr(appbundle, "_icon_source", lambda: source)
    monkeypatch.setattr(appbundle.sys, "platform", "darwin")
    monkeypatch.setattr(appbundle.shutil, "which", lambda name: None)

    assert appbundle._install_bundle_icon(resources_dir) is None


def test_install_bundle_icon_generates_icns_on_macos(tmp_path: Path, monkeypatch) -> None:
    resources_dir = tmp_path / "Resources"
    resources_dir.mkdir()
    source = tmp_path / "docs" / "hermit-icon.svg"
    source.parent.mkdir()
    source.write_text("<svg/>", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        *,
        check: bool = False,
        stdout=None,
        stderr=None,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "--out" in cmd:
            out = Path(cmd[cmd.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("png", encoding="utf-8")
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("icns", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="" if capture_output else None, stderr="")

    monkeypatch.setattr(appbundle, "_icon_source", lambda: source)
    monkeypatch.setattr(appbundle.sys, "platform", "darwin")
    monkeypatch.setattr(appbundle.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(appbundle.subprocess, "run", fake_run)

    icon_name = appbundle._install_bundle_icon(resources_dir)

    assert icon_name == "HermitMenu.icns"
    assert (resources_dir / "HermitMenu.icns").exists()
    assert not (resources_dir / "HermitMenu.iconset").exists()
    assert any(cmd[0] == "iconutil" for cmd in calls)


def test_login_item_helpers_and_open_bundle(tmp_path: Path, monkeypatch) -> None:
    bundle = tmp_path / "Hermit Dev.app"
    bundle.mkdir()

    scripts: list[str] = []
    popen_calls: list[list[str]] = []

    monkeypatch.setattr(
        appbundle, "_run_osascript", lambda script: scripts.append(script) or "true"
    )
    monkeypatch.setattr(
        appbundle.subprocess,
        "Popen",
        lambda cmd, stdout=None, stderr=None: popen_calls.append(cmd),
    )

    assert appbundle.login_item_enabled("Hermit Dev") is True
    assert appbundle.enable_login_item(bundle) == "Enabled login item for Hermit Dev."
    assert "make login item" in scripts[-1]
    assert appbundle.disable_login_item("Hermit Dev") == "Disabled login item for Hermit Dev."

    appbundle.open_app_bundle(bundle)

    assert popen_calls == [["open", str(bundle)]]

    monkeypatch.setattr(
        appbundle,
        "_run_osascript",
        lambda script: (_ for _ in ()).throw(RuntimeError("osascript failed")),
    )

    assert appbundle.login_item_enabled("Hermit Dev") is False


def test_enable_login_item_requires_installed_bundle(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="App bundle not found"):
        appbundle.enable_login_item(tmp_path / "Missing.app")


def test_parse_args_and_main_workflow(tmp_path: Path, monkeypatch, capsys) -> None:
    args = appbundle._parse_args(
        [
            "--target",
            str(tmp_path / "Hermit.app"),
            "--adapter",
            "slack",
            "--profile",
            "dev",
            "--base-dir",
            str(tmp_path / ".hermit-dev"),
            "--open",
            "--enable-login-item",
        ]
    )
    assert args.adapter == "slack"
    assert args.open is True
    assert args.enable_login_item is True

    bundle = tmp_path / "Hermit.app"
    opened: list[Path] = []

    monkeypatch.setattr(appbundle.sys, "platform", "darwin")
    monkeypatch.setattr(appbundle, "install_app_bundle", lambda **kwargs: bundle)
    monkeypatch.setattr(
        appbundle, "enable_login_item", lambda target=None: f"Enabled login item for {bundle.stem}."
    )
    monkeypatch.setattr(
        appbundle, "open_app_bundle", lambda target=None: opened.append(Path(target))
    )

    exit_code = appbundle.main(
        [
            "--target",
            str(bundle),
            "--adapter",
            "slack",
            "--profile",
            "dev",
            "--base-dir",
            str(tmp_path / ".hermit-dev"),
            "--open",
            "--enable-login-item",
        ]
    )

    assert exit_code == 0
    assert opened == [bundle]
    captured = capsys.readouterr()
    assert f"Installed app bundle: {bundle}" in captured.out
    assert f"Enabled login item for {bundle.stem}." in captured.out


def test_main_rejects_non_macos(monkeypatch, capsys) -> None:
    monkeypatch.setattr(appbundle.sys, "platform", "linux")

    assert appbundle.main([]) == 1
    assert "only supported on macOS" in capsys.readouterr().err


def test_appbundle_main_and_login_item_can_render_zh_cn(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    monkeypatch.setattr(appbundle.sys, "platform", "linux")

    assert appbundle.main([]) == 1
    assert "仅支持 macOS" in capsys.readouterr().err

    bundle = tmp_path / "Hermit Dev.app"
    bundle.mkdir()
    monkeypatch.setattr(appbundle, "_run_osascript", lambda script: "ok")
    assert appbundle.enable_login_item(bundle) == "已为 Hermit Dev 启用登录项。"
