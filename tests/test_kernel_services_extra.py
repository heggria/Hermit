from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.approval_copy import ApprovalCopyService
from hermit.kernel.reconcile import ReconcileService
from hermit.kernel.rollbacks import RollbackService


def test_approval_copy_service_covers_formatter_and_template_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")

    formatted = ApprovalCopyService(
        formatter=lambda facts: {
            "title": "Custom title",
            "summary": "Custom summary",
            "detail": "Custom detail",
        },
        locale="en-US",
    ).resolve_copy({"tool_name": "write_file", "target_paths": ["src/app.py"]}, "approval_1")
    fallback = ApprovalCopyService(
        formatter=lambda facts: {"summary": "missing title"},
        locale="en-US",
    ).resolve_copy({"tool_name": "bash", "command_preview": "rm -rf build"}, "approval_2")
    pushed = ApprovalCopyService(locale="en-US").resolve_copy(
        {"tool_name": "bash", "command_preview": "git push origin main"},
        "approval_3",
    )
    sensitive = ApprovalCopyService(locale="en-US").resolve_copy(
        {"tool_name": "write_file", "target_paths": ["/tmp/.env"]},
        "approval_4",
    )
    network = ApprovalCopyService(locale="en-US").resolve_copy(
        {"tool_name": "request", "network_hosts": ["api.example.com", "cdn.example.com"]},
        "approval_5",
    )
    packet = ApprovalCopyService(locale="en-US").resolve_copy(
        {
            "approval_packet": {"title": "Confirm deployment", "summary": "This deploys to prod."},
            "risk_level": "critical",
        },
        "approval_6",
    )

    assert formatted.title == "Custom title"
    assert fallback.title == "Confirm Delete Operation"
    assert "irreversible" in fallback.detail.lower()
    assert pushed.title == "Confirm Push to Remote"
    assert sensitive.title == "Confirm Sensitive File Change"
    assert network.title == "Confirm External System Change"
    assert packet.title == "Confirm deployment"


def test_approval_copy_service_covers_scheduler_update_delete_and_time_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    service = ApprovalCopyService(locale="en-US")
    monkeypatch.setattr(
        ApprovalCopyService,
        "_next_cron_run_text",
        lambda self, cron_expr: "2026-03-15 09:00",
    )

    update = service.resolve_copy(
        {
            "tool_name": "schedule_update",
            "tool_input": {
                "job_id": "job-1",
                "name": "Digest",
                "prompt": "Summarize important failures from the night run.",
                "enabled": False,
                "cron_expr": "0 9 * * 1-5",
            },
        },
        "approval_u",
    )
    delete = service.resolve_copy(
        {"tool_name": "schedule_delete", "tool_input": {"job_id": "job-2"}},
        "approval_d",
    )
    blocked = service.blocked_message(
        {"tool_name": "schedule_delete", "tool_input": {"job_id": "job-2"}},
        "approval_d",
    )
    prompt = service.model_prompt(
        {"tool_name": "schedule_update", "tool_input": {"job_id": "job-1"}},
        "approval_u",
    )

    assert update.title == "Confirm Scheduled Task Update"
    assert any("disabled" in item for item in update.sections[0].items)
    assert any("0 9 * * 1-5" in item for item in update.sections[0].items)
    assert delete.title == "Confirm Scheduled Task Deletion"
    assert "approval_d" in blocked
    assert "approval_u" in prompt
    assert service._format_interval(3600) == "1 hour"
    assert service._format_interval(120) == "2 minutes"
    assert service._format_interval(45) == "45 seconds"
    assert service._format_datetime_text("2026-03-15T14:00:00+08:00").startswith("2026-03-15")
    assert service._format_datetime_text("not-a-date") == "not-a-date"
    assert service._safe_int("12") == 12
    assert service._safe_int("bad") is None
    assert service._summarize_text("word " * 50, limit=30).endswith("...")


