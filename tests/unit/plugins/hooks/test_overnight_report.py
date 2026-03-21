"""Tests for the overnight dashboard and morning report plugin."""

from __future__ import annotations

import time
from types import SimpleNamespace

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.overnight.report import (
    OvernightReportService,
    OvernightSummary,
)
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def _insert_task(
    store: KernelStore,
    task_id: str,
    status: str,
    title: str = "Test task",
    updated_at: float | None = None,
) -> None:
    now = updated_at or time.time()
    with store._get_conn():
        store._get_conn().execute(
            """
            INSERT INTO tasks (
                task_id, conversation_id, title, goal, status, priority,
                owner_principal_id, policy_profile, source_channel,
                created_at, updated_at
            ) VALUES (?, 'conv-1', ?, 'goal', ?, 'normal', 'p-1', 'default', 'cli', ?, ?)
            """,
            (task_id, title, status, now, now),
        )


def _insert_receipt(
    store: KernelStore,
    receipt_id: str,
    created_at: float | None = None,
) -> None:
    now = created_at or time.time()
    with store._get_conn():
        store._get_conn().execute(
            """
            INSERT INTO receipts (
                receipt_id, task_id, step_id, step_attempt_id, action_type,
                input_refs_json, policy_result_json, output_refs_json,
                result_summary, result_code, created_at
            ) VALUES (?, 'task-1', 'step-1', 'sa-1', 'bash',
                '[]', '{}', '[]', 'ok', 'success', ?)
            """,
            (receipt_id, now),
        )


def _insert_approval(
    store: KernelStore,
    approval_id: str,
    status: str = "pending",
    requested_at: float | None = None,
) -> None:
    now = requested_at or time.time()
    with store._get_conn():
        store._get_conn().execute(
            """
            INSERT INTO approvals (
                approval_id, task_id, step_id, step_attempt_id, status,
                approval_type, requested_action_json, requested_at,
                resolution_json
            ) VALUES (?, 'task-1', 'step-1', 'sa-1', ?, 'action', '{}', ?, '{}')
            """,
            (approval_id, status, now),
        )


class TestOvernightSummaryDefaults:
    def test_default_values(self) -> None:
        summary = OvernightSummary()
        assert summary.tasks_completed == []
        assert summary.tasks_failed == []
        assert summary.tasks_blocked == []
        assert summary.tasks_auto_generated == []
        assert summary.total_governed_actions == 0
        assert summary.boundary_violations_prevented == 0
        assert summary.approvals_pending == []
        assert summary.signals_emitted == 0
        assert summary.signals_acted == 0
        assert summary.lookback_hours == 12
        assert summary.generated_at == 0.0


class TestOvernightReportServiceEmptyStore:
    def test_generate_empty_store(self, kernel_store: KernelStore) -> None:
        service = OvernightReportService(kernel_store)
        summary = service.generate(lookback_hours=12)
        assert summary.tasks_completed == []
        assert summary.tasks_failed == []
        assert summary.tasks_blocked == []
        assert summary.total_governed_actions == 0
        assert summary.approvals_pending == []
        assert summary.generated_at > 0
        assert summary.lookback_hours == 12


