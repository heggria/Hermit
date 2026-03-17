from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from hermit.kernel import (
    ApprovalCopyService,
    ProjectionService,
    RollbackService,
    SupervisionService,
)
from hermit.kernel.artifacts.lineage.claims import repository_claim_status, task_claim_status
from hermit.kernel.verification.proofs.proofs import ProofService
from hermit.runtime.assembly.config import get_settings

from ._helpers import (
    ensure_workspace,
    get_kernel_store,
    stop_runner_background_services,
)
from .main import cli_t, t, task_app, task_capability_app


@task_app.command("list")
def task_list(
    limit: int = typer.Option(
        20,
        help=cli_t("cli.task.list.limit", "Maximum number of tasks to show."),
    ),
) -> None:
    """List recent tasks from the kernel ledger."""
    store = get_kernel_store()
    tasks = store.list_tasks(limit=limit)
    if not tasks:
        typer.echo(t("cli.task.list.empty", "No tasks found."))
        return
    for task in tasks:
        typer.echo(
            t(
                "cli.task.list.item",
                "[{task_id}] {status} {source_channel} {title}",
                task_id=task.task_id,
                status=task.status,
                source_channel=task.source_channel,
                title=task.title,
            )
        )


@task_app.command("show")
def task_show(
    task_id: str = typer.Argument(..., help=cli_t("cli.task.common.task_id", "Task ID.")),
) -> None:
    """Show one task and its pending approvals."""
    store = get_kernel_store()
    task = store.get_task(task_id)
    if task is None:
        typer.echo(t("cli.task.show.not_found", "Task not found: {task_id}", task_id=task_id))
        raise typer.Exit(1)
    typer.echo(json.dumps(task.__dict__, ensure_ascii=False, indent=2))
    approvals = store.list_approvals(task_id=task_id, limit=20)
    if approvals:
        typer.echo("\n" + t("cli.task.show.approvals", "Pending/Recent approvals:"))
        copy_service = ApprovalCopyService()
        for approval in approvals:
            typer.echo(
                t(
                    "cli.task.show.approval_item",
                    "  [{approval_id}] {status} {approval_type}",
                    approval_id=approval.approval_id,
                    status=approval.status,
                    approval_type=approval.approval_type,
                )
            )
            summary = copy_service.resolve_copy(
                approval.requested_action, approval.approval_id
            ).summary
            typer.echo(t("cli.task.show.indented", "    {value}", value=summary))
            if approval.decision_ref:
                typer.echo(
                    t(
                        "cli.task.show.decision_ref",
                        "    decision_ref={decision_ref}",
                        decision_ref=approval.decision_ref,
                    )
                )
            if approval.state_witness_ref:
                typer.echo(
                    t(
                        "cli.task.show.witness_ref",
                        "    witness_ref={witness_ref}",
                        witness_ref=approval.state_witness_ref,
                    )
                )

    decisions = store.list_decisions(task_id=task_id, limit=20)
    if decisions:
        typer.echo("\n" + t("cli.task.show.decisions", "Recent decisions:"))
        for decision in decisions:
            typer.echo(
                t(
                    "cli.task.show.decision_item",
                    "  [{decision_id}] {verdict} {decision_type} action={action_type}",
                    decision_id=decision.decision_id,
                    verdict=decision.verdict,
                    decision_type=decision.decision_type,
                    action_type=decision.action_type,
                )
            )
            typer.echo(t("cli.task.show.indented", "    {value}", value=decision.reason))

    capability_grants = store.list_capability_grants(task_id=task_id, limit=20)
    if capability_grants:
        typer.echo("\n" + t("cli.task.show.capability_grants", "Recent capability grants:"))
        for grant in capability_grants:
            typer.echo(
                t(
                    "cli.task.show.capability_grant_item",
                    "  [{grant_id}] {status} {action_class}",
                    grant_id=grant.grant_id,
                    status=grant.status,
                    action_class=grant.action_class,
                )
            )
            typer.echo(
                t(
                    "cli.task.show.decision_ref",
                    "    decision_ref={decision_ref}",
                    decision_ref=grant.decision_ref,
                )
            )

    workspace_leases = store.list_workspace_leases(task_id=task_id, limit=20)
    if workspace_leases:
        typer.echo("\n" + t("cli.task.show.workspace_leases", "Recent workspace leases:"))
        for lease in workspace_leases:
            typer.echo(
                t(
                    "cli.task.show.workspace_lease_item",
                    "  [{lease_id}] {status} {mode} root={root_path}",
                    lease_id=lease.lease_id,
                    status=lease.status,
                    mode=lease.mode,
                    root_path=lease.root_path,
                )
            )
    if hasattr(store, "list_execution_contracts"):
        contracts = store.list_execution_contracts(task_id=task_id, limit=5)
        evidence_cases = store.list_evidence_cases(task_id=task_id, limit=5)
        authorization_plans = store.list_authorization_plans(task_id=task_id, limit=5)
        reconciliations = store.list_reconciliations(task_id=task_id, limit=5)
        if contracts or evidence_cases or authorization_plans or reconciliations:
            typer.echo("\n" + t("cli.task.show.contract_loop", "Contract loop:"))
        for contract in contracts[:3]:
            typer.echo(
                t(
                    "cli.task.show.indented",
                    "    {value}",
                    value=(
                        f"[contract:{contract.contract_id}] {contract.status} "
                        f"v{contract.contract_version} objective={contract.objective}"
                    ),
                )
            )
            if contract.expected_effects:
                typer.echo(
                    t(
                        "cli.task.show.indented",
                        "    {value}",
                        value=f"expected_effects={', '.join(contract.expected_effects[:4])}",
                    )
                )
        for evidence_case in evidence_cases[:3]:
            typer.echo(
                t(
                    "cli.task.show.indented",
                    "    {value}",
                    value=(
                        f"[evidence:{evidence_case.evidence_case_id}] {evidence_case.status} "
                        f"score={evidence_case.sufficiency_score:.2f} "
                        f"gaps={', '.join(evidence_case.unresolved_gaps) or '-'}"
                    ),
                )
            )
        for authorization_plan in authorization_plans[:3]:
            typer.echo(
                t(
                    "cli.task.show.indented",
                    "    {value}",
                    value=(
                        f"[authority:{authorization_plan.authorization_plan_id}] "
                        f"{authorization_plan.status} route={authorization_plan.approval_route} "
                        f"gaps={', '.join(authorization_plan.current_gaps) or '-'}"
                    ),
                )
            )
        for reconciliation in reconciliations[:3]:
            typer.echo(
                t(
                    "cli.task.show.indented",
                    "    {value}",
                    value=(
                        f"[reconciliation:{reconciliation.reconciliation_id}] "
                        f"{reconciliation.result_class} "
                        f"resolution={reconciliation.recommended_resolution or '-'}"
                    ),
                )
            )
    case = SupervisionService(store).build_task_case(task_id)
    claims = dict(case["operator_answers"].get("claims", {}) or {})
    task_gate = dict(claims.get("task_gate", {}) or {})
    claimable = list(claims.get("repository", {}).get("claimable_profiles", []) or [])
    reentry = dict(case["operator_answers"].get("reentry", {}) or {})
    typer.echo("\n" + t("cli.task.show.claims", "Claim status:"))
    typer.echo(
        t(
            "cli.task.show.indented",
            "    {value}",
            value=(
                f"repository={', '.join(claimable) or '-'} "
                f"verifiable_ready={bool(task_gate.get('verifiable_ready'))} "
                f"strong_verifiable_ready={bool(task_gate.get('strong_verifiable_ready'))} "
                f"proof_mode={task_gate.get('proof_mode') or '-'} "
                f"strongest_export_mode={task_gate.get('strongest_export_mode') or '-'}"
            ),
        )
    )
    typer.echo("\n" + t("cli.task.show.reentry", "Re-entry status:"))
    typer.echo(
        t(
            "cli.task.show.indented",
            "    {value}",
            value=(
                f"required={int(reentry.get('required_count', 0) or 0)} "
                f"resolved={int(reentry.get('resolved_count', 0) or 0)}"
            ),
        )
    )
    for item in list(reentry.get("recent_attempts", []) or [])[:3]:
        typer.echo(
            t(
                "cli.task.show.indented",
                "    {value}",
                value=(
                    f"[{item.get('step_attempt_id')}] {item.get('status')} "
                    f"reason={item.get('reentry_reason') or '-'} "
                    f"boundary={item.get('reentry_boundary') or '-'} "
                    f"recovery_required={bool(item.get('recovery_required'))}"
                ),
            )
        )


