"""Tests for kernel/policy/guards/tool_spec_adapter.py — tool spec to ActionRequest conversion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.policy.guards.tool_spec_adapter import (
    build_action_request,
    infer_action_class,
    normalize_scope_hints,
)
from hermit.runtime.capability.registry.tools import ToolSpec


def _make_readonly_tool(
    name: str = "test_tool",
    resource_scope_hint: str | list[str] | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="test",
        input_schema={"type": "object"},
        handler=lambda x: x,
        action_class="read_local",
        readonly=True,
        resource_scope_hint=resource_scope_hint,
        risk_hint=None,
        requires_receipt=False,
        supports_preview=False,
    )


def _make_mutating_tool(
    name: str = "write_tool",
    action_class: str = "write_local",
    resource_scope_hint: str | list[str] | None = None,
    risk_hint: str = "high",
    idempotent: bool = False,
    requires_receipt: bool = True,
    supports_preview: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="test",
        input_schema={"type": "object"},
        handler=lambda x: x,
        action_class=action_class,
        readonly=False,
        resource_scope_hint=resource_scope_hint,
        risk_hint=risk_hint,
        idempotent=idempotent,
        requires_receipt=requires_receipt,
        supports_preview=supports_preview,
    )


def _make_context(
    workspace_root: str = "/tmp/test-workspace",
    policy_profile: str = "default",
    ingress_metadata: dict | None = None,
) -> TaskExecutionContext:
    return TaskExecutionContext(
        conversation_id="conv-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="sa-1",
        source_channel="cli",
        workspace_root=workspace_root,
        policy_profile=policy_profile,
        ingress_metadata=ingress_metadata or {},
    )


class TestInferActionClass:
    def test_returns_explicit_action_class(self) -> None:
        tool = _make_mutating_tool(action_class="write_local")
        assert infer_action_class(tool) == "write_local"

    def test_readonly_returns_read_local(self) -> None:
        # Use SimpleNamespace to bypass ToolSpec validation
        spec = SimpleNamespace(action_class=None, readonly=True)
        assert infer_action_class(spec) == "read_local"  # type: ignore[arg-type]

    def test_non_readonly_no_class_returns_unknown(self) -> None:
        spec = SimpleNamespace(action_class=None, readonly=False)
        assert infer_action_class(spec) == "unknown"  # type: ignore[arg-type]

    def test_empty_string_action_class_returns_unknown(self) -> None:
        spec = SimpleNamespace(action_class="", readonly=False)
        assert infer_action_class(spec) == "unknown"  # type: ignore[arg-type]

    def test_readonly_tool_returns_read_local(self) -> None:
        tool = _make_readonly_tool()
        assert infer_action_class(tool) == "read_local"


class TestNormalizeScopeHints:
    def test_none_returns_unknown(self) -> None:
        result = normalize_scope_hints(None)
        assert result == ["unknown"]

    def test_empty_string_returns_unknown(self) -> None:
        result = normalize_scope_hints("")
        assert result == ["unknown"]

    def test_well_known_scope_passed_through(self) -> None:
        for scope in [
            "task_workspace",
            "repo",
            "home",
            "system",
            "network",
            "remote_service",
            "memory_store",
            "unknown",
        ]:
            result = normalize_scope_hints(scope)
            assert result == [scope]

    def test_list_of_known_scopes(self) -> None:
        result = normalize_scope_hints(["repo", "network"])
        assert result == ["repo", "network"]

    def test_path_within_workspace_becomes_task_workspace(self) -> None:
        result = normalize_scope_hints(
            "/tmp/ws/subdir",
            workspace_root="/tmp/ws",
        )
        assert result == ["task_workspace"]

    def test_path_is_workspace_root_becomes_task_workspace(self) -> None:
        result = normalize_scope_hints(
            "/tmp/ws",
            workspace_root="/tmp/ws",
        )
        assert result == ["task_workspace"]

    def test_home_path_becomes_home(self) -> None:
        home = str(Path.home())
        result = normalize_scope_hints(f"{home}/something")
        assert result == ["home"]

    def test_system_paths_become_system(self) -> None:
        # /Library and /System are real paths on macOS; /etc resolves to /private/etc
        for path in ["/Library/something", "/System/file"]:
            result = normalize_scope_hints(path)
            assert result == ["system"], f"Expected 'system' for path {path}"

    def test_other_path_becomes_repo(self) -> None:
        # Use a path that is not under home, workspace, or system dirs
        result = normalize_scope_hints("/var/data/path")
        assert result == ["repo"]

    def test_deduplicates_scopes(self) -> None:
        result = normalize_scope_hints(["repo", "repo", "network"])
        assert result == ["repo", "network"]

    def test_empty_items_in_list_skipped(self) -> None:
        result = normalize_scope_hints(["", "repo", ""])
        assert result == ["repo"]

    def test_all_empty_returns_unknown(self) -> None:
        result = normalize_scope_hints(["", ""])
        assert result == ["unknown"]


class TestBuildActionRequest:
    def test_basic_fields(self) -> None:
        tool = _make_readonly_tool()
        ctx = _make_context()
        req = build_action_request(tool, {"file": "test.txt"}, attempt_ctx=ctx)

        assert req.task_id == "task-1"
        assert req.step_id == "step-1"
        assert req.step_attempt_id == "sa-1"
        assert req.tool_name == "test_tool"
        assert req.tool_input == {"file": "test.txt"}
        assert req.action_class == "read_local"
        assert req.request_id.startswith("req_")

    def test_no_context_defaults(self) -> None:
        tool = _make_readonly_tool()
        req = build_action_request(tool, {})

        assert req.task_id == ""
        assert req.step_id == ""
        assert req.step_attempt_id == ""
        assert req.context["source_ingress"] == "unknown"
        assert req.context["policy_profile"] == "default"

    def test_idempotent_propagated(self) -> None:
        tool = _make_mutating_tool(idempotent=True)
        req = build_action_request(tool, {})
        assert req.idempotent is True

    def test_supports_preview_propagated(self) -> None:
        tool = _make_mutating_tool(supports_preview=True)
        req = build_action_request(tool, {})
        assert req.supports_preview is True

    def test_requires_receipt_from_tool(self) -> None:
        tool = _make_mutating_tool(requires_receipt=True)
        req = build_action_request(tool, {})
        assert req.requires_receipt is True

    def test_requires_receipt_false_on_readonly(self) -> None:
        tool = _make_readonly_tool()
        req = build_action_request(tool, {})
        assert req.requires_receipt is False

    def test_context_includes_workspace_root(self) -> None:
        tool = _make_readonly_tool()
        ctx = _make_context(workspace_root="/my/workspace")
        req = build_action_request(tool, {}, attempt_ctx=ctx)
        assert req.context["workspace_root"] == "/my/workspace"
        assert req.context["cwd"] == "/my/workspace"

    def test_context_includes_policy_profile(self) -> None:
        tool = _make_readonly_tool()
        ctx = _make_context(policy_profile="supervised")
        req = build_action_request(tool, {}, attempt_ctx=ctx)
        assert req.context["policy_profile"] == "supervised"

    def test_context_includes_plan_ref_from_ingress(self) -> None:
        tool = _make_readonly_tool()
        ctx = _make_context(ingress_metadata={"selected_plan_ref": "plan://abc"})
        req = build_action_request(tool, {}, attempt_ctx=ctx)
        assert req.context["selected_plan_ref"] == "plan://abc"

    def test_risk_hint_from_tool(self) -> None:
        tool = _make_mutating_tool(risk_hint="critical")
        req = build_action_request(tool, {})
        assert req.risk_hint == "critical"

    def test_conversation_id_from_context(self) -> None:
        tool = _make_readonly_tool()
        ctx = _make_context()
        req = build_action_request(tool, {}, attempt_ctx=ctx)
        assert req.conversation_id == "conv-1"
