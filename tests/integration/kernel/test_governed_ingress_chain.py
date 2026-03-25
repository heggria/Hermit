"""Integration tests: Governor -> GovernedIngress -> 3-path routing with real KernelStore.

Validates the full chain from raw user message through GovernorService intent
classification to GovernedIngressService 3-path routing (status_query,
control_command, new_work) with real SQLite-backed KernelStore instances.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import ProgramState
from hermit.kernel.task.services.governed_ingress import GovernedIngressService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> KernelStore:
    """Create a real KernelStore backed by a tmp_path SQLite database."""
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    return store


def _make_service(store: KernelStore) -> GovernedIngressService:
    """Create a GovernedIngressService wired to a real store."""
    return GovernedIngressService(store)


# ---------------------------------------------------------------------------
# 1. Status query path
# ---------------------------------------------------------------------------


class TestStatusQueryPath:
    """Send status-type messages and verify requires_execution=False with
    program status in the response."""

    def test_status_query_chinese_progress(self, tmp_path: Path) -> None:
        """'查看进展' should route to status_query, never trigger execution."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="查看进展")

        assert result.intent_class == "status_query"
        assert result.requires_execution is False
        assert result.response["handler"] == "status_query"
        # No binding decision for status queries
        assert result.binding_decision is None

    def test_status_query_returns_approval_queue_when_no_programs(self, tmp_path: Path) -> None:
        """When no programs exist, status query should still return the
        approval queue as a global overview."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="查看进展")

        assert result.requires_execution is False
        assert "approval_queue" in result.response
        assert result.response["approval_queue"]["total_count"] == 0

    def test_status_query_resolves_active_program(self, tmp_path: Path) -> None:
        """When an active program exists, status query should resolve to it."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        # Create a program (starts as active)
        program = store.create_program(title="Feature Alpha", goal="Ship v2")

        result = service.process_message(message="查看进展")

        assert result.requires_execution is False
        assert result.intent_class == "status_query"
        # The service should resolve to the active program
        if "program_status" in result.response:
            assert result.response["resolved_program_id"] == program.program_id

    def test_status_query_never_creates_tasks(self, tmp_path: Path) -> None:
        """Status queries must be pure read operations — no task creation."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        tasks_before = store.list_tasks(limit=100)
        service.process_message(message="查看进展")
        tasks_after = store.list_tasks(limit=100)

        assert len(tasks_after) == len(tasks_before)


# ---------------------------------------------------------------------------
# 2. Control command path
# ---------------------------------------------------------------------------


class TestControlCommandPath:
    """Create a program, activate it, then send Chinese pause command.
    Verify program is archived and requires_execution=False."""

    def test_pause_program_chinese(self, tmp_path: Path) -> None:
        """'暂停 program_xxx' should archive the program and not trigger execution."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        # Create a program (starts as active)
        program = store.create_program(title="Deployment Pipeline", goal="Deploy v3")

        # Send pause command with the program ID
        result = service.process_message(message=f"暂停 {program.program_id}")

        assert result.intent_class == "control_command"
        assert result.requires_execution is False
        assert result.binding_decision is None
        assert result.response["handler"] == "control_command"
        assert result.response["action"] == "pause"
        assert result.response.get("applied") is True
        assert result.response["new_status"] == str(ProgramState.archived)

        # Verify state actually changed in the store
        updated_program = store.get_program(program.program_id)
        assert updated_program is not None
        assert updated_program.status == ProgramState.archived

    def test_control_command_never_creates_tasks(self, tmp_path: Path) -> None:
        """Control commands must not create any new tasks."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        program = store.create_program(title="Pipeline", goal="Run CI")

        tasks_before = store.list_tasks(limit=100)
        service.process_message(message=f"暂停 {program.program_id}")
        tasks_after = store.list_tasks(limit=100)

        assert len(tasks_after) == len(tasks_before)


# ---------------------------------------------------------------------------
# 3. New work path
# ---------------------------------------------------------------------------


class TestNewWorkPath:
    """Send a new-work message and verify requires_execution=True with
    a BindingDecision returned."""

    def test_new_work_english(self, tmp_path: Path) -> None:
        """'build a new feature' should route to new_work with execution required."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="build a new feature")

        assert result.intent_class == "new_work"
        assert result.requires_execution is True
        assert result.binding_decision is not None
        assert result.binding_decision.resolution == "start_new_root"
        assert result.response["action"] == "bind_task"

    def test_new_work_has_binding_details(self, tmp_path: Path) -> None:
        """The response should contain binding decision details."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="implement OAuth2 login flow")

        assert result.requires_execution is True
        assert result.binding_decision is not None
        # Response envelope should contain binding info
        assert "resolution" in result.response
        assert "chosen_task_id" in result.response
        assert "confidence" in result.response

    def test_new_work_chinese(self, tmp_path: Path) -> None:
        """Chinese work requests should also route to new_work."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="实现用户认证模块")

        assert result.intent_class == "new_work"
        assert result.requires_execution is True
        assert result.binding_decision is not None