@task_app.command("events")
def task_events(
    task_id: str = typer.Argument(..., help=cli_t("cli.task.common.task_id", "Task ID.")),
    limit: int = 100,
) -> None:
    """Show task events."""
    store = get_kernel_store()
    typer.echo(
        json.dumps(store.list_events(task_id=task_id, limit=limit), ensure_ascii=False, indent=2)
    )


@task_app.command("receipts")
def task_receipts(
    task_id: str | None = typer.Option(
        None,
        help=cli_t("cli.task.receipts.task_id", "Optional task ID filter."),
    ),
    limit: int = 50,
) -> None:
    """Show receipts."""
    store = get_kernel_store()
    payload = [receipt.__dict__ for receipt in store.list_receipts(task_id=task_id, limit=limit)]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@task_app.command("explain")
def task_explain(
    task_id: str = typer.Argument(..., help=cli_t("cli.task.common.task_id", "Task ID.")),
) -> None:
    """Explain why a task executed, under what authority, and what changed."""
    store = get_kernel_store()
    typer.echo(
        json.dumps(SupervisionService(store).build_task_case(task_id), ensure_ascii=False, indent=2)
    )


@task_app.command("case")
def task_case(
    task_id: str = typer.Argument(..., help=cli_t("cli.task.common.task_id", "Task ID.")),
) -> None:
    """Show unified operator case view for one task."""
    store = get_kernel_store()
    typer.echo(
        json.dumps(SupervisionService(store).build_task_case(task_id), ensure_ascii=False, indent=2)
    )


