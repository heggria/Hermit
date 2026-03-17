# ruff: noqa: F403,F405
from tests.fixtures.task_kernel_support import *


def test_tool_executor_denied_action_records_failure_without_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-denied",
        goal="run dangerous shell",
        source_channel="chat",
        kind="respond",
    )
    ctx.workspace_root = str(workspace)
    executor = ToolExecutor(
        registry=_bash_registry(workspace),
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store),
        tool_output_limit=2000,
    )

    result = executor.execute(ctx, "bash", {"command": "sudo rm -rf /tmp/demo"})

    assert result.denied is True
    assert result.blocked is False
    assert store.list_approvals(task_id=ctx.task_id, limit=10) == []
    assert store.get_task(ctx.task_id).status == "failed"
    assert any(
        event["event_type"] == "policy.denied" for event in store.list_events(task_id=ctx.task_id)
    )


def test_executor_requires_new_approval_when_fingerprint_changes(tmp_path: Path) -> None:
    store, _artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)
    approval_service = ApprovalService(store)

    first = executor.execute(ctx, "write_file", {"path": ".env", "content": "hello\n"})
    assert first.approval_id is not None
    first_attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert first_attempt is not None
    assert first_attempt.context["phase"] == "awaiting_approval"
    approval_service.approve(first.approval_id)

    second = executor.execute(ctx, "write_file", {"path": ".env.local", "content": "hello\n"})

    assert second.blocked is True
    assert second.approval_id is not None
    assert second.approval_id != first.approval_id
    original_attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert original_attempt is not None and original_attempt.status == "superseded"
    events = store.list_events(task_id=ctx.task_id)
    assert any(event["event_type"] == "approval.mismatch" for event in events)
    assert any(event["event_type"] == "approval.drifted" for event in events)
    assert any(event["event_type"] == "step_attempt.superseded" for event in events)
    assert any(
        event["event_type"] == "step_attempt.phase_changed"
        and event["payload"].get("phase") == "awaiting_approval"
        for event in events
    )


def test_executor_creates_successor_attempt_when_witness_drifts(tmp_path: Path) -> None:
    store, _artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)
    target = Path(ctx.workspace_root) / ".env"
    target.write_text("before\n", encoding="utf-8")

    first = executor.execute(ctx, "write_file", {"path": ".env", "content": "after\n"})
    assert first.approval_id is not None
    approval = store.get_approval(first.approval_id)
    assert approval is not None
    assert approval.state_witness_ref is not None

    ApprovalService(store).approve(first.approval_id)
    target.write_text("changed-by-someone-else\n", encoding="utf-8")

    second = executor.execute(ctx, "write_file", {"path": ".env", "content": "after\n"})

    assert second.blocked is True
    assert second.approval_id is not None
    assert second.approval_id != first.approval_id
    original_attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert original_attempt is not None and original_attempt.status == "superseded"
    successor_approval = store.get_approval(second.approval_id)
    assert successor_approval is not None
    successor = store.get_step_attempt(successor_approval.step_attempt_id)
    assert successor is not None
    assert successor.step_attempt_id != ctx.step_attempt_id
    assert successor.status == "awaiting_approval"
    events = store.list_events(task_id=ctx.task_id)
    assert any(event["event_type"] == "witness.failed" for event in events)
    assert any(event["event_type"] == "step_attempt.superseded" for event in events)
    assert any(event["event_type"] == "evidence_case.invalidated" for event in events)
    assert any(event["event_type"] == "authorization_plan.invalidated" for event in events)