class TestOvernightReportServiceWithData:
    def test_generate_picks_up_recent_tasks(self, kernel_store: KernelStore) -> None:
        now = time.time()
        _insert_task(kernel_store, "t-1", "completed", "Deploy v2", updated_at=now - 100)
        _insert_task(kernel_store, "t-2", "failed", "Broken build", updated_at=now - 200)
        _insert_task(kernel_store, "t-3", "blocked", "Awaiting approval", updated_at=now - 300)
        # old task outside window
        _insert_task(kernel_store, "t-old", "completed", "Ancient task", updated_at=now - 50000)

        service = OvernightReportService(kernel_store)
        summary = service.generate(lookback_hours=12)

        assert len(summary.tasks_completed) == 1
        assert summary.tasks_completed[0]["task_id"] == "t-1"
        assert len(summary.tasks_failed) == 1
        assert summary.tasks_failed[0]["task_id"] == "t-2"
        assert len(summary.tasks_blocked) == 1
        assert summary.tasks_blocked[0]["task_id"] == "t-3"

    def test_generate_counts_receipts(self, kernel_store: KernelStore) -> None:
        now = time.time()
        _insert_receipt(kernel_store, "r-1", created_at=now - 100)
        _insert_receipt(kernel_store, "r-2", created_at=now - 200)
        _insert_receipt(kernel_store, "r-old", created_at=now - 50000)

        service = OvernightReportService(kernel_store)
        summary = service.generate(lookback_hours=12)
        assert summary.total_governed_actions == 2

    def test_generate_lists_pending_approvals(self, kernel_store: KernelStore) -> None:
        _insert_approval(kernel_store, "a-1", status="pending")
        _insert_approval(kernel_store, "a-2", status="approved")

        service = OvernightReportService(kernel_store)
        summary = service.generate(lookback_hours=12)
        assert len(summary.approvals_pending) == 1
        assert summary.approvals_pending[0]["approval_id"] == "a-1"


class TestFormatMarkdown:
    def test_format_markdown_output(self, kernel_store: KernelStore) -> None:
        summary = OvernightSummary(
            tasks_completed=[{"task_id": "t-1", "title": "Deploy"}],
            tasks_failed=[{"task_id": "t-2", "title": "Build"}],
            tasks_blocked=[],
            total_governed_actions=5,
            approvals_pending=[{"approval_id": "a-1"}],
            signals_emitted=3,
            signals_acted=1,
            lookback_hours=12,
            generated_at=time.time(),
        )
        service = OvernightReportService(kernel_store)
        md = service.format_markdown(summary)
        assert "# Overnight Report" in md
        assert "**Lookback**: 12h" in md
        assert "## Tasks Completed (1)" in md
        assert "[t-1] Deploy" in md
        assert "## Tasks Failed (1)" in md
        assert "[t-2] Build" in md
        assert "## Tasks Blocked (0)" in md
        assert "Governed actions: 5" in md
        assert "Pending approvals: 1" in md
        assert "Signals emitted: 3" in md
        assert "Signals acted: 1" in md


class TestFormatDashboardJson:
    def test_format_dashboard_json_output(self, kernel_store: KernelStore) -> None:
        summary = OvernightSummary(
            tasks_completed=[{"task_id": "t-1"}],
            tasks_failed=[],
            tasks_blocked=[{"task_id": "t-3"}, {"task_id": "t-4"}],
            total_governed_actions=10,
            approvals_pending=[{"approval_id": "a-1"}],
            signals_emitted=7,
            signals_acted=2,
            lookback_hours=8,
            generated_at=1234567890.0,
        )
        service = OvernightReportService(kernel_store)
        result = service.format_dashboard_json(summary)
        assert result["tasks_completed"] == 1
        assert result["tasks_failed"] == 0
        assert result["tasks_blocked"] == 2
        assert result["total_governed_actions"] == 10
        assert result["approvals_pending"] == 1
        assert result["signals_emitted"] == 7
        assert result["signals_acted"] == 2
        assert result["lookback_hours"] == 8
        assert result["generated_at"] == 1234567890.0


class TestOvernightHooks:
    def test_register_hooks(self) -> None:
        from hermit.plugins.builtin.hooks.overnight import hooks as overnight_hooks

        ctx = PluginContext(HooksEngine())
        overnight_hooks.register(ctx)

        # Verify the hook fires without error
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(overnight_enabled=True),
            runner=None,
        )

    def test_disabled_hook(self) -> None:
        from hermit.plugins.builtin.hooks.overnight import hooks as overnight_hooks

        ctx = PluginContext(HooksEngine())
        overnight_hooks.register(ctx)

        # Should not raise when disabled
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(overnight_enabled=False),
            runner=None,
        )
