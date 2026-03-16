from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_clean_build_artifacts_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "clean_build_artifacts.py"
    spec = importlib.util.spec_from_file_location("clean_build_artifacts", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clean_build_artifacts = _load_clean_build_artifacts_module()


def test_clean_build_artifacts_script_removes_generated_dirs(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "build" / "lib" / "pkg").mkdir(parents=True)
    (repo_dir / "dist").mkdir()
    (repo_dir / "sample.egg-info").mkdir()
    (repo_dir / "sample.dist-info").mkdir()
    (repo_dir / "build" / "lib" / "pkg" / "stale.py").write_text("x = 1\n", encoding="utf-8")

    removed = clean_build_artifacts.clean_build_artifacts(repo_dir)

    assert not (repo_dir / "build").exists()
    assert not (repo_dir / "dist").exists()
    assert not (repo_dir / "sample.egg-info").exists()
    assert not (repo_dir / "sample.dist-info").exists()
    assert {path.name for path in removed} == {
        "build",
        "dist",
        "sample.egg-info",
        "sample.dist-info",
    }


def test_install_macos_uses_clean_build_artifacts_script() -> None:
    script = Path("install-macos.sh").read_text(encoding="utf-8")
    assert "scripts/clean_build_artifacts.py" in script
    assert "clean_local_build_artifacts" in script
