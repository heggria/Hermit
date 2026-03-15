from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace

from typer.testing import CliRunner

import hermit
import hermit.main as main_mod


def test_cli_plugin_and_task_commands_cover_error_paths(tmp_path: Path, monkeypatch) -> None:
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        plugins_dir=tmp_path / ".hermit" / "plugins",
    )
    settings.plugins_dir.mkdir(parents=True, exist_ok=True)
    (settings.plugins_dir / "existing").mkdir()

    monkeypatch.setattr(main_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(main_mod, "_ensure_workspace", lambda settings: None)
    monkeypatch.setattr(
        main_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="clone failed"),
    )
    monkeypatch.setattr(
        main_mod,
        "_get_kernel_store",
        lambda: SimpleNamespace(
            get_task=lambda task_id: None,
            get_capability_grant=lambda grant_id: None,
            get_memory_record=lambda memory_id: None,
        ),
    )

    runner = CliRunner()
    install_exists = runner.invoke(
        main_mod.app, ["plugin", "install", "https://example.com/existing.git"]
    )
    install_failed = runner.invoke(
        main_mod.app, ["plugin", "install", "https://example.com/new.git"]
    )
    info_missing = runner.invoke(main_mod.app, ["plugin", "info", "missing"])
    task_show_missing = runner.invoke(main_mod.app, ["task", "show", "task-missing"])
    grant_missing = runner.invoke(main_mod.app, ["task", "capability", "revoke", "grant-missing"])
    memory_inspect_missing = runner.invoke(main_mod.app, ["memory", "inspect", "memory-missing"])
    memory_inspect_require_target = runner.invoke(main_mod.app, ["memory", "inspect"])

    assert install_exists.exit_code == 1
    assert "already exists" in install_exists.output
    assert install_failed.exit_code == 1
    assert "clone failed" in install_failed.output
    assert info_missing.exit_code == 1
    assert "Plugin not found" in info_missing.output
    assert task_show_missing.exit_code == 1
    assert "Task not found" in task_show_missing.output
    assert grant_missing.exit_code == 1
    assert "Capability grant not found" in grant_missing.output
    assert memory_inspect_missing.exit_code == 1
    assert "Memory not found" in memory_inspect_missing.output
    assert memory_inspect_require_target.exit_code == 1
    assert (
        "Provide either a memory_id argument or --claim-text."
        in memory_inspect_require_target.output
    )


def test_cli_schedule_and_autostart_cover_validation_and_not_found(
    tmp_path: Path, monkeypatch
) -> None:
    store = SimpleNamespace(
        update_schedule=lambda job_id, enabled: None,
        delete_schedule=lambda job_id: False,
        list_schedule_history=lambda job_id=None, limit=10: [],
        list_schedules=lambda: [],
    )
    autostart_mod = ModuleType("hermit.autostart")
    autostart_mod.enable = lambda adapter="feishu": f"enable:{adapter}"
    autostart_mod.disable = lambda adapter="feishu": f"disable:{adapter}"
    autostart_mod.status = lambda adapter=None: f"status:{adapter}"

    monkeypatch.setattr(main_mod, "_get_schedule_store", lambda: store)
    monkeypatch.setattr(hermit, "autostart", autostart_mod, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "hermit.autostart", autostart_mod)

    runner = CliRunner()
    add_invalid_choice = runner.invoke(
        main_mod.app,
        [
            "schedule",
            "add",
            "--name",
            "x",
            "--prompt",
            "y",
            "--cron",
            "* * * * *",
            "--interval",
            "60",
        ],
    )
    add_invalid_interval = runner.invoke(
        main_mod.app,
        ["schedule", "add", "--name", "x", "--prompt", "y", "--interval", "30"],
    )
    add_invalid_datetime = runner.invoke(
        main_mod.app,
        ["schedule", "add", "--name", "x", "--prompt", "y", "--once", "not-a-date"],
    )
    add_past_once = runner.invoke(
        main_mod.app,
        ["schedule", "add", "--name", "x", "--prompt", "y", "--once", "2020-01-01T00:00:00"],
    )
    remove_missing = runner.invoke(main_mod.app, ["schedule", "remove", "missing"])
    enable_missing = runner.invoke(main_mod.app, ["schedule", "enable", "missing"])
    disable_missing = runner.invoke(main_mod.app, ["schedule", "disable", "missing"])
    history_empty = runner.invoke(main_mod.app, ["schedule", "history"])
    list_empty = runner.invoke(main_mod.app, ["schedule", "list"])
    auto_enable = runner.invoke(main_mod.app, ["autostart", "enable", "--adapter", "feishu"])
    auto_disable = runner.invoke(main_mod.app, ["autostart", "disable", "--adapter", "feishu"])
    auto_status = runner.invoke(main_mod.app, ["autostart", "status", "--adapter", "feishu"])

    assert add_invalid_choice.exit_code == 1
    assert "exactly one" in add_invalid_choice.output
    assert add_invalid_interval.exit_code == 1
    assert ">= 60" in add_invalid_interval.output
    assert add_invalid_datetime.exit_code == 1
    assert "invalid datetime format" in add_invalid_datetime.output.lower()
    assert add_past_once.exit_code == 1
    assert "must be in the future" in add_past_once.output.lower()
    assert remove_missing.exit_code == 1
    assert "no task with id" in remove_missing.output
    assert enable_missing.exit_code == 1
    assert disable_missing.exit_code == 1
    assert history_empty.exit_code == 0
    assert "No execution history" in history_empty.output
    assert list_empty.exit_code == 0
    assert "No scheduled tasks" in list_empty.output
    assert "enable:feishu" in auto_enable.output
    assert "disable:feishu" in auto_disable.output
    assert "status:feishu" in auto_status.output
