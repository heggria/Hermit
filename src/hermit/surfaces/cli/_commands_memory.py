from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import typer

from hermit.kernel.context.memory.governance import MemoryGovernanceService
from hermit.kernel.context.memory.knowledge import MemoryRecordService
from hermit.runtime.assembly.config import Settings, get_settings

from ._helpers import (
    ensure_workspace,
    format_epoch,
    get_kernel_store,
)
from .main import cli_t, memory_app, t


def _memory_payload_from_record(record: Any, *, settings: Settings) -> dict[str, Any]:
    governance = MemoryGovernanceService()
    workspace_root = (
        record.scope_ref
        if getattr(record, "scope_kind", "") == "workspace" and getattr(record, "scope_ref", "")
        else str(settings.base_dir)
    )
    inspection = governance.inspect_claim(
        category=record.category,
        claim_text=record.claim_text,
        conversation_id=record.conversation_id,
        workspace_root=workspace_root,
        promotion_reason=record.promotion_reason,
    )
    assertion = dict(getattr(record, "structured_assertion", {}) or {})
    return {
        "memory_id": record.memory_id,
        "task_id": record.task_id,
        "conversation_id": record.conversation_id,
        "claim_text": record.claim_text,
        "stored_category": record.category,
        "status": record.status,
        "scope_kind": record.scope_kind,
        "scope_ref": record.scope_ref,
        "retention_class": record.retention_class,
        "promotion_reason": record.promotion_reason,
        "confidence": record.confidence,
        "trust_tier": record.trust_tier,
        "evidence_refs": list(record.evidence_refs),
        "supersedes": list(record.supersedes),
        "supersedes_memory_ids": list(record.supersedes_memory_ids),
        "superseded_by_memory_id": record.superseded_by_memory_id,
        "source_belief_ref": record.source_belief_ref,
        "invalidation_reason": record.invalidation_reason,
        "invalidated_at": record.invalidated_at,
        "expires_at": record.expires_at,
        "structured_assertion": assertion,
        "inspection": inspection,
    }


def _render_memory_payload(payload: dict[str, Any]) -> str:
    inspection = dict(payload.get("inspection", {}) or {})
    lines = [
        t(
            "cli.memory.inspect.label.memory_id",
            "Memory ID: {value}",
            value=payload.get("memory_id", "-"),
        ),
        t("cli.memory.inspect.label.claim", "Claim: {value}", value=payload.get("claim_text", "")),
        t(
            "cli.memory.inspect.label.stored_category",
            "Stored Category: {value}",
            value=payload.get("stored_category", payload.get("category", "-")),
        ),
        t(
            "cli.memory.inspect.label.resolved_category",
            "Resolved Category: {value}",
            value=inspection.get("category", "-"),
        ),
        t(
            "cli.memory.inspect.label.retention",
            "Retention: {value}",
            value=inspection.get("retention_class", payload.get("retention_class", "-")),
        ),
        t(
            "cli.memory.inspect.label.status",
            "Status: {value}",
            value=payload.get("status", inspection.get("status", "-")),
        ),
        t(
            "cli.memory.inspect.label.scope",
            "Scope: {kind} {ref}",
            kind=inspection.get("scope_kind", payload.get("scope_kind", "-")),
            ref=inspection.get("scope_ref", payload.get("scope_ref", "-")),
        ),
        t(
            "cli.memory.inspect.label.subject",
            "Subject: {value}",
            value=inspection.get("subject_key", "-") or "-",
        ),
        t(
            "cli.memory.inspect.label.topic",
            "Topic: {value}",
            value=inspection.get("topic_key", "-") or "-",
        ),
        t(
            "cli.memory.inspect.label.promotion_reason",
            "Promotion Reason: {value}",
            value=payload.get("promotion_reason", "-"),
        ),
        t(
            "cli.memory.inspect.label.confidence",
            "Confidence: {value}",
            value=payload.get("confidence", "-"),
        ),
        t(
            "cli.memory.inspect.label.trust_tier",
            "Trust Tier: {value}",
            value=payload.get("trust_tier", "-"),
        ),
        t(
            "cli.memory.inspect.label.expires_at",
            "Expires At: {value}",
            value=format_epoch(payload.get("expires_at")),
        ),
        t(
            "cli.memory.inspect.label.invalidated_at",
            "Invalidated At: {value}",
            value=format_epoch(payload.get("invalidated_at")),
        ),
        t(
            "cli.memory.inspect.label.superseded_by",
            "Superseded By: {value}",
            value=payload.get("superseded_by_memory_id") or "-",
        ),
    ]
    source_belief_ref = payload.get("source_belief_ref")
    if source_belief_ref:
        lines.append(
            t(
                "cli.memory.inspect.label.source_belief",
                "Source Belief: {value}",
                value=source_belief_ref,
            )
        )
    if payload.get("supersedes"):
        lines.append(t("cli.memory.inspect.label.supersedes", "Supersedes:"))
        lines.extend([f"  - {item}" for item in payload["supersedes"]])
    explanations = list(inspection.get("explanation", []) or [])
    if explanations:
        lines.append(t("cli.memory.inspect.label.governance", "Governance:"))
        lines.extend([f"  - {item}" for item in explanations])
    structured_assertion = cast(dict[str, Any], (inspection.get("structured_assertion", {}) or {}))
    matched_signals = cast(
        dict[str, list[str]], structured_assertion.get("matched_signals", {}) or {}
    )
    if matched_signals:
        lines.append(t("cli.memory.inspect.label.matched_signals", "Matched Signals:"))
        for name, hits in sorted(matched_signals.items()):
            lines.append(f"  - {name}: {', '.join(hits)}")
    return "\n".join(lines)


