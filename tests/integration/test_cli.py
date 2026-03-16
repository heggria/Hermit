from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from hermit.infra.system.i18n import tr
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService
from hermit.surfaces.cli.main import _build_serve_preflight, _notify_reload, app


@pytest.fixture(autouse=True)
def _force_cli_locale(monkeypatch):
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_init_creates_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".hermit" / "memory" / "memories.md").exists()
    assert (tmp_path / ".hermit" / "context.md").exists()
    assert (tmp_path / ".hermit" / "skills").exists()
    assert (tmp_path / ".hermit" / "plugins").exists()


def test_setup_writes_env_file(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([False, False])

    monkeypatch.setattr(main_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(main_mod.typer, "prompt", lambda *args, **kwargs: "sk-ant-test")

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert (tmp_path / ".hermit" / ".env").read_text(
        encoding="utf-8"
    ) == "ANTHROPIC_API_KEY=sk-ant-test\n"


def test_setup_shows_adapter_flag_in_next_steps(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([False, True])
    prompt_answers = iter(["sk-ant-test", "cli_xxx", "secret"])

    monkeypatch.setattr(main_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(main_mod.typer, "prompt", lambda *args, **kwargs: next(prompt_answers))

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert "hermit serve --adapter feishu" in result.output


def test_task_help_uses_locale_at_import_time(monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod

    runner = CliRunner()

    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    main_mod = importlib.reload(main_mod)
    zh_result = runner.invoke(main_mod.app, ["task", "--help"])
    assert zh_result.exit_code == 0
    assert "任务内核查看与审批命令" in zh_result.output

    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    main_mod = importlib.reload(main_mod)
    en_result = runner.invoke(main_mod.app, ["task", "--help"])
    assert en_result.exit_code == 0
    assert "Task kernel inspection and approval commands." in en_result.output


def test_serve_preflight_reports_missing_feishu_env(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    monkeypatch.setenv("HERMIT_PROVIDER", "claude")
    monkeypatch.delenv("HERMIT_PROFILE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 1
    assert "Hermit 启动前环境自检" in result.output
    assert "[OK] LLM 鉴权" in result.output
    assert "[缺失] 飞书 App ID" in result.output
    assert "[缺失] 飞书 App Secret" in result.output
    assert "启动前检查未通过" in result.output


def test_serve_preflight_shows_resolved_env_sources(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "secret")
    get_settings.cache_clear()

    serve_calls: list[tuple[str, str]] = []

    def fake_serve_loop(adapter: str, pid_file) -> None:
        serve_calls.append((adapter, str(pid_file)))

    monkeypatch.setattr(main_mod, "_serve_loop", fake_serve_loop)

    runner = CliRunner()
    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert "Hermit 启动前环境自检" in result.output
    assert "[OK] 飞书 App ID: HERMIT_FEISHU_APP_ID (shell 环境变量)" in result.output
    assert "[OK] 飞书 App Secret: HERMIT_FEISHU_APP_SECRET (shell 环境变量)" in result.output
    assert serve_calls and serve_calls[0][0] == "feishu"


def test_write_serve_status_persists_latest_status_and_history(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    settings = get_settings()
    main_mod._write_serve_status(
        settings,
        "feishu",
        phase="stopped",
        reason="signal",
        detail="SIGTERM received — stopping adapter for shutdown.",
        signal_name="SIGTERM",
        run_started_at="2026-03-12T14:03:09+08:00",
        append_history=True,
    )

    status_path = base_dir / "logs" / "serve-feishu-status.json"
    history_path = base_dir / "logs" / "serve-feishu-exit-history.jsonl"

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["phase"] == "stopped"
    assert status["reason"] == "signal"
    assert status["signal"] == "SIGTERM"
    assert status["run_started_at"] == "2026-03-12T14:03:09+08:00"

    history_lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(history_lines) == 1
    assert json.loads(history_lines[0])["detail"].startswith("SIGTERM received")


def test_serve_records_crash_status_when_serve_loop_raises(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "secret")
    get_settings.cache_clear()

    def fake_serve_loop(adapter: str, pid_file) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(main_mod, "_serve_loop", fake_serve_loop)

    runner = CliRunner()
    result = runner.invoke(app, ["serve"])

    status_path = base_dir / "logs" / "serve-feishu-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert not (base_dir / "serve-feishu.pid").exists()
    assert status["phase"] == "crashed"
    assert status["reason"] == "exception"
    assert status["exception_type"] == "RuntimeError"
    assert "boom" in status["exception_message"]


def test_profiles_list_reads_config_toml(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
default_profile = "codex-local"

[profiles.codex-local]
provider = "codex-oauth"
model = "gpt-5.4"

[profiles.claude-work]
provider = "claude"
model = "claude-3-7-sonnet-latest"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["profiles", "list"])

    assert result.exit_code == 0
    assert "codex-local（默认） 提供方=codex-oauth 模型=gpt-5.4" in result.output
    assert "claude-work 提供方=claude 模型=claude-3-7-sonnet-latest" in result.output


def test_profiles_list_reports_missing_config_toml(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["profiles", "list"])

    assert result.exit_code == 0
    assert (
        tr(
            "cli.profiles_list.no_config",
            locale="zh-CN",
            path=base_dir / "config.toml",
        )
        in result.output
    )


def test_config_show_includes_profile_and_auth_summary(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
default_profile = "shared"

[profiles.shared]
provider = "claude"
model = "claude-3-7-sonnet-latest"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.delenv("HERMIT_PROVIDER", raising=False)
    monkeypatch.delenv("HERMIT_PROFILE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["selected_profile"] == "shared"
    assert payload["provider"] == "claude"
    assert payload["auth"]["ok"] is True


def test_auth_status_reports_codex_oauth_from_local_auth(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    codex_home = tmp_path / ".codex"
    base_dir.mkdir(parents=True)
    codex_home.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
default_profile = "local"

[profiles.local]
provider = "codex-oauth"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (codex_home / "auth.json").write_text(
        '{"auth_mode":"chatgpt","tokens":{"access_token":"a","refresh_token":"b"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["provider"] == "codex-oauth"
    assert payload["ok"] is True
    assert payload["source"] == "~/.codex/auth.json"


def test_build_serve_preflight_uses_profile_feishu_settings(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        resolved_profile="local",
        provider="claude",
        claude_api_key="sk-ant-test",
        claude_auth_token=None,
        claude_base_url=None,
        resolved_openai_api_key=None,
        codex_auth_file_exists=False,
        codex_auth_mode=None,
        codex_access_token=None,
        codex_refresh_token=None,
        model="claude-3-7-sonnet-latest",
        feishu_app_id="cli_xxx",
        feishu_app_secret="secret",
        feishu_thread_progress=False,
        scheduler_feishu_chat_id="oc_123",
    )
    settings.base_dir.mkdir(parents=True)
    monkeypatch.delenv("HERMIT_PROFILE", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)

    items, errors = _build_serve_preflight("feishu", settings)
    details = {item.label: item.detail for item in items}

    assert errors == []
    assert details["配置档"] == "local（config.toml）"
    assert details["LLM 鉴权"] == "来自 config.toml 配置档"
    assert details["飞书 App ID"] == "来自 config.toml 配置档"
    assert details["飞书 App Secret"] == "来自 config.toml 配置档"
    assert details["飞书进度卡片"] == "关闭"
    assert details["Scheduler 飞书通知"] == "已配置"


def test_setup_next_steps_stay_localized_but_commands_remain_literal(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    get_settings.cache_clear()

    confirm_answers = iter([False, True])
    prompt_answers = iter(["sk-ant-test", "cli_xxx", "secret"])

    monkeypatch.setattr(main_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(main_mod.typer, "prompt", lambda *args, **kwargs: next(prompt_answers))

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert "Next steps:" in result.output
    assert "  hermit chat" in result.output
    assert "  hermit serve --adapter feishu" in result.output


def test_task_list_show_and_receipts_commands_read_kernel_state(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-task", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-task",
        title="CLI Task",
        goal="Inspect task CLI output",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "require_approval"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="write_file executed successfully",
    )

    runner = CliRunner()

    list_result = runner.invoke(app, ["task", "list"])
    assert list_result.exit_code == 0
    assert task.task_id in list_result.output
    assert "CLI Task" in list_result.output

    show_result = runner.invoke(app, ["task", "show", task.task_id])
    assert show_result.exit_code == 0
    assert '"task_id"' in show_result.output
    assert task.task_id in show_result.output

    receipts_result = runner.invoke(app, ["task", "receipts", "--task-id", task.task_id])
    assert receipts_result.exit_code == 0
    assert "write_file executed successfully" in receipts_result.output


def test_memory_inspect_command_reports_stored_and_preview_governance(
    tmp_path, monkeypatch
) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    record = store.create_memory_record(
        task_id="task-memory",
        conversation_id="chat-memory",
        category="其他",
        claim_text="当前无任何定时任务，刚刚已经全部清理完成。",
        confidence=0.9,
        evidence_refs=[],
    )

    runner = CliRunner()
    stored_result = runner.invoke(app, ["memory", "inspect", record.memory_id])
    preview_result = runner.invoke(
        app,
        [
            "memory",
            "inspect",
            "--claim-text",
            "以后都用简体中文回复我，不要再切英文。",
            "--json",
        ],
    )

    assert stored_result.exit_code == 0
    assert f"Memory ID: {record.memory_id}" in stored_result.output
    assert "Resolved Category: 进行中的任务" in stored_result.output
    assert "Subject: schedule" in stored_result.output
    assert "Governance:" in stored_result.output

    assert preview_result.exit_code == 0
    preview_payload = json.loads(preview_result.output)
    assert preview_payload["inspection"]["category"] == "用户偏好"
    assert preview_payload["inspection"]["retention_class"] == "user_preference"
    assert preview_payload["inspection"]["scope_kind"] == "global"


def test_memory_list_status_and_rebuild_commands_cover_inspection_suite(
    tmp_path, monkeypatch
) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    older = store.create_memory_record(
        task_id="task-older",
        conversation_id="chat-memory",
        category="进行中的任务",
        claim_text="已设定每日定时任务：每天早上 10 点自动搜索 AI 最新动态并推送日报到飞书群。",
        confidence=0.8,
        evidence_refs=[],
    )
    latest = store.create_memory_record(
        task_id="task-latest",
        conversation_id="chat-memory",
        category="进行中的任务",
        claim_text="当前无任何定时任务，刚刚已经全部清理完成。",
        confidence=0.9,
        evidence_refs=[],
    )
    store.create_memory_record(
        task_id="task-pref",
        conversation_id="chat-memory",
        category="用户偏好",
        claim_text="以后都用简体中文回复我。",
        confidence=0.9,
        evidence_refs=[],
    )

    runner = CliRunner()
    list_result = runner.invoke(app, ["memory", "list"])
    status_result = runner.invoke(app, ["memory", "status", "--json"])
    rebuild_result = runner.invoke(app, ["memory", "rebuild", "--json"])

    assert list_result.exit_code == 0
    assert older.memory_id in list_result.output
    assert latest.memory_id in list_result.output
    assert "task_state" in list_result.output

    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.output)
    assert status_payload["total_records"] >= 3
    assert status_payload["by_retention_class"]["task_state"] >= 2

    assert rebuild_result.exit_code == 0
    rebuild_payload = json.loads(rebuild_result.output)
    assert rebuild_payload["before_active"] >= rebuild_payload["after_active"]
    assert rebuild_payload["superseded_count"] >= 1
    assert Path(rebuild_payload["mirror_path"]).exists()


def test_task_explain_command_summarizes_authority_chain(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-explain", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-explain",
        title="CLI Explain Task",
        goal="Explain one governed execution",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed this write.",
        evidence_refs=["artifact_action", "artifact_policy"],
        action_type="write_local",
    )
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref="policy_1",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=["workspace"],
        constraints={"target_paths": ["workspace/example.txt"]},
        idempotency_key="idem_1",
        expires_at=None,
    )
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="write_file executed successfully",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        policy_ref="policy_1",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["task", "explain", task.task_id])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["task"]["task_id"] == task.task_id
    assert payload["operator_answers"]["why_execute"] == "Policy allowed this write."
    assert (
        payload["operator_answers"]["authority"]["capability_grant"]["grant_id"] == grant.grant_id
    )
    assert payload["operator_answers"]["authority"]["target_paths"] == ["workspace/example.txt"]
    assert (
        payload["operator_answers"]["outcome"]["result_summary"]
        == "write_file executed successfully"
    )


def test_task_proof_commands_report_and_export_proof_bundle(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-proof", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-proof",
        title="CLI Proof Task",
        goal="Export proof bundle",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed this write.",
        evidence_refs=["artifact_action"],
        action_type="write_local",
    )
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref="policy_1",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=["workspace"],
        constraints={"target_paths": ["workspace/example.txt"]},
        idempotency_key="idem_cli_proof",
        expires_at=None,
    )
    legacy_receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="legacy receipt",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        policy_ref="policy_1",
    )

    runner = CliRunner()
    proof_result = runner.invoke(app, ["task", "proof", task.task_id])
    assert proof_result.exit_code == 0
    proof_payload = json.loads(proof_result.output)
    assert proof_payload["chain_verification"]["valid"] is True
    assert proof_payload["missing_receipt_bundle_count"] == 1

    output_path = tmp_path / "proof.json"
    export_result = runner.invoke(
        app, ["task", "proof-export", task.task_id, "--output", str(output_path)]
    )
    assert export_result.exit_code == 0
    export_payload = json.loads(export_result.output)
    assert export_payload["status"] == "verified"
    assert export_payload["proof_bundle_ref"]
    assert output_path.read_text(encoding="utf-8").strip() == export_result.output.strip()
    refreshed_receipt = store.get_receipt(legacy_receipt.receipt_id)
    assert refreshed_receipt is not None and refreshed_receipt.receipt_bundle_ref is not None
    assert (
        ProofService(store).build_proof_summary(task.task_id)["missing_receipt_bundle_count"] == 0
    )


def test_task_claim_status_command_reports_repo_and_task_gates(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "proof-secret")
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-claims", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-claims",
        title="CLI Claim Task",
        goal="Inspect claim gate",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="claim receipt",
        result_code="succeeded",
    )
    ProofService(store).export_task_proof(task.task_id)

    runner = CliRunner()
    repo_result = runner.invoke(app, ["task", "claim-status"])
    task_result = runner.invoke(app, ["task", "claim-status", task.task_id])

    assert repo_result.exit_code == 0
    repo_payload = json.loads(repo_result.output)
    assert repo_payload["profiles"]["verifiable"]["claimable"] is True
    assert repo_payload["profiles"]["core"]["label"] == "Hermit Kernel v0.2 Core"

    assert task_result.exit_code == 0
    task_payload = json.loads(task_result.output)
    assert task_payload["task_id"] == task.task_id
    assert task_payload["task_gate"]["verifiable_ready"] is True
    assert task_payload["task_gate"]["strong_verifiable_ready"] is True
    refreshed_receipt = store.get_receipt(receipt.receipt_id)
    assert refreshed_receipt is not None
    assert refreshed_receipt.proof_mode == "signed_with_inclusion_proof"


def test_task_show_reports_contract_loop_summary(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-show-contracts", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-show-contracts",
        title="CLI Contract Loop Task",
        goal="Inspect contract loop",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    contract = store.create_execution_contract(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        objective="write_file: write_local",
        expected_effects=["path:workspace/demo.txt"],
        status="authorized",
        contract_version=2,
    )
    evidence_case = store.create_evidence_case(
        task_id=task.task_id,
        subject_kind="contract",
        subject_ref=contract.contract_id,
        support_refs=["artifact_context"],
        sufficiency_score=0.8,
        unresolved_gaps=[],
        status="sufficient",
    )
    authorization_plan = store.create_authorization_plan(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        contract_ref=contract.contract_id,
        policy_profile_ref="default",
        approval_route="operator",
        current_gaps=[],
        status="authorized",
    )
    reconciliation = store.create_reconciliation(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        contract_ref=contract.contract_id,
        receipt_refs=[],
        observed_output_refs=[],
        intended_effect_summary="write file",
        authorized_effect_summary="write file",
        observed_effect_summary="file written",
        receipted_effect_summary="receipt captured",
        result_class="satisfied",
        recommended_resolution="none",
    )
    store.update_step_attempt(
        attempt.step_attempt_id,
        execution_contract_ref=contract.contract_id,
        evidence_case_ref=evidence_case.evidence_case_id,
        authorization_plan_ref=authorization_plan.authorization_plan_id,
        reconciliation_ref=reconciliation.reconciliation_id,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["task", "show", task.task_id])

    assert result.exit_code == 0
    assert "Contract loop:" in result.output
    assert f"[contract:{contract.contract_id}] authorized v2" in result.output
    assert f"[evidence:{evidence_case.evidence_case_id}] sufficient" in result.output
    assert (
        f"[authority:{authorization_plan.authorization_plan_id}] authorized route=operator"
        in result.output
    )
    assert f"[reconciliation:{reconciliation.reconciliation_id}] satisfied" in result.output


def test_task_claim_status_reports_conditional_strong_proofs_without_signing(
    tmp_path, monkeypatch
) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.delenv("HERMIT_PROOF_SIGNING_SECRET", raising=False)
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-claims-conditional", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-claims-conditional",
        title="CLI Conditional Claim Task",
        goal="Inspect conditional claim gate",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="claim receipt",
        result_code="succeeded",
    )
    ProofService(store).export_task_proof(task.task_id)

    runner = CliRunner()
    repo_result = runner.invoke(app, ["task", "claim-status"])
    task_result = runner.invoke(app, ["task", "claim-status", task.task_id])

    assert repo_result.exit_code == 0
    repo_payload = json.loads(repo_result.output)
    assert repo_payload["profiles"]["verifiable"]["claimable"] is True
    assert repo_payload["conditional_capabilities"]["strong_signed_proofs_available"] is False
    signed_row = next(row for row in repo_payload["rows"] if row["id"] == "signed_proofs")
    assert signed_row["status"] == "conditional"

    assert task_result.exit_code == 0
    task_payload = json.loads(task_result.output)
    assert task_payload["task_gate"]["verifiable_ready"] is True
    assert task_payload["task_gate"]["strong_verifiable_ready"] is False


def test_repository_claim_status_rows_are_backed_by_semantic_probes(tmp_path, monkeypatch) -> None:
    from hermit.kernel.artifacts.lineage.claims import repository_claim_status

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))

    payload = repository_claim_status()

    event_row = next(row for row in payload["rows"] if row["id"] == "event_backed_truth")
    assert event_row["status"] == "implemented"
    assert event_row["evaluation"] == "semantic_probe"
    assert payload["cache"]["status"] == "fresh"


def test_repository_claim_status_probe_failures_block_profiles(tmp_path, monkeypatch) -> None:
    import hermit.kernel.artifacts.lineage.claims as claims_mod
    from hermit.kernel.artifacts.lineage.claim_manifest import CLAIM_ROWS

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    fake_rows = {
        str(row["id"]): {"status": "implemented", "evaluation": "semantic_probe"}
        for row in CLAIM_ROWS
        if row["id"] != "signed_proofs"
    }
    fake_rows["ingress_task_first"] = {
        "status": "partial",
        "evaluation": "semantic_probe",
        "probe_error": "RuntimeError: probe failed",
    }
    monkeypatch.setattr(
        claims_mod,
        "_semantic_probe_results",
        lambda **_: fake_rows,
    )

    payload = claims_mod.repository_claim_status()

    ingress_row = next(row for row in payload["rows"] if row["id"] == "ingress_task_first")
    assert ingress_row["status"] == "partial"
    assert ingress_row["evaluation"] == "semantic_probe"
    assert ingress_row["probe_error"] == "RuntimeError: probe failed"
    assert payload["profiles"]["core"]["claimable"] is False
    assert "ingress_task_first" in payload["profiles"]["core"]["blockers"]


def test_task_claim_status_reads_cached_repository_status_without_live_probes(
    tmp_path, monkeypatch
) -> None:
    import hermit.kernel.artifacts.lineage.claims as claims_mod
    from hermit.kernel.artifacts.lineage.claims import repository_claim_status, task_claim_status
    from hermit.kernel.ledger.journal.store import KernelStore

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-claims-cache", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-claims-cache",
        title="CLI Cached Claim Task",
        goal="Inspect cached claim gate",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="claim receipt",
        result_code="succeeded",
    )
    proof = ProofService(store).build_proof_summary(task.task_id)
    repository_claim_status()

    monkeypatch.setattr(
        claims_mod,
        "repository_claim_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live refresh should not run")
        ),
    )

    payload = task_claim_status(store, task.task_id, proof_summary=proof)
    assert payload["repository"]["cache"]["status"] == "fresh"
    assert payload["repository"]["profiles"]["core"]["claimable"] is True


def test_task_claim_status_refreshes_repository_status_when_cache_is_missing(
    tmp_path, monkeypatch
) -> None:
    import hermit.kernel.artifacts.lineage.claims as claims_mod
    from hermit.kernel.artifacts.lineage.claims import repository_claim_status, task_claim_status
    from hermit.kernel.ledger.journal.store import KernelStore

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-claims-miss", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-claims-miss",
        title="CLI Missing Claim Cache Task",
        goal="Refresh claim gate on cache miss",
        source_channel="chat",
    )
    proof = ProofService(store).build_proof_summary(task.task_id)

    calls = {"count": 0}
    original = repository_claim_status

    def _wrapped(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(claims_mod, "repository_claim_status", _wrapped)

    payload = task_claim_status(store, task.task_id, proof_summary=proof)

    assert calls["count"] >= 1
    assert payload["repository"]["cache"]["status"] == "fresh"
    assert payload["repository"]["profiles"]["core"]["claimable"] is True


def test_task_case_and_projection_rebuild_commands(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-case", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-case",
        title="CLI Case Task",
        goal="Show operator case",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed this write.",
        evidence_refs=[],
        action_type="write_local",
    )
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref="policy_case",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=["workspace"],
        constraints={"target_paths": ["workspace/case.txt"]},
        idempotency_key="idem_case",
        expires_at=None,
    )
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="case result",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        policy_ref="policy_case",
    )
    background = store.create_task(
        conversation_id="cli-case",
        title="Background task",
        goal="Keep another task open",
        source_channel="chat",
    )
    store.set_conversation_focus("cli-case", task_id=task.task_id, reason="explicit_task_switch")
    bound_ingress = store.create_ingress(
        conversation_id="cli-case",
        source_channel="chat",
        actor="user",
        raw_text="这个改成 Markdown，并保留 artifact_123",
        normalized_text="这个改成 markdown，并保留 artifact_123",
        reply_to_ref="msg_reply_1",
        quoted_message_ref="msg_quote_1",
        referenced_artifact_refs=["artifact_123"],
    )
    store.update_ingress(
        bound_ingress.ingress_id,
        status="bound",
        resolution="append_note",
        chosen_task_id=task.task_id,
        confidence=0.91,
        margin=0.48,
        rationale={
            "reason_codes": ["focus_task", "artifact_ref"],
            "shadow_binding": {
                "resolution": "append_note",
                "chosen_task_id": background.task_id,
                "match_actual": False,
            },
        },
    )
    pending_ingress = store.create_ingress(
        conversation_id="cli-case",
        source_channel="chat",
        actor="user",
        raw_text="这个也改一下",
        normalized_text="这个也改一下",
    )
    store.update_ingress(
        pending_ingress.ingress_id,
        status="pending_disambiguation",
        resolution="pending_disambiguation",
        rationale={"reason_codes": ["ambiguous_close_tie"]},
    )

    runner = CliRunner()
    case_result = runner.invoke(app, ["task", "case", task.task_id])
    rebuild_result = runner.invoke(app, ["task", "projections-rebuild", task.task_id])

    assert case_result.exit_code == 0
    case_payload = json.loads(case_result.output)
    assert case_payload["operator_answers"]["why_execute"] == "Policy allowed this write."
    assert (
        case_payload["operator_answers"]["claims"]["repository"]["profiles"]["core"]["claimable"]
        is True
    )
    assert case_payload["operator_answers"]["reentry"]["required_count"] == 0
    assert case_payload["ingress_observability"]["conversation"]["focus"]["task_id"] == task.task_id
    assert (
        case_payload["ingress_observability"]["conversation"]["metrics"]["resolution_counts"][
            "append_note"
        ]
        >= 1
    )
    assert case_payload["ingress_observability"]["conversation"]["pending_ingress_count"] >= 1
    assert any(
        item["reply_to_ref"] == "msg_reply_1"
        for item in case_payload["ingress_observability"]["conversation"]["recent_ingresses"]
    )
    assert (
        case_payload["ingress_observability"]["task"]["recent_related_ingresses"][0]["relation"]
        == "chosen_task"
    )
    assert (
        case_payload["ingress_observability"]["task"]["pending_disambiguations"][0]["status"]
        == "pending_disambiguation"
    )
    assert rebuild_result.exit_code == 0
    assert json.loads(rebuild_result.output)["task"]["task_id"] == task.task_id


def test_memory_export_command_writes_export_only_mirror(tmp_path, monkeypatch) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.create_memory_record(
        task_id="task-memory-export",
        conversation_id="chat-memory-export",
        category="项目约定",
        claim_text="默认在 /repo 执行命令",
        scope_kind="workspace",
        scope_ref="/repo",
        retention_class="project_convention",
    )

    output_path = tmp_path / "memory-export.md"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["memory", "export", "--output", str(output_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["render_mode"] == "export_only"
    assert payload["active_records"] == 1
    assert payload["export_path"] == str(output_path)
    assert output_path.exists()
    assert "默认在 /repo 执行命令" in output_path.read_text(encoding="utf-8")


def test_task_approve_and_deny_commands_delegate_to_runner(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-approval", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-approval",
        title="Pending approval",
        goal="Approve from CLI",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    approval = store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="write_local",
        requested_action={"tool_name": "write_file"},
        request_packet_ref=None,
    )

    calls: list[tuple[str, str, str, str]] = []

    class FakeRunner:
        def _resolve_approval(
            self, conversation_id: str, *, action: str, approval_id: str, reason: str = ""
        ):
            calls.append((conversation_id, action, approval_id, reason))
            return SimpleNamespace(text=f"{action}:{approval_id}")

    class FakePM:
        def stop_mcp_servers(self) -> None:
            return None

    monkeypatch.setattr(main_mod, "_build_runner", lambda settings: (FakeRunner(), FakePM()))

    runner = CliRunner()
    approve_result = runner.invoke(app, ["task", "approve", approval.approval_id])
    deny_result = runner.invoke(app, ["task", "deny", approval.approval_id, "--reason", "hold"])

    assert approve_result.exit_code == 0
    assert deny_result.exit_code == 0
    assert approve_result.output.strip() == f"approve_once:{approval.approval_id}"
    assert deny_result.output.strip() == f"deny:{approval.approval_id}"
    assert calls == [
        ("cli-approval", "approve_once", approval.approval_id, ""),
        ("cli-approval", "deny", approval.approval_id, "hold"),
    ]


def test_task_show_displays_approval_canonical_summary(monkeypatch, tmp_path) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-task-show", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-task-show",
        title="CLI Approval Summary",
        goal="Inspect approval summary",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    approval = store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="write_local",
        requested_action={
            "tool_name": "write_file",
            "display_copy": {
                "title": "确认文件修改",
                "summary": "准备修改 1 个文件：`src/app.py`。",
                "detail": "变更预览已生成；确认后将继续执行。",
            },
        },
        request_packet_ref=None,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["task", "show", task.task_id])

    assert result.exit_code == 0
    assert approval.approval_id in result.output
    assert "准备修改 1 个文件" in result.output


def test_task_list_and_show_use_localized_cli_copy(monkeypatch, tmp_path) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-task-list", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-task-list",
        title="Localized Task",
        goal="Inspect CLI localization",
        source_channel="chat",
    )

    runner = CliRunner()
    list_result = runner.invoke(app, ["task", "list"])
    show_result = runner.invoke(app, ["task", "show", task.task_id])

    assert list_result.exit_code == 0
    assert f"[{task.task_id}] {task.status} chat Localized Task" in list_result.output
    assert show_result.exit_code == 0
    assert "最近的审批记录：" not in show_result.output


def test_task_list_uses_english_cli_copy(monkeypatch, tmp_path) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-task-list-en", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-task-list-en",
        title="English Task",
        goal="Inspect CLI localization",
        source_channel="chat",
    )

    runner = CliRunner()
    list_result = runner.invoke(app, ["task", "list"])

    assert list_result.exit_code == 0
    assert f"[{task.task_id}] {task.status} chat English Task" in list_result.output


