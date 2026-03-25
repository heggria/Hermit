"""Tests for src/hermit/surfaces/cli/_commands_memory.py"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import typer.testing

from hermit.surfaces.cli._commands_memory import (
    _memory_list_payload,
    _memory_payload_from_record,
    _render_memory_payload,
)
from hermit.surfaces.cli.main import app

runner = typer.testing.CliRunner()


def _fake_settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
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


def _fake_record(**overrides) -> SimpleNamespace:
    defaults = dict(
        memory_id="mem-001",
        task_id="task-001",
        conversation_id="conv-001",
        claim_text="User prefers dark mode",
        category="preference",
        status="active",
        scope_kind="global",
        scope_ref="",
        retention_class="durable",
        promotion_reason="belief_promotion",
        confidence=0.9,
        trust_tier="high",
        evidence_refs=["ev-1", "ev-2"],
        supersedes=["old-claim"],
        supersedes_memory_ids=["mem-000"],
        superseded_by_memory_id=None,
        source_belief_ref="belief-001",
        invalidation_reason=None,
        invalidated_at=None,
        expires_at=None,
        structured_assertion={"key": "value"},
        updated_at=1700000000.0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# _memory_payload_from_record
# ---------------------------------------------------------------------------
class TestMemoryPayloadFromRecord:
    def test_basic_record(self, tmp_path: Path) -> None:
        record = _fake_record()
        settings = _fake_settings(tmp_path)
        with patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov:
            MockGov.return_value.inspect_claim.return_value = {
                "category": "preference",
                "retention_class": "durable",
                "status": "active",
                "scope_kind": "global",
                "scope_ref": "",
            }
            payload = _memory_payload_from_record(record, settings=settings)
        assert payload["memory_id"] == "mem-001"
        assert payload["claim_text"] == "User prefers dark mode"
        assert payload["evidence_refs"] == ["ev-1", "ev-2"]

    def test_workspace_scope(self, tmp_path: Path) -> None:
        record = _fake_record(scope_kind="workspace", scope_ref="/workspace/project")
        settings = _fake_settings(tmp_path)
        with patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov:
            MockGov.return_value.inspect_claim.return_value = {}
            _memory_payload_from_record(record, settings=settings)
        # Should use scope_ref as workspace_root
        call_kwargs = MockGov.return_value.inspect_claim.call_args[1]
        assert call_kwargs["workspace_root"] == "/workspace/project"


# ---------------------------------------------------------------------------
# _render_memory_payload
# ---------------------------------------------------------------------------
class TestRenderMemoryPayload:
    def test_basic_payload(self) -> None:
        payload = {
            "memory_id": "mem-001",
            "claim_text": "User likes cats",
            "stored_category": "preference",
            "status": "active",
            "scope_kind": "global",
            "scope_ref": "",
            "retention_class": "durable",
            "promotion_reason": "belief",
            "confidence": 0.9,
            "trust_tier": "high",
            "expires_at": None,
            "invalidated_at": None,
            "superseded_by_memory_id": None,
            "source_belief_ref": None,
            "supersedes": [],
            "inspection": {
                "category": "preference",
                "retention_class": "durable",
                "status": "active",
                "scope_kind": "global",
                "scope_ref": "",
                "subject_key": "user",
                "topic_key": "cats",
                "explanation": [],
                "structured_assertion": {},
            },
        }
        result = _render_memory_payload(payload)
        assert "mem-001" in result
        assert "User likes cats" in result

    def test_with_source_belief_ref(self) -> None:
        payload = {
            "memory_id": "mem-001",
            "claim_text": "test",
            "stored_category": "other",
            "status": "active",
            "scope_kind": "global",
            "scope_ref": "",
            "retention_class": "durable",
            "promotion_reason": "belief",
            "confidence": 0.5,
            "trust_tier": "low",
            "expires_at": None,
            "invalidated_at": None,
            "superseded_by_memory_id": None,
            "source_belief_ref": "belief-ref-123",
            "supersedes": [],
            "inspection": {},
        }
        result = _render_memory_payload(payload)
        assert "belief-ref-123" in result

    def test_with_supersedes(self) -> None:
        payload = {
            "memory_id": "mem-002",
            "claim_text": "test",
            "stored_category": "other",
            "status": "active",
            "scope_kind": "global",
            "scope_ref": "",
            "retention_class": "durable",
            "promotion_reason": "belief",
            "confidence": 0.5,
            "trust_tier": "low",
            "expires_at": None,
            "invalidated_at": None,
            "superseded_by_memory_id": None,
            "source_belief_ref": None,
            "supersedes": ["old-claim-1", "old-claim-2"],
            "inspection": {},
        }
        result = _render_memory_payload(payload)
        assert "old-claim-1" in result
        assert "old-claim-2" in result

    def test_with_governance_explanations(self) -> None:
        payload = {
            "memory_id": "mem-003",
            "claim_text": "test",
            "stored_category": "other",
            "status": "active",
            "scope_kind": "global",
            "scope_ref": "",
            "retention_class": "durable",
            "promotion_reason": "belief",
            "confidence": 0.5,
            "trust_tier": "low",
            "expires_at": None,
            "invalidated_at": None,
            "superseded_by_memory_id": None,
            "source_belief_ref": None,
            "supersedes": [],
            "inspection": {
                "explanation": ["Rule 1 applied", "Rule 2 applied"],
                "structured_assertion": {},
            },
        }
        result = _render_memory_payload(payload)
        assert "Rule 1 applied" in result

    def test_with_matched_signals(self) -> None:
        payload = {
            "memory_id": "mem-004",
            "claim_text": "test",
            "stored_category": "other",
            "status": "active",
            "scope_kind": "global",
            "scope_ref": "",
            "retention_class": "durable",
            "promotion_reason": "belief",
            "confidence": 0.5,
            "trust_tier": "low",
            "expires_at": None,
            "invalidated_at": None,
            "superseded_by_memory_id": None,
            "source_belief_ref": None,
            "supersedes": [],
            "inspection": {
                "structured_assertion": {
                    "matched_signals": {"signal_a": ["hit1", "hit2"]},
                },
            },
        }
        result = _render_memory_payload(payload)
        assert "signal_a" in result
        assert "hit1" in result


# ---------------------------------------------------------------------------
# _memory_list_payload
# ---------------------------------------------------------------------------
class TestMemoryListPayload:
    def test_multiple_records(self, tmp_path: Path) -> None:
        records = [_fake_record(memory_id=f"mem-{i}") for i in range(3)]
        settings = _fake_settings(tmp_path)
        with patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov:
            MockGov.return_value.subject_key_for_memory.return_value = "user"
            MockGov.return_value.topic_key_for_memory.return_value = "test"
            payload = _memory_list_payload(records, settings=settings)
        assert len(payload) == 3
        assert payload[0]["memory_id"] == "mem-0"


# ---------------------------------------------------------------------------
# memory inspect
# ---------------------------------------------------------------------------
class TestMemoryInspect:
    def test_with_memory_id_found(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        record = _fake_record()
        mock_store = MagicMock()
        mock_store.get_memory_record.return_value = record

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov,
        ):
            MockGov.return_value.inspect_claim.return_value = {
                "category": "preference",
                "retention_class": "durable",
                "status": "active",
                "scope_kind": "global",
                "scope_ref": "",
                "subject_key": None,
                "topic_key": None,
                "explanation": [],
                "structured_assertion": {},
            }
            result = runner.invoke(app, ["memory", "inspect", "mem-001"])
        assert result.exit_code == 0
        assert "mem-001" in result.output

    def test_with_memory_id_not_found(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_store = MagicMock()
        mock_store.get_memory_record.return_value = None

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
        ):
            result = runner.invoke(app, ["memory", "inspect", "nonexistent"])
        assert result.exit_code != 0

    def test_with_claim_text(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov,
        ):
            MockGov.return_value.inspect_claim.return_value = {
                "category": "other",
                "retention_class": "ephemeral",
                "status": "preview",
                "scope_kind": "global",
                "scope_ref": "",
                "expires_at": None,
                "structured_assertion": {},
                "subject_key": None,
                "topic_key": None,
                "explanation": [],
            }
            result = runner.invoke(app, ["memory", "inspect", "--claim-text", "Test claim"])
        assert result.exit_code == 0
        assert "preview" in result.output.lower() or "Test claim" in result.output

    def test_neither_provided(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
        ):
            result = runner.invoke(app, ["memory", "inspect"])
        assert result.exit_code != 0

    def test_json_output(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        record = _fake_record()
        mock_store = MagicMock()
        mock_store.get_memory_record.return_value = record

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov,
        ):
            MockGov.return_value.inspect_claim.return_value = {
                "category": "preference",
                "retention_class": "durable",
                "status": "active",
                "scope_kind": "global",
                "scope_ref": "",
            }
            result = runner.invoke(app, ["memory", "inspect", "mem-001", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["memory_id"] == "mem-001"


# ---------------------------------------------------------------------------
# memory list
# ---------------------------------------------------------------------------
class TestMemoryList:
    def test_with_records_text(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        records = [_fake_record(memory_id=f"mem-{i}") for i in range(3)]
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = records

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov,
        ):
            MockGov.return_value.subject_key_for_memory.return_value = "user"
            MockGov.return_value.topic_key_for_memory.return_value = "test"
            result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        assert "mem-0" in result.output

    def test_empty_records(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = []

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService"),
        ):
            result = runner.invoke(app, ["memory", "list"])
        assert result.exit_code == 0
        assert "No memory records" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        records = [_fake_record()]
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = records

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov,
        ):
            MockGov.return_value.subject_key_for_memory.return_value = "user"
            MockGov.return_value.topic_key_for_memory.return_value = "test"
            result = runner.invoke(app, ["memory", "list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# memory status
# ---------------------------------------------------------------------------
class TestMemoryStatus:
    def test_text_output(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        records = [
            _fake_record(status="active", retention_class="durable", category="preference"),
            _fake_record(
                memory_id="mem-002",
                status="invalidated",
                retention_class="ephemeral",
                category="other",
                superseded_by_memory_id="mem-003",
            ),
        ]
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = records

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryGovernanceService") as MockGov,
        ):
            MockGov.return_value.is_expired.return_value = False
            result = runner.invoke(app, ["memory", "status"])
        assert result.exit_code == 0
        assert "Total Records: 2" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = []

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
        ):
            result = runner.invoke(app, ["memory", "status", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["total_records"] == 0


# ---------------------------------------------------------------------------
# memory rebuild
# ---------------------------------------------------------------------------
class TestMemoryRebuild:
    def test_text_output(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = []

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryRecordService") as MockSvc,
        ):
            svc = MockSvc.return_value
            svc.reconcile_active_records.return_value = {
                "superseded_count": 2,
                "duplicate_count": 1,
            }
            svc.export_mirror.return_value = tmp_path / "memories.md"
            result = runner.invoke(app, ["memory", "rebuild"])
        assert result.exit_code == 0
        assert "Rebuilt" in result.output or "rebuilt" in result.output.lower()

    def test_json_output(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = []

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryRecordService") as MockSvc,
        ):
            svc = MockSvc.return_value
            svc.reconcile_active_records.return_value = {
                "superseded_count": 0,
                "duplicate_count": 0,
            }
            svc.export_mirror.return_value = tmp_path / "memories.md"
            result = runner.invoke(app, ["memory", "rebuild", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "before_active" in parsed


# ---------------------------------------------------------------------------
# memory export
# ---------------------------------------------------------------------------
class TestMemoryExport:
    def test_text_output(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = []

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryRecordService") as MockSvc,
        ):
            svc = MockSvc.return_value
            svc.export_mirror.return_value = tmp_path / "memories.md"
            result = runner.invoke(app, ["memory", "export"])
        assert result.exit_code == 0
        assert "Exported" in result.output or "export" in result.output.lower()

    def test_json_output(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_store = MagicMock()
        mock_store.list_memory_records.return_value = []

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_memory.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_memory.get_kernel_store", return_value=mock_store),
            patch("hermit.surfaces.cli._commands_memory.MemoryRecordService") as MockSvc,
        ):
            svc = MockSvc.return_value
            svc.export_mirror.return_value = tmp_path / "memories.md"
            result = runner.invoke(app, ["memory", "export", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "active_records" in parsed
