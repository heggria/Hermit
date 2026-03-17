from __future__ import annotations

from pathlib import Path

import pytest

from hermit.infra.system import executables


def test_resolve_uv_bin_prefers_env_override(monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_UV_BIN", "/tmp/custom-uv")

    assert executables.resolve_uv_bin() == "/tmp/custom-uv"


def test_resolve_uv_bin_uses_fallback_locations_when_uv_not_on_path(
    tmp_path: Path, monkeypatch
) -> None:
    fallback = tmp_path / "uv"
    fallback.write_text("#!/bin/sh\n", encoding="utf-8")
    fallback.chmod(0o755)

    monkeypatch.delenv("HERMIT_UV_BIN", raising=False)
    monkeypatch.setattr(executables.shutil, "which", lambda _name: None)
    monkeypatch.setattr(executables, "_uv_fallback_paths", lambda: (fallback,))

    assert executables.resolve_uv_bin() == str(fallback)


def test_resolve_uv_bin_raises_when_no_candidate_exists(monkeypatch) -> None:
    monkeypatch.delenv("HERMIT_UV_BIN", raising=False)
    monkeypatch.setattr(executables.shutil, "which", lambda _name: None)
    monkeypatch.setattr(executables, "_uv_fallback_paths", lambda: ())

    with pytest.raises(FileNotFoundError):
        executables.resolve_uv_bin()
