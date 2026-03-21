from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermit.kernel.task.services.governor import (
    ControlAction,
    GovernorService,
    IntentClass,
    IntentResolution,
)


@pytest.fixture()
def fake_store():
    return SimpleNamespace()


@pytest.fixture()
def service(fake_store) -> GovernorService:
    return GovernorService(fake_store)


# --- IntentClass enum ---


def test_intent_class_values() -> None:
    assert IntentClass.new_work == "new_work"
    assert IntentClass.status_query == "status_query"
    assert IntentClass.control_command == "control_command"


def test_control_action_values() -> None:
    assert ControlAction.pause_program == "pause_program"
    assert ControlAction.resume_team == "resume_team"
    assert ControlAction.raise_budget == "raise_budget"
    assert ControlAction.lower_concurrency == "lower_concurrency"
    assert ControlAction.promote_benchmark == "promote_benchmark"
    assert ControlAction.escalate_approval == "escalate_approval"


# --- IntentResolution dataclass ---


def test_intent_resolution_frozen() -> None:
    resolution = IntentResolution(
        intent_class=IntentClass.new_work,
        raw_input="build something",
        confidence=0.5,
    )
    with pytest.raises(AttributeError):
        resolution.confidence = 0.9  # type: ignore[misc]


def test_intent_resolution_defaults() -> None:
    resolution = IntentResolution(intent_class=IntentClass.new_work)
    assert resolution.target_program_id is None
    assert resolution.target_team_id is None
    assert resolution.target_task_id is None
    assert resolution.raw_input == ""
    assert resolution.confidence == 0.0
    assert resolution.metadata == {}


# --- classify_intent ---


def test_classify_status_query(service: GovernorService) -> None:
    result = service.classify_intent("show me the status of the build")
    assert result.intent_class == IntentClass.status_query
    assert result.confidence >= 0.6
    assert "status" in result.metadata["matched_keywords"]
    assert "show" in result.metadata["matched_keywords"]


def test_classify_control_command_pause(service: GovernorService) -> None:
    result = service.classify_intent("pause the deployment")
    assert result.intent_class == IntentClass.control_command
    assert result.confidence >= 0.6
    assert "pause" in result.metadata["matched_keywords"]


def test_classify_control_command_escalate(service: GovernorService) -> None:
    result = service.classify_intent("escalate this issue now")
    assert result.intent_class == IntentClass.control_command
    assert "escalate" in result.metadata["matched_keywords"]


def test_classify_new_work_default(service: GovernorService) -> None:
    result = service.classify_intent("implement the login feature")
    assert result.intent_class == IntentClass.new_work
    assert result.confidence == 0.5


def test_classify_empty_message(service: GovernorService) -> None:
    result = service.classify_intent("")
    assert result.intent_class == IntentClass.new_work
    assert result.confidence == 0.5


def test_classify_preserves_raw_input(service: GovernorService) -> None:
    msg = "  Show   me   the   progress  "
    result = service.classify_intent(msg)
    assert result.raw_input == msg


def test_classify_with_context(service: GovernorService) -> None:
    ctx = {"session_id": "sess-1"}
    result = service.classify_intent("check progress", context=ctx)
    assert result.metadata["source_context"] == ctx


def test_classify_extracts_task_id(service: GovernorService) -> None:
    result = service.classify_intent("show status of task_abc123")
    assert result.target_task_id == "task_abc123"
    assert result.intent_class == IntentClass.status_query


def test_classify_extracts_program_id(service: GovernorService) -> None:
    result = service.classify_intent("pause program_def456")
    assert result.target_program_id == "program_def456"
    assert result.intent_class == IntentClass.control_command


def test_classify_extracts_team_id(service: GovernorService) -> None:
    result = service.classify_intent("resume team_xyz789")
    assert result.target_team_id == "team_xyz789"
    assert result.intent_class == IntentClass.control_command


def test_classify_control_takes_precedence_over_status(service: GovernorService) -> None:
    result = service.classify_intent("pause and show status")
    assert result.intent_class == IntentClass.control_command


def test_classify_multiple_control_keywords_boost_confidence(service: GovernorService) -> None:
    single = service.classify_intent("pause the task")
    multiple = service.classify_intent("pause and cancel and stop everything")
    assert multiple.confidence > single.confidence


def test_classify_confidence_capped_at_one(service: GovernorService) -> None:
    result = service.classify_intent(
        "pause resume stop cancel raise lower increase decrease escalate"
    )
    assert result.confidence <= 1.0


# --- resolve_program ---


def test_resolve_program_with_valid_hint(service: GovernorService) -> None:
    assert service.resolve_program("prog_abc123") == "prog_abc123"


def test_resolve_program_with_program_prefix(service: GovernorService) -> None:
    assert service.resolve_program("program_def456") == "prog_def456"


def test_resolve_program_none_hint(service: GovernorService) -> None:
    assert service.resolve_program(None) is None


def test_resolve_program_no_match(service: GovernorService) -> None:
    assert service.resolve_program("random text here") is None


# --- handle_intent ---


def test_handle_intent_new_work(service: GovernorService) -> None:
    resolution = IntentResolution(
        intent_class=IntentClass.new_work,
        raw_input="build a feature",
        confidence=0.5,
    )
    result = service.handle_intent(resolution)
    assert result["action"] == "create_task"
    assert result["intent_class"] == "new_work"


