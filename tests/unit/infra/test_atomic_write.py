from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermit.infra.storage.atomic import atomic_write


def test_atomic_write_writes_and_cleans_up_temp_files_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "nested" / "value.txt"
    atomic_write(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"

    original_replace = os.replace

    def broken_replace(src, dst) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(RuntimeError, match="boom"):
        atomic_write(tmp_path / "nested" / "broken.txt", "broken")

    assert list((tmp_path / "nested").glob("broken.txt.*.tmp")) == []
    monkeypatch.setattr(os, "replace", original_replace)
