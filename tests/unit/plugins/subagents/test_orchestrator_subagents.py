"""Tests for orchestrator subagents registration."""

from __future__ import annotations

from hermit.plugins.builtin.subagents.orchestrator.subagents import register
from hermit.runtime.capability.contracts.base import PluginContext, SubagentSpec
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_register_adds_researcher_and_coder_subagents() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    assert len(ctx.subagents) == 2
    names = [s.name for s in ctx.subagents]
    assert "researcher" in names
    assert "coder" in names


def test_researcher_subagent_has_expected_tools() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    researcher = next(s for s in ctx.subagents if s.name == "researcher")
    assert "web_search" in researcher.tools
    assert "web_fetch" in researcher.tools
    assert "bash" in researcher.tools
    assert "read_file" in researcher.tools


def test_coder_subagent_has_expected_tools() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    coder = next(s for s in ctx.subagents if s.name == "coder")
    assert "web_search" in coder.tools
    assert "web_fetch" in coder.tools
    assert "read_file" in coder.tools
    assert "write_file" in coder.tools
    assert "bash" in coder.tools


def test_subagents_have_descriptions_and_system_prompts() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    for subagent in ctx.subagents:
        assert isinstance(subagent, SubagentSpec)
        assert len(subagent.description) > 0
        assert len(subagent.system_prompt) > 0
