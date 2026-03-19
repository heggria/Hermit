"""Tests for src/hermit/surfaces/cli/_commands_task.py"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import typer.testing

from hermit.surfaces.cli.main import app

runner = typer.testing.CliRunner()


def _fake_task(**overrides) -> SimpleNamespace:
    defaults = dict(
        task_id="task-001",
        status="completed",
        source_channel="mcp",
        title="Test task",
        conversation_id="conv-001",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_approval(**overrides) -> SimpleNamespace:
    defaults = dict(
        approval_id="apr-001",
        status="pending",
        approval_type="tool_use",
        requested_action={"action": "bash", "tool": "bash"},
        decision_ref="dec-001",
        state_witness_ref="wit-001",
        task_id="task-001",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_decision(**overrides) -> SimpleNamespace:
    defaults = dict(
        decision_id="dec-001",
        verdict="approved",
        decision_type="auto",
        action_type="tool_use",
        reason="Policy allows this action.",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_grant(**overrides) -> SimpleNamespace:
    defaults = dict(
        grant_id="grant-001",
        status="active",
        action_class="file_write",
        decision_ref="dec-001",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_lease(**overrides) -> SimpleNamespace:
    defaults = dict(
        lease_id="lease-001",
        status="active",
        mode="mutable",
        root_path="/workspace/project",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_store_with_all():
    """Create a mock store pre-loaded with task sub-records."""
    mock_store = MagicMock()
    mock_store.get_task.return_value = _fake_task()
    mock_store.list_approvals.return_value = [_fake_approval()]
    mock_store.list_decisions.return_value = [_fake_decision()]
    mock_store.list_capability_grants.return_value = [_fake_grant()]
    mock_store.list_workspace_leases.return_value = [_fake_lease()]
    mock_store.list_execution_contracts.return_value = []
    mock_store.list_evidence_cases.return_value = []
    mock_store.list_authorization_plans.return_value = []
    mock_store.list_reconciliations.return_value = []
    return mock_store


def _supervision_case() -> dict:
    return {
        "operator_answers": {
            "claims": {
                "task_gate": {
                    "verifiable_ready": True,
                    "strong_verifiable_ready": False,
                    "proof_mode": "standard",
                    "strongest_export_mode": "summary",
                },
                "repository": {"claimable_profiles": ["basic"]},
            },
            "reentry": {
                "required_count": 1,
                "resolved_count": 1,
                "recent_attempts": [
                    {
                        "step_attempt_id": "sa-001",
                        "status": "resolved",
                        "reentry_reason": "tool_error",
                        "reentry_boundary": "step",
                        "recovery_required": False,
                    }
                ],
            },
        }
    }


# ---------------------------------------------------------------------------
# task list
# ---------------------------------------------------------------------------
class TestTaskList:
    def test_with_tasks(self) -> None:
        mock_store = MagicMock()
        mock_store.list_tasks.return_value = [_fake_task(), _fake_task(task_id="task-002")]
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "list"])
        assert result.exit_code == 0
        assert "task-001" in result.output
        assert "task-002" in result.output

    def test_empty_tasks(self) -> None:
        mock_store = MagicMock()
        mock_store.list_tasks.return_value = []
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "list"])
        assert result.exit_code == 0
        assert "No tasks" in result.output


# ---------------------------------------------------------------------------
# task show
# ---------------------------------------------------------------------------
class TestTaskShow:
    def test_task_found(self) -> None:
        mock_store = _mock_store_with_all()
        copy_result = SimpleNamespace(summary="Approve bash tool execution")
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.ApprovalCopyService") as MockCopy,
            patch("hermit.surfaces.cli._commands_task.SupervisionService") as MockSuper,
        ):
            MockCopy.return_value.resolve_copy.return_value = copy_result
            MockSuper.return_value.build_task_case.return_value = _supervision_case()
            result = runner.invoke(app, ["task", "show", "task-001"])
        assert result.exit_code == 0
        assert "task-001" in result.output
        assert "apr-001" in result.output
        assert "dec-001" in result.output
        assert "grant-001" in result.output

    def test_task_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.get_task.return_value = None
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "show", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_task_with_contract_loop(self) -> None:
        mock_store = _mock_store_with_all()
        contract = SimpleNamespace(
            contract_id="ct-001",
            status="fulfilled",
            contract_version=1,
            objective="Fix bug",
            expected_effects=["file_write", "test_pass"],
        )
        evidence = SimpleNamespace(
            evidence_case_id="ev-001",
            status="sufficient",
            sufficiency_score=0.95,
            unresolved_gaps=[],
        )
        auth_plan = SimpleNamespace(
            authorization_plan_id="ap-001",
            status="approved",
            approval_route="auto",
            current_gaps=[],
        )
        reconciliation = SimpleNamespace(
            reconciliation_id="rc-001",
            result_class="match",
            recommended_resolution=None,
        )
        mock_store.list_execution_contracts.return_value = [contract]
        mock_store.list_evidence_cases.return_value = [evidence]
        mock_store.list_authorization_plans.return_value = [auth_plan]
        mock_store.list_reconciliations.return_value = [reconciliation]
        copy_result = SimpleNamespace(summary="Approve")
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.ApprovalCopyService") as MockCopy,
            patch("hermit.surfaces.cli._commands_task.SupervisionService") as MockSuper,
        ):
            MockCopy.return_value.resolve_copy.return_value = copy_result
            MockSuper.return_value.build_task_case.return_value = _supervision_case()
            result = runner.invoke(app, ["task", "show", "task-001"])
        assert result.exit_code == 0
        assert "ct-001" in result.output
        assert "ev-001" in result.output


# ---------------------------------------------------------------------------
# task events
# ---------------------------------------------------------------------------
class TestTaskEvents:
    def test_returns_json(self) -> None:
        mock_store = MagicMock()
        mock_store.list_events.return_value = [{"event": "created"}]
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "events", "task-001"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# task receipts
# ---------------------------------------------------------------------------
class TestTaskReceipts:
    def test_returns_json(self) -> None:
        mock_store = MagicMock()
        receipt = SimpleNamespace(receipt_id="r-001")
        mock_store.list_receipts.return_value = [receipt]
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "receipts"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert len(parsed) == 1


# ---------------------------------------------------------------------------
# task explain / case
# ---------------------------------------------------------------------------
class TestTaskExplainCase:
    def test_explain(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.SupervisionService") as MockSuper,
        ):
            MockSuper.return_value.build_task_case.return_value = {"case": "data"}
            result = runner.invoke(app, ["task", "explain", "task-001"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["case"] == "data"

    def test_case(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.SupervisionService") as MockSuper,
        ):
            MockSuper.return_value.build_task_case.return_value = {"case": "view"}
            result = runner.invoke(app, ["task", "case", "task-001"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# task proof / proof-export
# ---------------------------------------------------------------------------
class TestTaskProof:
    def test_proof_summary(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.ProofService") as MockProof,
        ):
            MockProof.return_value.build_proof_summary.return_value = {"proof": "summary"}
            result = runner.invoke(app, ["task", "proof", "task-001"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["proof"] == "summary"

    def test_proof_export_stdout(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.ProofService") as MockProof,
        ):
            MockProof.return_value.export_task_proof.return_value = {"bundle": "data"}
            result = runner.invoke(app, ["task", "proof-export", "task-001"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["bundle"] == "data"

    def test_proof_export_to_file(self, tmp_path: Path) -> None:
        output_file = tmp_path / "proof.json"
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.ProofService") as MockProof,
        ):
            MockProof.return_value.export_task_proof.return_value = {"bundle": "file"}
            result = runner.invoke(
                app, ["task", "proof-export", "task-001", "--output", str(output_file)]
            )
        assert result.exit_code == 0
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["bundle"] == "file"


# ---------------------------------------------------------------------------
# task claim-status
# ---------------------------------------------------------------------------
class TestTaskClaimStatus:
    def test_without_task_id(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch(
                "hermit.surfaces.cli._commands_task.repository_claim_status",
                return_value={"claims": "repo"},
            ),
        ):
            result = runner.invoke(app, ["task", "claim-status"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["claims"] == "repo"

    def test_with_task_id(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.ProofService") as MockProof,
            patch(
                "hermit.surfaces.cli._commands_task.task_claim_status",
                return_value={"task_claims": "data"},
            ),
        ):
            MockProof.return_value.build_proof_summary.return_value = {}
            result = runner.invoke(app, ["task", "claim-status", "task-001"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# task rollback
# ---------------------------------------------------------------------------
class TestTaskRollback:
    def test_rollback(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.RollbackService") as MockRB,
        ):
            MockRB.return_value.execute.return_value = {"rolled_back": True}
            result = runner.invoke(app, ["task", "rollback", "r-001"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["rolled_back"] is True


# ---------------------------------------------------------------------------
# task projections-rebuild
# ---------------------------------------------------------------------------
class TestProjectionsRebuild:
    def test_with_task_id(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.ProjectionService") as MockProj,
        ):
            MockProj.return_value.rebuild_task.return_value = {"rebuilt": "task-001"}
            result = runner.invoke(app, ["task", "projections-rebuild", "task-001"])
        assert result.exit_code == 0

    def test_with_all_flag(self) -> None:
        mock_store = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.surfaces.cli._commands_task.ProjectionService") as MockProj,
        ):
            MockProj.return_value.rebuild_all.return_value = {"rebuilt": "all"}
            result = runner.invoke(app, ["task", "projections-rebuild", "--all"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# task capability list / revoke
# ---------------------------------------------------------------------------
class TestTaskCapability:
    def test_capability_list(self) -> None:
        mock_store = MagicMock()
        grant = _fake_grant()
        mock_store.list_capability_grants.return_value = [grant]
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "capability", "list"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert len(parsed) == 1

    def test_capability_revoke_found(self) -> None:
        mock_store = MagicMock()
        mock_store.get_capability_grant.return_value = _fake_grant()
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "capability", "revoke", "grant-001"])
        assert result.exit_code == 0
        assert "Revoked" in result.output
        mock_store.update_capability_grant.assert_called_once()

    def test_capability_revoke_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.get_capability_grant.return_value = None
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "capability", "revoke", "bad-id"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# task steer / steerings
# ---------------------------------------------------------------------------
class TestTaskSteering:
    def test_steer_task_found(self) -> None:
        mock_store = MagicMock()
        mock_store.get_task.return_value = _fake_task()
        with (
            patch(
                "hermit.surfaces.cli._commands_task.get_kernel_store",
                return_value=mock_store,
            ),
            patch("hermit.kernel.signals.steering.SteeringProtocol") as MockSP,
            patch("hermit.kernel.signals.models.SteeringDirective") as MockSD,
        ):
            mock_directive = MagicMock()
            mock_directive.directive_id = "sd-test"
            MockSD.return_value = mock_directive
            result = runner.invoke(app, ["task", "steer", "task-001", "Change approach"])
        assert result.exit_code == 0
        assert "issued" in result.output.lower()
        MockSP.return_value.issue.assert_called_once()

    def test_steer_task_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.get_task.return_value = None
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "steer", "nonexistent", "Do something"])
        assert result.exit_code != 0

    def test_steerings_with_directives(self) -> None:
        mock_store = MagicMock()
        directive = SimpleNamespace(
            directive_id="sd-001",
            steering_type="scope",
            disposition="active",
            issued_by="operator",
            directive="Focus on module A",
        )
        mock_store.active_steerings_for_task.return_value = [directive]
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "steerings", "task-001"])
        assert result.exit_code == 0
        assert "sd-001" in result.output
        assert "Focus on module A" in result.output

    def test_steerings_empty(self) -> None:
        mock_store = MagicMock()
        mock_store.active_steerings_for_task.return_value = []
        with patch(
            "hermit.surfaces.cli._commands_task.get_kernel_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["task", "steerings", "task-001"])
        assert result.exit_code == 0
        assert "No active" in result.output


# ---------------------------------------------------------------------------
# task approve / deny / resume (via _task_resolution)
# ---------------------------------------------------------------------------
class TestTaskResolution:
    def test_approve_found(self, tmp_path: Path) -> None:
        mock_store = MagicMock()
        mock_store.get_approval.return_value = _fake_approval()
        mock_store.get_task.return_value = _fake_task()
        mock_runner = MagicMock()
        mock_runner._resolve_approval.return_value = SimpleNamespace(text="Approved!")
        mock_pm = MagicMock()
        fake_settings = SimpleNamespace(
            base_dir=tmp_path,
            memory_dir=tmp_path / "memory",
            skills_dir=tmp_path / "skills",
            rules_dir=tmp_path / "rules",
            hooks_dir=tmp_path / "hooks",
            plugins_dir=tmp_path / "plugins",
            sessions_dir=tmp_path / "sessions",
            image_memory_dir=tmp_path / "image-memory",
            kernel_dir=tmp_path / "kernel",
            kernel_artifacts_dir=tmp_path / "kernel" / "artifacts",
            context_file=tmp_path / "context.md",
            memory_file=tmp_path / "memory" / "memories.md",
            kernel_db_path=Path(":memory:"),
            locale="en-US",
        )

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=fake_settings),
            patch("hermit.surfaces.cli._commands_task.ensure_workspace"),
            patch(
                "hermit.surfaces.cli._commands_core.build_runner",
                return_value=(mock_runner, mock_pm),
            ),
            patch("hermit.surfaces.cli._commands_task.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_task.stop_runner_background_services"),
        ):
            result = runner.invoke(app, ["task", "approve", "apr-001"])
        assert result.exit_code == 0
        assert "Approved!" in result.output

    def test_approve_not_found(self, tmp_path: Path) -> None:
        mock_store = MagicMock()
        mock_store.get_approval.return_value = None
        mock_runner = MagicMock()
        mock_pm = MagicMock()
        fake_settings = SimpleNamespace(
            base_dir=tmp_path,
            memory_dir=tmp_path / "memory",
            skills_dir=tmp_path / "skills",
            rules_dir=tmp_path / "rules",
            hooks_dir=tmp_path / "hooks",
            plugins_dir=tmp_path / "plugins",
            sessions_dir=tmp_path / "sessions",
            image_memory_dir=tmp_path / "image-memory",
            kernel_dir=tmp_path / "kernel",
            kernel_artifacts_dir=tmp_path / "kernel" / "artifacts",
            context_file=tmp_path / "context.md",
            memory_file=tmp_path / "memory" / "memories.md",
            kernel_db_path=Path(":memory:"),
            locale="en-US",
        )

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=fake_settings),
            patch("hermit.surfaces.cli._commands_task.ensure_workspace"),
            patch(
                "hermit.surfaces.cli._commands_core.build_runner",
                return_value=(mock_runner, mock_pm),
            ),
            patch("hermit.surfaces.cli._commands_task.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_task.stop_runner_background_services"),
        ):
            result = runner.invoke(app, ["task", "approve", "bad-id"])
        assert result.exit_code != 0