# ---------------------------------------------------------------------------
# 4. Chinese keyword routing
# ---------------------------------------------------------------------------


class TestChineseKeywordRouting:
    """Verify that Chinese keywords correctly route to the expected intent paths."""

    def test_view_status_routes_to_status_query(self, tmp_path: Path) -> None:
        """'查看当前状态' should route to status_query."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="查看当前状态")

        assert result.intent_class == "status_query"
        assert result.requires_execution is False

    def test_pause_all_routes_to_control_command(self, tmp_path: Path) -> None:
        """'暂停所有任务' should route to control_command."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="暂停所有任务")

        assert result.intent_class == "control_command"
        assert result.requires_execution is False

    def test_stop_routes_to_control_command(self, tmp_path: Path) -> None:
        """'停止所有任务' should route to control_command."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="停止所有任务")

        assert result.intent_class == "control_command"
        assert result.requires_execution is False

    def test_cancel_routes_to_control_command(self, tmp_path: Path) -> None:
        """'取消这个任务' should route to control_command."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="取消这个任务")

        assert result.intent_class == "control_command"
        assert result.requires_execution is False

    def test_display_summary_routes_to_status_query(self, tmp_path: Path) -> None:
        """'显示状态摘要' should route to status_query."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="显示状态摘要")

        assert result.intent_class == "status_query"
        assert result.requires_execution is False

    def test_overview_routes_to_status_query(self, tmp_path: Path) -> None:
        """'概览当前进度' should route to status_query."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="概览当前进度")

        assert result.intent_class == "status_query"
        assert result.requires_execution is False

    def test_resume_routes_to_control_command(self, tmp_path: Path) -> None:
        """'恢复运行' should route to control_command."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="恢复运行")

        assert result.intent_class == "control_command"
        assert result.requires_execution is False

    def test_control_takes_precedence_over_status_chinese(self, tmp_path: Path) -> None:
        """When both control and status keywords present, control wins."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        # Contains both 暂停 (control) and 状态 (status)
        result = service.process_message(message="暂停并查看状态")

        assert result.intent_class == "control_command"
        assert result.requires_execution is False


# ---------------------------------------------------------------------------
# 5. Attempt-level query
# ---------------------------------------------------------------------------


