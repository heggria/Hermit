from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService
from hermit.surfaces.cli.main import app


def _seed_claim_cache(base_dir: Path) -> None:
    """Write a minimal valid repository-claim-status.json so that task_claim_status
    never falls through to the expensive repository_claim_status() live probes.
    Tests that specifically exercise claim probes set up their own environment and
    are not affected by this helper.
    """
    from hermit.kernel.artifacts.lineage.claim_manifest import CLAIM_ROWS, PROFILE_LABELS

    rows = []
    for row in CLAIM_ROWS:
        computed = dict(row)
        if str(row["id"]) == "signed_proofs":
            computed.update({"status": "conditional", "evaluation": "conditional_capability"})
        else:
            computed.update({"status": "implemented", "evaluation": "semantic_probe"})
        rows.append(computed)
    profiles = {
        profile: {"claimable": True, "label": label, "blockers": []}
        for profile, label in PROFILE_LABELS.items()
    }
    payload = {
        "rows": rows,
        "profiles": profiles,
        "claimable_profiles": list(PROFILE_LABELS.values()),
        "blockers": [],
        "conditional_capabilities": {
            "signing_configured": False,
            "strong_signed_proofs_available": False,
            "baseline_verifiable_available": True,
        },
        "cache": {
            "schema_version": "repository-claims-v1",
            "generated_at": time.time(),
            "include_expensive_probes": True,
            "status": "fresh",
        },
    }
    cache_path = base_dir / "kernel" / "repository-claim-status.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _parse_json_output(output: str) -> dict:
    """Parse JSON from CLI output, skipping any structlog lines before the JSON."""
    # Find the first line that starts with '{' or '[' (the JSON body).
    lines = output.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return json.loads("\n".join(lines[i:]))
    return json.loads(output)


def test_task_help_uses_locale_at_import_time(monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod

    runner = CliRunner()

    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    main_mod = importlib.reload(main_mod)
    zh_result = runner.invoke(main_mod.app, ["task", "--help"])
    assert zh_result.exit_code == 0
    assert "任务内核查看与审批命令" in zh_result.output

    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    main_mod = importlib.reload(main_mod)
    en_result = runner.invoke(main_mod.app, ["task", "--help"])
    assert en_result.exit_code == 0
    assert "Task kernel inspection and approval commands." in en_result.output


def test_task_list_show_and_receipts_commands_read_kernel_state(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()
    _seed_claim_cache(base_dir)

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-task", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-task",
        title="CLI Task",
        goal="Inspect task CLI output",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "require_approval"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="write_file executed successfully",
    )

    runner = CliRunner()

    list_result = runner.invoke(app, ["task", "list"])
    assert list_result.exit_code == 0
    assert task.task_id in list_result.output
    assert "CLI Task" in list_result.output

    show_result = runner.invoke(app, ["task", "show", task.task_id])
    assert show_result.exit_code == 0
    assert '"task_id"' in show_result.output
    assert task.task_id in show_result.output

    receipts_result = runner.invoke(app, ["task", "receipts", "--task-id", task.task_id])
    assert receipts_result.exit_code == 0
    assert "write_file executed successfully" in receipts_result.output


def test_memory_inspect_command_reports_stored_and_preview_governance(
    tmp_path, monkeypatch
) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    record = store.create_memory_record(
        task_id="task-memory",
        conversation_id="chat-memory",
        category="other",
        claim_text="当前无任何定时任务，刚刚已经全部清理完成。",
        confidence=0.9,
        evidence_refs=[],
    )

    runner = CliRunner()
    stored_result = runner.invoke(app, ["memory", "inspect", record.memory_id])
    preview_result = runner.invoke(
        app,
        [
            "memory",
            "inspect",
            "--claim-text",
            "以后都用简体中文回复我，不要再切英文。",
            "--json",
        ],
    )

    assert stored_result.exit_code == 0
    assert record.memory_id in stored_result.output
    assert "active_task" in stored_result.output
    assert "task_state" in stored_result.output

    assert preview_result.exit_code == 0
    preview_payload = json.loads(preview_result.output)
    assert preview_payload["inspection"]["category"] == "user_preference"
    assert preview_payload["inspection"]["retention_class"] == "user_preference"
    assert preview_payload["inspection"]["scope_kind"] == "global"


def test_memory_list_status_and_rebuild_commands_cover_inspection_suite(
    tmp_path, monkeypatch
) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    older = store.create_memory_record(
        task_id="task-older",
        conversation_id="chat-memory",
        category="active_task",
        claim_text="已设定每日定时任务：每天早上 10 点自动搜索 AI 最新动态并推送日报到飞书群。",
        confidence=0.8,
        evidence_refs=[],
    )
    latest = store.create_memory_record(
        task_id="task-latest",
        conversation_id="chat-memory",
        category="active_task",
        claim_text="当前无任何定时任务，刚刚已经全部清理完成。",
        confidence=0.9,
        evidence_refs=[],
    )
    store.create_memory_record(
        task_id="task-pref",
        conversation_id="chat-memory",
        category="user_preference",
        claim_text="以后都用简体中文回复我。",
        confidence=0.9,
        evidence_refs=[],
    )

    runner = CliRunner()
    list_result = runner.invoke(app, ["memory", "list"])
    status_result = runner.invoke(app, ["memory", "status", "--json"])
    rebuild_result = runner.invoke(app, ["memory", "rebuild", "--json"])

    assert list_result.exit_code == 0
    assert older.memory_id in list_result.output
    assert latest.memory_id in list_result.output
    assert "task_state" in list_result.output

    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.output)
    assert status_payload["total_records"] >= 3
    assert status_payload["by_retention_class"]["task_state"] >= 2

    assert rebuild_result.exit_code == 0
    rebuild_payload = json.loads(rebuild_result.output)
    assert rebuild_payload["before_active"] >= rebuild_payload["after_active"]
    assert rebuild_payload["superseded_count"] >= 0
    assert Path(rebuild_payload["mirror_path"]).exists()


