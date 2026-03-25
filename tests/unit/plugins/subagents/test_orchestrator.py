from __future__ import annotations

import pytest

from hermit.plugins.builtin.subagents.orchestrator import hooks as orchestrator_hooks
from hermit.plugins.builtin.subagents.orchestrator import state as orchestrator_state
from hermit.runtime.capability.contracts.base import HookEvent
from hermit.runtime.capability.contracts.hooks import HooksEngine


@pytest.mark.asyncio
async def test_orchestrator_routes_work_to_expected_worker() -> None:
    async def researcher(payload: dict[str, object]) -> dict[str, object]:
        return {**payload, "route": "direct", "research": "notes"}

    async def coder(payload: dict[str, object]) -> dict[str, object]:
        return {**payload, "route": "direct", "code": "patch"}

    research_state = orchestrator_state.SharedState(
        messages=[{"role": "user", "content": "hi"}], route="research"
    )
    code_state = orchestrator_state.SharedState(route="code")

    research_result = await orchestrator_state.SimpleOrchestrator(researcher, coder).run(
        research_state
    )
    code_result = await orchestrator_state.SimpleOrchestrator(researcher, coder).run(code_state)

    assert research_result.research == "notes"
    assert research_result.route == "direct"
    assert code_result.code == "patch"
    assert code_result.to_dict()["route"] == "direct"


def test_inject_instructions_includes_orchestrator_section() -> None:
    """The SYSTEM_PROMPT hook fragment must contain the orchestrator delegation block."""
    fragment = orchestrator_hooks._inject_instructions()

    assert "<orchestrator>" in fragment
    assert "delegate_*" in fragment
    assert "</orchestrator>" in fragment


def test_inject_instructions_includes_subtask_spawning_section() -> None:
    """The SYSTEM_PROMPT hook fragment must contain the subtask spawning capability block."""
    fragment = orchestrator_hooks._inject_instructions()

    assert "<subtask_spawning>" in fragment
    assert "SUBTASK_SPAWN" in fragment
    assert "SUBTASK_COMPLETE" in fragment
    assert "</subtask_spawning>" in fragment


def test_inject_instructions_documents_join_strategies() -> None:
    """The subtask spawning section must document all three join strategies."""
    fragment = orchestrator_hooks._inject_instructions()

    assert "all_required" in fragment
    assert "any_success" in fragment
    assert "best_effort" in fragment


def test_inject_instructions_registered_as_system_prompt_hook() -> None:
    """register() must attach _inject_instructions to the SYSTEM_PROMPT event."""
    from hermit.runtime.capability.contracts.base import PluginContext

    engine = HooksEngine()

    class _FakeCtx(PluginContext):
        pass

    ctx = _FakeCtx(hooks_engine=engine)
    orchestrator_hooks.register(ctx)

    results = engine.fire(HookEvent.SYSTEM_PROMPT)
    combined = "\n".join(str(r) for r in results if r)

    assert "<subtask_spawning>" in combined
    assert "<orchestrator>" in combined
