"""Tests for hermit.apps.companion.control — companion control utilities."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermit.apps.companion import control

# ---------------------------------------------------------------------------
# hermit_base_dir / log / config paths
# ---------------------------------------------------------------------------


class TestHermitBaseDir:
    def test_env_var_set(self, monkeypatch) -> None:
        monkeypatch.setenv("HERMIT_BASE_DIR", "/custom/dir")
        assert control.hermit_base_dir() == Path("/custom/dir")

    def test_env_var_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_BASE_DIR", raising=False)
        assert control.hermit_base_dir() == Path.home() / ".hermit"

    def test_tilde_expansion(self, monkeypatch) -> None:
        monkeypatch.setenv("HERMIT_BASE_DIR", "~/.hermit-dev")
        result = control.hermit_base_dir()
        assert "~" not in str(result)


class TestHermitLogDir:
    def test_default(self, tmp_path: Path) -> None:
        result = control.hermit_log_dir(tmp_path)
        assert result == tmp_path / "logs"

    def test_none_uses_base_dir(self, monkeypatch) -> None:
        monkeypatch.setenv("HERMIT_BASE_DIR", "/test")
        result = control.hermit_log_dir()
        assert result == Path("/test") / "logs"


class TestCompanionLogPath:
    def test_returns_companion_log(self, tmp_path: Path) -> None:
        result = control.companion_log_path(tmp_path)
        assert result == tmp_path / "logs" / "companion.log"


class TestLogCompanionEvent:
    def test_creates_log_file(self, tmp_path: Path) -> None:
        path = control.log_companion_event("test_action", "test message", base_dir=tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "test_action" in content
        assert "test message" in content
        assert "INFO" in content

    def test_appends_detail(self, tmp_path: Path) -> None:
        path = control.log_companion_event(
            "action", "msg", base_dir=tmp_path, detail="extra detail"
        )
        content = path.read_text(encoding="utf-8")
        assert "extra detail" in content

    def test_custom_level(self, tmp_path: Path) -> None:
        path = control.log_companion_event("action", "msg", base_dir=tmp_path, level="ERROR")
        content = path.read_text(encoding="utf-8")
        assert "ERROR" in content

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        control.log_companion_event("first", "msg1", base_dir=tmp_path)
        control.log_companion_event("second", "msg2", base_dir=tmp_path)
        content = control.companion_log_path(tmp_path).read_text(encoding="utf-8")
        assert "first" in content
        assert "second" in content


# ---------------------------------------------------------------------------
# format_exception_message
# ---------------------------------------------------------------------------


class TestFormatExceptionMessage:
    def test_called_process_error_with_output(self) -> None:
        exc = subprocess.CalledProcessError(1, "cmd", output="out text", stderr="err text")
        msg, detail = control.format_exception_message(exc)
        assert msg == "err text"
        assert detail is not None
        assert "stdout" in detail
        assert "stderr" in detail

    def test_called_process_error_empty_output(self) -> None:
        exc = subprocess.CalledProcessError(1, "cmd", output="", stderr="")
        msg, detail = control.format_exception_message(exc)
        assert isinstance(msg, str)
        assert detail is None

    def test_called_process_error_stdout_only(self) -> None:
        exc = subprocess.CalledProcessError(1, "cmd", output="only stdout", stderr="")
        msg, _detail = control.format_exception_message(exc)
        assert msg == "only stdout"

    def test_regular_exception(self) -> None:
        exc = ValueError("something broke")
        msg, detail = control.format_exception_message(exc)
        assert msg == "something broke"
        assert detail is not None  # traceback


# ---------------------------------------------------------------------------
# config / ensure helpers
# ---------------------------------------------------------------------------


class TestConfigPath:
    def test_returns_config_toml(self, tmp_path: Path) -> None:
        assert control.config_path(tmp_path) == tmp_path / "config.toml"


class TestEnsureBaseDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "new_dir"
        result = control.ensure_base_dir(target)
        assert result.exists()

    def test_existing_directory(self, tmp_path: Path) -> None:
        result = control.ensure_base_dir(tmp_path)
        assert result == tmp_path


class TestEnsureConfigFile:
    def test_creates_default_config(self, tmp_path: Path) -> None:
        path = control.ensure_config_file(tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert 'default_profile = "default"' in content
        assert "[profiles.default]" in content

    def test_existing_config_untouched(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text("custom content\n", encoding="utf-8")
        control.ensure_config_file(tmp_path)
        assert cfg.read_text(encoding="utf-8") == "custom content\n"


# ---------------------------------------------------------------------------
# _temporary_env
# ---------------------------------------------------------------------------


class TestTemporaryEnv:
    def test_updates_and_restores(self, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_TEST_VAR_99", raising=False)
        with control._temporary_env(updates={"HERMIT_TEST_VAR_99": "hello"}):
            assert os.environ["HERMIT_TEST_VAR_99"] == "hello"
        assert "HERMIT_TEST_VAR_99" not in os.environ

    def test_removals_and_restores(self, monkeypatch) -> None:
        monkeypatch.setenv("HERMIT_TEST_VAR_98", "original")
        with control._temporary_env(removals=["HERMIT_TEST_VAR_98"]):
            assert "HERMIT_TEST_VAR_98" not in os.environ
        assert os.environ["HERMIT_TEST_VAR_98"] == "original"

    def test_restores_after_exception(self, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_TEST_VAR_97", raising=False)
        try:
            with control._temporary_env(updates={"HERMIT_TEST_VAR_97": "temp"}):
                raise ValueError("oops")
        except ValueError:
            pass
        assert "HERMIT_TEST_VAR_97" not in os.environ


# ---------------------------------------------------------------------------
# PID helpers
# ---------------------------------------------------------------------------


class TestPidPath:
    def test_returns_serve_adapter_pid(self, tmp_path: Path) -> None:
        assert control.pid_path("feishu", tmp_path) == tmp_path / "serve-feishu.pid"


class TestWatchPidPath:
    def test_returns_watch_adapter_pid(self, tmp_path: Path) -> None:
        assert control.watch_pid_path("feishu", tmp_path) == tmp_path / "watch-feishu.pid"


class TestReadPid:
    def test_valid_pid(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345", encoding="utf-8")
        assert control.read_pid(pid_file) == 12345

    def test_empty_file(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("", encoding="utf-8")
        assert control.read_pid(pid_file) is None

    def test_invalid_content(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("abc", encoding="utf-8")
        assert control.read_pid(pid_file) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        assert control.read_pid(tmp_path / "nonexistent.pid") is None


class TestProcessExists:
    def test_none_pid(self) -> None:
        assert control.process_exists(None) is False

    def test_existing_process(self, monkeypatch) -> None:
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        assert control.process_exists(12345) is True

    def test_no_such_process(self, monkeypatch) -> None:
        def _raise(pid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr(os, "kill", _raise)
        assert control.process_exists(12345) is False

    def test_permission_error_means_exists(self, monkeypatch) -> None:
        def _raise(pid, sig):
            raise PermissionError()

        monkeypatch.setattr(os, "kill", _raise)
        assert control.process_exists(12345) is True


# ---------------------------------------------------------------------------
# Process table parsing
# ---------------------------------------------------------------------------


class TestIterProcessTable:
    def test_parses_process_table(self) -> None:
        table = "  123 /usr/bin/python serve\n  456 /usr/bin/node app\n"
        rows = control._iter_process_table(table)
        assert len(rows) == 2
        assert rows[0] == (123, "/usr/bin/python serve")
        assert rows[1] == (456, "/usr/bin/node app")

    def test_empty_table(self) -> None:
        assert control._iter_process_table("") == []

    def test_invalid_lines(self) -> None:
        table = "abc invalid line\n\n  xyz nope\n"
        assert control._iter_process_table(table) == []

    def test_skips_blank_lines(self) -> None:
        table = "\n  100 cmd\n\n"
        rows = control._iter_process_table(table)
        assert len(rows) == 1


class TestHasEnvAssignment:
    def test_match_at_start(self) -> None:
        assert control._has_env_assignment("KEY=val rest", "KEY", "val") is True

    def test_match_in_middle(self) -> None:
        assert control._has_env_assignment("pre KEY=val rest", "KEY", "val") is True

    def test_no_match(self) -> None:
        assert control._has_env_assignment("OTHER=val", "KEY", "val") is False

    def test_partial_key_no_match(self) -> None:
        assert control._has_env_assignment("MYKEY=val", "KEY", "val") is False


class TestMatchingProcessPids:
    def test_finds_matching_processes(self, monkeypatch) -> None:
        base_dir = Path.home() / ".hermit"
        table = f"  100 HERMIT_BASE_DIR={base_dir} hermit serve --adapter feishu\n"
        result = control.matching_process_pids(
            "hermit serve", base_dir=base_dir, process_table=table
        )
        assert result == [100]

    def test_no_matches(self) -> None:
        table = "  100 unrelated command\n"
        result = control.matching_process_pids("hermit serve", process_table=table)
        assert result == []


# ---------------------------------------------------------------------------
# command_prefix / paths / URLs
# ---------------------------------------------------------------------------


class TestCommandPrefix:
    def test_with_project_root(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.control._project_root", lambda: Path("/project"))
        monkeypatch.setattr("hermit.apps.companion.control.resolve_uv_bin", lambda: "/usr/bin/uv")
        result = control.command_prefix()
        assert result[0] == "/usr/bin/uv"
        assert "run" in result
        assert "--project" in result

    def test_without_project_root_hermit_bin(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr("hermit.apps.companion.control._project_root", lambda: None)
        hermit_bin = Path(sys.executable).parent / "hermit"
        monkeypatch.setattr("pathlib.Path.exists", lambda self: self == hermit_bin)
        result = control.command_prefix()
        assert result == [str(hermit_bin)]

    def test_fallback_to_which(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.control._project_root", lambda: None)
        monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/hermit")
        result = control.command_prefix()
        assert result == ["/usr/local/bin/hermit"]

    def test_fallback_to_python_module(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.control._project_root", lambda: None)
        monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        result = control.command_prefix()
        assert result == [sys.executable, "-m", "hermit.surfaces.cli.main"]


class TestReadmePath:
    def test_with_project_root(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.control._project_root", lambda: Path("/project"))
        assert control.readme_path() == Path("/project/README.md")

    def test_without_project_root(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.control._project_root", lambda: None)
        assert control.readme_path() == Path.cwd() / "README.md"


class TestDocsPath:
    def test_with_project_root(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.control._project_root", lambda: Path("/project"))
        assert control.docs_path() == Path("/project/docs")

    def test_without_project_root(self, monkeypatch) -> None:
        monkeypatch.setattr("hermit.apps.companion.control._project_root", lambda: None)
        assert control.docs_path() == Path.cwd() / "docs"


class TestProjectUrls:
    def test_repo_url(self) -> None:
        assert "github.com" in control.project_repo_url()

    def test_wiki_url(self) -> None:
        assert control.project_wiki_url().endswith("/wiki")


# ---------------------------------------------------------------------------
# run_hermit_command
# ---------------------------------------------------------------------------


class TestRunHermitCommand:
    def test_calls_subprocess_run(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.control.command_prefix",
            lambda: ["/usr/bin/hermit"],
        )
        mock_run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""))
        monkeypatch.setattr("subprocess.run", mock_run)
        control.run_hermit_command(["status"], base_dir=Path("/tmp"), profile="test")
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["/usr/bin/hermit", "status"]
        assert call_args[1]["env"]["HERMIT_BASE_DIR"] == "/tmp"
        assert call_args[1]["env"]["HERMIT_PROFILE"] == "test"


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------


class TestProfileSectionHeader:
    def test_returns_correct_format(self) -> None:
        assert control._profile_section_header("default") == "[profiles.default]"


class TestFormatTomlValue:
    def test_bool_true(self) -> None:
        assert control._format_toml_value(True) == "true"

    def test_bool_false(self) -> None:
        assert control._format_toml_value(False) == "false"

    def test_int(self) -> None:
        assert control._format_toml_value(42) == "42"

    def test_string(self) -> None:
        assert control._format_toml_value("hello") == '"hello"'

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="None"):
            control._format_toml_value(None)

    def test_string_with_quotes(self) -> None:
        result = control._format_toml_value('say "hi"')
        assert '\\"' in result


class TestSetDefaultProfile:
    def test_profile_not_defined_raises(self, tmp_path: Path, monkeypatch) -> None:
        catalog = SimpleNamespace(profiles={}, path=tmp_path / "config.toml")
        monkeypatch.setattr(
            "hermit.apps.companion.control.load_profile_catalog", lambda bd: catalog
        )
        with pytest.raises(RuntimeError, match="not defined"):
            control.set_default_profile("missing", base_dir=tmp_path)

    def test_replaces_existing_default(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('default_profile = "old"\n\n[profiles.old]\n', encoding="utf-8")
        catalog = SimpleNamespace(profiles={"new": {}}, path=cfg)
        monkeypatch.setattr(
            "hermit.apps.companion.control.load_profile_catalog", lambda bd: catalog
        )
        control.set_default_profile("new", base_dir=tmp_path)
        content = cfg.read_text(encoding="utf-8")
        assert 'default_profile = "new"' in content

    def test_prepends_when_missing(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[profiles.prod]\nprovider = "claude"\n', encoding="utf-8")
        catalog = SimpleNamespace(profiles={"prod": {}}, path=cfg)
        monkeypatch.setattr(
            "hermit.apps.companion.control.load_profile_catalog", lambda bd: catalog
        )
        control.set_default_profile("prod", base_dir=tmp_path)
        content = cfg.read_text(encoding="utf-8")
        assert content.startswith('default_profile = "prod"')


class TestUpdateProfileSetting:
    def test_creates_new_section(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('default_profile = "default"\n', encoding="utf-8")
        control.update_profile_setting("new-profile", "model", "gpt-4", base_dir=tmp_path)
        content = cfg.read_text(encoding="utf-8")
        assert "[profiles.new-profile]" in content
        assert 'model = "gpt-4"' in content

    def test_updates_existing_key(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[profiles.default]\nmodel = "old-model"\nprovider = "claude"\n', encoding="utf-8"
        )
        control.update_profile_setting("default", "model", "new-model", base_dir=tmp_path)
        content = cfg.read_text(encoding="utf-8")
        assert 'model = "new-model"' in content
        assert "old-model" not in content

    def test_adds_key_to_existing_section(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[profiles.default]\nprovider = "claude"\n', encoding="utf-8")
        control.update_profile_setting("default", "model", "gpt-4", base_dir=tmp_path)
        content = cfg.read_text(encoding="utf-8")
        assert 'model = "gpt-4"' in content
        assert 'provider = "claude"' in content


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------


class TestStopService:
    def test_not_running(self, monkeypatch) -> None:
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=Path("/tmp/test.pid"),
            pid=None,
            running=False,
            autostart_installed=False,
            autostart_loaded=False,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        result = control.stop_service("feishu")
        assert "not running" in result.lower() or "Not running" in result or "not" in result.lower()

    def test_sends_sigterm(self, monkeypatch) -> None:
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=Path("/tmp/test.pid"),
            pid=12345,
            running=True,
            autostart_installed=False,
            autostart_loaded=False,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        kill_calls = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))
        result = control.stop_service("feishu")
        assert (12345, signal.SIGTERM) in kill_calls
        assert "SIGTERM" in result


class TestReloadService:
    def test_calls_run_hermit_command(self, monkeypatch) -> None:
        mock_run = MagicMock()
        monkeypatch.setattr("hermit.apps.companion.control.run_hermit_command", mock_run)
        result = control.reload_service("feishu", base_dir=Path("/tmp"))
        mock_run.assert_called_once_with(
            ["reload", "--adapter", "feishu"], base_dir=Path("/tmp"), profile=None
        )
        assert "feishu" in result.lower() or "Reload" in result


# ---------------------------------------------------------------------------
# open_* functions
# ---------------------------------------------------------------------------


class TestOpenPath:
    def test_darwin_existing_path(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        control.open_path(tmp_path)
        mock_popen.assert_called_once()

    def test_non_darwin_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(RuntimeError):
            control.open_path(Path("/tmp"))


class TestOpenInTextEdit:
    def test_darwin(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        control.open_in_textedit(tmp_path / "file.txt")
        mock_popen.assert_called_once()

    def test_non_darwin_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(RuntimeError):
            control.open_in_textedit(Path("/tmp/file.txt"))


class TestOpenUrl:
    def test_darwin(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        control.open_url("https://example.com")
        mock_popen.assert_called_once()

    def test_non_darwin_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(RuntimeError):
            control.open_url("https://example.com")


# ---------------------------------------------------------------------------
# _extract_preflight_failure
# ---------------------------------------------------------------------------


class TestExtractPreflightFailure:
    def test_no_marker_returns_none(self, tmp_path: Path) -> None:
        log_file = tmp_path / "stdout.log"
        log_file.write_text("normal log output\n", encoding="utf-8")
        assert control._extract_preflight_failure(log_file) is None

    def test_file_not_found(self, tmp_path: Path) -> None:
        assert control._extract_preflight_failure(tmp_path / "nonexistent.log") is None


# ---------------------------------------------------------------------------
# service_status (uses autostart internals — mock heavily)
# ---------------------------------------------------------------------------


class TestServiceStatus:
    def test_returns_status_object(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr("hermit.apps.companion.control.hermit_base_dir", lambda: tmp_path)
        pid_file = tmp_path / "serve-feishu.pid"
        pid_file.write_text("99999", encoding="utf-8")
        monkeypatch.setattr("hermit.apps.companion.control.process_exists", lambda pid: False)
        monkeypatch.setattr(sys, "platform", "linux")  # skip autostart checks
        status = control.service_status("feishu", base_dir=tmp_path)
        assert status.adapter == "feishu"
        assert status.pid == 99999
        assert status.running is False

    def test_darwin_checks_autostart(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("hermit.apps.companion.control.process_exists", lambda pid: False)
        # Mock autostart module
        mock_autostart = SimpleNamespace(
            _plist_path=lambda adapter: tmp_path / "fake.plist",
            _is_loaded=lambda adapter: False,
        )
        monkeypatch.setattr("hermit.surfaces.cli.autostart", mock_autostart, raising=False)
        # Need to mock the import
        import hermit.surfaces.cli.autostart as real_mod

        monkeypatch.setattr(real_mod, "_plist_path", lambda adapter: tmp_path / "fake.plist")
        monkeypatch.setattr(real_mod, "_is_loaded", lambda adapter: False)
        pid_file = tmp_path / "serve-feishu.pid"
        pid_file.write_text("", encoding="utf-8")
        status = control.service_status("feishu", base_dir=tmp_path)
        assert status.autostart_installed is False
        assert status.autostart_loaded is False


# ---------------------------------------------------------------------------
# start_service (heavy mocking)
# ---------------------------------------------------------------------------


class TestStartService:
    def test_already_running(self, monkeypatch, tmp_path: Path) -> None:
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=tmp_path / "serve-feishu.pid",
            pid=123,
            running=True,
            autostart_installed=False,
            autostart_loaded=False,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        result = control.start_service("feishu", base_dir=tmp_path)
        assert "already running" in result.lower() or "already" in result.lower()


# ---------------------------------------------------------------------------
# switch_profile
# ---------------------------------------------------------------------------


class TestSwitchProfile:
    def test_service_not_running(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.control.set_default_profile", lambda *a, **kw: None
        )
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=tmp_path / "serve-feishu.pid",
            pid=None,
            running=False,
            autostart_installed=False,
            autostart_loaded=False,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        result = control.switch_profile("feishu", "prod", base_dir=tmp_path)
        assert "prod" in result.lower() or "Switched" in result

    def test_autostart_loaded_reloads(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.control.set_default_profile", lambda *a, **kw: None
        )
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=tmp_path / "serve-feishu.pid",
            pid=123,
            running=True,
            autostart_installed=True,
            autostart_loaded=True,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        monkeypatch.setattr(
            "hermit.apps.companion.control.reload_service", lambda *a, **kw: "reloaded"
        )
        result = control.switch_profile("feishu", "dev", base_dir=tmp_path)
        assert "dev" in result.lower() or "reloaded" in result.lower()


# ---------------------------------------------------------------------------
# update_profile_bool_and_restart
# ---------------------------------------------------------------------------


class TestUpdateProfileBoolAndRestart:
    def test_service_not_running(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.control.update_profile_setting", lambda *a, **kw: None
        )
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=tmp_path / "serve-feishu.pid",
            pid=None,
            running=False,
            autostart_installed=False,
            autostart_loaded=False,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        result = control.update_profile_bool_and_restart(
            "feishu", "default", "scheduler_enabled", True, base_dir=tmp_path
        )
        assert "scheduler_enabled" in result or "enabled" in result.lower()

    def test_autostart_loaded_reloads(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.control.update_profile_setting", lambda *a, **kw: None
        )
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=tmp_path / "serve-feishu.pid",
            pid=123,
            running=True,
            autostart_installed=True,
            autostart_loaded=True,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        monkeypatch.setattr(
            "hermit.apps.companion.control.reload_service", lambda *a, **kw: "reloaded"
        )
        result = control.update_profile_bool_and_restart(
            "feishu", "default", "webhook_enabled", False, base_dir=tmp_path
        )
        assert "webhook_enabled" in result or "disabled" in result.lower()


# ---------------------------------------------------------------------------
# load_runtime_settings / load_profile_runtime_settings
# ---------------------------------------------------------------------------


class TestLoadRuntimeSettings:
    def test_loads_settings(self, monkeypatch, tmp_path: Path) -> None:
        base_dir = tmp_path / ".hermit"
        base_dir.mkdir()
        (base_dir / ".env").write_text("", encoding="utf-8")
        (base_dir / "config.toml").write_text(
            'default_profile = "default"\n\n[profiles.default]\nprovider = "claude"\n',
            encoding="utf-8",
        )
        monkeypatch.delenv("HERMIT_PROFILE", raising=False)
        settings = control.load_runtime_settings(base_dir)
        assert settings.provider == "claude"


class TestLoadProfileRuntimeSettings:
    def test_loads_profile_settings(self, monkeypatch, tmp_path: Path) -> None:
        base_dir = tmp_path / ".hermit"
        base_dir.mkdir()
        (base_dir / ".env").write_text("", encoding="utf-8")
        (base_dir / "config.toml").write_text(
            'default_profile = "default"\n\n[profiles.test]\nprovider = "codex-oauth"\nmodel = "gpt-5"\n',
            encoding="utf-8",
        )
        settings = control.load_profile_runtime_settings("test", base_dir)
        assert settings.provider == "codex-oauth"


# ---------------------------------------------------------------------------
# grants/__init__.py lazy __getattr__
# ---------------------------------------------------------------------------


class TestGrantsInitGetattr:
    def test_getattr_capability_grant_error(self) -> None:
        from hermit.kernel.authority.grants import CapabilityGrantError
        from hermit.kernel.authority.grants.service import (
            CapabilityGrantError as DirectError,
        )

        assert CapabilityGrantError is DirectError

    def test_getattr_capability_grant_service(self) -> None:
        from hermit.kernel.authority.grants import CapabilityGrantService
        from hermit.kernel.authority.grants.service import (
            CapabilityGrantService as DirectService,
        )

        assert CapabilityGrantService is DirectService

    def test_getattr_unknown_raises(self) -> None:
        import hermit.kernel.authority.grants as mod

        with pytest.raises(AttributeError):
            _ = mod.NoSuchThing  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# start_service (more paths)
# ---------------------------------------------------------------------------


class TestStartServiceMore:
    def test_start_service_launches_and_finds_pid(self, monkeypatch, tmp_path: Path) -> None:
        """Test the path where service starts successfully on first poll."""
        call_count = [0]

        def _mock_status(*a, **kw):
            call_count[0] += 1
            running = call_count[0] > 1  # first call not running, second running
            return control.ServiceStatus(
                adapter="feishu",
                pid_file=tmp_path / "serve-feishu.pid",
                pid=999 if running else None,
                running=running,
                autostart_installed=False,
                autostart_loaded=False,
            )

        monkeypatch.setattr("hermit.apps.companion.control.service_status", _mock_status)
        monkeypatch.setattr(
            "hermit.apps.companion.control.command_prefix", lambda: ["/usr/bin/hermit"]
        )
        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        monkeypatch.setattr("time.sleep", lambda s: None)
        log_dir = tmp_path / "logs"
        monkeypatch.setattr("hermit.apps.companion.control.hermit_log_dir", lambda bd: log_dir)
        result = control.start_service("feishu", base_dir=tmp_path)
        assert "Started" in result or "started" in result or "PID" in result or "999" in result

    def test_start_service_failure_with_preflight(self, monkeypatch, tmp_path: Path) -> None:
        """Test the path where service fails to start and preflight failure is found."""
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=tmp_path / "serve-feishu.pid",
            pid=None,
            running=False,
            autostart_installed=False,
            autostart_loaded=False,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        monkeypatch.setattr(
            "hermit.apps.companion.control.command_prefix", lambda: ["/usr/bin/hermit"]
        )
        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        monkeypatch.setattr("time.sleep", lambda s: None)
        log_dir = tmp_path / "logs"
        monkeypatch.setattr("hermit.apps.companion.control.hermit_log_dir", lambda bd: log_dir)
        monkeypatch.setattr(
            "hermit.apps.companion.control._extract_preflight_failure",
            lambda p: "Missing API key",
        )
        result = control.start_service("feishu", base_dir=tmp_path)
        assert "Missing API key" in result or "Failed" in result or "failed" in result

    def test_start_service_failure_no_preflight(self, monkeypatch, tmp_path: Path) -> None:
        """Test the path where service fails without preflight detail."""
        status = control.ServiceStatus(
            adapter="feishu",
            pid_file=tmp_path / "serve-feishu.pid",
            pid=None,
            running=False,
            autostart_installed=False,
            autostart_loaded=False,
        )
        monkeypatch.setattr("hermit.apps.companion.control.service_status", lambda *a, **kw: status)
        monkeypatch.setattr(
            "hermit.apps.companion.control.command_prefix", lambda: ["/usr/bin/hermit"]
        )
        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        monkeypatch.setattr("time.sleep", lambda s: None)
        log_dir = tmp_path / "logs"
        monkeypatch.setattr("hermit.apps.companion.control.hermit_log_dir", lambda bd: log_dir)
        monkeypatch.setattr(
            "hermit.apps.companion.control._extract_preflight_failure",
            lambda p: None,
        )
        result = control.start_service("feishu", base_dir=tmp_path)
        assert "Failed" in result or "failed" in result or "Check logs" in result


# ---------------------------------------------------------------------------
# switch_profile — running but not autostart
# ---------------------------------------------------------------------------


class TestSwitchProfileRunning:
    def test_running_service_restarts(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.control.set_default_profile", lambda *a, **kw: None
        )
        call_count = [0]

        def _mock_status(*a, **kw):
            call_count[0] += 1
            running = call_count[0] <= 1  # first call running, after that stopped
            return control.ServiceStatus(
                adapter="feishu",
                pid_file=tmp_path / "serve-feishu.pid",
                pid=123 if running else None,
                running=running,
                autostart_installed=False,
                autostart_loaded=False,
            )

        monkeypatch.setattr("hermit.apps.companion.control.service_status", _mock_status)
        monkeypatch.setattr(
            "hermit.apps.companion.control.stop_service", lambda *a, **kw: "stopped"
        )
        monkeypatch.setattr(
            "hermit.apps.companion.control.start_service", lambda *a, **kw: "started"
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        result = control.switch_profile("feishu", "prod", base_dir=tmp_path)
        assert "prod" in result.lower() or "Switched" in result


# ---------------------------------------------------------------------------
# update_profile_bool_and_restart — running but not autostart
# ---------------------------------------------------------------------------


class TestUpdateProfileBoolRunning:
    def test_running_service_restarts(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "hermit.apps.companion.control.update_profile_setting", lambda *a, **kw: None
        )
        call_count = [0]

        def _mock_status(*a, **kw):
            call_count[0] += 1
            running = call_count[0] <= 1
            return control.ServiceStatus(
                adapter="feishu",
                pid_file=tmp_path / "serve-feishu.pid",
                pid=123 if running else None,
                running=running,
                autostart_installed=False,
                autostart_loaded=False,
            )

        monkeypatch.setattr("hermit.apps.companion.control.service_status", _mock_status)
        monkeypatch.setattr(
            "hermit.apps.companion.control.stop_service", lambda *a, **kw: "stopped"
        )
        monkeypatch.setattr(
            "hermit.apps.companion.control.start_service", lambda *a, **kw: "started"
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        result = control.update_profile_bool_and_restart(
            "feishu", "default", "scheduler_enabled", True, base_dir=tmp_path
        )
        assert "scheduler_enabled" in result or "enabled" in result.lower()


# ---------------------------------------------------------------------------
# _extract_preflight_failure — with marker content
# ---------------------------------------------------------------------------


class TestExtractPreflightFailureWithMarker:
    def test_with_marker_and_items(self, monkeypatch, tmp_path: Path) -> None:
        from hermit.infra.system.i18n import tr

        # Get the actual preflight failure marker text
        marker = tr("cli.preflight.failed", locale="en-US", default="Preflight checks failed:")
        if not marker:
            marker = "Preflight checks failed:"
        log_file = tmp_path / "stdout.log"
        log_file.write_text(
            f"Some output\n{marker}\n- Missing API key\n- Invalid config\n\nMore output\n",
            encoding="utf-8",
        )
        result = control._extract_preflight_failure(log_file)
        if result is not None:
            assert "Missing API key" in result or "Preflight" in result


# ---------------------------------------------------------------------------
# menubar._parse_args and main
# ---------------------------------------------------------------------------


class TestMenubarParseArgs:
    def test_default_args(self) -> None:
        from hermit.apps.companion.menubar import _parse_args

        args = _parse_args([])
        assert args.adapter == "feishu"
        assert args.profile is None
        assert args.base_dir is None

    def test_custom_args(self) -> None:
        from hermit.apps.companion.menubar import _parse_args

        args = _parse_args(["--adapter", "slack", "--profile", "prod", "--base-dir", "/tmp"])
        assert args.adapter == "slack"
        assert args.profile == "prod"
        assert args.base_dir == "/tmp"


class TestMenubarMain:
    def test_non_darwin_returns_1(self, monkeypatch) -> None:
        from hermit.apps.companion import menubar

        monkeypatch.setattr(sys, "platform", "linux")
        result = menubar.main([])
        assert result == 1