def test_approval_copy_service_covers_formatter_timeout_and_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")

    def slow_formatter(_facts: dict[str, Any]) -> dict[str, str]:
        import time

        time.sleep(0.1)
        return {"title": "slow", "summary": "slow", "detail": "slow"}

    timeout_copy = ApprovalCopyService(
        formatter=slow_formatter,
        formatter_timeout_ms=1,
        locale="en-US",
    ).resolve_copy({"tool_name": "write_file", "target_paths": ["src/app.py"]}, "approval_t")
    string_copy = ApprovalCopyService(
        formatter=lambda facts: "Use string fallback",
        locale="en-US",
    ).resolve_copy({"tool_name": "write_file", "target_paths": ["src/app.py"]}, "approval_s")
    outside = ApprovalCopyService(locale="en-US").resolve_copy(
        {"tool_name": "write_file", "target_paths": ["/outside/demo.txt"], "outside_workspace": True},
        "approval_o",
    )
    multi = ApprovalCopyService(locale="en-US").resolve_copy(
        {"tool_name": "write_file", "target_paths": ["a.txt", "b.txt"]},
        "approval_m",
    )

    assert timeout_copy.title == "Confirm File Change"
    assert string_copy.summary == "Use string fallback"
    assert outside.title == "Confirm Write Outside Workspace"
    assert multi.title == "Confirm Bulk File Changes"


def test_reconcile_service_covers_local_command_git_and_remote_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ReconcileService()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "demo.txt"
    target.write_text("hello", encoding="utf-8")

    local_applied = service.reconcile(
        action_type="write_local",
        tool_input={"path": "demo.txt", "content": "hello"},
        workspace_root=str(workspace),
    )
    local_missing = service.reconcile(
        action_type="write_local",
        tool_input={"path": "missing.txt", "content": "hello"},
        workspace_root=str(workspace),
    )
    command_changed = service.reconcile(
        action_type="execute_command",
        tool_input={},
        workspace_root=str(workspace),
        observables={"target_paths": [str(target)]},
        witness={"files": [{"path": str(target), "exists": False}]},
    )

    monkeypatch.setattr(ReconcileService, "_git_changed", lambda self, **kwargs: False)
    vcs_unchanged = service.reconcile(
        action_type="vcs_mutation",
        tool_input={},
        workspace_root=str(workspace),
        observables={"vcs_operation": "commit"},
        witness={"git": {"head": "abc", "dirty": False}},
    )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 403, "nope", None, io.BytesIO(b"forbidden"))
        ),
    )
    remote_observed = service.reconcile(
        action_type="network_write",
        tool_input={"url": "https://example.com/resource"},
        workspace_root=str(workspace),
    )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 404, "missing", None, io.BytesIO(b"missing"))
        ),
    )
    remote_missing = service.reconcile(
        action_type="network_write",
        tool_input={"url": "https://example.com/missing"},
        workspace_root=str(workspace),
    )

    assert local_applied.result_code == "reconciled_applied"
    assert local_missing.result_code == "reconciled_not_applied"
    assert command_changed.result_code == "reconciled_applied"
    assert vcs_unchanged.result_code == "reconciled_not_applied"
    assert remote_observed.result_code == "reconciled_observed"
    assert remote_missing.result_code == "reconciled_not_applied"
    assert service._path_state(workspace)["kind"] == "directory"
    assert service._changed_paths(target_paths=[str(target)], witness_files=[{"path": str(target), "exists": True}]) == [str(target)]


def test_reconcile_service_covers_git_helpers_and_unknown_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ReconcileService()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    outputs = iter(["abc123\n", " M demo.txt\n"])

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=next(outputs))

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert service._git_state(workspace) == {"head": "abc123", "dirty": True}
    monkeypatch.setattr(ReconcileService, "_git_state", lambda self, root: {"head": "abc123", "dirty": True})
    assert service._git_changed(workspace_root=str(workspace), witness={"head": "old", "dirty": False}) is True
    assert service.reconcile(action_type="unknown", tool_input={}, workspace_root=str(workspace)).result_code == "still_unknown"


