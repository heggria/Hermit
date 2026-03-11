from __future__ import annotations

import plistlib
from pathlib import Path

from hermit.companion import appbundle


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
    assert 'exec "$APP_ROOT/python3" -m hermit.companion.menubar --adapter "feishu"' in launcher_text


def test_install_app_bundle_uses_environment_specific_name_and_bundle_id(tmp_path: Path, monkeypatch) -> None:
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


def test_install_app_bundle_uses_uv_project_launcher_when_repo_available(tmp_path: Path, monkeypatch) -> None:
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
    assert 'exec /opt/homebrew/bin/uv run --project "/Users/beta/work/Hermit" --python 3.11 python -m hermit.companion.menubar --adapter "feishu"' in launcher_text


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
