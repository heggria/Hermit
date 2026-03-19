from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.runtime.control.lifecycle.budgets import get_runtime_budget

log = structlog.get_logger()


@dataclass
class ReconcileOutcome:
    result_code: str
    summary: str
    observed_refs: list[str]


class ReconcileService:
    def __init__(
        self,
        git_worktree: GitWorktreeInspector | None = None,
        store: KernelStore | None = None,
    ) -> None:
        self.git_worktree = git_worktree or GitWorktreeInspector()
        self.store = store

    def reconcile(
        self,
        *,
        action_type: str,
        tool_input: dict[str, Any],
        workspace_root: str,
        observables: dict[str, Any] | None = None,
        witness: dict[str, Any] | None = None,
    ) -> ReconcileOutcome:
        observed = dict(observables or {})
        witness_payload = dict(witness or {})
        if action_type == "execute_command_readonly":
            return ReconcileOutcome(
                result_code="reconciled_applied",
                summary="Read-only command executed; no side effects expected.",
                observed_refs=[],
            )
        if action_type in {"write_local", "patch_file"}:
            outcome = self._reconcile_local_write(
                tool_input=tool_input, workspace_root=workspace_root
            )
            if outcome is not None:
                return outcome
        if action_type in {"execute_command", "vcs_mutation"}:
            outcome = self._reconcile_command_or_vcs(
                action_type=action_type,
                workspace_root=workspace_root,
                observables=observed,
                witness=witness_payload,
            )
            if outcome is not None:
                return outcome
        if action_type in {"network_write", "credentialed_api_call"}:
            outcome = self._reconcile_remote_write(tool_input=tool_input)
            if outcome is not None:
                return outcome
        if action_type in {"publication", "external_mutation"}:
            outcome = self._reconcile_remote_write(tool_input=tool_input)
            if outcome is not None:
                return outcome
        if action_type in {
            "store_observation",
            "scheduler_mutation",
            "memory_write",
            "rollback",
            "approval_resolution",
        }:
            outcome = self._reconcile_store_observation(
                action_type=action_type, tool_input=tool_input, observables=observed
            )
            if outcome is not None:
                return outcome
        return ReconcileOutcome(
            result_code="still_unknown",
            summary=f"Unable to reconcile outcome for {action_type}.",
            observed_refs=[],
        )

    def _reconcile_local_write(
        self,
        *,
        tool_input: dict[str, Any],
        workspace_root: str,
    ) -> ReconcileOutcome | None:
        path = str(tool_input.get("path", "")).strip()
        content = str(tool_input.get("content", ""))
        if not path or not workspace_root:
            return None
        candidate = (Path(workspace_root) / path).resolve()
        if not candidate.exists():
            return ReconcileOutcome(
                result_code="reconciled_not_applied",
                summary=f"Observed local write target is missing for {path}.",
                observed_refs=[str(candidate)],
            )
        try:
            actual = candidate.read_text(encoding="utf-8")
        except OSError:
            actual = None
        if actual == content:
            return ReconcileOutcome(
                result_code="reconciled_applied",
                summary=f"Reconciled local write for {path}.",
                observed_refs=[str(candidate)],
            )
        return ReconcileOutcome(
            result_code="reconciled_not_applied",
            summary=f"Observed local state does not match requested write for {path}.",
            observed_refs=[str(candidate)],
        )

    def _reconcile_command_or_vcs(
        self,
        *,
        action_type: str,
        workspace_root: str,
        observables: dict[str, Any],
        witness: dict[str, Any],
    ) -> ReconcileOutcome | None:
        changed_paths = self._changed_paths(
            target_paths=[str(path) for path in observables.get("target_paths", [])],
            witness_files=list(witness.get("files", [])),
        )
        if changed_paths:
            return ReconcileOutcome(
                result_code="reconciled_applied",
                summary=f"Observed command side effects on {len(changed_paths)} path(s).",
                observed_refs=changed_paths,
            )

        git_changed = self._git_changed(workspace_root=workspace_root, witness=witness.get("git"))
        if git_changed is True:
            return ReconcileOutcome(
                result_code="reconciled_applied",
                summary=f"Observed repository state change after {action_type}.",
                observed_refs=[str(Path(workspace_root).resolve())] if workspace_root else [],
            )
        if git_changed is False and (
            action_type == "vcs_mutation" or str(observables.get("vcs_operation", "")).strip()
        ):
            return ReconcileOutcome(
                result_code="reconciled_not_applied",
                summary=f"Observed repository state did not change after {action_type}.",
                observed_refs=[str(Path(workspace_root).resolve())] if workspace_root else [],
            )

        if observables.get("command_preview") and observables.get("target_paths"):
            return ReconcileOutcome(
                result_code="reconciled_not_applied",
                summary="Observed command target paths remain unchanged after dispatch.",
                observed_refs=[str(path) for path in observables.get("target_paths", [])],
            )
        return None

    def _reconcile_remote_write(self, *, tool_input: dict[str, Any]) -> ReconcileOutcome | None:
        probe_url = ""
        for key in ("url", "resource_url", "webhook_url"):
            value = str(tool_input.get(key, "")).strip()
            if value.startswith(("http://", "https://")):
                probe_url = value
                break
        if not probe_url:
            return None
        request = urllib.request.Request(probe_url, method="HEAD")
        try:
            with urllib.request.urlopen(
                request, timeout=get_runtime_budget().provider_read_timeout
            ) as response:
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ReconcileOutcome(
                    result_code="reconciled_not_applied",
                    summary=f"Observed remote resource is not present at {probe_url}.",
                    observed_refs=[probe_url],
                )
            if exc.code in {401, 403, 405}:
                return ReconcileOutcome(
                    result_code="reconciled_observed",
                    summary=f"Remote endpoint at {probe_url} is reachable but requires a stronger verifier.",
                    observed_refs=[probe_url],
                )
            return None
        except OSError:
            return None
        if 200 <= status < 400:
            return ReconcileOutcome(
                result_code="reconciled_observed",
                summary=f"Observed remote endpoint responded for {probe_url}.",
                observed_refs=[probe_url],
            )
        return None

    _STORE_ACTION_ID_KEYS: dict[str, tuple[str, str]] = {
        "scheduler_mutation": ("schedule_id", "get_schedule"),
        "memory_write": ("memory_id", "get_memory_record"),
        "rollback": ("receipt_id", "get_receipt"),
        "approval_resolution": ("approval_id", "get_approval"),
    }

    def _reconcile_store_observation(
        self,
        *,
        action_type: str,
        tool_input: dict[str, Any],
        observables: dict[str, Any],
    ) -> ReconcileOutcome | None:
        if self.store is None:
            return None
        record_id = self._extract_store_record_id(action_type, tool_input, observables)
        if not record_id:
            return None
        record = self._lookup_store_record(action_type, record_id, observables)
        if record is None:
            return ReconcileOutcome(
                result_code="reconciled_not_applied",
                summary=f"Store record {record_id} not found during reconciliation.",
                observed_refs=[record_id],
            )
        return ReconcileOutcome(
            result_code="reconciled_applied",
            summary=f"Store record {record_id} exists and is observable.",
            observed_refs=[record_id],
        )

    def _extract_store_record_id(
        self, action_type: str, tool_input: dict[str, Any], observables: dict[str, Any]
    ) -> str:
        mapping = self._STORE_ACTION_ID_KEYS.get(action_type)
        if mapping is not None:
            id_key, _getter = mapping
            value = str(tool_input.get(id_key, "") or "").strip()
            if value:
                return value
        return str(tool_input.get("record_id", "") or "").strip()

    def _lookup_store_record(
        self, action_type: str, record_id: str, observables: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self.store is None:
            return None
        mapping = self._STORE_ACTION_ID_KEYS.get(action_type)
        if mapping is not None:
            _id_key, getter_name = mapping
            if hasattr(self.store, getter_name):
                result = getattr(self.store, getter_name)(record_id)
                if result is not None:
                    return {"record_id": record_id, "action_type": action_type}
                return None
        entity_type = str(observables.get("entity_type", "") or "").strip()
        getter_name = f"get_{entity_type}" if entity_type else ""
        if getter_name and hasattr(self.store, getter_name):
            result = getattr(self.store, getter_name)(record_id)
            if result is not None:
                return {"record_id": record_id, "entity_type": entity_type}
        return None

    def _changed_paths(
        self,
        *,
        target_paths: list[str],
        witness_files: list[dict[str, Any]],
    ) -> list[str]:
        witness_map = {
            str(entry.get("path", "")): dict(entry) for entry in witness_files if entry.get("path")
        }
        changed: list[str] = []
        for raw_path in target_paths:
            path = Path(raw_path)
            current = self._path_state(path)
            previous = witness_map.get(str(path), {"exists": False})
            if current != previous:
                changed.append(str(path))
        return changed

    def _path_state(self, path: Path) -> dict[str, Any]:
        result: dict[str, Any] = {"path": str(path)}
        try:
            exists = path.exists()
        except OSError as exc:
            return {"path": str(path), "exists": False, "error": str(exc)}
        result["exists"] = exists
        if not exists:
            return result
        try:
            stat = path.stat()
            result["mtime_ns"] = stat.st_mtime_ns
            result["size"] = stat.st_size
            if path.is_file():
                result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                result["kind"] = "directory"
        except OSError as exc:
            result["error"] = str(exc)
        return result

    def _git_changed(self, *, workspace_root: str, witness: Any) -> bool | None:
        if not workspace_root or not isinstance(witness, dict):
            return None
        witness_d: dict[str, Any] = cast(dict[str, Any], witness)
        root = Path(workspace_root).resolve()
        current = self._git_state(root)
        if current is None:
            return None
        previous_head = str(witness_d.get("head", "") or "")
        previous_dirty = bool(witness_d.get("dirty", False))
        return current["head"] != previous_head or current["dirty"] != previous_dirty

    def _git_state(self, workspace_root: Path) -> dict[str, Any] | None:
        return self.git_worktree.snapshot(workspace_root).to_state()