def test_executor_revalidates_when_evidence_case_is_invalidated(tmp_path: Path) -> None:
    store, _artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)

    first = executor.execute(ctx, "write_file", {"path": ".env", "content": "after\n"})
    assert first.approval_id is not None
    first_approval = store.get_approval(first.approval_id)
    assert first_approval is not None
    assert first_approval.evidence_case_ref is not None

    ApprovalService(store).approve(first.approval_id)
    executor.evidence_cases.invalidate(
        first_approval.evidence_case_ref,
        contradictions=["manual_probe"],
        summary="Evidence invalidated before execution resumed.",
    )

    second = executor.execute(ctx, "write_file", {"path": ".env", "content": "after\n"})

    assert second.blocked is True
    assert second.approval_id is not None
    assert second.approval_id != first.approval_id
    original_attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert original_attempt is not None and original_attempt.status == "superseded"
    successor_approval = store.get_approval(second.approval_id)
    assert successor_approval is not None
    successor_attempt = store.get_step_attempt(successor_approval.step_attempt_id)
    assert successor_attempt is not None
    assert successor_attempt.reentry_reason == "evidence_drift"
    events = store.list_events(task_id=ctx.task_id)
    assert any(event["event_type"] == "evidence_case.invalidated" for event in events)
    assert any(event["event_type"] == "step_attempt.superseded" for event in events)


def test_executor_marks_unknown_outcome_and_reconciles_local_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-uncertain",
        goal="write maybe",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    def flaky_write(payload: dict[str, Any]) -> str:
        path = workspace / str(payload["path"])
        path.write_text(str(payload["content"]), encoding="utf-8")
        raise RuntimeError("post-write crash")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="write_file",
            description="Write a UTF-8 text file inside the workspace.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=flaky_write,
            action_class="write_local",
            resource_scope_hint=str(workspace),
            risk_hint="high",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    executor = ToolExecutor(
        registry=registry,
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store),
        tool_output_limit=2000,
    )

    result = executor.execute(ctx, "write_file", {"path": "maybe.txt", "content": "hello\n"})

    assert result.receipt_id is not None
    assert result.result_code == "reconciled_applied"
    assert result.execution_status == "reconciling"
    assert "[Execution Requires Attention]" in str(result.model_content)
    assert (workspace / "maybe.txt").read_text(encoding="utf-8") == "hello\n"
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    grant = store.get_capability_grant(result.capability_grant_id or "")
    receipt = store.list_receipts(task_id=ctx.task_id, limit=1)[0]
    assert attempt is not None and attempt.status == "reconciling"
    assert store.get_task(ctx.task_id).status == "reconciling"
    assert grant is not None and grant.status == "uncertain"
    assert receipt.result_code == "reconciled_applied"
    assert receipt.capability_grant_ref == result.capability_grant_id
    assert any(
        event["event_type"] == "outcome.uncertain"
        for event in store.list_events(task_id=ctx.task_id)
    )


def test_runner_preserves_reconciling_status_for_reconciled_tool_outcomes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")

    def flaky_write(payload: dict[str, Any]) -> str:
        path = workspace / str(payload["path"])
        path.write_text(str(payload["content"]), encoding="utf-8")
        raise RuntimeError("post-write crash")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="write_file",
            description="Write a UTF-8 text file inside the workspace.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=flaky_write,
            action_class="write_local",
            resource_scope_hint=str(workspace),
            risk_hint="high",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    runtime = AgentRuntime(
        provider=FakeProvider(
            responses=[
                ProviderResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "write_file",
                            "input": {"path": "runner.txt", "content": "hello\n"},
                        }
                    ],
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=2, output_tokens=1),
                )
            ]
        ),
        registry=registry,
        model="fake",
        tool_executor=ToolExecutor(
            registry=registry,
            store=store,
            artifact_store=artifacts,
            policy_engine=PolicyEngine(),
            approval_service=ApprovalService(store),
            receipt_service=ReceiptService(store),
            tool_output_limit=2000,
        ),
    )
    runtime.workspace_root = str(workspace)  # type: ignore[attr-defined]
    runner = AgentRunner(
        runtime,
        SessionManager(tmp_path / "sessions", store=store),
        PluginManager(),
        task_controller=TaskController(store),
    )

    result = runner.handle("chat-runner-reconcile", "write it")

    task = store.get_last_task_for_conversation("chat-runner-reconcile")
    assert task is not None
    attempt_id = next(
        event["entity_id"]
        for event in store.list_events(task_id=task.task_id, limit=50)
        if event["event_type"] == "step_attempt.started"
    )
    attempt = store.get_step_attempt(attempt_id)
    assert result.execution_status == "reconciling"
    assert attempt is not None and attempt.status == "reconciling"
    assert store.get_task(task.task_id).status == "reconciling"