def test_task_capability_subcommands_list_and_revoke(monkeypatch, tmp_path) -> None:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("cli-grants", source_channel="chat")
    task = store.create_task(
        conversation_id="cli-grants",
        title="Capability CLI Task",
        goal="Inspect capability commands",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref="decision_1",
        approval_ref="approval_1",
        policy_ref="policy_1",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=[str((tmp_path / "Desktop").resolve())],
        constraints={"target_paths": [str((tmp_path / "Desktop").resolve())]},
        idempotency_key="cli-capability",
        expires_at=None,
    )

    runner = CliRunner()
    list_result = runner.invoke(app, ["task", "capability", "list"])
    revoke_result = runner.invoke(app, ["task", "capability", "revoke", grant.grant_id])

    assert list_result.exit_code == 0
    assert grant.grant_id in list_result.output
    assert revoke_result.exit_code == 0
    assert f"已撤销能力授权 '{grant.grant_id}'。" in revoke_result.output
    assert store.get_capability_grant(grant.grant_id).status == "revoked"


def test_notify_reload_uses_settings_scheduler_chat_id(monkeypatch, tmp_path) -> None:
    import hermit.surfaces.cli.main as main_mod

    fired: list[dict[str, object]] = []

    class FakeHooks:
        def fire(self, event, **kwargs):
            fired.append(kwargs)

    class FakePluginManager:
        def __init__(self, settings=None):
            self.hooks = FakeHooks()

        def discover_and_load(self, *args, **kwargs):
            return None

    monkeypatch.setattr(main_mod, "PluginManager", FakePluginManager)
    settings = SimpleNamespace(
        scheduler_feishu_chat_id="oc_cfg_chat",
        plugins_dir=tmp_path / "plugins",
    )

    _notify_reload(settings, "feishu")

    assert fired and fired[0]["notify"] == {"feishu_chat_id": "oc_cfg_chat"}


def test_reload_removes_stale_pid_file(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli.main as main_mod
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()
    pid_path = base_dir / "serve-feishu.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(
        main_mod.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())
    )

    runner = CliRunner()
    result = runner.invoke(app, ["reload"])

    assert result.exit_code == 1
    assert "PID 文件已过期" in result.output
    assert not pid_path.exists()
