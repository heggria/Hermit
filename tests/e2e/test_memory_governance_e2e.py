"""E2E: Memory governance — record creation, inspection, rebuild, and export.

Exercises the evidence-bound memory system from creation through governance
classification, supersedence detection, and CLI export.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.surfaces.cli.main import app


def test_memory_lifecycle_create_inspect_rebuild_export(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Full memory lifecycle: create records → inspect → rebuild (supersedence) → export."""
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))  # type: ignore[union-attr]
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")

    # 1. Create conflicting memory records (older superseded by newer)
    older = store.create_memory_record(
        task_id="task-memory-1",
        conversation_id="chat-memory",
        category="进行中的任务",
        claim_text="已设定每日定时任务：每天早上 10 点搜索 AI 最新动态。",
        confidence=0.8,
        evidence_refs=[],
    )
    newer = store.create_memory_record(
        task_id="task-memory-2",
        conversation_id="chat-memory",
        category="进行中的任务",
        claim_text="当前无任何定时任务，已全部清理完成。",
        confidence=0.9,
        evidence_refs=[],
    )
    _preference = store.create_memory_record(
        task_id="task-memory-3",
        conversation_id="chat-memory",
        category="用户偏好",
        claim_text="以后都用简体中文回复我。",
        confidence=0.95,
        evidence_refs=[],
    )

    runner = CliRunner()

    # 2. Inspect a stored record
    inspect_result = runner.invoke(app, ["memory", "inspect", newer.memory_id])
    assert inspect_result.exit_code == 0
    assert f"Memory ID: {newer.memory_id}" in inspect_result.output
    assert "Governance:" in inspect_result.output

    # 3. Preview governance for a new claim
    preview_result = runner.invoke(
        app,
        ["memory", "inspect", "--claim-text", "以后都用简体中文回复我，不要再切英文。", "--json"],
    )
    assert preview_result.exit_code == 0
    preview = json.loads(preview_result.output)
    assert preview["inspection"]["category"] == "用户偏好"
    assert preview["inspection"]["retention_class"] == "user_preference"

    # 4. List records
    list_result = runner.invoke(app, ["memory", "list"])
    assert list_result.exit_code == 0
    assert older.memory_id in list_result.output
    assert newer.memory_id in list_result.output

    # 5. Status summary
    status_result = runner.invoke(app, ["memory", "status", "--json"])
    assert status_result.exit_code == 0
    status = json.loads(status_result.output)
    assert status["total_records"] >= 3

    # 6. Rebuild — should detect supersedence
    rebuild_result = runner.invoke(app, ["memory", "rebuild", "--json"])
    assert rebuild_result.exit_code == 0
    rebuild = json.loads(rebuild_result.output)
    assert rebuild["before_active"] >= rebuild["after_active"]
    assert rebuild["superseded_count"] >= 1
    assert Path(rebuild["mirror_path"]).exists()

    # 7. Export
    output_path = tmp_path / "memory-export.md"
    export_result = runner.invoke(app, ["memory", "export", "--output", str(output_path), "--json"])
    assert export_result.exit_code == 0
    export = json.loads(export_result.output)
    assert export["render_mode"] == "export_only"
    assert export["active_records"] >= 1
    assert output_path.exists()
    export_content = output_path.read_text(encoding="utf-8")
    # At least one record should be in the export
    assert "简体中文" in export_content or "定时任务" in export_content
