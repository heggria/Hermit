from __future__ import annotations

import pytest

from hermit.kernel.policy.guards.rules_filesystem import evaluate_filesystem_rules
from hermit.kernel.policy.models.models import ActionRequest


def _make_request(
    action_class: str = "write_local",
    tool_name: str = "write_file",
    target_paths: list[str] | None = None,
    sensitive_paths: list[str] | None = None,
    kernel_paths: list[str] | None = None,
    outside_workspace: bool = False,
    supports_preview: bool = False,
    risk_hint: str = "high",
) -> ActionRequest:
    derived: dict = {}
    if target_paths is not None:
        derived["target_paths"] = target_paths
    if sensitive_paths is not None:
        derived["sensitive_paths"] = sensitive_paths
    if kernel_paths is not None:
        derived["kernel_paths"] = kernel_paths
    if outside_workspace:
        derived["outside_workspace"] = True

    return ActionRequest(
        request_id="test-req-1",
        action_class=action_class,
        tool_name=tool_name,
        derived=derived,
        supports_preview=supports_preview,
        risk_hint=risk_hint,
    )


# ---------------------------------------------------------------------------
# Non-filesystem actions return None
# ---------------------------------------------------------------------------


class TestNonFilesystemActions:
    @pytest.mark.parametrize(
        "action_class",
        ["bash", "read_local", "unknown", "network", ""],
    )
    def test_returns_none_for_non_filesystem_actions(self, action_class: str) -> None:
        request = _make_request(action_class=action_class)
        assert evaluate_filesystem_rules(request) is None


# ---------------------------------------------------------------------------
# Protected paths: sensitive + outside workspace -> hard deny
# ---------------------------------------------------------------------------


