"""Unit tests for reconciliation service covering all action type branches."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from hermit.kernel.execution.recovery.reconcile import ReconcileService
from hermit.kernel.execution.recovery.reconciliations import ReconciliationService


def _mock_http_response(status: int = 200) -> MagicMock:
    """Create a mock that works as a context manager returning an object with .status."""
    response = MagicMock()
    response.status = status
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


class _FakeStore:
    """Minimal store for store-observation reconciliation tests."""

    def __init__(self) -> None:
        self._records: dict[str, Any] = {}

    def add_record(self, method: str, key: str, value: Any) -> None:
        self._records.setdefault(method, {})[key] = value

    def get_schedule(self, identifier: str) -> Any | None:
        return self._records.get("get_schedule", {}).get(identifier)

    def get_memory_record(self, identifier: str) -> Any | None:
        return self._records.get("get_memory_record", {}).get(identifier)

    def get_belief(self, identifier: str) -> Any | None:
        return self._records.get("get_belief", {}).get(identifier)

    def get_rollback(self, identifier: str) -> Any | None:
        return self._records.get("get_rollback", {}).get(identifier)

    def get_receipt(self, identifier: str) -> Any | None:
        return self._records.get("get_receipt", {}).get(identifier)

    def get_approval(self, identifier: str) -> Any | None:
        return self._records.get("get_approval", {}).get(identifier)


class TestStoreObservationReconciliation:
    """Test _reconcile_store_observation for all store-observed action types."""

    def test_scheduler_mutation_reconciled_when_record_exists(self) -> None:
        fake_store = _FakeStore()
        fake_store.add_record("get_schedule", "sched-1", SimpleNamespace(id="sched-1"))
        service = ReconcileService(store=fake_store)
        outcome = service.reconcile(
            action_type="scheduler_mutation",
            tool_input={"schedule_id": "sched-1"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "reconciled_applied"
        assert "sched-1" in outcome.observed_refs

    def test_scheduler_mutation_not_applied_when_missing(self) -> None:
        fake_store = _FakeStore()
        service = ReconcileService(store=fake_store)
        outcome = service.reconcile(
            action_type="scheduler_mutation",
            tool_input={"schedule_id": "sched-missing"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "reconciled_not_applied"

    def test_memory_write_reconciled_when_record_exists(self) -> None:
        fake_store = _FakeStore()
        fake_store.add_record("get_memory_record", "mem-1", SimpleNamespace(id="mem-1"))
        service = ReconcileService(store=fake_store)
        outcome = service.reconcile(
            action_type="memory_write",
            tool_input={"memory_id": "mem-1"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "reconciled_applied"

    def test_memory_write_not_applied_when_missing(self) -> None:
        fake_store = _FakeStore()
        service = ReconcileService(store=fake_store)
        outcome = service.reconcile(
            action_type="memory_write",
            tool_input={"memory_id": "mem-missing"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "reconciled_not_applied"

    def test_rollback_reconciled_via_receipt(self) -> None:
        fake_store = _FakeStore()
        fake_store.add_record("get_receipt", "rcpt-1", SimpleNamespace(id="rcpt-1"))
        service = ReconcileService(store=fake_store)
        outcome = service.reconcile(
            action_type="rollback",
            tool_input={"receipt_id": "rcpt-1"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "reconciled_applied"

    def test_approval_resolution_reconciled_when_exists(self) -> None:
        fake_store = _FakeStore()
        fake_store.add_record("get_approval", "appr-1", SimpleNamespace(id="appr-1"))
        service = ReconcileService(store=fake_store)
        outcome = service.reconcile(
            action_type="approval_resolution",
            tool_input={"approval_id": "appr-1"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "reconciled_applied"

    def test_store_observation_returns_still_unknown_without_store(self) -> None:
        service = ReconcileService(store=None)
        outcome = service.reconcile(
            action_type="scheduler_mutation",
            tool_input={"schedule_id": "sched-1"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "still_unknown"

    def test_store_observation_returns_still_unknown_without_identifier(self) -> None:
        fake_store = _FakeStore()
        service = ReconcileService(store=fake_store)
        outcome = service.reconcile(
            action_type="scheduler_mutation",
            tool_input={},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "still_unknown"


class TestLocalWriteReconciliation:
    """Test _reconcile_local_write for write_local and patch_file."""

    def test_local_write_reconciled_when_content_matches(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("hello world", encoding="utf-8")
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "hello world"},
            workspace_root=str(tmp_path),
        )
        assert outcome.result_code == "reconciled_applied"

    def test_local_write_not_applied_when_file_missing(self, tmp_path: Path) -> None:
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="write_local",
            tool_input={"path": "missing.txt", "content": "hello"},
            workspace_root=str(tmp_path),
        )
        assert outcome.result_code == "reconciled_not_applied"

    def test_local_write_not_applied_when_content_differs(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("different content", encoding="utf-8")
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "expected content"},
            workspace_root=str(tmp_path),
        )
        assert outcome.result_code == "reconciled_not_applied"

    def test_local_write_returns_still_unknown_without_path(self) -> None:
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="write_local",
            tool_input={"content": "hello"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "still_unknown"

    def test_local_write_returns_still_unknown_without_workspace_root(self) -> None:
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "hello"},
            workspace_root="",
        )
        assert outcome.result_code == "still_unknown"

    def test_local_write_handles_permission_error(self, tmp_path: Path) -> None:
        target = tmp_path / "locked.txt"
        target.write_text("original", encoding="utf-8")
        target.chmod(0o000)
        service = ReconcileService()
        try:
            outcome = service.reconcile(
                action_type="write_local",
                tool_input={"path": "locked.txt", "content": "text"},
                workspace_root=str(tmp_path),
            )
            assert outcome.result_code == "reconciled_not_applied"
        finally:
            target.chmod(0o644)

    def test_patch_file_uses_local_write_path(self, tmp_path: Path) -> None:
        target = tmp_path / "patched.txt"
        target.write_text("patched content", encoding="utf-8")
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="patch_file",
            tool_input={"path": "patched.txt", "content": "patched content"},
            workspace_root=str(tmp_path),
        )
        assert outcome.result_code == "reconciled_applied"


class TestCommandAndVcsReconciliation:
    """Test _reconcile_command_or_vcs for execute_command and vcs_mutation."""

    def test_changed_paths_detected(self, tmp_path: Path) -> None:
        target = tmp_path / "output.txt"
        target.write_text("new content", encoding="utf-8")
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="execute_command",
            tool_input={},
            workspace_root=str(tmp_path),
            observables={"target_paths": [str(target)]},
            witness={"files": [{"path": str(target), "exists": False}]},
        )
        assert outcome.result_code == "reconciled_applied"
        assert str(target) in outcome.observed_refs

    def test_no_changes_detected_falls_through(self, tmp_path: Path) -> None:
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="execute_command",
            tool_input={},
            workspace_root=str(tmp_path),
            observables={},
            witness={},
        )
        # With no observable targets, the reconciler now returns
        # reconciled_inferred instead of still_unknown.
        assert outcome.result_code == "reconciled_inferred"

    def test_vcs_mutation_not_applied_when_git_unchanged(self, tmp_path: Path) -> None:
        service = ReconcileService()
        with patch.object(service, "_git_changed", return_value=False):
            outcome = service.reconcile(
                action_type="vcs_mutation",
                tool_input={},
                workspace_root=str(tmp_path),
                observables={},
                witness={},
            )
        assert outcome.result_code == "reconciled_not_applied"

    def test_git_changed_returns_reconciled_applied(self, tmp_path: Path) -> None:
        service = ReconcileService()
        with patch.object(service, "_git_changed", return_value=True):
            outcome = service.reconcile(
                action_type="execute_command",
                tool_input={},
                workspace_root=str(tmp_path),
                observables={},
                witness={},
            )
        assert outcome.result_code == "reconciled_applied"


class TestRemoteWriteReconciliation:
    """Test _reconcile_remote_write for network_write, publication, etc."""

    def test_remote_write_returns_still_unknown_without_url(self) -> None:
        service = ReconcileService()
        outcome = service.reconcile(
            action_type="network_write",
            tool_input={"data": "payload"},
            workspace_root="/tmp",
        )
        assert outcome.result_code == "still_unknown"

    def test_remote_write_200_returns_reconciled_observed(self) -> None:
        service = ReconcileService()
        with patch("urllib.request.urlopen", return_value=_mock_http_response(200)):
            outcome = service.reconcile(
                action_type="network_write",
                tool_input={"url": "https://example.com/resource"},
                workspace_root="/tmp",
            )
        assert outcome.result_code == "reconciled_observed"

    def test_remote_write_404_returns_not_applied(self) -> None:
        service = ReconcileService()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "https://example.com/resource", 404, "Not Found", {}, None
            ),
        ):
            outcome = service.reconcile(
                action_type="network_write",
                tool_input={"url": "https://example.com/resource"},
                workspace_root="/tmp",
            )
        assert outcome.result_code == "reconciled_not_applied"

    def test_remote_write_401_returns_reconciled_observed(self) -> None:
        service = ReconcileService()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "https://example.com/resource", 401, "Unauthorized", {}, None
            ),
        ):
            outcome = service.reconcile(
                action_type="network_write",
                tool_input={"url": "https://example.com/resource"},
                workspace_root="/tmp",
            )
        assert outcome.result_code == "reconciled_observed"

    def test_remote_write_500_returns_still_unknown(self) -> None:
        service = ReconcileService()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "https://example.com/resource", 500, "Server Error", {}, None
            ),
        ):
            outcome = service.reconcile(
                action_type="network_write",
                tool_input={"url": "https://example.com/resource"},
                workspace_root="/tmp",
            )
        assert outcome.result_code == "still_unknown"

    def test_remote_write_oserror_returns_still_unknown(self) -> None:
        service = ReconcileService()
        with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
            outcome = service.reconcile(
                action_type="network_write",
                tool_input={"url": "https://example.com/resource"},
                workspace_root="/tmp",
            )
        assert outcome.result_code == "still_unknown"

    def test_publication_routes_to_remote_write(self) -> None:
        service = ReconcileService()
        with patch("urllib.request.urlopen", return_value=_mock_http_response(200)):
            outcome = service.reconcile(
                action_type="publication",
                tool_input={"url": "https://example.com/pub"},
                workspace_root="/tmp",
            )
        assert outcome.result_code == "reconciled_observed"

    def test_external_mutation_routes_to_remote_write(self) -> None:
        service = ReconcileService()
        with patch("urllib.request.urlopen", return_value=_mock_http_response(200)):
            outcome = service.reconcile(
                action_type="external_mutation",
                tool_input={"url": "https://example.com/ext"},
                workspace_root="/tmp",
            )
        assert outcome.result_code == "reconciled_observed"


class TestReconciliationIdempotency:
    """Test that reconcile_attempt is idempotent — same receipt yields same result."""

    def test_duplicate_reconcile_attempt_returns_existing(self, tmp_path: Path) -> None:
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore
        from hermit.kernel.ledger.journal.store import KernelStore
        from hermit.kernel.task.services.controller import TaskController

        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-idempotent",
            goal="test idempotency",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / "test.txt"
        target.write_text("hello\n", encoding="utf-8")

        reconcile_service = ReconcileService()
        service = ReconciliationService(store, artifacts, reconcile_service)

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write a file",
            status="executing",
        )
        receipt = store.create_receipt(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            receipt_class="write_local",
            action_type="write_local",
            result_code="succeeded",
            input_refs=[],
            output_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            result_summary="wrote file",
        )

        first = service.reconcile_attempt(
            attempt_ctx=ctx,
            contract_ref=contract.contract_id,
            receipt_ref=receipt.receipt_id,
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "hello\n"},
            workspace_root=str(workspace),
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="write test.txt",
        )
        first_reconciliation, _, _ = first

        second = service.reconcile_attempt(
            attempt_ctx=ctx,
            contract_ref=contract.contract_id,
            receipt_ref=receipt.receipt_id,
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "hello\n"},
            workspace_root=str(workspace),
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="write test.txt",
        )
        second_reconciliation, _, _ = second

        assert first_reconciliation.reconciliation_id == second_reconciliation.reconciliation_id
        reconciliations = store.list_reconciliations(step_attempt_id=ctx.step_attempt_id, limit=50)
        assert len(reconciliations) == 1