def test_task_explain_command_summarizes_authority_chain(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-explain", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-explain",
        title="CLI Explain Task",
        goal="Explain one governed execution",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed this write.",
        evidence_refs=["artifact_action", "artifact_policy"],
        action_type="write_local",
    )
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref="policy_1",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=["workspace"],
        constraints={"target_paths": ["workspace/example.txt"]},
        idempotency_key="idem_1",
        expires_at=None,
    )
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="write_file executed successfully",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        policy_ref="policy_1",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["task", "explain", task.task_id])

    assert result.exit_code == 0
    payload = _parse_json_output(result.output)
    assert payload["task"]["task_id"] == task.task_id
    assert payload["operator_answers"]["why_execute"] == "Policy allowed this write."
    assert (
        payload["operator_answers"]["authority"]["capability_grant"]["grant_id"] == grant.grant_id
    )
    assert payload["operator_answers"]["authority"]["target_paths"] == ["workspace/example.txt"]
    assert (
        payload["operator_answers"]["outcome"]["result_summary"]
        == "write_file executed successfully"
    )


def test_task_proof_commands_report_and_export_proof_bundle(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-proof", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-proof",
        title="CLI Proof Task",
        goal="Export proof bundle",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed this write.",
        evidence_refs=["artifact_action"],
        action_type="write_local",
    )
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref="policy_1",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=["workspace"],
        constraints={"target_paths": ["workspace/example.txt"]},
        idempotency_key="idem_cli_proof",
        expires_at=None,
    )
    legacy_receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="legacy receipt",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        policy_ref="policy_1",
    )

    runner = CliRunner()
    proof_result = runner.invoke(app, ["task", "proof", task.task_id])
    assert proof_result.exit_code == 0
    proof_payload = json.loads(proof_result.output)
    assert proof_payload["chain_verification"]["valid"] is True
    assert proof_payload["missing_receipt_bundle_count"] == 1

    output_path = tmp_path / "proof.json"
    export_result = runner.invoke(
        app, ["task", "proof-export", task.task_id, "--output", str(output_path)]
    )
    assert export_result.exit_code == 0
    export_payload = json.loads(export_result.output)
    assert export_payload["status"] == "verified"
    assert export_payload["proof_bundle_ref"]
    assert output_path.read_text(encoding="utf-8").strip() == export_result.output.strip()
    refreshed_receipt = store.get_receipt(legacy_receipt.receipt_id)
    assert refreshed_receipt is not None and refreshed_receipt.receipt_bundle_ref is not None
    assert (
        ProofService(store).build_proof_summary(task.task_id)["missing_receipt_bundle_count"] == 0
    )


