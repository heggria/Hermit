from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_build_macos_dmg_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "build_macos_dmg.py"
    spec = importlib.util.spec_from_file_location("build_macos_dmg", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_macos_dmg = _load_build_macos_dmg_module()


def test_build_dmg_creates_stage_bundle_and_applications_link(tmp_path: Path, monkeypatch) -> None:
    installed_targets: list[Path] = []
    hdiutil_calls: list[list[str]] = []

    monkeypatch.setattr(build_macos_dmg.sys, "platform", "darwin")
    monkeypatch.setattr(build_macos_dmg.shutil, "which", lambda name: "/usr/bin/hdiutil")
    monkeypatch.setattr(build_macos_dmg, "app_name", lambda base_dir=None: "Hermit Dev")

    def fake_install_app_bundle(
        *,
        target: Path | None = None,
        adapter: str = "feishu",
        profile: str | None = None,
        base_dir: Path | None = None,
    ) -> Path:
        assert target is not None
        installed_targets.append(target)
        (target / "Contents").mkdir(parents=True)
        return target

    def fake_run(cmd: list[str], check: bool) -> None:
        assert check is True
        hdiutil_calls.append(cmd)
        assert cmd[:6] == [
            "hdiutil",
            "create",
            "-volname",
            f"Hermit Dev {build_macos_dmg.__version__}",
            "-srcfolder",
            str(installed_targets[0].parent),
        ]
        assert Path(cmd[5], "Applications").is_symlink()
        Path(cmd[-1]).write_text("fake dmg", encoding="utf-8")

    monkeypatch.setattr(build_macos_dmg, "install_app_bundle", fake_install_app_bundle)
    monkeypatch.setattr(build_macos_dmg.subprocess, "run", fake_run)

    dmg_path = build_macos_dmg.build_dmg(
        adapter="feishu",
        profile="local",
        base_dir=tmp_path / ".hermit-dev",
        out_dir=tmp_path / "dist",
    )

    assert dmg_path == tmp_path / "dist" / f"Hermit-Dev-{build_macos_dmg.__version__}.dmg"
    assert dmg_path.read_text(encoding="utf-8") == "fake dmg"
    assert len(installed_targets) == 1
    assert len(hdiutil_calls) == 1


def test_build_dmg_rejects_non_macos(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(build_macos_dmg.sys, "platform", "linux")

    try:
        build_macos_dmg.build_dmg(
            adapter="feishu",
            profile=None,
            base_dir=None,
            out_dir=tmp_path / "dist",
        )
    except RuntimeError as exc:
        assert str(exc) == "DMG build is only supported on macOS."
    else:
        raise AssertionError("Expected RuntimeError on non-macOS platform")