def _memory_list_payload(
    records: list[Any],
    *,
    settings: Settings,
) -> list[dict[str, Any]]:
    governance = MemoryGovernanceService()
    payload: list[dict[str, Any]] = []
    for record in records:
        payload.append(
            {
                "memory_id": record.memory_id,
                "status": record.status,
                "category": record.category,
                "retention_class": record.retention_class,
                "scope_kind": record.scope_kind,
                "scope_ref": record.scope_ref,
                "subject_key": governance.subject_key_for_memory(record),
                "topic_key": governance.topic_key_for_memory(record),
                "claim_text": record.claim_text,
                "updated_at": record.updated_at,
                "expires_at": record.expires_at,
                "superseded_by_memory_id": record.superseded_by_memory_id,
            }
        )
    return payload


@memory_app.command("inspect")
def memory_inspect(
    memory_id: str | None = typer.Argument(
        None,
        help=cli_t("cli.memory.inspect.memory_id", "Optional memory ID."),
    ),
    claim_text: str | None = typer.Option(
        None,
        "--claim-text",
        help=cli_t(
            "cli.memory.inspect.claim_text",
            "Inspect a raw claim without reading a stored memory record.",
        ),
    ),
    category: str = typer.Option(
        "other",
        "--category",
        help=cli_t("cli.memory.inspect.category", "Category hint used for raw claim inspection."),
    ),
    conversation_id: str | None = typer.Option(
        None,
        "--conversation-id",
        help=cli_t("cli.memory.inspect.conversation_id", "Conversation scope hint."),
    ),
    workspace_root: Path | None = typer.Option(
        None,
        "--workspace-root",
        help=cli_t("cli.memory.inspect.workspace_root", "Workspace scope hint."),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=cli_t("cli.memory.inspect.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """Inspect a stored memory record or preview governance classification for a raw claim."""
    settings = get_settings()
    ensure_workspace(settings)
    governance = MemoryGovernanceService()

    if not memory_id and not claim_text:
        typer.echo(
            t(
                "cli.memory.inspect.require_target",
                "Provide either a memory_id argument or --claim-text.",
            )
        )
        raise typer.Exit(1)

    if memory_id:
        store = get_kernel_store()
        record = store.get_memory_record(memory_id)
        if record is None:
            typer.echo(
                t(
                    "cli.memory.inspect.not_found",
                    "Memory not found: {memory_id}",
                    memory_id=memory_id,
                )
            )
            raise typer.Exit(1)
        payload = _memory_payload_from_record(record, settings=settings)
    else:
        resolved_workspace_root = (
            str(workspace_root.resolve()) if workspace_root else str(settings.base_dir)
        )
        inspection = governance.inspect_claim(
            category=category,
            claim_text=str(claim_text or ""),
            conversation_id=conversation_id,
            workspace_root=resolved_workspace_root,
        )
        payload: dict[str, Any] = {
            "memory_id": None,
            "claim_text": claim_text,
            "stored_category": category,
            "status": "preview",
            "scope_kind": inspection["scope_kind"],
            "scope_ref": inspection["scope_ref"],
            "retention_class": inspection["retention_class"],
            "promotion_reason": "belief_promotion",
            "confidence": None,
            "trust_tier": None,
            "supersedes": [],
            "superseded_by_memory_id": None,
            "source_belief_ref": None,
            "invalidated_at": None,
            "expires_at": inspection["expires_at"],
            "structured_assertion": inspection["structured_assertion"],
            "inspection": inspection,
        }

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(_render_memory_payload(payload))


@memory_app.command("list")
def memory_list(
    status: str | None = typer.Option(
        "active",
        "--status",
        help=cli_t("cli.memory.list.status", "Optional status filter."),
    ),
    conversation_id: str | None = typer.Option(
        None,
        "--conversation-id",
        help=cli_t("cli.memory.list.conversation_id", "Optional conversation filter."),
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        help=cli_t("cli.memory.list.limit", "Maximum number of memory records to show."),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=cli_t("cli.memory.list.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """List recent memory records with governance-facing metadata."""
    settings = get_settings()
    ensure_workspace(settings)
    store = get_kernel_store()
    records = store.list_memory_records(status=status, conversation_id=conversation_id, limit=limit)
    payload = _memory_list_payload(records, settings=settings)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not payload:
        typer.echo(t("cli.memory.list.empty", "No memory records found."))
        return
    for item in payload:
        typer.echo(
            f"[{item['memory_id']}] {item['status']} {item['category']} "
            f"{item['retention_class']} {item['subject_key'] or '-'} {item['claim_text']}"
        )


@memory_app.command("status")
def memory_status(
    limit: int = typer.Option(
        1000,
        "--limit",
        help=cli_t("cli.memory.status.limit", "Maximum number of memory records to scan."),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=cli_t("cli.memory.status.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """Show aggregate memory health and governance counts."""
    settings = get_settings()
    ensure_workspace(settings)
    governance = MemoryGovernanceService()
    store = get_kernel_store()
    records = store.list_memory_records(limit=limit)
    by_status: dict[str, int] = {}
    by_retention: dict[str, int] = {}
    by_category: dict[str, int] = {}
    expired = 0
    superseded_links = 0
    for record in records:
        by_status[record.status] = by_status.get(record.status, 0) + 1
        by_retention[record.retention_class] = by_retention.get(record.retention_class, 0) + 1
        by_category[record.category] = by_category.get(record.category, 0) + 1
        if governance.is_expired(record):
            expired += 1
        if record.superseded_by_memory_id:
            superseded_links += 1
    payload = {
        "total_records": len(records),
        "active_records": sum(
            1
            for record in records
            if record.status == "active" and not governance.is_expired(record)
        ),
        "expired_records": expired,
        "superseded_links": superseded_links,
        "by_status": by_status,
        "by_retention_class": by_retention,
        "by_category": by_category,
        "memory_file": str(settings.memory_file),
        "kernel_db_path": str(settings.kernel_db_path),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(
        t(
            "cli.memory.status.label.total_records",
            "Total Records: {value}",
            value=payload["total_records"],
        )
    )
    typer.echo(
        t(
            "cli.memory.status.label.active_records",
            "Active Records: {value}",
            value=payload["active_records"],
        )
    )
    typer.echo(
        t(
            "cli.memory.status.label.expired_records",
            "Expired Records: {value}",
            value=payload["expired_records"],
        )
    )
    typer.echo(
        t(
            "cli.memory.status.label.superseded_links",
            "Superseded Links: {value}",
            value=payload["superseded_links"],
        )
    )
    typer.echo(t("cli.memory.status.label.by_status", "By Status:"))
    for key, value in sorted(by_status.items()):
        typer.echo(f"  - {key}: {value}")
    typer.echo(t("cli.memory.status.label.by_retention", "By Retention:"))
    for key, value in sorted(by_retention.items()):
        typer.echo(f"  - {key}: {value}")
    typer.echo(t("cli.memory.status.label.by_category", "By Category:"))
    for key, value in sorted(by_category.items()):
        typer.echo(f"  - {key}: {value}")


@memory_app.command("rebuild")
def memory_rebuild(
    json_output: bool = typer.Option(
        False,
        "--json",
        help=cli_t("cli.memory.rebuild.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """Reconcile active records and export the mirror file from kernel state."""
    settings = get_settings()
    ensure_workspace(settings)
    store = get_kernel_store()
    service = MemoryRecordService(store, mirror_path=settings.memory_file)
    before_active = len(store.list_memory_records(status="active", limit=5000))
    result = service.reconcile_active_records()
    export_path = service.export_mirror(settings.memory_file)
    after_active = len(store.list_memory_records(status="active", limit=5000))
    payload = {
        "before_active": before_active,
        "after_active": after_active,
        **result,
        "mirror_path": str(settings.memory_file),
        "export_path": str(export_path) if export_path is not None else None,
        "render_mode": "export_only",
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(
        t(
            "cli.memory.rebuild.done",
            "Rebuilt memory mirror. active {before} -> {after}; superseded={superseded} duplicate={duplicate}",
            before=before_active,
            after=after_active,
            superseded=result["superseded_count"],
            duplicate=result["duplicate_count"],
        )
    )


@memory_app.command("export")
def memory_export(
    output: Path | None = typer.Option(
        None,
        "--output",
        help=cli_t("cli.memory.export.output", "Optional output path for the exported mirror."),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=cli_t("cli.memory.export.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """Export the current kernel-backed memory mirror without mutating records."""
    settings = get_settings()
    ensure_workspace(settings)
    store = get_kernel_store()
    target = output or settings.memory_file
    service = MemoryRecordService(store, mirror_path=target)
    try:
        export_path = service.export_mirror(target)
    except OSError as exc:
        typer.echo(t("cli.memory.export.error", "Export failed: {error}", error=str(exc)))
        raise typer.Exit(1)
    active_records = len(store.list_memory_records(status="active", limit=5000))
    payload = {
        "active_records": active_records,
        "export_path": str(export_path) if export_path is not None else None,
        "render_mode": "export_only",
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(
        t(
            "cli.memory.export.done",
            "Exported memory mirror from kernel state to {path} ({count} active records).",
            path=payload["export_path"] or "-",
            count=active_records,
        )
    )