def test_task_claim_status_command_reports_repo_and_task_gates(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "proof-secret")
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-claims", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-claims",
        title="CLI Claim Task",
        goal="Inspect claim gate",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="claim receipt",
        result_code="succeeded",
    )
    ProofService(store).export_task_proof(task.task_id)

    runner = CliRunner()
    repo_result = runner.invoke(app, ["task", "claim-status"])
    task_result = runner.invoke(app, ["task", "claim-status", task.task_id])

    assert repo_result.exit_code == 0
    repo_payload = _parse_json_output(repo_result.output)
    assert repo_payload["profiles"]["verifiable"]["claimable"] is True
    assert repo_payload["profiles"]["core"]["label"] == "Hermit Kernel v0.3 Core"

    assert task_result.exit_code == 0
    task_payload = _parse_json_output(task_result.output)
    assert task_payload["task_id"] == task.task_id
    assert task_payload["task_gate"]["verifiable_ready"] is True
    assert task_payload["task_gate"]["strong_verifiable_ready"] is True
    refreshed_receipt = store.get_receipt(receipt.receipt_id)
    assert refreshed_receipt is not None
    assert refreshed_receipt.proof_mode == "signed_with_inclusion_proof"


def test_task_show_reports_contract_loop_summary(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()
    _seed_claim_cache(base_dir)

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-show-contracts", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-show-contracts",
        title="CLI Contract Loop Task",
        goal="Inspect contract loop",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    contract = store.create_execution_contract(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        objective="write_file: write_local",
        expected_effects=["path:workspace/demo.txt"],
        status="authorized",
        contract_version=2,
    )
    evidence_case = store.create_evidence_case(
        task_id=task.task_id,
        subject_kind="contract",
        subject_ref=contract.contract_id,
        support_refs=["artifact_context"],
        sufficiency_score=0.8,
        unresolved_gaps=[],
        status="sufficient",
    )
    authorization_plan = store.create_authorization_plan(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        contract_ref=contract.contract_id,
        policy_profile_ref="default",
        approval_route="operator",
        current_gaps=[],
        status="authorized",
    )
    reconciliation = store.create_reconciliation(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        contract_ref=contract.contract_id,
        receipt_refs=[],
        observed_output_refs=[],
        intended_effect_summary="write file",
        authorized_effect_summary="write file",
        observed_effect_summary="file written",
        receipted_effect_summary="receipt captured",
        result_class="satisfied",
        recommended_resolution="none",
    )
    store.update_step_attempt(
        attempt.step_attempt_id,
        execution_contract_ref=contract.contract_id,
        evidence_case_ref=evidence_case.evidence_case_id,
        authorization_plan_ref=authorization_plan.authorization_plan_id,
        reconciliation_ref=reconciliation.reconciliation_id,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["task", "show", task.task_id])

    assert result.exit_code == 0
    assert "Contract loop:" in result.output
    assert f"[contract:{contract.contract_id}] authorized v2" in result.output
    assert f"[evidence:{evidence_case.evidence_case_id}] sufficient" in result.output
    assert (
        f"[authority:{authorization_plan.authorization_plan_id}] authorized route=operator"
        in result.output
    )
    assert f"[reconciliation:{reconciliation.reconciliation_id}] satisfied" in result.output


def test_task_claim_status_reports_conditional_strong_proofs_without_signing(
    tmp_path, monkeypatch
) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.delenv("HERMIT_PROOF_SIGNING_SECRET", raising=False)
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-claims-conditional", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-claims-conditional",
        title="CLI Conditional Claim Task",
        goal="Inspect conditional claim gate",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="claim receipt",
        result_code="succeeded",
    )
    ProofService(store).export_task_proof(task.task_id)

    runner = CliRunner()
    repo_result = runner.invoke(app, ["task", "claim-status"])
    task_result = runner.invoke(app, ["task", "claim-status", task.task_id])

    assert repo_result.exit_code == 0
    repo_payload = _parse_json_output(repo_result.output)
    assert repo_payload["profiles"]["verifiable"]["claimable"] is True
    assert repo_payload["conditional_capabilities"]["strong_signed_proofs_available"] is False
    signed_row = next(row for row in repo_payload["rows"] if row["id"] == "signed_proofs")
    assert signed_row["status"] == "conditional"

    assert task_result.exit_code == 0
    task_payload = _parse_json_output(task_result.output)
    assert task_payload["task_gate"]["verifiable_ready"] is True
    assert task_payload["task_gate"]["strong_verifiable_ready"] is False


