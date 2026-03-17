from __future__ import annotations

import pytest

from hermit.plugins.builtin.subagents.orchestrator import state as orchestrator_state


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
