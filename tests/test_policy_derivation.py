from __future__ import annotations

from pathlib import Path

from hermit.kernel.policy.derivation import _resolve_target, derive_command_observables


def test_resolve_target_expands_home_before_workspace_join(tmp_path: Path) -> None:
    target = _resolve_target("~/Desktop/hello.txt", str(tmp_path))

    assert target == str((Path.home() / "Desktop" / "hello.txt").resolve())


def test_extract_command_paths_ignores_shell_control_tokens(tmp_path: Path) -> None:
    command = (
        "rm -f ~/Desktop/hello.txt && "
        "if [ ! -e ~/Desktop/hello.txt ]; then echo deleted; else echo still_exists; fi"
    )

    observables = derive_command_observables(command, workspace_root=str(tmp_path))

    assert observables["command_flags"]["deletes_files"] is True
    assert observables["target_paths"] == [str((Path.home() / "Desktop" / "hello.txt").resolve())]


def test_extracts_pathlib_home_write_targets_from_python_commands(tmp_path: Path) -> None:
    command = """python3 - <<'PY'
from pathlib import Path
path = Path.home() / "Desktop" / "hello.txt"
path.write_text("hello\\n", encoding="utf-8")
print(path)
PY"""

    observables = derive_command_observables(command, workspace_root=str(tmp_path))

    assert observables["command_flags"]["writes_disk"] is True
    assert observables["target_paths"] == [str((Path.home() / "Desktop" / "hello.txt").resolve())]