def test_repository_claim_status_rows_are_backed_by_semantic_probes(tmp_path, monkeypatch) -> None:
    from hermit.kernel.artifacts.lineage.claims import repository_claim_status

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))

    payload = repository_claim_status()

    event_row = next(row for row in payload["rows"] if row["id"] == "event_backed_truth")
    assert event_row["status"] == "implemented"
    assert event_row["evaluation"] == "semantic_probe"
    assert payload["cache"]["status"] == "fresh"


def test_repository_claim_status_probe_failures_block_profiles(tmp_path, monkeypatch) -> None:
    import hermit.kernel.artifacts.lineage.claims as claims_mod
    from hermit.kernel.artifacts.lineage.claim_manifest import CLAIM_ROWS

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    fake_rows = {
        str(row["id"]): {"status": "implemented", "evaluation": "semantic_probe"}
        for row in CLAIM_ROWS
        if row["id"] != "signed_proofs"
    }
    fake_rows["ingress_task_first"] = {
        "status": "partial",
        "evaluation": "semantic_probe",
        "probe_error": "RuntimeError: probe failed",
    }
    monkeypatch.setattr(
        claims_mod,
        "_semantic_probe_results",
        lambda **_: fake_rows,
    )

    payload = claims_mod.repository_claim_status()

    ingress_row = next(row for row in payload["rows"] if row["id"] == "ingress_task_first")
    assert ingress_row["status"] == "partial"
    assert ingress_row["evaluation"] == "semantic_probe"
    assert ingress_row["probe_error"] == "RuntimeError: probe failed"
    assert payload["profiles"]["core"]["claimable"] is False
    assert "ingress_task_first" in payload["profiles"]["core"]["blockers"]


def test_task_claim_status_reads_cached_repository_status_without_live_probes(
    tmp_path, monkeypatch
) -> None:
    import hermit.kernel.artifacts.lineage.claims as claims_mod
    from hermit.kernel.artifacts.lineage.claims import repository_claim_status, task_claim_status
    from hermit.kernel.ledger.journal.store import KernelStore

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-claims-cache", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-claims-cache",
        title="CLI Cached Claim Task",
        goal="Inspect cached claim gate",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="claim receipt",
        result_code="succeeded",
    )
    proof = ProofService(store).build_proof_summary(task.task_id)
    repository_claim_status()

    monkeypatch.setattr(
        claims_mod,
        "repository_claim_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live refresh should not run")
        ),
    )

    payload = task_claim_status(store, task.task_id, proof_summary=proof)
    assert payload["repository"]["cache"]["status"] == "fresh"
    assert payload["repository"]["profiles"]["core"]["claimable"] is True


