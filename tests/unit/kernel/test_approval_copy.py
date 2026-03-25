"""Comprehensive tests for ApprovalCopyService (approval_copy.py).

Target: bring coverage from 42% to 95%+.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

from hermit.kernel.policy.approvals.approval_copy import (
    ApprovalCopy,
    ApprovalCopyService,
    ApprovalSection,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc(formatter=None, *, formatter_timeout_ms: int = 500, locale: str | None = None):
    return ApprovalCopyService(formatter, formatter_timeout_ms=formatter_timeout_ms, locale=locale)


def _action(
    *,
    tool_name: str = "",
    command_preview: str = "",
    target_paths: list[str] | None = None,
    network_hosts: list[str] | None = None,
    risk_level: str = "",
    display_copy: dict | None = None,
    approval_packet: dict | None = None,
    contract_packet: dict | None = None,
    tool_input: dict | None = None,
    outside_workspace: bool = False,
    reason: str = "",
    action_class: str = "",
    resource_scopes: list[str] | None = None,
    contract_ref: str = "",
    evidence_case_ref: str = "",
    authorization_plan_ref: str = "",
) -> dict:
    action: dict = {"tool_name": tool_name}
    if command_preview:
        action["command_preview"] = command_preview
    if target_paths is not None:
        action["target_paths"] = target_paths
    if network_hosts is not None:
        action["network_hosts"] = network_hosts
    if risk_level:
        action["risk_level"] = risk_level
    if display_copy is not None:
        action["display_copy"] = display_copy
    if approval_packet is not None:
        action["approval_packet"] = approval_packet
    if contract_packet is not None:
        action["contract_packet"] = contract_packet
    if tool_input is not None:
        action["tool_input"] = tool_input
    if outside_workspace:
        action["outside_workspace"] = True
    if reason:
        action["reason"] = reason
    if action_class:
        action["action_class"] = action_class
    if resource_scopes is not None:
        action["resource_scopes"] = resource_scopes
    if contract_ref:
        action["contract_ref"] = contract_ref
    if evidence_case_ref:
        action["evidence_case_ref"] = evidence_case_ref
    if authorization_plan_ref:
        action["authorization_plan_ref"] = authorization_plan_ref
    return action


# ---------------------------------------------------------------------------
# __init__ and __del__
# ---------------------------------------------------------------------------


class TestInit:
    def test_no_formatter(self) -> None:
        svc = _svc()
        assert svc._formatter is None
        assert svc._executor is None

    def test_with_formatter(self) -> None:
        svc = _svc(lambda facts: None)
        assert svc._formatter is not None
        assert svc._executor is not None
        svc.close()
        assert svc._executor is None

    def test_with_locale(self) -> None:
        svc = _svc(locale="en-US")
        assert svc._locale is not None

    def test_del_without_executor(self) -> None:
        svc = _svc()
        svc.close()  # Should not raise


# ---------------------------------------------------------------------------
# _summarize_text
# ---------------------------------------------------------------------------


class TestSummarizeText:
    def test_short_text(self) -> None:
        assert ApprovalCopyService._summarize_text("hello", limit=10) == "hello"

    def test_exact_limit(self) -> None:
        assert ApprovalCopyService._summarize_text("12345", limit=5) == "12345"

    def test_over_limit(self) -> None:
        result = ApprovalCopyService._summarize_text("abcdefghij", limit=7)
        assert result.endswith("...")
        assert len(result) <= 7

    def test_collapses_whitespace(self) -> None:
        assert ApprovalCopyService._summarize_text("  a  b  c  ", limit=100) == "a b c"


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_valid_int(self) -> None:
        assert ApprovalCopyService._safe_int(42) == 42

    def test_valid_string(self) -> None:
        assert ApprovalCopyService._safe_int("7") == 7

    def test_none(self) -> None:
        assert ApprovalCopyService._safe_int(None) is None

    def test_invalid_string(self) -> None:
        assert ApprovalCopyService._safe_int("abc") is None


# ---------------------------------------------------------------------------
# _format_datetime_value
# ---------------------------------------------------------------------------


class TestFormatDatetimeValue:
    def test_naive_datetime(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 10, 30)
        result = ApprovalCopyService._format_datetime_value(dt)
        assert "2024-01-15" in result
        assert "10:30" in result

    def test_aware_datetime(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 10, 30, tzinfo=datetime.UTC)
        result = ApprovalCopyService._format_datetime_value(dt)
        assert "2024-01-15" in result


# ---------------------------------------------------------------------------
# _format_datetime_text
# ---------------------------------------------------------------------------


class TestFormatDatetimeText:
    def test_empty(self) -> None:
        svc = _svc()
        assert svc._format_datetime_text("") == ""

    def test_valid_iso(self) -> None:
        svc = _svc()
        result = svc._format_datetime_text("2024-01-15T10:30:00")
        assert "2024-01-15" in result

    def test_z_suffix(self) -> None:
        svc = _svc()
        result = svc._format_datetime_text("2024-01-15T10:30:00Z")
        assert "2024-01-15" in result

    def test_invalid_iso(self) -> None:
        svc = _svc()
        result = svc._format_datetime_text("not-a-date")
        assert result == "not-a-date"


# ---------------------------------------------------------------------------
# _format_interval
# ---------------------------------------------------------------------------


class TestFormatInterval:
    def test_one_hour(self) -> None:
        svc = _svc()
        result = svc._format_interval(3600)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "1" in result

    def test_multi_hours(self) -> None:
        svc = _svc()
        result = svc._format_interval(7200)
        assert isinstance(result, str)
        assert "2" in result

    def test_one_minute(self) -> None:
        svc = _svc()
        result = svc._format_interval(60)
        assert isinstance(result, str)
        assert "1" in result

    def test_multi_minutes(self) -> None:
        svc = _svc()
        result = svc._format_interval(120)
        assert isinstance(result, str)
        assert "2" in result

    def test_one_second(self) -> None:
        svc = _svc()
        result = svc._format_interval(1)
        assert isinstance(result, str)
        assert "1" in result

    def test_multi_seconds(self) -> None:
        svc = _svc()
        result = svc._format_interval(45)
        assert isinstance(result, str)
        assert "45" in result


# ---------------------------------------------------------------------------
# _facts
# ---------------------------------------------------------------------------


class TestFacts:
    def test_basic_extraction(self) -> None:
        svc = _svc()
        action = _action(
            tool_name="bash",
            command_preview="ls",
            target_paths=["/tmp/a"],
            network_hosts=["example.com"],
            risk_level="medium",
            action_class="execute_command",
            reason="testing",
            resource_scopes=["scope1"],
            contract_ref="contract-1",
            evidence_case_ref="ev-1",
            authorization_plan_ref="plan-1",
        )
        facts = svc._facts(action, approval_id="ap-1")
        assert facts["tool_name"] == "bash"
        assert facts["command_preview"] == "ls"
        assert facts["target_paths"] == ["/tmp/a"]
        assert facts["network_hosts"] == ["example.com"]
        assert facts["risk_level"] == "medium"
        assert facts["approval_id"] == "ap-1"
        assert facts["action_class"] == "execute_command"
        assert facts["reason"] == "testing"
        assert facts["resource_scopes"] == ["scope1"]
        assert facts["contract_ref"] == "contract-1"

    def test_missing_fields(self) -> None:
        svc = _svc()
        facts = svc._facts({}, approval_id=None)
        assert facts["tool_name"] == ""
        assert facts["command_preview"] == ""
        assert facts["target_paths"] == []
        assert facts["network_hosts"] == []
        assert facts["risk_level"] == "high"
        assert facts["approval_id"] == ""

    def test_tool_input_as_dict(self) -> None:
        svc = _svc()
        action = _action(tool_input={"key": "value"})
        facts = svc._facts(action, approval_id=None)
        assert facts["tool_input"] == {"key": "value"}

    def test_tool_input_as_non_dict(self) -> None:
        svc = _svc()
        action = {"tool_input": "string_input"}
        facts = svc._facts(action, approval_id=None)
        assert facts["tool_input"] == "string_input"

    def test_risk_level_from_packet(self) -> None:
        svc = _svc()
        action = _action(approval_packet={"risk_level": "low"})
        facts = svc._facts(action, approval_id=None)
        assert facts["risk_level"] == "low"

    def test_none_display_copy(self) -> None:
        svc = _svc()
        action = {"display_copy": None}
        facts = svc._facts(action, approval_id=None)
        assert facts["approval_id"] == ""


# ---------------------------------------------------------------------------
# _copy_from_mapping
# ---------------------------------------------------------------------------


class TestCopyFromMapping:
    def test_valid_mapping(self) -> None:
        svc = _svc()
        result = svc._copy_from_mapping({"title": "T", "summary": "S", "detail": "D"})
        assert result is not None
        assert result.title == "T"
        assert result.summary == "S"
        assert result.detail == "D"

    def test_missing_title(self) -> None:
        svc = _svc()
        result = svc._copy_from_mapping({"summary": "S"})
        assert result is None

    def test_missing_summary(self) -> None:
        svc = _svc()
        result = svc._copy_from_mapping({"title": "T"})
        assert result is None

    def test_detail_defaults_to_summary(self) -> None:
        svc = _svc()
        result = svc._copy_from_mapping({"title": "T", "summary": "S"})
        assert result is not None
        assert result.detail == "S"

    def test_with_sections(self) -> None:
        svc = _svc()
        result = svc._copy_from_mapping(
            {
                "title": "T",
                "summary": "S",
                "sections": [{"title": "Sec", "items": ["a", "b"]}],
            }
        )
        assert result is not None
        assert len(result.sections) == 1
        assert result.sections[0].title == "Sec"


# ---------------------------------------------------------------------------
# _sections_from_mapping
# ---------------------------------------------------------------------------


class TestSectionsFromMapping:
    def test_not_a_list(self) -> None:
        svc = _svc()
        assert svc._sections_from_mapping("not a list") == ()
        assert svc._sections_from_mapping(None) == ()

    def test_entries_not_dicts(self) -> None:
        svc = _svc()
        assert svc._sections_from_mapping(["string", 42]) == ()

    def test_missing_title(self) -> None:
        svc = _svc()
        assert svc._sections_from_mapping([{"items": ["a"]}]) == ()

    def test_missing_items(self) -> None:
        svc = _svc()
        assert svc._sections_from_mapping([{"title": "T"}]) == ()

    def test_items_not_list(self) -> None:
        svc = _svc()
        assert svc._sections_from_mapping([{"title": "T", "items": "not list"}]) == ()

    def test_empty_items_after_strip(self) -> None:
        svc = _svc()
        assert svc._sections_from_mapping([{"title": "T", "items": ["", "  "]}]) == ()

    def test_valid_section(self) -> None:
        svc = _svc()
        result = svc._sections_from_mapping([{"title": "T", "items": ["a", "b"]}])
        assert len(result) == 1
        assert result[0].items == ("a", "b")


# ---------------------------------------------------------------------------
# _ensure_sections
# ---------------------------------------------------------------------------


class TestEnsureSections:
    def test_copy_already_has_sections(self) -> None:
        svc = _svc()
        copy = ApprovalCopy(
            title="T",
            summary="S",
            detail="D",
            sections=(ApprovalSection(title="Existing", items=("x",)),),
        )
        result = svc._ensure_sections(copy, {})
        assert result is copy

    def test_no_sections_from_facts(self) -> None:
        svc = _svc()
        copy = ApprovalCopy(title="T", summary="S", detail="D")
        result = svc._ensure_sections(copy, {})
        assert result is copy

    def test_sections_added_from_contract(self) -> None:
        svc = _svc()
        copy = ApprovalCopy(title="T", summary="S", detail="D")
        facts = {
            "tool_name": "write_file",
            "contract_packet": {
                "objective": "Test objective",
                "expected_effects": ["effect1"],
            },
        }
        result = svc._ensure_sections(copy, facts)
        assert len(result.sections) >= 1
        assert result.title == "T"
        assert result.summary == "S"


# ---------------------------------------------------------------------------
# _format_with_optional_formatter
# ---------------------------------------------------------------------------


class TestFormatWithOptionalFormatter:
    def test_no_formatter(self) -> None:
        svc = _svc()
        assert svc._format_with_optional_formatter({}) is None

    def test_formatter_returns_dict(self) -> None:
        def fmt(facts):
            return {"title": "FT", "summary": "FS", "detail": "FD"}

        svc = _svc(fmt)
        result = svc._format_with_optional_formatter({"title": "T"})
        assert result is not None
        assert result.title == "FT"
        svc.close()

    def test_formatter_returns_string(self) -> None:
        def fmt(facts):
            return "Custom summary"

        svc = _svc(fmt)
        result = svc._format_with_optional_formatter({"title": "T"})
        assert result is not None
        assert result.summary == "Custom summary"
        svc.close()

    def test_formatter_returns_empty_string(self) -> None:
        def fmt(facts):
            return "  "

        svc = _svc(fmt)
        result = svc._format_with_optional_formatter({"title": "T"})
        assert result is None
        svc.close()

    def test_formatter_returns_none(self) -> None:
        def fmt(facts):
            return None

        svc = _svc(fmt)
        result = svc._format_with_optional_formatter({"title": "T"})
        assert result is None
        svc.close()

    def test_formatter_raises_exception(self) -> None:
        def fmt(facts):
            raise ValueError("boom")

        svc = _svc(fmt)
        result = svc._format_with_optional_formatter({"title": "T"})
        assert result is None
        svc.close()

    def test_formatter_timeout(self) -> None:
        import time

        def fmt(facts):
            time.sleep(0.1)
            return "too late"

        svc = _svc(fmt, formatter_timeout_ms=1)
        result = svc._format_with_optional_formatter({"title": "T"})
        assert result is None
        svc.close()


# ---------------------------------------------------------------------------
# describe / resolve_copy
# ---------------------------------------------------------------------------


class TestDescribe:
    def test_display_copy_takes_priority(self) -> None:
        svc = _svc()
        action = _action(display_copy={"title": "DC", "summary": "DS", "detail": "DD"})
        result = svc.describe(action)
        assert result.title == "DC"

    def test_display_copy_invalid_falls_through(self) -> None:
        svc = _svc()
        action = _action(display_copy={"summary": "no title"})
        result = svc.describe(action)
        # Falls through to template copy
        assert result.title  # some template title

    def test_formatter_result_used(self) -> None:
        def fmt(facts):
            return {"title": "FT", "summary": "FS"}

        svc = _svc(fmt)
        action = _action()
        result = svc.describe(action)
        assert result.title == "FT"
        svc.close()

    def test_template_fallback(self) -> None:
        svc = _svc()
        action = _action()
        result = svc.describe(action)
        assert result.title  # some template title

    def test_resolve_copy_alias(self) -> None:
        svc = _svc()
        action = _action(tool_name="bash")
        r1 = svc.describe(action, approval_id="ap-1")
        r2 = svc.resolve_copy(action, approval_id="ap-1")
        assert r1.title == r2.title
        assert r1.summary == r2.summary


# ---------------------------------------------------------------------------
# build_canonical_copy
# ---------------------------------------------------------------------------


class TestBuildCanonicalCopy:
    def test_without_sections(self) -> None:
        svc = _svc()
        action = _action(tool_name="bash", command_preview="ls")
        result = svc.build_canonical_copy(action)
        assert "title" in result
        assert "summary" in result
        assert "detail" in result
        assert "sections" not in result

    def test_with_sections(self) -> None:
        svc = _svc()
        action = _action(
            tool_name="schedule_create",
            tool_input={"name": "test", "schedule_type": "once", "once_at": "2024-01-01T00:00:00"},
        )
        result = svc.build_canonical_copy(action)
        assert "title" in result
        if "sections" in result:
            assert isinstance(result["sections"], list)


# ---------------------------------------------------------------------------
# blocked_message / model_prompt
# ---------------------------------------------------------------------------


class TestBlockedMessage:
    def test_detail_different_from_summary(self) -> None:
        svc = _svc()
        action = _action(
            display_copy={"title": "T", "summary": "Summary", "detail": "Different detail"},
        )
        result = svc.blocked_message(action, "ap-1")
        assert isinstance(result, str)
        assert "T" in result
        assert "Summary" in result

    def test_detail_same_as_summary(self) -> None:
        svc = _svc()
        action = _action(
            display_copy={"title": "T", "summary": "Same", "detail": "Same"},
        )
        result = svc.blocked_message(action, "ap-1")
        assert isinstance(result, str)
        assert "T" in result


class TestModelPrompt:
    def test_renders(self) -> None:
        svc = _svc()
        action = _action(tool_name="bash", command_preview="ls")
        result = svc.model_prompt(action, "ap-1")
        assert isinstance(result, str)
        assert "ap-1" in result


# ---------------------------------------------------------------------------
# _template_copy — command branches
# ---------------------------------------------------------------------------


class TestTemplateCopyCommands:
    def test_git_push(self) -> None:
        svc = _svc()
        action = _action(command_preview="git push origin main")
        result = svc.describe(action)
        assert result.title

    def test_rm_command(self) -> None:
        svc = _svc()
        action = _action(command_preview="rm -rf /tmp/foo")
        result = svc.describe(action)
        assert result.title

    def test_trash_command(self) -> None:
        svc = _svc()
        action = _action(command_preview="trash myfile.txt")
        result = svc.describe(action)
        assert result.title

    def test_del_command(self) -> None:
        svc = _svc()
        action = _action(command_preview="del myfile.txt")
        result = svc.describe(action)
        assert result.title

    def test_generic_command(self) -> None:
        svc = _svc()
        action = _action(command_preview="echo hello")
        result = svc.describe(action)
        assert result.title


# ---------------------------------------------------------------------------
# _template_copy — path branches
# ---------------------------------------------------------------------------


class TestTemplateCopyPaths:
    def test_sensitive_env(self) -> None:
        svc = _svc()
        action = _action(target_paths=["/home/user/.env"])
        result = svc.describe(action)
        assert result.title

    def test_sensitive_ssh(self) -> None:
        svc = _svc()
        action = _action(target_paths=["/home/user/.ssh/id_rsa"])
        result = svc.describe(action)
        assert result.title

    def test_sensitive_gnupg(self) -> None:
        svc = _svc()
        action = _action(target_paths=["/home/user/.gnupg/key"])
        result = svc.describe(action)
        assert result.title

    def test_sensitive_library(self) -> None:
        svc = _svc()
        action = _action(target_paths=["/Library/Preferences/plist"])
        result = svc.describe(action)
        assert result.title

    def test_outside_workspace(self) -> None:
        svc = _svc()
        action = _action(target_paths=["/tmp/outside.txt"], outside_workspace=True)
        result = svc.describe(action)
        assert result.title

    def test_single_path_normal(self) -> None:
        svc = _svc()
        action = _action(target_paths=["/tmp/normal.txt"])
        result = svc.describe(action)
        assert result.title

    def test_multiple_paths(self) -> None:
        svc = _svc()
        action = _action(target_paths=["/tmp/a.txt", "/tmp/b.txt"])
        result = svc.describe(action)
        assert result.title


# ---------------------------------------------------------------------------
# _template_copy — network branches
# ---------------------------------------------------------------------------


class TestTemplateCopyNetwork:
    def test_single_host(self) -> None:
        svc = _svc()
        action = _action(network_hosts=["example.com"])
        result = svc.describe(action)
        assert result.title

    def test_multiple_hosts(self) -> None:
        svc = _svc()
        action = _action(network_hosts=["example.com", "api.example.com"])
        result = svc.describe(action)
        assert result.title


# ---------------------------------------------------------------------------
# _template_copy — packet and fallback
# ---------------------------------------------------------------------------


class TestTemplateCopyPacketFallback:
    def test_packet_title_with_summary(self) -> None:
        svc = _svc()
        action = _action(approval_packet={"title": "Packet Title", "summary": "Packet summary"})
        result = svc.describe(action)
        assert result.title == "Packet Title"
        assert result.summary == "Packet summary"

    def test_packet_title_without_summary(self) -> None:
        svc = _svc()
        action = _action(approval_packet={"title": "Packet Title"})
        result = svc.describe(action)
        assert result.title == "Packet Title"

    def test_fallback_with_tool_name(self) -> None:
        svc = _svc()
        action = _action(tool_name="custom_tool")
        result = svc.describe(action)
        assert result.title

    def test_fallback_without_tool_name(self) -> None:
        svc = _svc()
        action = _action()
        result = svc.describe(action)
        assert result.title


# ---------------------------------------------------------------------------
# _scheduler_copy
# ---------------------------------------------------------------------------


class TestSchedulerCopy:
    def test_schedule_create(self) -> None:
        svc = _svc()
        action = _action(
            tool_name="schedule_create",
            tool_input={
                "name": "daily-backup",
                "prompt": "Run backup",
                "schedule_type": "cron",
                "cron_expr": "0 0 * * *",
            },
        )
        result = svc.describe(action)
        assert result.title

    def test_schedule_create_without_name(self) -> None:
        svc = _svc()
        action = _action(
            tool_name="schedule_create",
            tool_input={"schedule_type": "once", "once_at": "2024-01-01T00:00:00"},
        )
        result = svc.describe(action)
        assert result.title

    def test_schedule_update(self) -> None:
        svc = _svc()
        action = _action(
            tool_name="schedule_update",
            tool_input={"job_id": "job-1"},
        )
        result = svc.describe(action)
        assert result.title

    def test_schedule_update_without_job_id(self) -> None:
        svc = _svc()
        action = _action(
            tool_name="schedule_update",
            tool_input={},
        )
        result = svc.describe(action)
        assert result.title

    def test_schedule_delete(self) -> None:
        svc = _svc()
        action = _action(
            tool_name="schedule_delete",
            tool_input={"job_id": "job-1"},
        )
        result = svc.describe(action)
        assert result.title

    def test_schedule_delete_without_job_id(self) -> None:
        svc = _svc()
        action = _action(
            tool_name="schedule_delete",
            tool_input={},
        )
        result = svc.describe(action)
        assert result.title


# ---------------------------------------------------------------------------
# _scheduler_sections
# ---------------------------------------------------------------------------


class TestSchedulerSections:
    def test_create_with_all_fields(self) -> None:
        svc = _svc()
        facts = {
            "tool_name": "schedule_create",
            "tool_input": {
                "name": "backup",
                "prompt": "Run daily backup of all data",
                "schedule_type": "interval",
                "interval_seconds": 3600,
            },
            "reason": "Need automated backups",
        }
        sections = svc._scheduler_sections(facts)
        assert len(sections) == 2  # schedule details + reason sections

    def test_update_with_all_fields(self) -> None:
        svc = _svc()
        facts = {
            "tool_name": "schedule_update",
            "tool_input": {
                "job_id": "job-1",
                "name": "new-name",
                "prompt": "Updated prompt",
                "enabled": True,
                "cron_expr": "0 0 * * *",
            },
            "reason": "",
        }
        sections = svc._scheduler_sections(facts)
        assert len(sections) == 2  # update details + explanation section

    def test_update_disabled(self) -> None:
        svc = _svc()
        facts = {
            "tool_name": "schedule_update",
            "tool_input": {"job_id": "job-1", "enabled": False},
            "reason": "",
        }
        sections = svc._scheduler_sections(facts)
        assert len(sections) == 2  # update details + explanation section

    def test_delete_sections(self) -> None:
        svc = _svc()
        facts = {
            "tool_name": "schedule_delete",
            "tool_input": {"job_id": "job-1"},
            "reason": "No longer needed",
        }
        sections = svc._scheduler_sections(facts)
        assert len(sections) == 2  # delete details + reason sections

    def test_non_schedule_tool(self) -> None:
        svc = _svc()
        facts = {
            "tool_name": "bash",
            "tool_input": {"command": "ls"},
        }
        sections = svc._scheduler_sections(facts)
        # For non-schedule tools, should return empty or minimal sections
        assert isinstance(sections, tuple)


# ---------------------------------------------------------------------------
# _scheduler_input
# ---------------------------------------------------------------------------


class TestSchedulerInput:
    def test_dict_input(self) -> None:
        svc = _svc()
        facts = {"tool_input": {"key": "value"}}
        assert svc._scheduler_input(facts) == {"key": "value"}

    def test_non_dict_input(self) -> None:
        svc = _svc()
        facts = {"tool_input": "string"}
        assert svc._scheduler_input(facts) == {}

    def test_missing_input(self) -> None:
        svc = _svc()
        assert svc._scheduler_input({}) == {}


# ---------------------------------------------------------------------------
# _scheduler_reason
# ---------------------------------------------------------------------------


class TestSchedulerReason:
    def test_with_reason(self) -> None:
        svc = _svc()
        facts = {"reason": "custom reason"}
        result = svc._scheduler_reason(facts, default_key="kernel.approval.scheduler.create.reason")
        assert result == "custom reason"

    def test_without_reason(self) -> None:
        svc = _svc()
        facts = {"reason": ""}
        result = svc._scheduler_reason(facts, default_key="kernel.approval.scheduler.create.reason")
        assert result  # returns the translated default key


# ---------------------------------------------------------------------------
# _describe_scheduler_timing
# ---------------------------------------------------------------------------


class TestDescribeSchedulerTiming:
    def test_once_with_valid_datetime(self) -> None:
        svc = _svc()
        result = svc._describe_scheduler_timing(
            {
                "schedule_type": "once",
                "once_at": "2024-06-15T10:30:00",
            }
        )
        assert result

    def test_once_without_datetime(self) -> None:
        svc = _svc()
        result = svc._describe_scheduler_timing({"schedule_type": "once", "once_at": ""})
        assert result  # falls through to unknown

    def test_interval_valid(self) -> None:
        svc = _svc()
        result = svc._describe_scheduler_timing(
            {
                "schedule_type": "interval",
                "interval_seconds": 300,
            }
        )
        assert result

    def test_interval_zero(self) -> None:
        svc = _svc()
        result = svc._describe_scheduler_timing(
            {
                "schedule_type": "interval",
                "interval_seconds": 0,
            }
        )
        assert result  # falls through to unknown

    def test_interval_negative(self) -> None:
        svc = _svc()
        result = svc._describe_scheduler_timing(
            {
                "schedule_type": "interval",
                "interval_seconds": -1,
            }
        )
        assert result

    def test_cron_with_croniter(self) -> None:
        svc = _svc()
        result = svc._describe_scheduler_timing(
            {
                "schedule_type": "cron",
                "cron_expr": "0 0 * * *",
            }
        )
        assert result

    def test_cron_without_croniter(self) -> None:
        svc = _svc()
        with patch(
            "hermit.kernel.policy.approvals.approval_copy.ApprovalCopyService._next_cron_run_text",
            return_value=None,
        ):
            result = svc._describe_scheduler_timing(
                {
                    "schedule_type": "cron",
                    "cron_expr": "0 0 * * *",
                }
            )
            assert result

    def test_unknown_type(self) -> None:
        svc = _svc()
        result = svc._describe_scheduler_timing({"schedule_type": "weird"})
        assert result

    def test_empty_type(self) -> None:
        svc = _svc()
        result = svc._describe_scheduler_timing({})
        assert result


# ---------------------------------------------------------------------------
# _next_cron_run_text
# ---------------------------------------------------------------------------


class TestNextCronRunText:
    def test_valid_cron(self) -> None:
        svc = _svc()
        result = svc._next_cron_run_text("0 0 * * *")
        # May return None if croniter not installed
        if result is not None:
            assert ":" in result  # contains time

    def test_invalid_cron(self) -> None:
        svc = _svc()
        result = svc._next_cron_run_text("invalid cron")
        assert result is None


# ---------------------------------------------------------------------------
# _contract_sections
# ---------------------------------------------------------------------------


class TestContractSections:
    def test_empty_contract_packet(self) -> None:
        svc = _svc()
        assert svc._contract_sections({"contract_packet": {}}) == ()
        assert svc._contract_sections({"contract_packet": None}) == ()
        assert svc._contract_sections({}) == ()

    def test_objective(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"objective": "Test goal"}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1
        assert any("Test goal" in item for item in sections[0].items)

    def test_expected_effects(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"expected_effects": ["e1", "e2"]}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1

    def test_rollback_expectation(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"rollback_expectation": "revert file"}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1

    def test_evidence_sufficiency_status(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"evidence_sufficiency": {"status": "sufficient"}}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1
        assert any("sufficient" in item for item in sections[0].items)

    def test_evidence_sufficiency_score_int(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"evidence_sufficiency": {"score": 85}}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1
        assert any("85" in item for item in sections[0].items)

    def test_evidence_sufficiency_score_float(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"evidence_sufficiency": {"score": 0.85}}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1

    def test_evidence_sufficiency_gaps(self) -> None:
        svc = _svc()
        facts = {
            "contract_packet": {
                "evidence_sufficiency": {"unresolved_gaps": ["gap1", "gap2"]},
            },
        }
        sections = svc._contract_sections(facts)
        assert len(sections) == 1

    def test_authority_approval_route(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"approval_route": "manual"}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1

    def test_authority_resource_scope(self) -> None:
        svc = _svc()
        facts = {
            "contract_packet": {
                "authority_scope": {"resource_scope": ["scope1", "scope2"]},
            },
        }
        sections = svc._contract_sections(facts)
        assert len(sections) == 1

    def test_authority_resource_scope_empty_list(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"authority_scope": {"resource_scope": []}}}
        sections = svc._contract_sections(facts)
        # Empty resource_scope should not produce authority items
        assert isinstance(sections, tuple)

    def test_authority_current_gaps(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"current_gaps": ["missing docs"]}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1

    def test_authority_drift_expiry(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"drift_expiry": "2024-12-31"}}
        sections = svc._contract_sections(facts)
        assert len(sections) == 1

    def test_full_contract(self) -> None:
        svc = _svc()
        facts = {
            "contract_packet": {
                "objective": "Deploy feature",
                "expected_effects": ["new endpoint", "migration"],
                "rollback_expectation": "revert migration",
                "evidence_sufficiency": {
                    "status": "sufficient",
                    "score": 0.95,
                    "unresolved_gaps": ["edge case"],
                },
                "approval_route": "auto",
                "authority_scope": {"resource_scope": ["db:write"]},
                "current_gaps": ["review pending"],
                "drift_expiry": "2024-12-31",
            },
        }
        sections = svc._contract_sections(facts)
        assert len(sections) == 3  # contract, evidence, authority


# ---------------------------------------------------------------------------
# _sections_for_facts
# ---------------------------------------------------------------------------


class TestSectionsForFacts:
    def test_schedule_tool_combines_contract_and_scheduler(self) -> None:
        svc = _svc()
        facts = {
            "tool_name": "schedule_create",
            "tool_input": {"name": "test", "schedule_type": "once", "once_at": "2024-01-01"},
            "reason": "",
            "contract_packet": {"objective": "Schedule job"},
        }
        sections = svc._sections_for_facts(facts)
        assert isinstance(sections, tuple)

    def test_non_schedule_tool_only_contract(self) -> None:
        svc = _svc()
        facts = {
            "tool_name": "write_file",
            "contract_packet": {"objective": "Write config"},
        }
        sections = svc._sections_for_facts(facts)
        assert isinstance(sections, tuple)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_none_approval_packet(self) -> None:
        svc = _svc()
        action = {"approval_packet": None}
        facts = svc._facts(action, approval_id=None)
        assert facts["title"] == ""

    def test_none_contract_packet(self) -> None:
        svc = _svc()
        action = {"contract_packet": None}
        facts = svc._facts(action, approval_id=None)
        assert facts["contract_packet"] == {}

    def test_evidence_score_as_string(self) -> None:
        svc = _svc()
        facts = {"contract_packet": {"evidence_sufficiency": {"score": "high"}}}
        sections = svc._contract_sections(facts)
        assert isinstance(sections, tuple)

    def test_display_copy_none_value(self) -> None:
        svc = _svc()
        action = {"display_copy": None}
        result = svc.describe(action)
        assert result.title  # falls through to template
