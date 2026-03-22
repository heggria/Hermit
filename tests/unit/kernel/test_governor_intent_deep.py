"""Deep tests for GovernorService intent classification.

Tests Chinese and English intent classification, ambiguous inputs,
priority strategy resolution, and handle_intent dispatch.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermit.kernel.task.services.governor import (
    ControlAction,
    GovernorService,
    IntentClass,
    IntentResolution,
)


def _make_store(programs: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    """Build a minimal store mock for GovernorService."""
    stored = list(programs or [])

    def list_programs(status: str | None = None, limit: int = 50) -> list[SimpleNamespace]:
        if status:
            return [p for p in stored if p.status == status][:limit]
        return stored[:limit]

    return SimpleNamespace(list_programs=list_programs)


@pytest.fixture()
def governor() -> GovernorService:
    return GovernorService(store=_make_store())


# ---------------------------------------------------------------------------
# Chinese intent classification
# ---------------------------------------------------------------------------


class TestChineseIntentClassification:
    """Test intent classification for Chinese inputs."""

    def test_create_task_fix_bug_cn(self, governor: GovernorService) -> None:
        """'创建任务修复bug' — contains no status/control keywords → new_work."""
        result = governor.classify_intent("创建任务修复bug")
        assert result.intent_class == IntentClass.new_work

    def test_view_progress_cn(self, governor: GovernorService) -> None:
        """'查看进展' — contains status keyword '查看' and '进展' → status_query."""
        result = governor.classify_intent("查看进展")
        assert result.intent_class == IntentClass.status_query

    def test_pause_program_cn(self, governor: GovernorService) -> None:
        """'暂停程序' — contains control keyword '暂停' → control_command."""
        result = governor.classify_intent("暂停程序")
        assert result.intent_class == IntentClass.control_command

    def test_check_status_cn(self, governor: GovernorService) -> None:
        """'查看状态' — contains '查看' and '状态' → status_query."""
        result = governor.classify_intent("查看状态")
        assert result.intent_class == IntentClass.status_query

    def test_stop_task_cn(self, governor: GovernorService) -> None:
        """'停止任务' — contains '停止' → control_command."""
        result = governor.classify_intent("停止任务")
        assert result.intent_class == IntentClass.control_command

    def test_increase_budget_cn(self, governor: GovernorService) -> None:
        """'增加预算' — contains '增加' + '预算' → control_command."""
        result = governor.classify_intent("增加预算")
        assert result.intent_class == IntentClass.control_command

    def test_show_summary_cn(self, governor: GovernorService) -> None:
        """'显示摘要' — contains '显示' + '摘要' → status_query."""
        result = governor.classify_intent("显示摘要")
        assert result.intent_class == IntentClass.status_query

    def test_pure_chinese_new_work(self, governor: GovernorService) -> None:
        """'写一个新功能' — no status/control keywords → new_work."""
        result = governor.classify_intent("写一个新功能")
        assert result.intent_class == IntentClass.new_work

    def test_restart_service_cn(self, governor: GovernorService) -> None:
        """'重启服务' — contains '重启' → control_command."""
        result = governor.classify_intent("重启服务")
        assert result.intent_class == IntentClass.control_command


# ---------------------------------------------------------------------------
# English intent classification
# ---------------------------------------------------------------------------


class TestEnglishIntentClassification:
    """Test intent classification for English inputs."""

    def test_fix_auth_bug_en(self, governor: GovernorService) -> None:
        """'fix the authentication bug' — no status/control keywords → new_work."""
        result = governor.classify_intent("fix the authentication bug")
        assert result.intent_class == IntentClass.new_work

    def test_show_status_en(self, governor: GovernorService) -> None:
        """'show status' — contains 'show' + 'status' → status_query."""
        result = governor.classify_intent("show status")
        assert result.intent_class == IntentClass.status_query

    def test_check_progress_en(self, governor: GovernorService) -> None:
        """'check the progress' — 'check' + 'progress' → status_query."""
        result = governor.classify_intent("check the progress")
        assert result.intent_class == IntentClass.status_query

    def test_pause_the_program_en(self, governor: GovernorService) -> None:
        """'pause the program' — 'pause' → control_command."""
        result = governor.classify_intent("pause the program")
        assert result.intent_class == IntentClass.control_command

    def test_resume_execution_en(self, governor: GovernorService) -> None:
        """'resume execution' — 'resume' → control_command."""
        result = governor.classify_intent("resume execution")
        assert result.intent_class == IntentClass.control_command

    def test_cancel_task_en(self, governor: GovernorService) -> None:
        """'cancel the task' — 'cancel' → control_command."""
        result = governor.classify_intent("cancel the task")
        assert result.intent_class == IntentClass.control_command

    def test_show_dashboard_en(self, governor: GovernorService) -> None:
        """'show dashboard' — 'show' + 'dashboard' → status_query."""
        result = governor.classify_intent("show dashboard")
        assert result.intent_class == IntentClass.status_query

    def test_scale_up_en(self, governor: GovernorService) -> None:
        """'scale up the workers' — 'scale' → control_command."""
        result = governor.classify_intent("scale up the workers")
        assert result.intent_class == IntentClass.control_command

    def test_write_new_feature_en(self, governor: GovernorService) -> None:
        """'write a new sorting algorithm' — no match → new_work."""
        result = governor.classify_intent("write a new sorting algorithm")
        assert result.intent_class == IntentClass.new_work

    def test_escalate_approval_en(self, governor: GovernorService) -> None:
        """'escalate the approval' — 'escalate' → control_command."""
        result = governor.classify_intent("escalate the approval")
        assert result.intent_class == IntentClass.control_command


# ---------------------------------------------------------------------------
# Ambiguous inputs
# ---------------------------------------------------------------------------


class TestAmbiguousInputs:
    """Test default behavior for ambiguous or minimal inputs."""

    def test_hello_defaults_to_new_work(self, governor: GovernorService) -> None:
        result = governor.classify_intent("hello")
        assert result.intent_class == IntentClass.new_work

    def test_empty_string_defaults_to_new_work(self, governor: GovernorService) -> None:
        result = governor.classify_intent("")
        assert result.intent_class == IntentClass.new_work

    def test_single_word_no_match(self, governor: GovernorService) -> None:
        result = governor.classify_intent("banana")
        assert result.intent_class == IntentClass.new_work

    def test_ambiguous_confidence_is_lower(self, governor: GovernorService) -> None:
        """Ambiguous/default classification should have lower confidence."""
        ambiguous = governor.classify_intent("hello world")
        specific = governor.classify_intent("show status report")
        assert ambiguous.confidence <= specific.confidence

    def test_whitespace_only(self, governor: GovernorService) -> None:
        result = governor.classify_intent("   ")
        assert result.intent_class == IntentClass.new_work

    def test_numbers_only(self, governor: GovernorService) -> None:
        result = governor.classify_intent("12345")
        assert result.intent_class == IntentClass.new_work


# ---------------------------------------------------------------------------
# Confidence scores
# ---------------------------------------------------------------------------


class TestConfidenceScores:
    """Test confidence scoring behavior."""

    def test_default_new_work_confidence(self, governor: GovernorService) -> None:
        result = governor.classify_intent("implement a feature")
        assert result.confidence == 0.5

    def test_single_keyword_match_confidence(self, governor: GovernorService) -> None:
        result = governor.classify_intent("status")
        assert result.confidence >= 0.6

    def test_multiple_keyword_match_higher_confidence(self, governor: GovernorService) -> None:
        single = governor.classify_intent("status")
        multiple = governor.classify_intent("show status report")
        assert multiple.confidence >= single.confidence

    def test_confidence_capped_at_1(self, governor: GovernorService) -> None:
        """Even with many keywords, confidence should not exceed 1.0."""
        result = governor.classify_intent("show status progress report overview summary check")
        assert result.confidence <= 1.0

    def test_control_confidence_starts_at_0_6(self, governor: GovernorService) -> None:
        result = governor.classify_intent("pause")
        assert result.confidence >= 0.6


# ---------------------------------------------------------------------------
# Target ID extraction
# ---------------------------------------------------------------------------


class TestTargetIdExtraction:
    """Test extraction of task/program/team/attempt IDs from messages."""

    def test_extract_task_id(self, governor: GovernorService) -> None:
        result = governor.classify_intent("check status of task_abc123xyz")
        assert result.target_task_id is not None
        assert "task_abc123xyz" in result.target_task_id

    def test_extract_program_id(self, governor: GovernorService) -> None:
        result = governor.classify_intent("show status of prog_abc123xyz")
        assert result.target_program_id is not None

    def test_extract_team_id(self, governor: GovernorService) -> None:
        result = governor.classify_intent("check team_abc123xyz")
        assert result.target_team_id is not None

    def test_extract_attempt_id(self, governor: GovernorService) -> None:
        result = governor.classify_intent("show attempt_abc123xyz status")
        assert result.target_attempt_id is not None

    def test_no_id_extraction_on_plain_text(self, governor: GovernorService) -> None:
        result = governor.classify_intent("fix the bug please")
        assert result.target_task_id is None
        assert result.target_program_id is None
        assert result.target_team_id is None

    def test_multiple_ids_in_single_message(self, governor: GovernorService) -> None:
        result = governor.classify_intent("check task_abc123xyz in program_def456uvw")
        assert result.target_task_id is not None
        assert result.target_program_id is not None


# ---------------------------------------------------------------------------
# Priority strategy resolution (resolve_program)
# ---------------------------------------------------------------------------


class TestPriorityStrategyResolution:
    """Test GovernorService.resolve_program() priority strategy."""

    def test_session_bound_program_is_highest_priority(self) -> None:
        """Session-bound program takes precedence over everything."""
        prog = SimpleNamespace(program_id="prog_active", status="active")
        gov = GovernorService(store=_make_store(programs=[prog]))

        resolved = gov.resolve_program(
            hint="prog_other999999",
            session_bound_program_id="session-bound-id",
        )
        assert resolved == "session-bound-id"

    def test_explicit_id_from_hint_is_second_priority(self) -> None:
        gov = GovernorService(store=_make_store())
        resolved = gov.resolve_program(hint="check prog_abc123xyz")
        assert resolved is not None
        assert "abc123xyz" in resolved

    def test_most_recently_active_program_is_third_priority(self) -> None:
        prog = SimpleNamespace(program_id="prog_recent", status="active")
        gov = GovernorService(store=_make_store(programs=[prog]))
        resolved = gov.resolve_program()
        assert resolved == "prog_recent"

    def test_no_match_returns_none(self) -> None:
        gov = GovernorService(store=_make_store())
        resolved = gov.resolve_program()
        assert resolved is None

    def test_no_active_programs_returns_none(self) -> None:
        """If all programs are completed, resolve_program returns None."""
        prog = SimpleNamespace(program_id="prog_done", status="completed")
        gov = GovernorService(store=_make_store(programs=[prog]))
        resolved = gov.resolve_program()
        assert resolved is None

    def test_hint_without_program_id_falls_through(self) -> None:
        """Hint text without a prog_* pattern falls to active program lookup."""
        prog = SimpleNamespace(program_id="prog_fallback", status="active")
        gov = GovernorService(store=_make_store(programs=[prog]))
        resolved = gov.resolve_program(hint="check the status")
        assert resolved == "prog_fallback"


# ---------------------------------------------------------------------------
# handle_intent dispatch
# ---------------------------------------------------------------------------


class TestHandleIntent:
    """Test handle_intent returns correct action mapping."""

    def test_new_work_maps_to_create_task(self, governor: GovernorService) -> None:
        resolution = IntentResolution(
            intent_class=IntentClass.new_work,
            raw_input="fix the bug",
            confidence=0.5,
        )
        result = governor.handle_intent(resolution)
        assert result["action"] == "create_task"

    def test_status_query_maps_to_query_status(self, governor: GovernorService) -> None:
        resolution = IntentResolution(
            intent_class=IntentClass.status_query,
            raw_input="show status",
            confidence=0.7,
        )
        result = governor.handle_intent(resolution)
        assert result["action"] == "query_status"

    def test_control_command_maps_to_dispatch_control(self, governor: GovernorService) -> None:
        resolution = IntentResolution(
            intent_class=IntentClass.control_command,
            raw_input="pause the program",
            confidence=0.7,
        )
        result = governor.handle_intent(resolution)
        assert result["action"] == "dispatch_control"

    def test_handle_intent_includes_raw_input(self, governor: GovernorService) -> None:
        resolution = IntentResolution(
            intent_class=IntentClass.new_work,
            raw_input="original text",
            confidence=0.5,
        )
        result = governor.handle_intent(resolution)
        assert result["raw_input"] == "original text"

    def test_handle_intent_includes_confidence(self, governor: GovernorService) -> None:
        resolution = IntentResolution(
            intent_class=IntentClass.status_query,
            raw_input="status",
            confidence=0.8,
        )
        result = governor.handle_intent(resolution)
        assert result["confidence"] == 0.8

    def test_handle_intent_includes_target_ids(self, governor: GovernorService) -> None:
        resolution = IntentResolution(
            intent_class=IntentClass.status_query,
            target_program_id="prog_123",
            target_task_id="task_456",
            raw_input="check prog_123",
            confidence=0.7,
        )
        result = governor.handle_intent(resolution)
        assert result["target_program_id"] == "prog_123"
        assert result["target_task_id"] == "task_456"


# ---------------------------------------------------------------------------
# IntentResolution model
# ---------------------------------------------------------------------------


class TestIntentResolutionModel:
    """Verify IntentResolution dataclass behavior."""

    def test_frozen(self) -> None:
        r = IntentResolution(
            intent_class=IntentClass.new_work,
            raw_input="test",
            confidence=0.5,
        )
        with pytest.raises(AttributeError):
            r.intent_class = IntentClass.status_query  # type: ignore[misc]

    def test_default_none_targets(self) -> None:
        r = IntentResolution(
            intent_class=IntentClass.new_work,
            raw_input="test",
            confidence=0.5,
        )
        assert r.target_program_id is None
        assert r.target_team_id is None
        assert r.target_task_id is None
        assert r.target_attempt_id is None

    def test_metadata_default_empty(self) -> None:
        r = IntentResolution(
            intent_class=IntentClass.new_work,
            raw_input="test",
            confidence=0.5,
        )
        assert r.metadata == {}


# ---------------------------------------------------------------------------
# ControlAction enum
# ---------------------------------------------------------------------------


class TestControlActionEnum:
    def test_all_actions_present(self) -> None:
        expected = {
            "pause_program",
            "resume_team",
            "raise_budget",
            "lower_concurrency",
            "promote_benchmark",
            "escalate_approval",
        }
        assert {a.value for a in ControlAction} == expected


# ---------------------------------------------------------------------------
# IntentClass enum
# ---------------------------------------------------------------------------


class TestIntentClassEnum:
    def test_all_classes_present(self) -> None:
        expected = {"new_work", "status_query", "control_command"}
        assert {c.value for c in IntentClass} == expected


# ---------------------------------------------------------------------------
# Control keywords prioritize over status keywords
# ---------------------------------------------------------------------------


class TestKeywordPriority:
    """Verify control keywords take precedence when both match."""

    def test_control_before_status_cn(self, governor: GovernorService) -> None:
        """'暂停并查看状态' — has both control ('暂停') and status ('查看', '状态')
        but control should win because it's checked first."""
        result = governor.classify_intent("暂停并查看状态")
        assert result.intent_class == IntentClass.control_command

    def test_control_before_status_en(self, governor: GovernorService) -> None:
        """'pause and show status' — both 'pause' (control) and 'show'+'status'
        (status) match, but control wins."""
        result = governor.classify_intent("pause and show status")
        assert result.intent_class == IntentClass.control_command