def test_task_claim_status_refreshes_repository_status_when_cache_is_missing(
    tmp_path, monkeypatch
) -> None:
    import hermit.kernel.artifacts.lineage.claims as claims_mod
    from hermit.kernel.artifacts.lineage.claims import repository_claim_status, task_claim_status
    from hermit.kernel.ledger.journal.store import KernelStore

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-claims-miss", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-claims-miss",
        title="CLI Missing Claim Cache Task",
        goal="Refresh claim gate on cache miss",
        source_channel="chat",
    )
    proof = ProofService(store).build_proof_summary(task.task_id)

    calls = {"count": 0}
    original = repository_claim_status

    def _wrapped(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(claims_mod, "repository_claim_status", _wrapped)

    payload = task_claim_status(store, task.task_id, proof_summary=proof)

    assert calls["count"] >= 1
    assert payload["repository"]["cache"]["status"] == "fresh"
    assert payload["repository"]["profiles"]["core"]["claimable"] is True


def test_task_case_and_projection_rebuild_commands(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-case", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-case",
        title="CLI Case Task",
        goal="Show operator case",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed this write.",
        evidence_refs=[],
        action_type="write_local",
    )
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref="policy_case",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=["workspace"],
        constraints={"target_paths": ["workspace/case.txt"]},
        idempotency_key="idem_case",
        expires_at=None,
    )
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="case result",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        policy_ref="policy_case",
    )
    background = store.create_task(
        conversation_id="cli-case",
        title="Background task",
        goal="Keep another task open",
        source_channel="chat",
    )
    store.set_conversation_focus("cli-case", task_id=task.task_id, reason="explicit_task_switch")
    bound_ingress = store.create_ingress(
        conversation_id="cli-case",
        source_channel="chat",
        actor="user",
        raw_text="这个改成 Markdown，并保留 artifact_123",
        normalized_text="这个改成 markdown，并保留 artifact_123",
        reply_to_ref="msg_reply_1",
        quoted_message_ref="msg_quote_1",
        referenced_artifact_refs=["artifact_123"],
    )
    store.update_ingress(
        bound_ingress.ingress_id,
        status="bound",
        resolution="append_note",
        chosen_task_id=task.task_id,
        confidence=0.91,
        margin=0.48,
        rationale={
            "reason_codes": ["focus_task", "artifact_ref"],
            "shadow_binding": {
                "resolution": "append_note",
                "chosen_task_id": background.task_id,
                "match_actual": False,
            },
        },
    )
    pending_ingress = store.create_ingress(
        conversation_id="cli-case",
        source_channel="chat",
        actor="user",
        raw_text="这个也改一下",
        normalized_text="这个也改一下",
    )
    store.update_ingress(
        pending_ingress.ingress_id,
        status="pending_disambiguation",
        resolution="pending_disambiguation",
        rationale={"reason_codes": ["ambiguous_close_tie"]},
    )

    runner = CliRunner()
    case_result = runner.invoke(app, ["task", "case", task.task_id])
    rebuild_result = runner.invoke(app, ["task", "projections-rebuild", task.task_id])

    assert case_result.exit_code == 0
    case_payload = _parse_json_output(case_result.output)
    assert case_payload["operator_answers"]["why_execute"] == "Policy allowed this write."
    assert (
        case_payload["operator_answers"]["claims"]["repository"]["profiles"]["core"]["claimable"]
        is True
    )
    assert case_payload["operator_answers"]["reentry"]["required_count"] == 0
    assert case_payload["ingress_observability"]["conversation"]["focus"]["task_id"] == task.task_id
    assert (
        case_payload["ingress_observability"]["conversation"]["metrics"]["resolution_counts"][
            "append_note"
        ]
        >= 1
    )
    assert case_payload["ingress_observability"]["conversation"]["pending_ingress_count"] >= 1
    assert any(
        item["reply_to_ref"] == "msg_reply_1"
        for item in case_payload["ingress_observability"]["conversation"]["recent_ingresses"]
    )
    assert (
        case_payload["ingress_observability"]["task"]["recent_related_ingresses"][0]["relation"]
        == "chosen_task"
    )
    assert (
        case_payload["ingress_observability"]["task"]["pending_disambiguations"][0]["status"]
        == "pending_disambiguation"
    )
    assert rebuild_result.exit_code == 0
    assert _parse_json_output(rebuild_result.output)["task"]["task_id"] == task.task_id