def test_handle_intent_status_query(service: GovernorService) -> None:
    resolution = IntentResolution(
        intent_class=IntentClass.status_query,
        target_task_id="task_abc123",
        raw_input="check status",
        confidence=0.7,
    )
    result = service.handle_intent(resolution)
    assert result["action"] == "query_status"
    assert result["target_task_id"] == "task_abc123"


def test_handle_intent_control_command(service: GovernorService) -> None:
    resolution = IntentResolution(
        intent_class=IntentClass.control_command,
        target_program_id="prog_abc123",
        raw_input="pause prog_abc123",
        confidence=0.8,
    )
    result = service.handle_intent(resolution)
    assert result["action"] == "dispatch_control"
    assert result["target_program_id"] == "prog_abc123"
    assert result["confidence"] == 0.8


# --- Chinese keyword classification (zh-CN) ---


def test_classify_status_query_chinese(service: GovernorService) -> None:
    result = service.classify_intent("查看当前进展")
    assert result.intent_class == IntentClass.status_query
    assert result.confidence >= 0.6


def test_classify_status_query_chinese_status(service: GovernorService) -> None:
    result = service.classify_intent("状态报告")
    assert result.intent_class == IntentClass.status_query
    assert "状态" in result.metadata["matched_keywords"]


def test_classify_status_query_chinese_summary(service: GovernorService) -> None:
    result = service.classify_intent("给我一个摘要")
    assert result.intent_class == IntentClass.status_query
    assert "摘要" in result.metadata["matched_keywords"]


def test_classify_status_query_chinese_overview(service: GovernorService) -> None:
    result = service.classify_intent("概览全局")
    assert result.intent_class == IntentClass.status_query


def test_classify_control_command_chinese_pause(service: GovernorService) -> None:
    result = service.classify_intent("暂停部署")
    assert result.intent_class == IntentClass.control_command
    assert "暂停" in result.metadata["matched_keywords"]


def test_classify_control_command_chinese_resume(service: GovernorService) -> None:
    result = service.classify_intent("恢复运行")
    assert result.intent_class == IntentClass.control_command
    assert "恢复" in result.metadata["matched_keywords"]


def test_classify_control_command_chinese_stop(service: GovernorService) -> None:
    result = service.classify_intent("停止所有任务")
    assert result.intent_class == IntentClass.control_command


def test_classify_control_command_chinese_cancel(service: GovernorService) -> None:
    result = service.classify_intent("取消这个任务")
    assert result.intent_class == IntentClass.control_command
    assert "取消" in result.metadata["matched_keywords"]


def test_classify_control_command_chinese_escalate(service: GovernorService) -> None:
    result = service.classify_intent("升级审批")
    assert result.intent_class == IntentClass.control_command
    assert "升级" in result.metadata["matched_keywords"]


def test_classify_control_command_chinese_budget(service: GovernorService) -> None:
    result = service.classify_intent("提高预算")
    assert result.intent_class == IntentClass.control_command


def test_classify_control_command_chinese_concurrency(service: GovernorService) -> None:
    result = service.classify_intent("降低并发")
    assert result.intent_class == IntentClass.control_command


def test_classify_control_takes_precedence_chinese(service: GovernorService) -> None:
    result = service.classify_intent("暂停并查看状态")
    assert result.intent_class == IntentClass.control_command


def test_classify_chinese_new_work(service: GovernorService) -> None:
    result = service.classify_intent("实现登录功能")
    assert result.intent_class == IntentClass.new_work
    assert result.confidence == 0.5


def test_classify_promote_keyword(service: GovernorService) -> None:
    result = service.classify_intent("promote benchmark results")
    assert result.intent_class == IntentClass.control_command
    assert "promote" in result.metadata["matched_keywords"]


# --- resolve_program strategy ---


def test_resolve_program_session_bound_highest_priority(service: GovernorService) -> None:
    """Session-bound program takes priority over hint text."""
    result = service.resolve_program(
        "prog_other123",
        session_bound_program_id="prog_session_bound",
    )
    assert result == "prog_session_bound"


def test_resolve_program_session_bound_over_store(service: GovernorService) -> None:
    """Session-bound program takes priority even when store has active programs."""
    result = service.resolve_program(
        session_bound_program_id="prog_from_session",
    )
    assert result == "prog_from_session"


def test_resolve_program_hint_over_store() -> None:
    """Explicit ID in hint takes priority over store fallback."""
    mock_store = SimpleNamespace(
        list_programs=lambda status, limit: [SimpleNamespace(program_id="prog_aaa111bbb")]
    )
    svc = GovernorService(mock_store)
    result = svc.resolve_program("prog_fromhint1")
    assert result == "prog_fromhint1"


def test_resolve_program_falls_back_to_active_store() -> None:
    """When no hint and no session binding, use most recent active program."""
    mock_store = SimpleNamespace(
        list_programs=lambda status, limit: [SimpleNamespace(program_id="prog_active_one")]
    )
    svc = GovernorService(mock_store)
    result = svc.resolve_program()
    assert result == "prog_active_one"


def test_resolve_program_none_when_no_store_fallback() -> None:
    """Returns None when store has no active programs and no hint given."""
    mock_store = SimpleNamespace(list_programs=lambda status, limit: [])
    svc = GovernorService(mock_store)
    result = svc.resolve_program()
    assert result is None


def test_resolve_program_none_when_store_missing_method() -> None:
    """Returns None gracefully when store lacks list_programs."""
    svc = GovernorService(SimpleNamespace())
    result = svc.resolve_program()
    assert result is None
