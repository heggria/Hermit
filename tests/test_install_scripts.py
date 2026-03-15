from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_clean_build_artifacts_script_removes_generated_dirs(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "build" / "lib" / "pkg").mkdir(parents=True)
    (repo_dir / "dist").mkdir()
    (repo_dir / "sample.egg-info").mkdir()
    (repo_dir / "sample.dist-info").mkdir()
    (repo_dir / "build" / "lib" / "pkg" / "stale.py").write_text("x = 1\n", encoding="utf-8")

    script = Path("scripts/clean_build_artifacts.py").resolve()
    subprocess.run([sys.executable, str(script), str(repo_dir)], check=True)

    assert not (repo_dir / "build").exists()
    assert not (repo_dir / "dist").exists()
    assert not (repo_dir / "sample.egg-info").exists()
    assert not (repo_dir / "sample.dist-info").exists()


def test_install_macos_uses_clean_build_artifacts_script() -> None:
    script = Path("install-macos.sh").read_text(encoding="utf-8")
    assert "scripts/clean_build_artifacts.py" in script
    assert "clean_local_build_artifacts" in script
