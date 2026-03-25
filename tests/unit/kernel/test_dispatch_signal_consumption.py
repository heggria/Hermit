"""Tests for signal consumption wired into KernelDispatchService."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.execution.coordination.dispatch import (
    KernelDispatchService,
)


def _make_runner() -> SimpleNamespace:
    store = MagicMock()
    tc = SimpleNamespace(store=store)
    runner = SimpleNamespace(
        task_controller=tc,
        process_claimed_attempt=MagicMock(),
        pm=None,
        _competition_service=None,
    )
    return runner


class TestKernelDispatchServiceInit:
    """Verify KernelDispatchService can be instantiated."""

    def test_creates_instance(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner, worker_count=1)
        assert svc.worker_count == 1
        assert not svc.stop_event.is_set()
        assert not svc.wake_event.is_set()
