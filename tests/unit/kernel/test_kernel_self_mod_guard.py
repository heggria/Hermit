"""Tests for the kernel self-modification policy guard.

Verifies that writes targeting src/hermit/kernel/ paths are escalated to
approval_required with critical risk level, while reads remain unaffected.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.policy.evaluators.derivation import _is_kernel_path, derive_request
from hermit.kernel.policy.guards.rules import evaluate_rules
from hermit.kernel.policy.models.models import ActionRequest


def _make_request(
    action_class: str,
    tool_name: str = "write_file",
    path: str = "",
    workspace_root: str = "",
    derived: dict | None = None,
) -> ActionRequest:
    context: dict = {"workspace_root": workspace_root}
    tool_input = {"path": path} if path else {}
    return ActionRequest(
        request_id="test-kernel-guard",
        tool_name=tool_name,
        tool_input=tool_input,
        action_class=action_class,
        risk_hint="high",
        context=context,
        derived=derived or {},
        actor={"kind": "agent", "agent_id": "hermit"},
    )


# ---------------------------------------------------------------------------
# Derivation: _is_kernel_path
# ---------------------------------------------------------------------------


def test_kernel_path_detected_in_derivation(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    kernel_file = str(tmp_path / "src" / "hermit" / "kernel" / "policy" / "guards" / "rules.py")
    assert _is_kernel_path(kernel_file, workspace) is True


def test_non_kernel_path_not_flagged(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    plugin_file = str(tmp_path / "src" / "hermit" / "plugins" / "builtin" / "tools.py")
    assert _is_kernel_path(plugin_file, workspace) is False


def test_kernel_path_without_workspace() -> None:
    """Segment-based fallback detects kernel paths even without workspace_root."""
    assert _is_kernel_path("/some/path/src/hermit/kernel/foo.py", "") is True


def test_kernel_path_without_workspace_non_kernel() -> None:
    assert _is_kernel_path("/some/path/src/hermit/plugins/foo.py", "") is False


def test_kernel_path_detected_from_subdirectory_workspace(tmp_path: Path) -> None:
    """Guard fires when workspace_root is a subdirectory of the real repo root."""
    repo_root = tmp_path / "repo"
    kernel_file = str(repo_root / "src" / "hermit" / "kernel" / "policy" / "guards" / "rules.py")
    subdirectory = str(repo_root / "docs")
    assert _is_kernel_path(kernel_file, subdirectory) is True


def test_kernel_path_oserror_returns_false(monkeypatch: object) -> None:
    import hermit.kernel.policy.evaluators.derivation as mod

    def _exploding_resolve(self: Path, *a: object, **kw: object) -> Path:
        raise OSError("boom")

    monkeypatch.setattr(Path, "resolve", _exploding_resolve)  # type: ignore[arg-type]
    assert mod._is_kernel_path("/x/src/hermit/kernel/foo.py", "/x") is False


def test_derive_request_sets_kernel_paths(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    kernel_file = str(tmp_path / "src" / "hermit" / "kernel" / "task" / "models.py")
    request = _make_request(
        "write_local",
        tool_name="write_file",
        path=kernel_file,
        workspace_root=workspace,
    )
    enriched = derive_request(request)
    assert "kernel_paths" in enriched.derived
    assert len(enriched.derived["kernel_paths"]) == 1


def test_derive_request_no_kernel_paths_for_non_kernel(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    other_file = str(tmp_path / "src" / "hermit" / "runtime" / "control" / "runner.py")
    request = _make_request(
        "write_local",
        tool_name="write_file",
        path=other_file,
        workspace_root=workspace,
    )
    enriched = derive_request(request)
    assert "kernel_paths" not in enriched.derived


# ---------------------------------------------------------------------------
# Guard: kernel self-modification
# ---------------------------------------------------------------------------


def test_kernel_write_requires_approval(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    kernel_file = str(tmp_path / "src" / "hermit" / "kernel" / "policy" / "guards" / "rules.py")
    request = _make_request(
        "write_local",
        tool_name="write_file",
        path=kernel_file,
        workspace_root=workspace,
        derived={"target_paths": [kernel_file], "kernel_paths": [kernel_file]},
    )
    outcomes = evaluate_rules(request)
    assert len(outcomes) >= 1
    assert outcomes[0].verdict == "approval_required"


def test_kernel_patch_requires_approval(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    kernel_file = str(tmp_path / "src" / "hermit" / "kernel" / "ledger" / "store.py")
    request = _make_request(
        "patch_file",
        tool_name="write_file",
        path=kernel_file,
        workspace_root=workspace,
        derived={"target_paths": [kernel_file], "kernel_paths": [kernel_file]},
    )
    outcomes = evaluate_rules(request)
    assert len(outcomes) >= 1
    assert outcomes[0].verdict == "approval_required"


def test_kernel_read_not_affected(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    kernel_file = str(tmp_path / "src" / "hermit" / "kernel" / "policy" / "guards" / "rules.py")
    request = _make_request(
        "read_local",
        tool_name="read_file",
        path=kernel_file,
        workspace_root=workspace,
        derived={"target_paths": [kernel_file], "kernel_paths": [kernel_file]},
    )
    outcomes = evaluate_rules(request)
    assert len(outcomes) >= 1
    assert outcomes[0].verdict == "allow"


def test_kernel_guard_reason_code(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    kernel_file = str(tmp_path / "src" / "hermit" / "kernel" / "execution" / "executor.py")
    request = _make_request(
        "write_local",
        tool_name="write_file",
        path=kernel_file,
        workspace_root=workspace,
        derived={"target_paths": [kernel_file], "kernel_paths": [kernel_file]},
    )
    outcomes = evaluate_rules(request)
    assert outcomes[0].reasons[0].code == "kernel_self_modification"


def test_kernel_guard_risk_level_critical(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    kernel_file = str(tmp_path / "src" / "hermit" / "kernel" / "verification" / "proofs.py")
    request = _make_request(
        "write_local",
        tool_name="write_file",
        path=kernel_file,
        workspace_root=workspace,
        derived={"target_paths": [kernel_file], "kernel_paths": [kernel_file]},
    )
    outcomes = evaluate_rules(request)
    assert outcomes[0].risk_level == "critical"


def test_kernel_guard_obligations(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    kernel_file = str(tmp_path / "src" / "hermit" / "kernel" / "context" / "compiler.py")
    request = _make_request(
        "write_local",
        tool_name="write_file",
        path=kernel_file,
        workspace_root=workspace,
        derived={"target_paths": [kernel_file], "kernel_paths": [kernel_file]},
    )
    outcomes = evaluate_rules(request)
    obligations = outcomes[0].obligations
    assert obligations.require_receipt is True
    assert obligations.require_preview is True
    assert obligations.require_approval is True
    assert obligations.require_evidence is True
    assert obligations.approval_risk_level == "critical"