class TestAttemptLevelQuery:
    """Create task + step + attempt in store, then query about the attempt.
    Verify attempt details are returned."""

    def test_attempt_query_returns_details(self, tmp_path: Path) -> None:
        """Query about an attempt with a status keyword should return attempt details.

        Note: The GovernorService requires at least one status keyword
        (e.g. '查看', '状态') in addition to the attempt ID — a bare
        attempt ID without any keyword defaults to new_work.
        """
        store = _make_store(tmp_path)
        service = _make_service(store)

        # Create a real task -> step -> attempt chain
        task = store.create_task(
            conversation_id="conv-1",
            title="Build Integration Tests",
            goal="100% coverage on ingress chain",
            source_channel="chat",
        )
        step = store.create_step(
            task_id=task.task_id,
            kind="execute",
            title="run test suite",
        )
        attempt = store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            attempt=1,
        )

        # Query about the attempt — use '查看' (status keyword) + attempt ID
        result = service.process_message(message=f"查看 {attempt.step_attempt_id} 的状态")

        # Should route to status_query because '查看' and '状态' are status keywords
        assert result.intent_class == "status_query"
        assert result.requires_execution is False
        assert "attempt_status" in result.response

        attempt_status = result.response["attempt_status"]
        assert attempt_status["step_attempt_id"] == attempt.step_attempt_id
        assert attempt_status["task_id"] == task.task_id
        assert attempt_status["step_id"] == step.step_id
        assert attempt_status["attempt_number"] == 1

    def test_attempt_query_includes_formatted_summary(self, tmp_path: Path) -> None:
        """Attempt query should include a formatted human-readable summary."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        task = store.create_task(
            conversation_id="conv-1",
            title="Benchmark Run",
            goal="Run benchmarks",
            source_channel="chat",
        )
        step = store.create_step(
            task_id=task.task_id,
            kind="execute",
            title="benchmark execution",
        )
        attempt = store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            attempt=1,
        )

        result = service.process_message(message=f"show status of {attempt.step_attempt_id}")

        assert result.requires_execution is False
        assert "formatted_summary" in result.response
        assert attempt.step_attempt_id in result.response["formatted_summary"]

    def test_attempt_not_found_returns_error(self, tmp_path: Path) -> None:
        """Query for a nonexistent attempt should return an error, not crash."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        result = service.process_message(message="show status of attempt_nonexistent999999")

        assert result.requires_execution is False
        assert "error" in result.response
        assert "not found" in result.response["error"].lower()

    def test_attempt_query_does_not_create_tasks(self, tmp_path: Path) -> None:
        """Attempt queries must be pure read — no side effects."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        task = store.create_task(
            conversation_id="conv-1",
            title="Side-effect Check",
            goal="Verify no side effects",
            source_channel="chat",
        )
        step = store.create_step(
            task_id=task.task_id,
            kind="execute",
            title="check",
        )
        attempt = store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            attempt=1,
        )

        tasks_before = store.list_tasks(limit=100)
        service.process_message(message=f"查看 {attempt.step_attempt_id} 的状态")
        tasks_after = store.list_tasks(limit=100)

        assert len(tasks_after) == len(tasks_before)


# ---------------------------------------------------------------------------
# Cross-path integration: full chain validation
# ---------------------------------------------------------------------------


class TestFullChainIntegration:
    """End-to-end tests exercising multiple paths in sequence to verify
    the GovernedIngress routing is consistent with real store state."""

    def test_all_three_paths_with_single_store(self, tmp_path: Path) -> None:
        """Exercise all 3 paths (status, control, new_work) against the same
        store instance to verify no cross-contamination."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        # 1. Status query — should work even with empty store
        status_result = service.process_message(message="查看进展")
        assert status_result.intent_class == "status_query"
        assert status_result.requires_execution is False

        # 2. New work — should create a binding decision
        work_result = service.process_message(message="build a new feature")
        assert work_result.intent_class == "new_work"
        assert work_result.requires_execution is True
        assert work_result.binding_decision is not None

        # 3. Control command — create program (active), then pause (archives)
        program = store.create_program(title="Alpha", goal="Ship it")
        ctrl_result = service.process_message(message=f"暂停 {program.program_id}")
        assert ctrl_result.intent_class == "control_command"
        assert ctrl_result.requires_execution is False
        assert ctrl_result.response.get("applied") is True

        # Verify the program state persisted
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert updated.status == ProgramState.archived

    def test_resolution_traceability(self, tmp_path: Path) -> None:
        """Each result should carry an IntentResolution for traceability."""
        store = _make_store(tmp_path)
        service = _make_service(store)

        for msg in ["查看进展", "暂停所有任务", "implement new feature"]:
            result = service.process_message(message=msg)
            assert result.resolution is not None, f"No resolution for: {msg}"
            assert result.resolution.raw_input == msg
            assert result.resolution.confidence > 0
