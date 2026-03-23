from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation import (
    CandidateProposal,
    CritiqueRecord,
)
from hermit.kernel.execution.competition.llm_critic import (
    CriticRole,
    CritiqueGenerator,
    _parse_critiques_response,
)
from hermit.kernel.execution.workers.models import (
    WorkerPoolConfig,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager
from hermit.kernel.ledger.journal.store import KernelStore


def _make_provider(text: str) -> MagicMock:
    provider = MagicMock()
    provider.generate.return_value = SimpleNamespace(
        content=[{"type": "text", "text": text}]
    )
    return provider


def _make_pool() -> WorkerPoolManager:
    config = WorkerPoolConfig(
        pool_id="test",
        team_id="test",
        slots={
            WorkerRole.reviewer: WorkerSlotConfig(role=WorkerRole.reviewer, max_active=3),
        },
    )
    return WorkerPoolManager(config)


def _make_proposals() -> list[CandidateProposal]:
    return [
        CandidateProposal(
            candidate_id="cand_1", proposer_role="engineer", target_scope="scope",
            plan_summary="Plan A", contract_draft={"steps": 1},
            expected_cost="low", expected_risk="low",
        ),
        CandidateProposal(
            candidate_id="cand_2", proposer_role="architect", target_scope="scope",
            plan_summary="Plan B", contract_draft={"steps": 3},
            expected_cost="high", expected_risk="high",
        ),
    ]


class TestParseCritiquesResponse:
    def test_valid_array(self) -> None:
        text = json.dumps([{"target_candidate_id": "c1", "severity": "high"}])
        assert len(_parse_critiques_response(text)) == 1

    def test_single_object_wrapped(self) -> None:
        assert len(_parse_critiques_response(json.dumps({"severity": "low"}))) == 1

    def test_code_fenced(self) -> None:
        assert len(_parse_critiques_response('```json\n[{"s": "m"}]\n```')) == 1

    def test_invalid_returns_empty(self) -> None:
        assert _parse_critiques_response("no json") == []


class TestCritiqueGenerator:
    def _make_generator(
        self,
        response_text: str,
        *,
        critic_roles: tuple[CriticRole, ...] | None = None,
    ) -> CritiqueGenerator:
        def factory() -> Any:
            return _make_provider(response_text)

        return CritiqueGenerator(
            factory, default_model="test-model", max_workers=2, critic_roles=critic_roles,
        )

    def test_generates_critiques_pool_gated(self, tmp_path: Path) -> None:
        response = json.dumps([{
            "target_candidate_id": "cand_1",
            "issue_type": "security",
            "severity": "high",
            "evidence_refs": ["CVE-001"],
            "suggested_fix": "add validation",
        }])
        gen = self._make_generator(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        critiques = gen.generate_critiques(
            _make_proposals(), {},
            task_id="t1", debate_id="d1", pool=pool, store=store, artifact_store=arts,
        )
        # 3 default roles × 1 critique each = 3.
        assert len(critiques) == 3
        for c in critiques:
            assert isinstance(c, CritiqueRecord)
            assert c.target_candidate_id == "cand_1"

        # Slots released.
        assert pool.get_status().active_slots == 0

    def test_filters_invalid_target_ids(self, tmp_path: Path) -> None:
        response = json.dumps([{"target_candidate_id": "nonexistent", "severity": "low"}])
        gen = self._make_generator(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        critiques = gen.generate_critiques(
            _make_proposals(), {},
            task_id="t1", debate_id="d1", pool=pool, store=store, artifact_store=arts,
        )
        assert len(critiques) == 0

    def test_normalizes_invalid_severity(self, tmp_path: Path) -> None:
        response = json.dumps([{
            "target_candidate_id": "cand_1", "severity": "extreme",
        }])
        gen = self._make_generator(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        critiques = gen.generate_critiques(
            _make_proposals(), {},
            task_id="t1", debate_id="d1", pool=pool, store=store, artifact_store=arts,
        )
        for c in critiques:
            assert c.severity == "medium"

    def test_empty_proposals_returns_empty(self, tmp_path: Path) -> None:
        gen = self._make_generator("[]")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        assert gen.generate_critiques(
            [], {}, task_id="t1", debate_id="d1", pool=pool, store=store, artifact_store=arts,
        ) == []

    def test_handles_provider_exception(self, tmp_path: Path) -> None:
        def failing_factory() -> Any:
            p = MagicMock()
            p.generate.side_effect = RuntimeError("LLM down")
            return p

        gen = CritiqueGenerator(failing_factory, default_model="test-model", max_workers=2)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        critiques = gen.generate_critiques(
            _make_proposals(), {},
            task_id="t1", debate_id="d1", pool=pool, store=store, artifact_store=arts,
        )
        assert len(critiques) == 0
        assert pool.get_status().active_slots == 0
