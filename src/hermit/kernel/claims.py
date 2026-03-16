from __future__ import annotations

import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable, cast

from hermit.kernel.claim_manifest import CLAIM_ROWS, PROFILE_LABELS
from hermit.kernel.proofs import ProofService, proof_capabilities
from hermit.kernel.store import KernelStore
from hermit.storage.atomic import atomic_write

_CLAIM_CACHE_SCHEMA_VERSION = "repository-claims-v1"
_CLAIM_CACHE_FILENAME = "repository-claim-status.json"


def _probe_write_registry(
    workspace: Path,
    *,
    handler: Callable[[dict[str, Any]], Any] | None = None,
):
    from hermit.core.tools import ToolRegistry, ToolSpec

    registry = ToolRegistry()

    def write_file(payload: dict[str, Any]) -> str:
        path = workspace / str(payload["path"])
        path.write_text(str(payload["content"]), encoding="utf-8")
        return "ok"

    registry.register(
        ToolSpec(
            name="write_file",
            description="Write a UTF-8 text file inside the workspace.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=handler or write_file,
            action_class="write_local",
            resource_scope_hint=str(workspace),
            risk_hint="high",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    return registry


def _probe_task_runtime(
    base: Path,
    *,
    goal: str = "Claim probe task",
    registry: Any | None = None,
):
    from hermit.kernel.approvals import ApprovalService
    from hermit.kernel.artifacts import ArtifactStore
    from hermit.kernel.controller import TaskController
    from hermit.kernel.executor import ToolExecutor
    from hermit.kernel.policy import PolicyEngine
    from hermit.kernel.receipts import ReceiptService

    workspace = base / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(base / "kernel" / "state.db")
    artifacts = ArtifactStore(base / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="claim-probe",
        goal=goal,
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )
    executor = ToolExecutor(
        registry=registry or _probe_write_registry(workspace),
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store, artifacts),
        tool_output_limit=2000,
    )
    return store, artifacts, controller, executor, ctx, workspace


def _close_store(store: KernelStore | None) -> None:
    if store is None:
        return
    close = getattr(store, "close", None)
    if callable(close):
        close()


def _probe_ingress_task_first() -> None:
    from hermit.kernel.controller import TaskController

    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            store = KernelStore(base / "kernel" / "state.db")
            controller = TaskController(store)
            decision = controller.decide_ingress(
                conversation_id="claim-probe",
                source_channel="chat",
                raw_text="Please update the README",
                prompt="Please update the README",
            )
            assert decision.ingress_id
            ingress = store.get_ingress(decision.ingress_id)
            assert ingress is not None
            assert ingress.status == "bound"
            assert ingress.resolution == "start_new_root"

            ctx = controller.start_task(
                conversation_id="claim-probe",
                goal="Please update the README",
                source_channel="chat",
                kind="respond",
                ingress_metadata={
                    "ingress_id": decision.ingress_id,
                    "ingress_resolution": decision.resolution,
                    "binding_reason_codes": list(decision.reason_codes or []),
                },
            )
            rebound = store.get_ingress(decision.ingress_id)
            assert rebound is not None
            assert rebound.chosen_task_id == ctx.task_id
            conversation = store.get_conversation("claim-probe")
            assert conversation is not None and conversation.focus_task_id == ctx.task_id
        finally:
            _close_store(store)


def _probe_event_backed_truth() -> None:
    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            store, _artifacts, _controller, _executor, ctx, _workspace = _probe_task_runtime(base)
            verification = ProofService(store).verify_task_chain(ctx.task_id)
            events = store.list_events(task_id=ctx.task_id, limit=20)
            assert verification["valid"] is True
            assert verification["event_count"] >= 3
            assert events
            assert all(str(event.get("event_hash") or "").strip() for event in events)
        finally:
            _close_store(store)


def _probe_no_tool_bypass() -> None:
    from hermit.core.tools import ToolGovernanceError, ToolRegistry, ToolSpec
    from hermit.kernel.context import TaskExecutionContext
    from hermit.provider.contracts import ProviderFeatures
    from hermit.provider.runtime import AgentRuntime

    try:
        ToolSpec(
            name="missing_governance",
            description="Broken tool",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda _payload: "ok",
        )
    except ToolGovernanceError:
        pass
    else:
        raise AssertionError("Tool governance metadata is not enforced.")

    from hermit.provider.contracts import Provider as ProviderProtocol

    runtime = AgentRuntime(
        provider=cast(
            ProviderProtocol, SimpleNamespace(name="claim-probe", features=ProviderFeatures())
        ),
        registry=ToolRegistry(),
        model="claim-probe-model",
    )
    ctx = TaskExecutionContext(
        conversation_id="claim-probe",
        task_id="task-probe",
        step_id="step-probe",
        step_attempt_id="attempt-probe",
        source_channel="chat",
    )
    try:
        # Access via getattr to avoid pyright private-usage warning in probe code
        execute_tool = cast(Any, runtime)._execute_tool
        execute_tool(
            task_context=ctx,
            tool_name="write_file",
            tool_input={"path": "README.md", "content": "probe\n"},
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Tool execution bypassed the kernel executor gate.")


def _probe_scoped_authority() -> None:
    from hermit.capabilities import CapabilityGrantService
    from hermit.kernel.approvals import ApprovalService
    from hermit.kernel.decisions import DecisionService
    from hermit.workspaces import WorkspaceLeaseService

    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            store, artifacts, _controller, _executor, ctx, workspace = _probe_task_runtime(
                base, goal="Exercise scoped authority"
            )
            target_path = workspace / "scoped.txt"
            decision_id = DecisionService(store).record(
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                step_attempt_id=ctx.step_attempt_id,
                decision_type="policy_gate",
                verdict="allow_with_receipt",
                reason="Claim probe scoped authority",
                action_type="write_local",
            )
            approvals = ApprovalService(store)
            approval_id = approvals.request(
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                step_attempt_id=ctx.step_attempt_id,
                approval_type="once",
                requested_action={
                    "tool_name": "write_file",
                    "action_class": "write_local",
                    "target_paths": [str(target_path)],
                },
                request_packet_ref="artifact_request_packet_probe",
                approval_packet_ref="artifact_approval_packet_probe",
                decision_ref=decision_id,
            )
            receipt_id = approvals.approve_once(approval_id)
            approval = store.get_approval(approval_id)
            assert approval is not None and approval.status == "granted"
            assert receipt_id is not None and store.get_receipt(receipt_id) is not None

            lease = WorkspaceLeaseService(store, artifacts).acquire(
                task_id=ctx.task_id,
                step_attempt_id=ctx.step_attempt_id,
                workspace_id="claim-probe",
                root_path=str(workspace),
                holder_principal_id="user",
                mode="scoped",
                resource_scope=[str(workspace)],
            )
            grant_service = CapabilityGrantService(store)
            grant_id = grant_service.issue(
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                step_attempt_id=ctx.step_attempt_id,
                decision_ref=decision_id,
                approval_ref=approval_id,
                policy_ref=None,
                issued_to_principal_id="user",
                issued_by_principal_id="kernel",
                workspace_lease_ref=lease.lease_id,
                action_class="write_local",
                resource_scope=[str(target_path)],
                idempotency_key="claim-probe-grant",
                constraints={
                    "lease_root_path": str(workspace),
                    "target_paths": [str(target_path)],
                },
            )
            grant = grant_service.enforce(
                grant_id,
                action_class="write_local",
                resource_scope=[str(target_path)],
                constraints={"target_paths": [str(target_path)]},
            )
            assert grant.grant_id == grant_id
            assert grant.workspace_lease_ref == lease.lease_id
        finally:
            _close_store(store)


def _probe_receipts() -> None:
    from hermit.kernel.receipts import ReceiptService

    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            store, artifacts, _controller, _executor, ctx, _workspace = _probe_task_runtime(
                base, goal="Issue receipt"
            )
            receipt_id = ReceiptService(store, artifacts).issue(
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                step_attempt_id=ctx.step_attempt_id,
                action_type="write_local",
                input_refs=[],
                environment_ref=None,
                policy_result={"decision": "allow_with_receipt"},
                approval_ref=None,
                output_refs=[],
                result_summary="Claim probe receipt",
            )
            receipt = store.get_receipt(receipt_id)
            assert receipt is not None
            assert str(receipt.receipt_bundle_ref or "").strip()
            assert store.get_artifact(receipt.receipt_bundle_ref or "") is not None
        finally:
            _close_store(store)


def _probe_uncertain_outcome() -> None:
    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            workspace = base / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            def flaky_write(payload: dict[str, Any]) -> str:
                path = workspace / str(payload["path"])
                path.write_text(str(payload["content"]), encoding="utf-8")
                raise RuntimeError("post-write crash")

            registry = _probe_write_registry(workspace, handler=flaky_write)
            store, _artifacts, _controller, executor, ctx, _workspace = _probe_task_runtime(
                base,
                goal="Exercise uncertain outcome",
                registry=registry,
            )
            result = executor.execute(
                ctx, "write_file", {"path": "maybe.txt", "content": "hello\n"}
            )
            assert result.receipt_id is not None
            assert result.execution_status in {"succeeded", "reconciling", "needs_attention"}
            assert any(
                event["event_type"] == "outcome.uncertain"
                for event in store.list_events(task_id=ctx.task_id, limit=100)
            )
            assert any(
                event["event_type"] == "reconciliation.closed"
                for event in store.list_events(task_id=ctx.task_id, limit=100)
            )
        finally:
            _close_store(store)


def _probe_durable_reentry() -> None:
    from hermit.kernel.approvals import ApprovalService

    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            store, _artifacts, _controller, executor, ctx, _workspace = _probe_task_runtime(
                base, goal="Exercise witness drift re-entry"
            )
            target = base / "outside-workspace.txt"
            target.write_text("before\n", encoding="utf-8")

            first = executor.execute(
                ctx,
                "write_file",
                {"path": str(target), "content": "after\n"},
            )
            assert first.approval_id is not None
            ApprovalService(store).approve(first.approval_id)
            target.write_text("changed-by-someone-else\n", encoding="utf-8")

            second = executor.execute(
                ctx,
                "write_file",
                {"path": str(target), "content": "after\n"},
            )
            assert second.blocked is True
            assert second.approval_id is not None
            assert second.approval_id != first.approval_id
            assert any(
                event["event_type"] == "witness.failed"
                for event in store.list_events(task_id=ctx.task_id, limit=100)
            )
            assert any(
                event["event_type"] == "step_attempt.superseded"
                for event in store.list_events(task_id=ctx.task_id, limit=100)
            )
        finally:
            _close_store(store)


def _probe_artifact_context() -> None:
    from hermit.kernel.provider_input import ProviderInputCompiler

    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            store, artifacts, _controller, _executor, ctx, _workspace = _probe_task_runtime(
                base, goal="Compile provider input"
            )
            compiled = ProviderInputCompiler(store, artifacts).compile(
                task_context=ctx,
                final_prompt="Please update README.md\n```py\nprint('hello')\n```",
                raw_text="Please update README.md\n```py\nprint('hello')\n```",
            )
            attempt = store.get_step_attempt(ctx.step_attempt_id)
            assert compiled.context_pack_ref is not None
            assert compiled.ingress_artifact_refs
            assert attempt is not None
            assert attempt.context_pack_ref == compiled.context_pack_ref
            assert attempt.executor_mode == "compiled_provider_input"
            artifact = store.get_artifact(compiled.context_pack_ref)
            assert artifact is not None and str(artifact.kind).startswith("context.pack/")
        finally:
            _close_store(store)


def _probe_memory_evidence() -> None:
    from hermit.kernel.artifacts import ArtifactStore
    from hermit.kernel.knowledge import BeliefService, MemoryRecordService
    from hermit.kernel.memory_governance import MemoryGovernanceService

    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            store = KernelStore(base / "kernel" / "state.db")
            artifacts = ArtifactStore(base / "kernel" / "artifacts")
            store.ensure_conversation("claim-memory", source_channel="chat")
            uri, content_hash = artifacts.store_json({"kind": "claim-probe", "detail": "memory"})
            artifact = store.create_artifact(
                task_id=None,
                step_id=None,
                kind="claim.probe.evidence",
                uri=uri,
                content_hash=content_hash,
                producer="claim_probe",
                retention_class="audit",
                trust_tier="observed",
            )
            belief = BeliefService(store).record(
                task_id="task-memory",
                conversation_id="claim-memory",
                scope_kind="conversation",
                scope_ref="claim-memory",
                category="项目约定",
                content="默认工作目录固定到 /repo",
                confidence=0.8,
                evidence_refs=[artifact.artifact_id],
            )
            inspection = cast(
                dict[str, Any],
                MemoryGovernanceService().inspect_claim(
                    category="项目约定",
                    claim_text=belief.claim_text,
                    conversation_id="claim-memory",
                    workspace_root=str(base / "workspace"),
                ),
            )
            reconciliation = store.create_reconciliation(
                task_id="task-memory",
                step_id="step-memory",
                step_attempt_id="attempt-memory",
                contract_ref="contract-memory",
                receipt_refs=[],
                observed_output_refs=[],
                intended_effect_summary="Promote durable memory.",
                authorized_effect_summary="Promote durable memory.",
                observed_effect_summary="Memory evidence reconciled.",
                receipted_effect_summary="Memory evidence reconciled.",
                result_class="satisfied",
                recommended_resolution="promote_learning",
            )
            memory = MemoryRecordService(store).promote_from_belief(
                belief=belief,
                conversation_id="claim-memory",
                workspace_root=str(base / "workspace"),
                reconciliation_ref=reconciliation.reconciliation_id,
            )
            assert memory is not None
            assert memory.evidence_refs == [artifact.artifact_id]
            assert memory.trust_tier == "durable"
            assert memory.source_belief_ref == belief.belief_id
            assert dict(inspection.get("structured_assertion", {}))
        finally:
            _close_store(store)


def _probe_proof_export() -> None:
    from hermit.kernel.receipts import ReceiptService

    store: KernelStore | None = None
    with TemporaryDirectory() as tmpdir:
        try:
            base = Path(tmpdir)
            store, artifacts, _controller, _executor, ctx, _workspace = _probe_task_runtime(
                base, goal="Export proof bundle"
            )
            ReceiptService(store, artifacts).issue(
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                step_attempt_id=ctx.step_attempt_id,
                action_type="write_local",
                input_refs=[],
                environment_ref=None,
                policy_result={"decision": "allow_with_receipt"},
                approval_ref=None,
                output_refs=[],
                result_summary="Claim probe proof export",
            )
            exported = ProofService(store, artifacts).export_task_proof(ctx.task_id)
            assert exported["status"] == "verified"
            assert exported["chain_verification"]["valid"] is True
            assert exported["receipt_bundles"]
            assert str(exported.get("proof_bundle_ref", "") or "").strip()
        finally:
            _close_store(store)


def _semantic_probe(row_id: str, probe: Callable[[], None]) -> dict[str, Any]:
    try:
        probe()
    except Exception as exc:  # pragma: no cover - error path exercised via monkeypatch
        return {
            "status": "partial",
            "evaluation": "semantic_probe",
            "probe_error": f"{type(exc).__name__}: {exc}",
        }
    return {"status": "implemented", "evaluation": "semantic_probe"}


def _semantic_probe_results(*, include_expensive_probes: bool = True) -> dict[str, dict[str, Any]]:
    probes: dict[str, Callable[[], None]] = {
        "ingress_task_first": _probe_ingress_task_first,
        "event_backed_truth": _probe_event_backed_truth,
        "no_tool_bypass": _probe_no_tool_bypass,
        "scoped_authority": _probe_scoped_authority,
        "receipts": _probe_receipts,
        "uncertain_outcome": _probe_uncertain_outcome,
        "durable_reentry": _probe_durable_reentry,
        "memory_evidence": _probe_memory_evidence,
        "proof_export": _probe_proof_export,
    }
    if include_expensive_probes:
        probes["artifact_context"] = _probe_artifact_context
    return {row_id: _semantic_probe(row_id, probe) for row_id, probe in probes.items()}


def _conditional_row_status(row_id: str, caps: dict[str, Any]) -> dict[str, Any]:
    if row_id != "signed_proofs":
        return {
            "status": "partial",
            "evaluation": "semantic_probe",
            "probe_error": f"Missing semantic probe for row '{row_id}'.",
        }
    return {
        "status": "implemented" if caps["signing_configured"] else "conditional",
        "evaluation": "conditional_capability",
    }


def _claim_cache_path(*, store: KernelStore | None = None, base_dir: Path | None = None) -> Path:
    if store is not None:
        return store.db_path.parent / _CLAIM_CACHE_FILENAME
    if base_dir is None:
        base_dir_raw = os.environ.get("HERMIT_BASE_DIR", "")
        base_dir = Path(base_dir_raw).expanduser() if base_dir_raw else Path.home() / ".hermit"
    return base_dir / "kernel" / _CLAIM_CACHE_FILENAME


def _repository_claim_status_payload(
    *,
    semantic: dict[str, dict[str, Any]],
    generated_at: float,
    include_expensive_probes: bool,
    cache_status: str,
) -> dict[str, Any]:
    proof_caps = proof_capabilities()
    rows: list[dict[str, Any]] = []
    blockers_by_profile: dict[str, list[str]] = {profile: [] for profile in PROFILE_LABELS}
    for row in CLAIM_ROWS:
        row_id = str(row["id"])
        computed = dict(row)
        computed.update(semantic.get(row_id) or _conditional_row_status(row_id, proof_caps))
        rows.append(computed)
        for profile in row.get("profiles", []):
            if computed["status"] != "implemented":
                blockers_by_profile[str(profile)].append(row_id)

    profiles = {
        profile: {
            "claimable": not blockers_by_profile[profile],
            "label": PROFILE_LABELS[profile],
            "blockers": list(blockers_by_profile[profile]),
        }
        for profile in PROFILE_LABELS
    }
    repo_blockers = sorted({blocker for items in blockers_by_profile.values() for blocker in items})
    return {
        "rows": rows,
        "profiles": profiles,
        "claimable_profiles": [
            payload["label"] for payload in profiles.values() if payload["claimable"]
        ],
        "blockers": repo_blockers,
        "conditional_capabilities": {
            "signing_configured": proof_caps["signing_configured"],
            "strong_signed_proofs_available": proof_caps["strong_signed_proofs_available"],
            "baseline_verifiable_available": proof_caps["baseline_verifiable_available"],
        },
        "cache": {
            "schema_version": _CLAIM_CACHE_SCHEMA_VERSION,
            "generated_at": generated_at,
            "include_expensive_probes": include_expensive_probes,
            "status": cache_status,
        },
    }


def _repository_claim_status_cache_miss(
    *, include_expensive_probes: bool = False
) -> dict[str, Any]:
    semantic: dict[str, dict[str, Any]] = {}
    for row in CLAIM_ROWS:
        row_id = str(row["id"])
        if row_id == "signed_proofs":
            continue
        semantic[row_id] = {
            "status": "unknown",
            "evaluation": "cache_miss",
            "probe_error": "Repository claim cache is not available yet.",
        }
    return _repository_claim_status_payload(
        semantic=semantic,
        generated_at=0.0,
        include_expensive_probes=include_expensive_probes,
        cache_status="missing",
    )


def _write_repository_claim_status_cache(
    payload: dict[str, Any], *, store: KernelStore | None = None, base_dir: Path | None = None
) -> Path:
    path = _claim_cache_path(store=store, base_dir=base_dir)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def read_repository_claim_status_cache(
    *, store: KernelStore | None = None, base_dir: Path | None = None
) -> dict[str, Any]:
    path = _claim_cache_path(store=store, base_dir=base_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return _repository_claim_status_cache_miss()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _repository_claim_status_cache_miss()
    if not isinstance(payload, dict):
        return _repository_claim_status_cache_miss()
    typed_payload = cast(dict[str, Any], payload)
    if not isinstance(typed_payload.get("rows"), list) or not isinstance(
        typed_payload.get("profiles"), dict
    ):
        return _repository_claim_status_cache_miss()
    cache = cast(dict[str, Any], dict(typed_payload.get("cache") or {}))
    if str(cache.get("schema_version", "")) != _CLAIM_CACHE_SCHEMA_VERSION:
        return _repository_claim_status_cache_miss()
    return typed_payload


def repository_claim_status(*, include_expensive_probes: bool = True) -> dict[str, Any]:
    semantic = _semantic_probe_results(include_expensive_probes=include_expensive_probes)
    payload = _repository_claim_status_payload(
        semantic=semantic,
        generated_at=time.time(),
        include_expensive_probes=include_expensive_probes,
        cache_status="fresh",
    )
    _write_repository_claim_status_cache(payload)
    return payload


def task_claim_status(
    store: KernelStore, task_id: str, *, proof_summary: dict[str, Any]
) -> dict[str, Any]:
    repo = read_repository_claim_status_cache(store=store)
    cache = dict(repo.get("cache") or {})
    if str(cache.get("status", "")) == "missing":
        payload = repository_claim_status(include_expensive_probes=True)
        repo = dict(payload)
        _write_repository_claim_status_cache(repo, store=store)
    coverage = dict(proof_summary.get("proof_coverage", {}) or {})
    chain = dict(proof_summary.get("chain_verification", {}) or {})
    receipt_bundle = dict(coverage.get("receipt_bundle_coverage", {}) or {})
    signature_coverage = dict(coverage.get("signature_coverage", {}) or {})
    inclusion_coverage = dict(coverage.get("inclusion_proof_coverage", {}) or {})
    verifiable_ready = bool(chain.get("valid")) and (
        int(receipt_bundle.get("bundled_receipts", 0) or 0)
        == int(receipt_bundle.get("total_receipts", 0) or 0)
    )
    strong_mode = proof_summary.get("strongest_export_mode") == "signed_with_inclusion_proof"
    strongest_ready = (
        verifiable_ready
        and strong_mode
        and (
            int(signature_coverage.get("signed_receipts", 0) or 0)
            == int(signature_coverage.get("total_receipts", 0) or 0)
        )
        and (
            int(inclusion_coverage.get("proved_receipts", 0) or 0)
            == int(inclusion_coverage.get("total_receipts", 0) or 0)
        )
    )
    return {
        "task_id": task_id,
        "repository": repo,
        "task_gate": {
            "chain_valid": bool(chain.get("valid")),
            "verifiable_ready": verifiable_ready,
            "strong_verifiable_ready": strongest_ready,
            "proof_mode": proof_summary.get("proof_mode"),
            "strongest_export_mode": proof_summary.get("strongest_export_mode"),
        },
    }


__all__ = [
    "read_repository_claim_status_cache",
    "repository_claim_status",
    "task_claim_status",
]
