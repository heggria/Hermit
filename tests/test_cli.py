from __future__ import annotations

from typer.testing import CliRunner

from hermit.main import app


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
    from hermit.config import get_settings
    import hermit.main as main_mod

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([False, False])

    monkeypatch.setattr(main_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(main_mod.typer, "prompt", lambda *args, **kwargs: "sk-ant-test")

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert (tmp_path / ".hermit" / ".env").read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=sk-ant-test\n"


def test_setup_shows_adapter_flag_in_next_steps(tmp_path, monkeypatch) -> None:
    from hermit.config import get_settings
    import hermit.main as main_mod

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


def test_serve_preflight_reports_missing_feishu_env(tmp_path, monkeypatch) -> None:
    from hermit.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
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
    assert "[MISSING] 飞书 App ID" in result.output
    assert "[MISSING] 飞书 App Secret" in result.output
    assert "启动前检查未通过" in result.output


def test_serve_preflight_shows_resolved_env_sources(tmp_path, monkeypatch) -> None:
    from hermit.config import get_settings
    import hermit.main as main_mod

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
    assert "[OK] 飞书 App ID: HERMIT_FEISHU_APP_ID (shell env)" in result.output
    assert "[OK] 飞书 App Secret: HERMIT_FEISHU_APP_SECRET (shell env)" in result.output
    assert serve_calls and serve_calls[0][0] == "feishu"