class TestProtectedPaths:
    def test_sensitive_and_outside_workspace_returns_deny(self) -> None:
        request = _make_request(
            sensitive_paths=["/etc/passwd"],
            outside_workspace=True,
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "deny"
        assert outcomes[0].risk_level == "critical"
        assert outcomes[0].normalized_constraints["denied_paths"] == ["/etc/passwd"]
        assert outcomes[0].reasons[0].code == "protected_path"
        assert outcomes[0].obligations.require_receipt is False

    def test_protected_path_with_patch_file_action(self) -> None:
        request = _make_request(
            action_class="patch_file",
            sensitive_paths=["/root/.ssh/id_rsa"],
            outside_workspace=True,
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "deny"

    def test_protected_path_multiple_sensitive_paths(self) -> None:
        paths = ["/etc/shadow", "/etc/passwd"]
        request = _make_request(
            sensitive_paths=paths,
            outside_workspace=True,
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert outcomes[0].normalized_constraints["denied_paths"] == paths


# ---------------------------------------------------------------------------
# Sensitive paths (inside workspace): require approval
# ---------------------------------------------------------------------------


class TestSensitivePaths:
    def test_sensitive_path_inside_workspace_requires_approval(self) -> None:
        request = _make_request(
            sensitive_paths=[".env"],
            tool_name="write_file",
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "approval_required"
        assert outcomes[0].risk_level == "critical"
        assert outcomes[0].obligations.require_receipt is True
        assert outcomes[0].obligations.require_preview is True
        assert outcomes[0].obligations.require_approval is True
        assert outcomes[0].obligations.approval_risk_level == "critical"
        assert outcomes[0].reasons[0].code == "sensitive_path"

    def test_sensitive_path_approval_packet_includes_tool_name(self) -> None:
        request = _make_request(
            sensitive_paths=[".env"],
            tool_name="patch_file",
            action_class="patch_file",
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        packet = outcomes[0].approval_packet
        assert packet is not None
        assert "patch_file" in packet["title"]
        assert packet["risk_level"] == "critical"


# ---------------------------------------------------------------------------
# Kernel paths: self-modification guard
# ---------------------------------------------------------------------------


class TestKernelPaths:
    def test_kernel_paths_require_elevated_approval(self) -> None:
        request = _make_request(
            kernel_paths=["src/hermit/kernel/policy/guards/rules.py"],
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "approval_required"
        assert outcomes[0].risk_level == "critical"
        assert outcomes[0].obligations.require_evidence is True
        assert outcomes[0].obligations.require_approval is True
        assert outcomes[0].reasons[0].code == "kernel_self_modification"

    def test_kernel_paths_approval_packet_lists_filenames(self) -> None:
        request = _make_request(
            kernel_paths=[
                "src/hermit/kernel/task/models/records.py",
                "src/hermit/kernel/ledger/journal/store.py",
            ],
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        packet = outcomes[0].approval_packet
        assert packet is not None
        assert "records.py" in packet["summary"]
        assert "store.py" in packet["summary"]

    def test_kernel_paths_with_sensitive_paths_returns_both(self) -> None:
        """When both sensitive and kernel paths are present (inside workspace),
        both outcomes should be emitted before kernel guard short-circuits."""
        request = _make_request(
            sensitive_paths=[".env"],
            kernel_paths=["src/hermit/kernel/policy/guards/rules.py"],
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 2
        codes = {o.reasons[0].code for o in outcomes}
        assert "sensitive_path" in codes
        assert "kernel_self_modification" in codes

    def test_kernel_paths_short_circuits_before_workspace_mutation(self) -> None:
        """Kernel paths should return early, skipping the workspace mutation rule."""
        request = _make_request(
            kernel_paths=["src/hermit/kernel/execution/executor/executor.py"],
            target_paths=["some/file.py"],
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        codes = [o.reasons[0].code for o in outcomes]
        assert "kernel_self_modification" in codes
        assert "workspace_mutation" not in codes


# ---------------------------------------------------------------------------
# Outside workspace (non-sensitive): require approval
# ---------------------------------------------------------------------------


class TestOutsideWorkspace:
    def test_outside_workspace_requires_approval(self) -> None:
        request = _make_request(
            target_paths=["/tmp/output.txt"],
            outside_workspace=True,
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "approval_required"
        assert outcomes[0].reasons[0].code == "outside_workspace_write"
        assert outcomes[0].obligations.require_receipt is True
        assert outcomes[0].obligations.require_approval is True

    def test_outside_workspace_uses_risk_hint(self) -> None:
        request = _make_request(
            target_paths=["/tmp/output.txt"],
            outside_workspace=True,
            risk_hint="medium",
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert outcomes[0].risk_level == "medium"
        assert outcomes[0].obligations.approval_risk_level == "medium"

    def test_outside_workspace_defaults_risk_to_high(self) -> None:
        request = _make_request(
            target_paths=["/tmp/output.txt"],
            outside_workspace=True,
            risk_hint="",
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert outcomes[0].risk_level == "high"

    def test_outside_workspace_approval_packet_includes_tool_name(self) -> None:
        request = _make_request(
            target_paths=["/tmp/out.txt"],
            outside_workspace=True,
            tool_name="write_file",
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        packet = outcomes[0].approval_packet
        assert packet is not None
        assert "write_file" in packet["title"]

    def test_outside_workspace_uses_supports_preview(self) -> None:
        request = _make_request(
            target_paths=["/tmp/out.txt"],
            outside_workspace=True,
            supports_preview=True,
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert outcomes[0].obligations.require_preview is True

    def test_outside_workspace_short_circuits_before_workspace_mutation(self) -> None:
        request = _make_request(
            target_paths=["/tmp/out.txt"],
            outside_workspace=True,
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        codes = [o.reasons[0].code for o in outcomes]
        assert "outside_workspace_write" in codes
        assert "workspace_mutation" not in codes


# ---------------------------------------------------------------------------
# Non-sensitive workspace mutation
# ---------------------------------------------------------------------------


class TestWorkspaceMutation:
    def test_workspace_write_with_preview_support(self) -> None:
        request = _make_request(
            target_paths=["src/app.py"],
            supports_preview=True,
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "preview_required"
        assert outcomes[0].obligations.require_preview is True
        assert outcomes[0].obligations.require_approval is False
        assert outcomes[0].approval_packet is None
        assert outcomes[0].reasons[0].code == "workspace_mutation"

    def test_workspace_write_without_preview_requires_approval(self) -> None:
        request = _make_request(
            target_paths=["src/app.py"],
            supports_preview=False,
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "approval_required"
        assert outcomes[0].obligations.require_approval is True
        assert outcomes[0].obligations.require_preview is False
        assert outcomes[0].approval_packet is not None

    def test_workspace_write_uses_risk_hint(self) -> None:
        request = _make_request(
            target_paths=["src/app.py"],
            risk_hint="low",
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert outcomes[0].risk_level == "low"

    def test_workspace_write_defaults_risk_to_high(self) -> None:
        request = _make_request(
            target_paths=["src/app.py"],
            risk_hint="",
        )
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert outcomes[0].risk_level == "high"

    def test_workspace_write_constraints_include_target_paths(self) -> None:
        paths = ["src/a.py", "src/b.py"]
        request = _make_request(target_paths=paths)
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert outcomes[0].normalized_constraints["allowed_paths"] == paths

    def test_empty_derived_still_produces_workspace_mutation(self) -> None:
        """No target_paths, no sensitive_paths, no kernel_paths, no outside_workspace
        should still produce a workspace_mutation outcome."""
        request = _make_request()
        outcomes = evaluate_filesystem_rules(request)

        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].reasons[0].code == "workspace_mutation"