@task_app.command("proof")
def task_proof(
    task_id: str = typer.Argument(..., help=cli_t("cli.task.common.task_id", "Task ID.")),
) -> None:
    """Show proof summary for one task."""
    store = get_kernel_store()
    summary = ProofService(store).build_proof_summary(task_id)
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@task_app.command("proof-export")
def task_proof_export(
    task_id: str = typer.Argument(..., help=cli_t("cli.task.common.task_id", "Task ID.")),
    output: Path | None = typer.Option(
        None,
        help=cli_t(
            "cli.task.proof_export.output",
            "Optional path to write the exported proof bundle.",
        ),
    ),
) -> None:
    """Export one task's proof bundle."""
    store = get_kernel_store()
    bundle = ProofService(store).export_task_proof(task_id)
    payload = json.dumps(bundle, ensure_ascii=False, indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    typer.echo(payload)


@task_app.command("claim-status")
def task_claims(
    task_id: str | None = typer.Argument(
        None,
        help=cli_t("cli.task.common.task_id", "Optional task ID."),
    ),
) -> None:
    """Show repository claim gate status, optionally with task-level proof readiness."""
    store = get_kernel_store()
    if not task_id:
        typer.echo(json.dumps(repository_claim_status(), ensure_ascii=False, indent=2))
        return
    proof = ProofService(store).build_proof_summary(task_id)
    typer.echo(
        json.dumps(
            task_claim_status(store, task_id, proof_summary=proof),
            ensure_ascii=False,
            indent=2,
        )
    )


@task_app.command("rollback")
def task_rollback(
    receipt_id: str = typer.Argument(
        ..., help=cli_t("cli.task.rollback.receipt_id", "Receipt ID.")
    ),
) -> None:
    """Execute a supported rollback for one receipt."""
    store = get_kernel_store()
    payload = RollbackService(store).execute(receipt_id)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@task_app.command("projections-rebuild")
def task_projections_rebuild(
    task_id: str | None = typer.Argument(
        None,
        help=cli_t("cli.task.projections.task_id", "Optional task ID."),
    ),
    all_tasks: bool = typer.Option(
        False,
        "--all",
        help=cli_t("cli.task.projections.all", "Rebuild all task projections."),
    ),
) -> None:
    """Rebuild operator projection cache."""
    store = get_kernel_store()
    service = ProjectionService(store)
    payload = service.rebuild_all() if all_tasks or not task_id else service.rebuild_task(task_id)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _task_resolution(action: str, approval_id: str, reason: str = "") -> None:
    settings = get_settings()
    ensure_workspace(settings)
    from ._commands_core import build_runner  # lazy to avoid circular import

    runner, pm = build_runner(settings)
    try:
        store = get_kernel_store()
        approval = store.get_approval(approval_id)
        if approval is None:
            typer.echo(
                t(
                    "cli.task.approval.not_found",
                    "Approval not found: {approval_id}",
                    approval_id=approval_id,
                )
            )
            raise typer.Exit(1)
        task = store.get_task(approval.task_id)
        conversation_id = task.conversation_id if task is not None else "cli"
        result = runner._resolve_approval(  # type: ignore[attr-defined]
            conversation_id,
            action=action,
            approval_id=approval_id,
            reason=reason,
        )
        typer.echo(result.text)
    finally:
        stop_runner_background_services(runner)
        pm.stop_mcp_servers()


@task_app.command("approve")
def task_approve(
    approval_id: str = typer.Argument(
        ...,
        help=cli_t("cli.task.common.approval_id", "Approval ID."),
    ),
) -> None:
    """Approve once and resume a blocked task."""
    _task_resolution("approve_once", approval_id)


@task_app.command("approve-mutable-workspace")
def task_approve_mutable_workspace(
    approval_id: str = typer.Argument(
        ...,
        help=cli_t("cli.task.common.approval_id", "Approval ID."),
    ),
) -> None:
    """Approve a mutable workspace lease for the current blocked attempt."""
    _task_resolution("approve_mutable_workspace", approval_id)


@task_app.command("deny")
def task_deny(
    approval_id: str = typer.Argument(
        ...,
        help=cli_t("cli.task.common.approval_id", "Approval ID."),
    ),
    reason: str = typer.Option(
        "",
        help=cli_t("cli.task.deny.reason", "Optional deny reason."),
    ),
) -> None:
    """Deny a blocked task."""
    _task_resolution("deny", approval_id, reason=reason)


@task_app.command("resume")
def task_resume(
    approval_id: str = typer.Argument(
        ...,
        help=cli_t("cli.task.resume.approval_id", "Approval ID to resume."),
    ),
) -> None:
    """Resume a blocked task by approving its latest pending approval."""
    _task_resolution("approve_once", approval_id)


def _task_capability_list(
    limit: int = typer.Option(
        50,
        help=cli_t("cli.task.capability.limit", "Maximum number of grants to show."),
    ),
) -> None:
    """Show active and recent capability grants."""
    store = get_kernel_store()
    payload = [grant.__dict__ for grant in store.list_capability_grants(limit=limit)]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _task_capability_revoke(
    grant_id: str = typer.Argument(
        ..., help=cli_t("cli.task.capability.grant_id", "Capability grant ID.")
    ),
) -> None:
    """Revoke a capability grant."""
    store = get_kernel_store()
    grant = store.get_capability_grant(grant_id)
    if grant is None:
        typer.echo(
            t(
                "cli.task.capability.not_found",
                "Capability grant not found: {grant_id}",
                grant_id=grant_id,
            )
        )
        raise typer.Exit(1)
    store.update_capability_grant(
        grant_id,
        status="revoked",
        revoked_at=time.time(),
    )
    typer.echo(
        t(
            "cli.task.capability.revoked",
            "Revoked capability grant '{grant_id}'.",
            grant_id=grant_id,
        )
    )


@task_capability_app.command("list")
def task_capability_list(
    limit: int = typer.Option(
        50,
        help=cli_t("cli.task.capability.limit", "Maximum number of grants to show."),
    ),
) -> None:
    """Show active and recent capability grants."""
    _task_capability_list(limit=limit)


@task_capability_app.command("revoke")
def task_capability_revoke(
    grant_id: str = typer.Argument(
        ..., help=cli_t("cli.task.capability.grant_id", "Capability grant ID.")
    ),
) -> None:
    """Revoke a capability grant."""
    _task_capability_revoke(grant_id)