def test_memory_export_command_writes_export_only_mirror(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.create_memory_record(
        task_id="task-memory-export",
        conversation_id="chat-memory-export",
        category="project_convention",
        claim_text="默认在 /repo 执行命令",
        scope_kind="workspace",
        scope_ref="/repo",
        retention_class="project_convention",
    )

    output_path = tmp_path / "memory-export.md"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["memory", "export", "--output", str(output_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["render_mode"] == "export_only"
    assert payload["active_records"] == 1
    assert payload["export_path"] == str(output_path)
    assert output_path.exists()
    assert "默认在 /repo 执行命令" in output_path.read_text(encoding="utf-8")


def test_task_approve_and_deny_commands_delegate_to_runner(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli._commands_core as core_mod
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-approval", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-approval",
        title="Pending approval",
        goal="Approve from CLI",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    approval = store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="write_local",
        requested_action={"tool_name": "write_file"},
        request_packet_ref=None,
    )

    calls: list[tuple[str, str, str, str]] = []

    class FakeRunner:
        def _resolve_approval(
            self, conversation_id: str, *, action: str, approval_id: str, reason: str = ""
        ):
            calls.append((conversation_id, action, approval_id, reason))
            return SimpleNamespace(text=f"{action}:{approval_id}")

    class FakePM:
        def stop_mcp_servers(self) -> None:
            return None

    monkeypatch.setattr(core_mod, "build_runner", lambda settings: (FakeRunner(), FakePM()))

    runner = CliRunner()
    approve_result = runner.invoke(app, ["task", "approve", approval.approval_id])
    deny_result = runner.invoke(app, ["task", "deny", approval.approval_id, "--reason", "hold"])

    assert approve_result.exit_code == 0
    assert deny_result.exit_code == 0
    assert approve_result.output.strip() == f"approve_once:{approval.approval_id}"
    assert deny_result.output.strip() == f"deny:{approval.approval_id}"
    assert calls == [
        ("cli-approval", "approve_once", approval.approval_id, ""),
        ("cli-approval", "deny", approval.approval_id, "hold"),
    ]


def test_task_show_displays_approval_canonical_summary(monkeypatch, tmp_path) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()
    _seed_claim_cache(base_dir)

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-task-show", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-task-show",
        title="CLI Approval Summary",
        goal="Inspect approval summary",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    approval = store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="write_local",
        requested_action={
            "tool_name": "write_file",
            "display_copy": {
                "title": "确认文件修改",
                "summary": "准备修改 1 个文件：`src/app.py`。",
                "detail": "变更预览已生成；确认后将继续执行。",
            },
        },
        request_packet_ref=None,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["task", "show", task.task_id])

    assert result.exit_code == 0
    assert approval.approval_id in result.output
    assert "准备修改 1 个文件" in result.output


def test_task_list_and_show_use_localized_cli_copy(monkeypatch, tmp_path) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    get_settings.cache_clear()
    _seed_claim_cache(base_dir)

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-task-list", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-task-list",
        title="Localized Task",
        goal="Inspect CLI localization",
        source_channel="chat",
    )

    runner = CliRunner()
    list_result = runner.invoke(app, ["task", "list"])
    show_result = runner.invoke(app, ["task", "show", task.task_id])

    assert list_result.exit_code == 0
    assert f"[{task.task_id}] {task.status} chat Localized Task" in list_result.output
    assert show_result.exit_code == 0
    assert "最近的审批记录：" not in show_result.output


def test_task_list_uses_english_cli_copy(monkeypatch, tmp_path) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-task-list-en", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-task-list-en",
        title="English Task",
        goal="Inspect CLI localization",
        source_channel="chat",
    )

    runner = CliRunner()
    list_result = runner.invoke(app, ["task", "list"])

    assert list_result.exit_code == 0
    assert f"[{task.task_id}] {task.status} chat English Task" in list_result.output


def test_task_capability_subcommands_list_and_revoke(monkeypatch, tmp_path) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-grants", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-grants",
        title="Capability CLI Task",
        goal="Inspect capability commands",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref="decision_1",
        approval_ref="approval_1",
        policy_ref="policy_1",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=[str((tmp_path / "Desktop").resolve())],
        constraints={"target_paths": [str((tmp_path / "Desktop").resolve())]},
        idempotency_key="cli-capability",
        expires_at=None,
    )

    runner = CliRunner()
    list_result = runner.invoke(app, ["task", "capability", "list"])
    revoke_result = runner.invoke(app, ["task", "capability", "revoke", grant.grant_id])

    assert list_result.exit_code == 0
    assert grant.grant_id in list_result.output
    assert revoke_result.exit_code == 0
    assert f"已撤销能力授权 '{grant.grant_id}'。" in revoke_result.output
    assert store.get_capability_grant(grant.grant_id).status == "revoked"