def test_rollback_service_covers_apply_rollback_and_error_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "demo.txt"
    target.write_text("current", encoding="utf-8")

    class FakeStore:
        def __init__(self) -> None:
            self.memory_updates: list[tuple[str, str]] = []
            self.belief_updates: list[tuple[str, str]] = []
            self.rollback_updates: list[dict[str, str]] = []

        def update_memory_record(self, memory_id: str, status: str) -> None:
            self.memory_updates.append((memory_id, status))

        def update_belief(self, belief_id: str, status: str) -> None:
            self.belief_updates.append((belief_id, status))

        def update_receipt_rollback_fields(self, receipt_id: str, rollback_status: str, rollback_ref: str | None = None) -> None:
            self.rollback_updates.append({"receipt_id": receipt_id, "status": rollback_status})

        def get_artifact(self, artifact_id: str):
            return SimpleNamespace(uri="artifact://1")

    service = object.__new__(RollbackService)
    service.store = FakeStore()
    service.artifact_store = SimpleNamespace(read_text=lambda uri: json.dumps({"path": str(target), "existed": True, "content": "before"}))
    def _translate(key: str, **kwargs: Any) -> str:
        if key == "kernel.rollback.result.file_restore":
            return f"restored {kwargs['target_path']}"
        if key == "kernel.rollback.error.dirty_repo":
            return "repo dirty"
        if key == "kernel.rollback.result.git_reset":
            return f"reset {kwargs['head']}"
        if key == "kernel.rollback.result.memory_invalidate":
            return f"invalidated {kwargs['count']}"
        if key == "kernel.rollback.error.strategy_not_executable":
            return f"unsupported {kwargs['strategy']}"
        if key == "kernel.rollback.error.prestate_missing":
            return "missing prestate"
        if key == "kernel.rollback.error.artifact_missing":
            return "missing artifact"
        if key == "kernel.rollback.unsupported":
            return "unsupported"
        raise KeyError(key)

    service._t = _translate

    receipt = SimpleNamespace(action_type="write_local", rollback_artifact_refs=["artifact-1"], receipt_id="rcpt-1", rollback_status="pending")
    assert service._apply_rollback(receipt, "file_restore") == {"result_summary": f"restored {target}"}
    assert target.read_text(encoding="utf-8") == "before"

    target.write_text("new", encoding="utf-8")
    service.artifact_store = SimpleNamespace(read_text=lambda uri: json.dumps({"path": str(target), "existed": False}))
    assert service._apply_rollback(receipt, "file_restore") == {"result_summary": f"restored {target}"}
    assert not target.exists()

    service.artifact_store = SimpleNamespace(read_text=lambda uri: json.dumps({"repo_path": str(workspace), "head": "abc", "dirty": True}))
    with pytest.raises(RuntimeError, match="repo dirty"):
        service._apply_rollback(SimpleNamespace(action_type="vcs_mutation", rollback_artifact_refs=["artifact-1"]), "git_revert_or_reset")

    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append(args) or SimpleNamespace())
    service.artifact_store = SimpleNamespace(read_text=lambda uri: json.dumps({"repo_path": str(workspace), "head": "abc", "dirty": False}))
    assert service._apply_rollback(SimpleNamespace(action_type="vcs_mutation", rollback_artifact_refs=["artifact-1"]), "git_revert_or_reset") == {"result_summary": "reset abc"}
    assert calls[0][:3] == ["git", "reset", "--hard"]

    service.artifact_store = SimpleNamespace(read_text=lambda uri: json.dumps({"memory_ids": ["m1"], "belief_ids": ["b1"]}))
    result = service._apply_rollback(SimpleNamespace(action_type="memory_write", rollback_artifact_refs=["artifact-1"]), "supersede_or_invalidate")
    assert result == {"result_summary": "invalidated 1"}
    assert service.store.memory_updates == [("m1", "invalidated")]
    assert service.store.belief_updates == [("b1", "invalidated")]
    assert service._mark_unsupported(SimpleNamespace(receipt_id="rcpt-1", rollback_status="pending"), "unsupported") == {
        "status": "unsupported",
        "result_summary": "unsupported",
    }
    with pytest.raises(RuntimeError, match="missing prestate"):
        service._prestate_payload(SimpleNamespace(rollback_artifact_refs=[]))
    service.store.get_artifact = lambda artifact_id: None
    with pytest.raises(RuntimeError, match="missing artifact"):
        service._prestate_payload(SimpleNamespace(rollback_artifact_refs=["missing"]))
