from __future__ import annotations

import sys
import time

from hermit.core.sandbox import CommandSandbox
from hermit.core.tools import create_builtin_tool_registry


def _wait_for_poll(
    sandbox: CommandSandbox,
    job_id: str,
    *,
    timeout: float,
    predicate,
) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        poll = sandbox.poll(job_id)
        if predicate(poll):
            return poll
        time.sleep(0.02)
    return None


def test_builtin_tools_can_read_and_write_workspace_files(tmp_path) -> None:
    registry = create_builtin_tool_registry(tmp_path, CommandSandbox(mode="l0", cwd=tmp_path))

    registry.call("write_file", {"path": "notes/test.txt", "content": "hello"})
    content = registry.call("read_file", {"path": "notes/test.txt"})

    assert content == "hello"


def test_builtin_tools_block_workspace_escape(tmp_path) -> None:
    registry = create_builtin_tool_registry(tmp_path, CommandSandbox(mode="l0", cwd=tmp_path))

    try:
        registry.call("read_file", {"path": "../secret.txt"})
    except ValueError as exc:
        assert "escapes workspace" in str(exc)
    else:
        raise AssertionError("Expected workspace escape error")


def test_builtin_bash_tool_returns_command_result(tmp_path) -> None:
    registry = create_builtin_tool_registry(tmp_path, CommandSandbox(mode="l0", cwd=tmp_path))

    result = registry.call("bash", {"command": "printf 'ok'"})

    assert result["returncode"] == 0
    assert result["stdout"] == "ok"


def test_builtin_config_tools_can_manage_hermit_directory(tmp_path) -> None:
    config_dir = tmp_path / ".hermit"
    registry = create_builtin_tool_registry(
        tmp_path,
        CommandSandbox(mode="l0", cwd=tmp_path),
        config_root_dir=config_dir,
    )

    registry.call("write_hermit_file", {"path": "rules/a.md", "content": "rule"})
    content = registry.call("read_hermit_file", {"path": "rules/a.md"})
    listing = registry.call("list_hermit_files", {"path": "rules"})

    assert content == "rule"
    assert listing == ["rules/a.md"]


def test_builtin_config_tools_block_escape(tmp_path) -> None:
    registry = create_builtin_tool_registry(
        tmp_path,
        CommandSandbox(mode="l0", cwd=tmp_path),
        config_root_dir=tmp_path / ".hermit",
    )

    try:
        registry.call("read_hermit_file", {"path": "../secret.txt"})
    except ValueError as exc:
        assert "escapes workspace" in str(exc)
    else:
        raise AssertionError("Expected Hermit config escape error")


def test_read_hermit_file_returns_message_for_missing_file(tmp_path) -> None:
    registry = create_builtin_tool_registry(
        tmp_path,
        CommandSandbox(mode="l0", cwd=tmp_path),
        config_root_dir=tmp_path / ".hermit",
    )

    content = registry.call("read_hermit_file", {"path": "memory/session_state.json"})

    assert content == "File not found: memory/session_state.json"


def test_builtin_tools_localize_descriptions_and_messages(tmp_path) -> None:
    registry = create_builtin_tool_registry(
        tmp_path,
        CommandSandbox(mode="l0", cwd=tmp_path),
        config_root_dir=tmp_path / ".hermit",
        locale="zh-CN",
    )

    read_tool = registry.get("read_file")
    missing = registry.call("read_hermit_file", {"path": "memory/session_state.json"})

    assert read_tool.description == "读取工作区内的 UTF-8 文本文件。"
    assert read_tool.input_schema["properties"]["path"]["description"] == "要读取的工作区相对路径。"
    assert missing == "未找到文件：memory/session_state.json"


def test_command_sandbox_observation_emits_progress_and_ready(tmp_path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path, timeout_seconds=0.05)
    command = (
        f"{sys.executable} -u -c "
        '"import sys,time; '
        "print('Booting server'); sys.stdout.flush(); "
        "time.sleep(0.25); "
        "print('READY http://127.0.0.1:3000'); sys.stdout.flush(); "
        'time.sleep(0.4)"'
    )

    result = sandbox.run(
        {
            "command": command,
            "display_name": "Dev Server",
            "ready_return": True,
            "ready_patterns": [
                {
                    "pattern": r"READY (?P<url>https?://\S+)",
                    "summary": "{display_name} ready at {url}",
                    "detail": "{line}",
                }
            ],
            "progress_patterns": [
                {
                    "pattern": r"Booting server",
                    "phase": "starting",
                    "summary": "{display_name} is starting",
                    "progress_percent": 10,
                }
            ],
        }
    )

    assert "_hermit_observation" in result
    ticket = result["_hermit_observation"]

    starting = _wait_for_poll(
        sandbox,
        ticket["job_id"],
        timeout=0.4,
        predicate=lambda poll: poll.get("progress", {}).get("phase") == "starting",
    )
    assert starting is not None
    assert starting["status"] == "observing"
    assert starting["progress"]["phase"] == "starting"
    assert starting["progress"]["summary"] == "Dev Server is starting"

    ready = _wait_for_poll(
        sandbox,
        ticket["job_id"],
        timeout=0.9,
        predicate=lambda poll: poll.get("progress", {}).get("ready") is True,
    )
    assert ready is not None
    assert ready["status"] == "observing"
    assert ready["progress"]["ready"] is True
    assert ready["progress"]["summary"] == "Dev Server ready at http://127.0.0.1:3000"
    assert ready["result"]["ready"] is True


def test_command_sandbox_observation_uses_coarse_running_progress_without_metadata(
    tmp_path,
) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path, timeout_seconds=0.05)
    command = f'{sys.executable} -u -c "import time; time.sleep(0.2)"'

    result = sandbox.run({"command": command, "display_name": "Background Task"})

    assert "_hermit_observation" in result
    ticket = result["_hermit_observation"]

    observing = _wait_for_poll(
        sandbox,
        ticket["job_id"],
        timeout=0.25,
        predicate=lambda poll: (
            poll.get("status") == "observing" and poll.get("progress", {}).get("phase") == "running"
        ),
    )
    assert observing is not None
    assert observing["progress"]["summary"] == "Background Task is still running."

    completed = _wait_for_poll(
        sandbox,
        ticket["job_id"],
        timeout=0.5,
        predicate=lambda poll: poll.get("status") == "completed",
    )
    assert completed is not None
    assert completed["result"]["returncode"] == 0


def test_command_sandbox_coarse_observation_only_extends_completion_once(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("hermit.core.sandbox._COARSE_OBSERVATION_GRACE_SECONDS", 1.0)
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path, timeout_seconds=0.05)
    command = f'{sys.executable} -u -c "import time; time.sleep(0.11)"'

    result = sandbox.run({"command": command, "display_name": "Short Task"})

    assert "_hermit_observation" in result
    ticket = result["_hermit_observation"]

    time.sleep(0.12)

    observing = sandbox.poll(ticket["job_id"])
    assert observing["status"] == "observing"
    assert observing["progress"]["phase"] == "running"
    assert observing["progress"]["summary"] == "Short Task is still running."

    completed = sandbox.poll(ticket["job_id"])
    assert completed["status"] == "completed"
    assert completed["result"]["returncode"] == 0

    repeated = sandbox.poll(ticket["job_id"])
    assert repeated["status"] == "completed"
    assert repeated["result"]["returncode"] == 0
