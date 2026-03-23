"""Tests for assurance MCP tool definitions and handler."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.verification.assurance.mcp_tools import (
    ASSURANCE_TOOLS,
    handle_assurance_tool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lab(
    *,
    replay_task_return: Any = None,
    load_task_trace_return: list[Any] | None = None,
    invariant_violations: list[Any] | None = None,
    contract_violations: list[Any] | None = None,
) -> MagicMock:
    """Build a minimal mock AssuranceLab for handler tests."""
    lab = MagicMock()
    lab.replay_task.return_value = replay_task_return

    # recorder.load_task_trace
    lab.recorder.load_task_trace.return_value = (
        load_task_trace_return if load_task_trace_return is not None else []
    )

    # invariant_engine.check
    lab.invariant_engine.check.return_value = (
        invariant_violations if invariant_violations is not None else []
    )

    # contract_engine.evaluate_post_run
    lab.contract_engine.evaluate_post_run.return_value = (
        contract_violations if contract_violations is not None else []
    )

    return lab


def _fake_report() -> SimpleNamespace:
    """Create a fake AssuranceReport-like object for emit_json."""
    return SimpleNamespace(
        report_id="rpt-abc",
        scenario_id="scn-1",
        run_id="run-1",
        status="pass",
        verdict="clean",
        first_violation=None,
        timelines={},
        violations=[],
        attribution=None,
        fault_impact_graph={},
        recovery={},
        duplicates={},
        stuck_orphans={},
        side_effect_audit={},
        approval_bottlenecks={},
        adversarial={},
        regression_comparison={},
        replay_diff={},
        evidence_refs=[],
        created_at=1000.0,
    )


def _fake_invariant_violation(
    *, invariant_id: str = "inv-1", severity: str = "blocker"
) -> SimpleNamespace:
    return SimpleNamespace(invariant_id=invariant_id, severity=severity)


def _fake_contract_violation(
    *, contract_id: str = "con-1", severity: str = "high"
) -> SimpleNamespace:
    return SimpleNamespace(contract_id=contract_id, severity=severity)


# ---------------------------------------------------------------------------
# ASSURANCE_TOOLS definitions
# ---------------------------------------------------------------------------


class TestAssuranceToolDefinitions:
    """Verify the ASSURANCE_TOOLS list has the expected structure."""

    def test_tool_count(self) -> None:
        assert len(ASSURANCE_TOOLS) == 3

    @pytest.mark.parametrize(
        "name",
        [
            "hermit_assurance_replay_task",
            "hermit_assurance_check_trace",
            "hermit_assurance_report",
        ],
    )
    def test_tool_names_present(self, name: str) -> None:
        names = [t["name"] for t in ASSURANCE_TOOLS]
        assert name in names

    def test_all_tools_have_input_schema(self) -> None:
        for tool in ASSURANCE_TOOLS:
            assert "inputSchema" in tool
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema

    def test_replay_task_schema_has_task_id_required(self) -> None:
        tool = next(t for t in ASSURANCE_TOOLS if t["name"] == "hermit_assurance_replay_task")
        assert "task_id" in tool["inputSchema"]["required"]

    def test_check_trace_schema_has_task_id_required(self) -> None:
        tool = next(t for t in ASSURANCE_TOOLS if t["name"] == "hermit_assurance_check_trace")
        assert "task_id" in tool["inputSchema"]["required"]

    def test_report_schema_has_task_id_required(self) -> None:
        tool = next(t for t in ASSURANCE_TOOLS if t["name"] == "hermit_assurance_report")
        assert "task_id" in tool["inputSchema"]["required"]


# ---------------------------------------------------------------------------
# handle_assurance_tool — unknown tool
# ---------------------------------------------------------------------------


class TestHandleUnknownTool:
    def test_unknown_tool_returns_error(self) -> None:
        lab = _make_lab()
        result = handle_assurance_tool("hermit_nonexistent", {"task_id": "t1"}, lab=lab)
        assert "error" in result
        assert "Unknown tool" in result["error"]


# ---------------------------------------------------------------------------
# hermit_assurance_replay_task
# ---------------------------------------------------------------------------


class TestReplayTask:
    def test_replay_no_trace_returns_error(self) -> None:
        lab = _make_lab(replay_task_return=None)
        result = handle_assurance_tool(
            "hermit_assurance_replay_task",
            {"task_id": "task-missing"},
            lab=lab,
        )
        assert "error" in result
        assert "No trace found" in result["error"]
        assert "task-missing" in result["error"]

    def test_replay_success_returns_report_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        report = _fake_report()
        lab = _make_lab(replay_task_return=report)

        # Patch AssuranceReporter.emit_json to return a known dict
        fake_json = {"report_id": "rpt-abc", "status": "pass"}
        mock_reporter_cls = MagicMock()
        mock_reporter_cls.return_value.emit_json.return_value = fake_json

        monkeypatch.setattr(
            "hermit.kernel.verification.assurance.mcp_tools.handle_assurance_tool.__module__",
            "hermit.kernel.verification.assurance.mcp_tools",
        )
        # We need to patch in the reporting module that gets imported inside the handler
        import hermit.kernel.verification.assurance.reporting as reporting_mod

        monkeypatch.setattr(reporting_mod, "AssuranceReporter", mock_reporter_cls)

        result = handle_assurance_tool(
            "hermit_assurance_replay_task",
            {"task_id": "task-1"},
            lab=lab,
        )
        assert result == fake_json
        lab.replay_task.assert_called_once_with("task-1", attribution_mode="post_run")

    def test_replay_with_attribution_off(self) -> None:
        lab = _make_lab(replay_task_return=None)
        handle_assurance_tool(
            "hermit_assurance_replay_task",
            {"task_id": "t1", "attribution_mode": "off"},
            lab=lab,
        )
        lab.replay_task.assert_called_once_with("t1", attribution_mode="off")


# ---------------------------------------------------------------------------
# hermit_assurance_check_trace
# ---------------------------------------------------------------------------


class TestCheckTrace:
    def test_check_trace_no_envelopes_returns_error(self) -> None:
        lab = _make_lab(load_task_trace_return=[])
        result = handle_assurance_tool(
            "hermit_assurance_check_trace",
            {"task_id": "task-empty"},
            lab=lab,
        )
        assert "error" in result
        assert "No trace found" in result["error"]

    def test_check_trace_pass(self) -> None:
        fake_envs = [SimpleNamespace(task_id="t1")]
        lab = _make_lab(
            load_task_trace_return=fake_envs,
            invariant_violations=[],
            contract_violations=[],
        )
        result = handle_assurance_tool(
            "hermit_assurance_check_trace",
            {"task_id": "t1"},
            lab=lab,
        )
        assert result["status"] == "pass"
        assert result["task_id"] == "t1"
        assert result["envelope_count"] == 1
        assert result["invariant_violations"] == 0
        assert result["contract_violations"] == 0
        assert result["first_violation"] is None

    def test_check_trace_fail_with_invariant_violation(self) -> None:
        fake_envs = [SimpleNamespace(task_id="t1")]
        inv_v = _fake_invariant_violation(invariant_id="state.transition", severity="blocker")
        lab = _make_lab(
            load_task_trace_return=fake_envs,
            invariant_violations=[inv_v],
            contract_violations=[],
        )
        result = handle_assurance_tool(
            "hermit_assurance_check_trace",
            {"task_id": "t1"},
            lab=lab,
        )
        assert result["status"] == "fail"
        assert result["invariant_violations"] == 1
        assert result["first_violation"]["type"] == "invariant"
        assert result["first_violation"]["id"] == "state.transition"
        assert result["first_violation"]["severity"] == "blocker"

    def test_check_trace_fail_with_contract_violation_only(self) -> None:
        fake_envs = [SimpleNamespace(task_id="t1")]
        con_v = _fake_contract_violation(contract_id="approval.gating", severity="high")
        lab = _make_lab(
            load_task_trace_return=fake_envs,
            invariant_violations=[],
            contract_violations=[con_v],
        )
        result = handle_assurance_tool(
            "hermit_assurance_check_trace",
            {"task_id": "t1"},
            lab=lab,
        )
        assert result["status"] == "fail"
        assert result["contract_violations"] == 1
        assert result["first_violation"]["type"] == "contract"
        assert result["first_violation"]["id"] == "approval.gating"

    def test_check_trace_calls_engines_with_task_id(self) -> None:
        fake_envs = [SimpleNamespace(task_id="t1")]
        lab = _make_lab(load_task_trace_return=fake_envs)
        handle_assurance_tool(
            "hermit_assurance_check_trace",
            {"task_id": "t1"},
            lab=lab,
        )
        lab.invariant_engine.check.assert_called_once_with(fake_envs, task_id="t1")
        lab.contract_engine.evaluate_post_run.assert_called_once_with(fake_envs, task_id="t1")


# ---------------------------------------------------------------------------
# hermit_assurance_report
# ---------------------------------------------------------------------------


class TestAssuranceReport:
    def test_report_no_trace_returns_error(self) -> None:
        lab = _make_lab(replay_task_return=None)
        result = handle_assurance_tool(
            "hermit_assurance_report",
            {"task_id": "task-missing"},
            lab=lab,
        )
        assert "error" in result
        assert "No trace found" in result["error"]

    def test_report_json_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        report = _fake_report()
        lab = _make_lab(replay_task_return=report)

        fake_json = {"report_id": "rpt-abc", "status": "pass"}
        mock_reporter_cls = MagicMock()
        mock_reporter_cls.return_value.emit_json.return_value = fake_json

        import hermit.kernel.verification.assurance.reporting as reporting_mod

        monkeypatch.setattr(reporting_mod, "AssuranceReporter", mock_reporter_cls)

        result = handle_assurance_tool(
            "hermit_assurance_report",
            {"task_id": "t1", "format": "json"},
            lab=lab,
        )
        assert result == fake_json

    def test_report_markdown_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        report = _fake_report()
        lab = _make_lab(replay_task_return=report)

        mock_reporter_cls = MagicMock()
        mock_reporter_cls.return_value.emit_markdown.return_value = "# Report\n\nclean"

        import hermit.kernel.verification.assurance.reporting as reporting_mod

        monkeypatch.setattr(reporting_mod, "AssuranceReporter", mock_reporter_cls)

        result = handle_assurance_tool(
            "hermit_assurance_report",
            {"task_id": "t1", "format": "markdown"},
            lab=lab,
        )
        assert "markdown" in result
        assert "# Report" in result["markdown"]

    def test_report_default_format_is_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        report = _fake_report()
        lab = _make_lab(replay_task_return=report)

        fake_json = {"report_id": "rpt-abc"}
        mock_reporter_cls = MagicMock()
        mock_reporter_cls.return_value.emit_json.return_value = fake_json

        import hermit.kernel.verification.assurance.reporting as reporting_mod

        monkeypatch.setattr(reporting_mod, "AssuranceReporter", mock_reporter_cls)

        result = handle_assurance_tool(
            "hermit_assurance_report",
            {"task_id": "t1"},
            lab=lab,
        )
        # Should call emit_json, not emit_markdown
        mock_reporter_cls.return_value.emit_json.assert_called_once()
        mock_reporter_cls.return_value.emit_markdown.assert_not_called()
        assert result == fake_json
