"""Tests for src/hermit/infra/storage/store.py (JsonStore)

Targets the ~4 missed statements: read() edge cases, update() exception path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermit.infra.storage.store import JsonStore

# ---------------------------------------------------------------------------
# JsonStore.read()
# ---------------------------------------------------------------------------


class TestJsonStoreRead:
    def test_read_nonexistent_returns_default(self, tmp_path: Path) -> None:
        store = JsonStore(tmp_path / "missing.json")
        assert store.read() == {}

    def test_read_custom_default(self, tmp_path: Path) -> None:
        store = JsonStore(tmp_path / "missing.json", default={"key": "value"})
        result = store.read()
        assert result == {"key": "value"}

    def test_read_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        path.write_text('{"hello": "world"}', encoding="utf-8")
        store = JsonStore(path)
        assert store.read() == {"hello": "world"}

    def test_read_invalid_json_returns_default(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        store = JsonStore(path, default={"fallback": True})
        result = store.read()
        assert result == {"fallback": True}

    def test_read_empty_file_returns_default(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        store = JsonStore(path, default={"empty": True})
        result = store.read()
        assert result == {"empty": True}

    def test_read_returns_copy_of_default(self, tmp_path: Path) -> None:
        default = {"key": "value"}
        store = JsonStore(tmp_path / "missing.json", default=default)
        result = store.read()
        result["key"] = "modified"
        assert default["key"] == "value"  # original unchanged


# ---------------------------------------------------------------------------
# JsonStore.write()
# ---------------------------------------------------------------------------


class TestJsonStoreWrite:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        path = tmp_path / "new.json"
        store = JsonStore(path)
        store.write({"created": True})
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"created": True}

    def test_write_overwrites_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        path.write_text('{"old": true}', encoding="utf-8")
        store = JsonStore(path)
        store.write({"new": True})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"new": True}

    def test_write_unicode(self, tmp_path: Path) -> None:
        path = tmp_path / "unicode.json"
        store = JsonStore(path)
        store.write({"name": "hermit"})
        content = path.read_text(encoding="utf-8")
        assert "hermit" in content


# ---------------------------------------------------------------------------
# JsonStore.update()
# ---------------------------------------------------------------------------


class TestJsonStoreUpdate:
    def test_update_modifies_and_persists(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        store = JsonStore(path)
        store.write({"counter": 0})

        with store.update() as data:
            data["counter"] = data["counter"] + 1

        result = store.read()
        assert result["counter"] == 1

    def test_update_exception_does_not_persist(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        store = JsonStore(path)
        store.write({"value": "original"})

        with pytest.raises(RuntimeError), store.update() as data:
            data["value"] = "modified"
            raise RuntimeError("abort!")

        result = store.read()
        assert result["value"] == "original"

    def test_update_from_nonexistent_file(self, tmp_path: Path) -> None:
        path = tmp_path / "new.json"
        store = JsonStore(path, default={"init": True})

        with store.update() as data:
            data["added"] = "value"

        result = store.read()
        assert result["init"] is True
        assert result["added"] == "value"

    def test_update_sequential(self, tmp_path: Path) -> None:
        path = tmp_path / "seq.json"
        store = JsonStore(path, default={"count": 0})

        for _ in range(3):
            with store.update() as data:
                data["count"] = data.get("count", 0) + 1

        result = store.read()
        assert result["count"] == 3
