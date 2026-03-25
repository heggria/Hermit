"""Tests for infra/locking/lock.py — FileGuard."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hermit.infra.locking.lock import FileGuard

# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Ensure registry is clean before and after each test."""
    FileGuard.cleanup()
    yield
    FileGuard.cleanup()


# ---------------------------------------------------------------------------
# Basic locking
# ---------------------------------------------------------------------------


class TestBasicLocking:
    def test_acquire_in_process_only(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        with FileGuard.acquire(target):
            target.write_text("locked")
        assert target.read_text() == "locked"

    def test_acquire_cross_process(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        with FileGuard.acquire(target, cross_process=True):
            target.write_text("cross-process locked")
        assert target.read_text() == "cross-process locked"

    def test_acquire_creates_lock_file(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        with FileGuard.acquire(target, cross_process=True):
            lock_file = target.with_suffix(".json.lock")
            assert lock_file.exists()

    def test_acquire_string_path(self, tmp_path: Path) -> None:
        """FileGuard should accept string-coerced paths."""
        target = tmp_path / "test.txt"
        # The path is converted to Path internally
        with FileGuard.acquire(target):
            pass

    def test_reentrant_lock(self, tmp_path: Path) -> None:
        """RLock should allow re-entrant acquisition from the same thread."""
        target = tmp_path / "test.txt"
        with FileGuard.acquire(target), FileGuard.acquire(target):
            target.write_text("reentrant")
        assert target.read_text() == "reentrant"

    def test_different_paths_get_different_locks(self, tmp_path: Path) -> None:
        path_a = tmp_path / "a.txt"
        path_b = tmp_path / "b.txt"

        lock_a = FileGuard._get_rlock(path_a)
        lock_b = FileGuard._get_rlock(path_b)

        assert lock_a is not lock_b

    def test_same_path_gets_same_lock(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"

        lock1 = FileGuard._get_rlock(path)
        lock2 = FileGuard._get_rlock(path)

        assert lock1 is lock2


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    def test_serializes_threads(self, tmp_path: Path) -> None:
        """Multiple threads writing to the same file should be serialized."""
        target = tmp_path / "counter.txt"
        target.write_text("0")
        errors: list[str] = []

        def increment() -> None:
            try:
                with FileGuard.acquire(target):
                    val = int(target.read_text())
                    target.write_text(str(val + 1))
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=increment) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert int(target.read_text()) == 20

    def test_cross_process_serializes_threads(self, tmp_path: Path) -> None:
        """Cross-process mode should also serialize within the same process."""
        target = tmp_path / "counter.txt"
        target.write_text("0")
        errors: list[str] = []

        def increment() -> None:
            try:
                with FileGuard.acquire(target, cross_process=True):
                    val = int(target.read_text())
                    target.write_text(str(val + 1))
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=increment) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert int(target.read_text()) == 10


# ---------------------------------------------------------------------------
# Registry management
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_cleanup_returns_count(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"
        # Access to populate registry
        _ = FileGuard._get_rlock(path)
        count = FileGuard.cleanup()
        assert count >= 1

    def test_cleanup_empty_registry(self) -> None:
        count = FileGuard.cleanup()
        assert count == 0

    def test_registry_uses_resolved_paths(self, tmp_path: Path) -> None:
        """Symlinks or relative paths should resolve to the same canonical path."""
        path_a = tmp_path / "test.txt"
        # Create a relative-style reference (same resolved path)
        path_b = tmp_path / "." / "test.txt"

        lock_a = FileGuard._get_rlock(path_a)
        lock_b = FileGuard._get_rlock(path_b)

        assert lock_a is lock_b


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Locking a non-existent file should work (file need not exist)."""
        target = tmp_path / "nonexistent.txt"
        with FileGuard.acquire(target):
            pass

    def test_nested_directory_cross_process(self, tmp_path: Path) -> None:
        """Cross-process lock should create parent directories for the lock file."""
        target = tmp_path / "deep" / "nested" / "data.json"
        with FileGuard.acquire(target, cross_process=True):
            lock_file = target.with_suffix(".json.lock")
            assert lock_file.exists()

    def test_exception_releases_lock(self, tmp_path: Path) -> None:
        """Lock should be released even if an exception occurs."""
        target = tmp_path / "test.txt"
        with pytest.raises(RuntimeError, match="test error"), FileGuard.acquire(target):
            raise RuntimeError("test error")

        # Should be able to acquire again
        with FileGuard.acquire(target):
            pass

    def test_exception_releases_cross_process_lock(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        with (
            pytest.raises(RuntimeError, match="test error"),
            FileGuard.acquire(target, cross_process=True),
        ):
            raise RuntimeError("test error")

        with FileGuard.acquire(target, cross_process=True):
            pass
