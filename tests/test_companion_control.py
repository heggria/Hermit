from __future__ import annotations

from pathlib import Path

from hermit.companion import control


def test_load_runtime_settings_ignores_profile_env_override(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    (base_dir / ".env").write_text("", encoding="utf-8")
    (base_dir / "config.toml").write_text(
        """
default_profile = "codex-oauth"

[profiles.codex-oauth]
provider = "codex-oauth"
model = "gpt-5.4"

[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_PROFILE", "claude-code")

    settings = control.load_runtime_settings(base_dir)

    assert settings.resolved_profile == "codex-oauth"
    assert settings.provider == "codex-oauth"
    assert settings.model == "gpt-5.4"


def test_load_profile_runtime_settings_resolves_requested_profile(
    tmp_path: Path, monkeypatch
) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    (base_dir / ".env").write_text("", encoding="utf-8")
    (base_dir / "config.toml").write_text(
        """
default_profile = "codex-oauth"

[profiles.codex-oauth]
provider = "codex-oauth"
model = "gpt-5.4"

[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"
claude_api_key = "sk-ant-test"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_PROFILE", "codex-oauth")

    settings = control.load_profile_runtime_settings("claude-code", base_dir)

    assert settings.resolved_profile == "claude-code"
    assert settings.provider == "claude"
    assert settings.model == "claude-sonnet-4-6"
    assert settings.has_auth is True


def test_set_default_profile_updates_existing_config(tmp_path: Path) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    config_path = base_dir / "config.toml"
    config_path.write_text(
        """
default_profile = "claude-code"

[profiles.codex-oauth]
provider = "codex-oauth"
model = "gpt-5.4"

[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    updated_path = control.set_default_profile("codex-oauth", base_dir=base_dir)

    assert updated_path == config_path
    assert 'default_profile = "codex-oauth"' in config_path.read_text(encoding="utf-8")


def test_update_profile_setting_updates_existing_bool_field(tmp_path: Path) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    config_path = base_dir / "config.toml"
    config_path.write_text(
        """
default_profile = "claude-code"

[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"
scheduler_enabled = true
""".strip()
        + "\n",
        encoding="utf-8",
    )

    updated_path = control.update_profile_setting(
        "claude-code",
        "scheduler_enabled",
        False,
        base_dir=base_dir,
    )

    assert updated_path == config_path
    assert "scheduler_enabled = false" in config_path.read_text(encoding="utf-8")


def test_update_profile_setting_adds_missing_setting_to_profile_section(tmp_path: Path) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    config_path = base_dir / "config.toml"
    config_path.write_text(
        """
default_profile = "claude-code"

[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    control.update_profile_setting(
        "claude-code",
        "webhook_enabled",
        False,
        base_dir=base_dir,
    )

    text = config_path.read_text(encoding="utf-8")
    assert "[profiles.claude-code]" in text
    assert "webhook_enabled = false" in text


def test_log_companion_event_writes_log_file(tmp_path: Path) -> None:
    base_dir = tmp_path / ".hermit-dev"

    path = control.log_companion_event(
        "start_service",
        "Started Hermit service for 'feishu' (PID 123).",
        base_dir=base_dir,
    )

    text = path.read_text(encoding="utf-8")
    assert path == base_dir / "logs" / "companion.log"
    assert "INFO start_service" in text
    assert "Started Hermit service for 'feishu' (PID 123)." in text


def test_read_pid_rejects_invalid_content(tmp_path: Path) -> None:
    path = tmp_path / "serve.pid"
    path.write_text("abc", encoding="utf-8")

    assert control.read_pid(path) is None


def test_service_status_reports_pid_without_autostart(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir()
    pid_file = base_dir / "serve-feishu.pid"
    pid_file.write_text("123", encoding="utf-8")
    monkeypatch.setattr(control, "process_exists", lambda pid: pid == 123)
    monkeypatch.setattr(control.sys, "platform", "linux")

    status = control.service_status("feishu", base_dir=base_dir)

    assert status.pid_file == pid_file
    assert status.pid == 123
    assert status.running is True
    assert status.autostart_installed is False
    assert status.autostart_loaded is False


def test_ensure_config_file_writes_template(tmp_path: Path) -> None:
    path = control.ensure_config_file(tmp_path / ".hermit")

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert 'default_profile = "default"' in text
    assert "[profiles.default]" in text


def test_project_reference_paths_point_to_repo_files() -> None:
    assert control.readme_path().name == "README.md"
    assert control.readme_path().exists()
    assert control.docs_path().name == "docs"
    assert control.docs_path().exists()
    assert control.project_repo_url().endswith("/heggria/Hermit")
    assert control.project_wiki_url().endswith("/heggria/Hermit/wiki")


def test_start_service_reports_failure_when_process_does_not_stay_up(
    tmp_path: Path, monkeypatch
) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()

    class _FakeProc:
        pass

    monkeypatch.setattr(control, "command_prefix", lambda: ["python", "-m", "hermit.main"])
    monkeypatch.setattr(control.subprocess, "Popen", lambda *args, **kwargs: _FakeProc())
    monkeypatch.setattr(control.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        control,
        "service_status",
        lambda adapter, base_dir=None: control.ServiceStatus(
            adapter=adapter,
            pid_file=Path(base_dir or tmp_path) / f"serve-{adapter}.pid",
            pid=None,
            running=False,
            autostart_installed=False,
            autostart_loaded=False,
        ),
    )

    message = control.start_service("feishu", base_dir=base_dir)

    assert message.startswith("Failed to start Hermit service for 'feishu'.")
    assert str(base_dir / "logs" / "feishu-menubar-stdout.log") in message


def test_start_service_surfaces_preflight_failure_from_log(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit-dev"
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "feishu-menubar-stdout.log").write_text(
        """
启动前检查未通过：
  - 缺少 Claude 鉴权。请设置 `HERMIT_CLAUDE_API_KEY`。
""".strip()
        + "\n",
        encoding="utf-8",
    )

    class _FakeProc:
        pass

    monkeypatch.setattr(control, "command_prefix", lambda: ["python", "-m", "hermit.main"])
    monkeypatch.setattr(control.subprocess, "Popen", lambda *args, **kwargs: _FakeProc())
    monkeypatch.setattr(control.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        control,
        "service_status",
        lambda adapter, base_dir=None: control.ServiceStatus(
            adapter=adapter,
            pid_file=Path(base_dir or tmp_path) / f"serve-{adapter}.pid",
            pid=None,
            running=False,
            autostart_installed=False,
            autostart_loaded=False,
        ),
    )

    message = control.start_service("feishu", base_dir=base_dir)

    assert "Preflight failed: 缺少 Claude 鉴权。" in message


def test_switch_profile_restarts_running_service(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    (base_dir / "config.toml").write_text(
        """
default_profile = "claude-code"

[profiles.codex-oauth]
provider = "codex-oauth"
model = "gpt-5.4"

[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    statuses = iter(
        [
            control.ServiceStatus("feishu", base_dir / "serve-feishu.pid", 123, True, False, False),
            control.ServiceStatus(
                "feishu", base_dir / "serve-feishu.pid", 123, False, False, False
            ),
        ]
    )
    monkeypatch.setattr(control, "service_status", lambda adapter, base_dir=None: next(statuses))
    monkeypatch.setattr(control, "stop_service", lambda adapter, base_dir=None: "stopped")
    monkeypatch.setattr(
        control,
        "start_service",
        lambda adapter, base_dir=None, profile=None: (
            "Started Hermit service for 'feishu' (PID 456)."
        ),
    )
    monkeypatch.setattr(control.time, "sleep", lambda _seconds: None)

    message = control.switch_profile("feishu", "codex-oauth", base_dir=base_dir)

    assert "Switched default profile to 'codex-oauth'." in message
    assert "Started Hermit service for 'feishu' (PID 456)." in message
    assert 'default_profile = "codex-oauth"' in (base_dir / "config.toml").read_text(
        encoding="utf-8"
    )


def test_update_profile_bool_and_restart_restarts_running_service(
    tmp_path: Path, monkeypatch
) -> None:
    base_dir = tmp_path / ".hermit-dev"
    base_dir.mkdir()
    config_path = base_dir / "config.toml"
    config_path.write_text(
        """
default_profile = "claude-code"

[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"
scheduler_enabled = true
""".strip()
        + "\n",
        encoding="utf-8",
    )

    statuses = iter(
        [
            control.ServiceStatus("feishu", base_dir / "serve-feishu.pid", 123, True, False, False),
            control.ServiceStatus(
                "feishu", base_dir / "serve-feishu.pid", 123, False, False, False
            ),
        ]
    )
    monkeypatch.setattr(control, "service_status", lambda adapter, base_dir=None: next(statuses))
    monkeypatch.setattr(control, "stop_service", lambda adapter, base_dir=None: "stopped")
    monkeypatch.setattr(
        control,
        "start_service",
        lambda adapter, base_dir=None, profile=None: (
            "Started Hermit service for 'feishu' (PID 456)."
        ),
    )
    monkeypatch.setattr(control.time, "sleep", lambda _seconds: None)

    message = control.update_profile_bool_and_restart(
        "feishu",
        "claude-code",
        "scheduler_enabled",
        False,
        base_dir=base_dir,
    )

    assert "Set 'scheduler_enabled' to disabled for profile 'claude-code'." in message
    assert "Started Hermit service for 'feishu' (PID 456)." in message
    assert "scheduler_enabled = false" in config_path.read_text(encoding="utf-8")
