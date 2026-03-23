from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation import CandidateProposal
from hermit.kernel.execution.competition.llm_proposer import (
    ProposalGenerator,
    ProposalPerspective,
    _parse_json_response,
)
from hermit.kernel.execution.workers.models import (
    WorkerPoolConfig,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager
from hermit.kernel.ledger.journal.store import KernelStore


def _make_provider_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[{"type": "text", "text": text}])


def _make_provider(text: str) -> MagicMock:
    provider = MagicMock()
    provider.generate.return_value = _make_provider_response(text)
    return provider


def _make_pool() -> WorkerPoolManager:
    config = WorkerPoolConfig(
        pool_id="test",
        team_id="test",
        slots={
            WorkerRole.planner: WorkerSlotConfig(role=WorkerRole.planner, max_active=3),
        },
    )
    return WorkerPoolManager(config)


class TestParseJsonResponse:
    def test_plain_json(self) -> None:
        assert _parse_json_response('{"plan_summary": "test"}')["plan_summary"] == "test"

    def test_json_in_code_fence(self) -> None:
        assert _parse_json_response('```json\n{"k": "v"}\n```')["k"] == "v"

    def test_json_with_surrounding_text(self) -> None:
        assert _parse_json_response('text {"k": "v"} more')["k"] == "v"

    def test_invalid_json_returns_empty(self) -> None:
        assert _parse_json_response("not json") == {}


class TestProposalGenerator:
    def _make_generator(
        self,
        response_text: str,
        *,
        perspectives: tuple[ProposalPerspective, ...] | None = None,
    ) -> ProposalGenerator:
        def factory() -> Any:
            return _make_provider(response_text)

        return ProposalGenerator(
            factory,
            default_model="test-model",
            max_workers=2,
            perspectives=perspectives,
        )

    def test_generates_proposals_pool_gated(self, tmp_path: Path) -> None:
        response = json.dumps(
            {
                "plan_summary": "test plan",
                "contract_draft": {"steps": 1},
                "expected_cost": "low",
                "expected_risk": "low",
                "expected_reward": "high",
            }
        )
        gen = self._make_generator(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        proposals = gen.generate_proposals(
            "debate_1",
            "Should we refactor?",
            {},
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        # Default 3 perspectives.
        assert len(proposals) == 3
        for p in proposals:
            assert isinstance(p, CandidateProposal)
            assert p.plan_summary == "test plan"

        # Verify steps were created in the store.
        # Pool slots should all be released.
        status = pool.get_status()
        assert status.active_slots == 0

    def test_custom_perspectives(self, tmp_path: Path) -> None:
        response = json.dumps({"plan_summary": "custom"})
        perspectives = (
            ProposalPerspective(role="alpha", system_prompt="alpha"),
            ProposalPerspective(role="beta", system_prompt="beta"),
        )
        gen = self._make_generator(response, perspectives=perspectives)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        proposals = gen.generate_proposals(
            "debate_2",
            "dp",
            {},
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert len(proposals) == 2
        roles = {p.proposer_role for p in proposals}
        assert roles == {"alpha", "beta"}

    def test_skips_unparseable_responses(self, tmp_path: Path) -> None:
        gen = self._make_generator("not valid json")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        proposals = gen.generate_proposals(
            "debate_3",
            "dp",
            {},
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert len(proposals) == 0

    def test_handles_provider_exception(self, tmp_path: Path) -> None:
        def failing_factory() -> Any:
            provider = MagicMock()
            provider.generate.side_effect = RuntimeError("LLM down")
            return provider

        gen = ProposalGenerator(failing_factory, default_model="test-model", max_workers=2)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        proposals = gen.generate_proposals(
            "debate_4",
            "dp",
            {},
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert len(proposals) == 0
        # Slots should be released even on failure.
        assert pool.get_status().active_slots == 0

    def test_slot_unavailable_skips_gracefully(self, tmp_path: Path) -> None:
        """When pool has no available planner slots, proposals are skipped."""
        response = json.dumps({"plan_summary": "test"})
        gen = self._make_generator(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")

        # Create a pool with 0 planner slots.
        config = WorkerPoolConfig(
            pool_id="empty",
            team_id="test",
            slots={WorkerRole.planner: WorkerSlotConfig(role=WorkerRole.planner, max_active=0)},
        )
        empty_pool = WorkerPoolManager(config)

        proposals = gen.generate_proposals(
            "debate_5",
            "dp",
            {},
            task_id="t1",
            pool=empty_pool,
            store=store,
            artifact_store=arts,
        )
        assert len(proposals) == 0
