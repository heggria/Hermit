from __future__ import annotations

from types import SimpleNamespace

from hermit.plugins.builtin.bundles.usage import commands as usage_commands
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_usage_command_registers_and_formats_session_totals() -> None:
    session = SimpleNamespace(
        messages=[{"role": "user"}, {"role": "assistant"}, {"role": "user"}],
        total_input_tokens=1200,
        total_output_tokens=340,
        total_cache_read_tokens=10,
        total_cache_creation_tokens=20,
    )
    runner = SimpleNamespace(
        session_manager=SimpleNamespace(get_or_create=lambda session_id: session)
    )
    ctx = PluginContext(HooksEngine())

    usage_commands.register(ctx)
    result = ctx.commands[0].handler(runner, "session-1", "/usage")

    assert ctx.commands[0].name == "/usage"
    assert result.is_command is True
    assert "Input: 1,200" in result.text
    assert "User turns: 2" in result.text
