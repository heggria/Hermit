"""Tests for src/hermit/surfaces/cli/_commands_schedule.py"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import typer.testing

from hermit.surfaces.cli.main import app

runner = typer.testing.CliRunner()


def _fake_job(**overrides) -> SimpleNamespace:
    defaults = dict(
        id="job-001",
        name="Test Job",
        enabled=True,
        cron_expr="0 9 * * 1-5",
        once_at=None,
        interval_seconds=None,
        next_run_at=1700000000.0,
        last_run_at=1699990000.0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_history_record(**overrides) -> SimpleNamespace:
    defaults = dict(
        success=True,
        started_at=1700000000.0,
        finished_at=1700000010.0,
        result_text="Task completed successfully",
        error=None,
        job_name="Test Job",
    )
    defaults.update(overrides)
    record = SimpleNamespace(**defaults)
    record.to_dict = lambda: defaults
    return record


# ---------------------------------------------------------------------------
# schedule list
# ---------------------------------------------------------------------------
class TestScheduleList:
    def test_with_cron_jobs(self) -> None:
        mock_store = MagicMock()
        mock_store.list_schedules.return_value = [_fake_job()]
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "list"])
        assert result.exit_code == 0
        assert "job-001" in result.output
        assert "Test Job" in result.output
        assert "enabled" in result.output

    def test_with_once_job(self) -> None:
        mock_store = MagicMock()
        job = _fake_job(cron_expr=None, once_at=1700000000.0)
        mock_store.list_schedules.return_value = [job]
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "list"])
        assert result.exit_code == 0
        assert "once at" in result.output.lower() or "once" in result.output

    def test_with_interval_job(self) -> None:
        mock_store = MagicMock()
        job = _fake_job(cron_expr=None, once_at=None, interval_seconds=300)
        mock_store.list_schedules.return_value = [job]
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "list"])
        assert result.exit_code == 0
        assert "300" in result.output

    def test_with_unknown_schedule(self) -> None:
        mock_store = MagicMock()
        job = _fake_job(cron_expr=None, once_at=None, interval_seconds=None)
        mock_store.list_schedules.return_value = [job]
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "list"])
        assert result.exit_code == 0
        assert "unknown" in result.output.lower()

    def test_disabled_job(self) -> None:
        mock_store = MagicMock()
        job = _fake_job(enabled=False)
        mock_store.list_schedules.return_value = [job]
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "list"])
        assert result.exit_code == 0
        assert "disabled" in result.output

    def test_no_jobs(self) -> None:
        mock_store = MagicMock()
        mock_store.list_schedules.return_value = []
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "list"])
        assert result.exit_code == 0
        assert "No scheduled" in result.output

    def test_none_next_run(self) -> None:
        mock_store = MagicMock()
        job = _fake_job(next_run_at=None, last_run_at=None)
        mock_store.list_schedules.return_value = [job]
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "list"])
        assert result.exit_code == 0
        assert "N/A" in result.output


# ---------------------------------------------------------------------------
# schedule add
# ---------------------------------------------------------------------------
class TestScheduleAdd:
    def test_add_with_cron(self) -> None:
        mock_store = MagicMock()
        mock_croniter = MagicMock()
        with (
            patch(
                "hermit.surfaces.cli._commands_schedule.get_schedule_store",
                return_value=mock_store,
            ),
            patch.dict("sys.modules", {"croniter": mock_croniter}),
        ):
            result = runner.invoke(
                app,
                [
                    "schedule",
                    "add",
                    "--name",
                    "Daily task",
                    "--prompt",
                    "Do work",
                    "--cron",
                    "0 9 * * *",
                ],
            )
        assert result.exit_code == 0
        assert "Added" in result.output
        mock_store.create_schedule.assert_called_once()

    def test_add_with_interval(self) -> None:
        mock_store = MagicMock()
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                [
                    "schedule",
                    "add",
                    "--name",
                    "Periodic",
                    "--prompt",
                    "Check",
                    "--interval",
                    "120",
                ],
            )
        assert result.exit_code == 0
        assert "Added" in result.output

    def test_add_with_once(self) -> None:
        mock_store = MagicMock()
        future_time = "2099-12-31T23:59"
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                [
                    "schedule",
                    "add",
                    "--name",
                    "OneTime",
                    "--prompt",
                    "Run once",
                    "--once",
                    future_time,
                ],
            )
        assert result.exit_code == 0
        assert "Added" in result.output

    def test_add_no_schedule_type(self) -> None:
        result = runner.invoke(
            app,
            ["schedule", "add", "--name", "Bad", "--prompt", "No schedule"],
        )
        assert result.exit_code != 0
        assert "exactly one" in result.output.lower()

    def test_add_multiple_schedule_types(self) -> None:
        result = runner.invoke(
            app,
            [
                "schedule",
                "add",
                "--name",
                "Bad",
                "--prompt",
                "Too many",
                "--cron",
                "0 * * * *",
                "--interval",
                "120",
            ],
        )
        assert result.exit_code != 0

    def test_add_invalid_cron(self) -> None:
        mock_croniter_mod = MagicMock()
        mock_croniter_mod.croniter.side_effect = ValueError("bad cron")
        with patch.dict("sys.modules", {"croniter": mock_croniter_mod}):
            result = runner.invoke(
                app,
                [
                    "schedule",
                    "add",
                    "--name",
                    "Bad",
                    "--prompt",
                    "Invalid",
                    "--cron",
                    "bad-expr",
                ],
            )
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_add_invalid_datetime(self) -> None:
        result = runner.invoke(
            app,
            [
                "schedule",
                "add",
                "--name",
                "Bad",
                "--prompt",
                "Invalid",
                "--once",
                "not-a-date",
            ],
        )
        assert result.exit_code != 0
        assert "invalid" in result.output.lower() or "format" in result.output.lower()

    def test_add_once_in_past(self) -> None:
        result = runner.invoke(
            app,
            [
                "schedule",
                "add",
                "--name",
                "Past",
                "--prompt",
                "Old",
                "--once",
                "2020-01-01T00:00",
            ],
        )
        assert result.exit_code != 0
        assert "future" in result.output.lower()

    def test_add_interval_too_small(self) -> None:
        result = runner.invoke(
            app,
            [
                "schedule",
                "add",
                "--name",
                "Fast",
                "--prompt",
                "TooFast",
                "--interval",
                "30",
            ],
        )
        assert result.exit_code != 0
        assert "60" in result.output


# ---------------------------------------------------------------------------
# schedule remove
# ---------------------------------------------------------------------------
class TestScheduleRemove:
    def test_remove_found(self) -> None:
        mock_store = MagicMock()
        mock_store.delete_schedule.return_value = True
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "remove", "job-001"])
        assert result.exit_code == 0
        assert "Removed" in result.output

    def test_remove_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.delete_schedule.return_value = False
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "remove", "bad-id"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "no task" in result.output.lower()


# ---------------------------------------------------------------------------
# schedule enable / disable
# ---------------------------------------------------------------------------
class TestScheduleEnableDisable:
    def test_enable_found(self) -> None:
        mock_store = MagicMock()
        mock_store.update_schedule.return_value = True
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "enable", "job-001"])
        assert result.exit_code == 0
        assert "Enabled" in result.output

    def test_enable_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.get_schedule.return_value = None
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "enable", "bad-id"])
        assert result.exit_code != 0

    def test_disable_found(self) -> None:
        mock_store = MagicMock()
        mock_store.update_schedule.return_value = True
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "disable", "job-001"])
        assert result.exit_code == 0
        assert "Disabled" in result.output

    def test_disable_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.update_schedule.return_value = False
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "disable", "bad-id"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# schedule history
# ---------------------------------------------------------------------------
class TestScheduleHistory:
    def test_with_records(self) -> None:
        mock_store = MagicMock()
        records = [_fake_history_record(), _fake_history_record(success=False, error="Timeout")]
        mock_store.list_schedule_history.return_value = records
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "history"])
        assert result.exit_code == 0
        assert "OK" in result.output
        assert "FAIL" in result.output
        assert "Timeout" in result.output

    def test_empty_history(self) -> None:
        mock_store = MagicMock()
        mock_store.list_schedule_history.return_value = []
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "history"])
        assert result.exit_code == 0
        assert "No execution history" in result.output

    def test_with_job_id_filter(self) -> None:
        mock_store = MagicMock()
        mock_store.list_schedule_history.return_value = [_fake_history_record()]
        with patch(
            "hermit.surfaces.cli._commands_schedule.get_schedule_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["schedule", "history", "--job-id", "job-001"])
        assert result.exit_code == 0
        mock_store.list_schedule_history.assert_called_once_with(job_id="job-001", limit=10)