def test_executor_reconciles_command_side_effects_from_target_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-command-reconcile",
        goal="run command maybe",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    def flaky_bash(payload: dict[str, Any]) -> dict[str, Any]:
        target = workspace / "from-cmd.txt"
        target.write_text("cmd\n", encoding="utf-8")
        raise RuntimeError(f"command crashed after writing: {payload['command']}")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="bash",
            description="Run shell command.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=flaky_bash,
            action_class="execute_command",
            resource_scope_hint=str(workspace),
            risk_hint="critical",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    executor = ToolExecutor(
        registry=registry,
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store),
        tool_output_limit=2000,
    )

    first = executor.execute(ctx, "bash", {"command": "touch from-cmd.txt"})
    assert first.approval_id is not None
    ApprovalService(store).approve(first.approval_id)

    result = executor.execute(ctx, "bash", {"command": "touch from-cmd.txt"})

    assert result.receipt_id is not None
    assert result.result_code == "reconciled_applied"
    assert result.execution_status == "reconciling"
    assert (workspace / "from-cmd.txt").read_text(encoding="utf-8") == "cmd\n"
    receipt = store.list_receipts(task_id=ctx.task_id, limit=1)[0]
    assert receipt.result_code == "reconciled_applied"


def test_executor_reconciles_git_mutation_from_repo_state(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True, exist_ok=True)
    tracked = workspace / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    git_worktree = FakeGitWorktree(
        [
            {"present": True, "head": "commit-before", "dirty": False},
            {"present": True, "head": "commit-before", "dirty": False},
            {"present": True, "head": "commit-before", "dirty": False},
            {"present": True, "head": "commit-after", "dirty": False},
        ]
    )

    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-git-reconcile",
        goal="git mutate maybe",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    def flaky_git(payload: dict[str, Any]) -> dict[str, Any]:
        tracked.write_text("after\n", encoding="utf-8")
        raise RuntimeError(f"git crashed after mutation: {payload['command']}")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="git_mutation",
            description="Run a git mutation command.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=flaky_git,
            action_class="vcs_mutation",
            resource_scope_hint=str(workspace),
            risk_hint="critical",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    executor = ToolExecutor(
        registry=registry,
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store),
        git_worktree=git_worktree,
        tool_output_limit=2000,
    )

    first = executor.execute(ctx, "git_mutation", {"command": "git commit -am after"})
    assert first.approval_id is not None
    ApprovalService(store).approve(first.approval_id)

    result = executor.execute(ctx, "git_mutation", {"command": "git commit -am after"})

    assert result.receipt_id is not None
    assert result.result_code == "reconciled_applied"
    assert result.execution_status == "reconciling"
    assert tracked.read_text(encoding="utf-8") == "after\n"
    assert len(git_worktree.snapshot_calls) >= 4
    assert any(
        event["event_type"] == "outcome.uncertain"
        for event in store.list_events(task_id=ctx.task_id)
    )


def test_executor_prepares_vcs_rollback_plan_from_git_worktree_seam(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True, exist_ok=True)

    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-git-rollback-plan",
        goal="prepare git rollback",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )
    git_worktree = FakeGitWorktree([{"present": True, "head": "commit-before", "dirty": False}])
    executor = ToolExecutor(
        registry=_bash_registry(workspace),
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store),
        git_worktree=git_worktree,
        tool_output_limit=2000,
    )

    plan = executor._prepare_rollback_plan(  # type: ignore[attr-defined]
        action_type="vcs_mutation",
        tool_name="git_mutation",
        tool_input={"command": "git commit -am after"},
        attempt_ctx=ctx,
    )

    assert plan["supported"] is True
    assert plan["strategy"] == "git_revert_or_reset"
    assert len(plan["artifact_refs"]) == 1
    artifact = store.get_artifact(plan["artifact_refs"][0])
    assert artifact is not None
    assert json.loads(artifacts.read_text(artifact.uri)) == {
        "repo_path": str(workspace.resolve()),
        "head": "commit-before",
        "dirty": False,
    }
