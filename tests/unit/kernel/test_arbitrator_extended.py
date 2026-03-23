"""Extended ArbitrationEngine tests: post_execution_reviews, confidence bounds, slot paths."""
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
    DebateBundle,
    DeliberationTrigger,
    PostExecutionReview,
)
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
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
            WorkerRole.verifier: WorkerSlotConfig(role=WorkerRole.verifier, max_active=1),
        },
    )
    return WorkerPoolManager(config)


def _make_engine(response_text: str) -> ArbitrationEngine:
    def factory() -> Any:
        return _make_provider(response_text)

    return ArbitrationEngine(factory, default_model="test-model")


def _make_bundle(
    *,
    proposals: list[CandidateProposal] | None = None,
    critiques: list[CritiqueRecord] | None = None,
    reviews: list[PostExecutionReview] | None = None,
) -> DebateBundle:
    return DebateBundle(
        debate_id="debate_1",
        decision_point="Should we refactor?",
        trigger=DeliberationTrigger.high_risk_planning,
        proposals=proposals or [],
        critiques=critiques or [],
        post_execution_reviews=reviews or [],
    )


def _make_proposal(cid: str = "c1", role: str = "engineer") -> CandidateProposal:
    return CandidateProposal(
        candidate_id=cid,
        proposer_role=role,
        target_scope="scope",
        plan_summary=f"Plan by {role}",
        contract_draft={"steps": 1},
        expected_cost="low",
        expected_risk="low",
        expected_reward="high",
    )


def _make_critique(target: str, severity: str = "medium") -> CritiqueRecord:
    return CritiqueRecord(
        critique_id=f"crit_{target}",
        target_candidate_id=target,
        critic_role="reviewer",
        issue_type="correctness",
        severity=severity,
    )


def _make_review(
    severity: str = "medium",
    challenge_type: str = "spec_compliance",
) -> PostExecutionReview:
    return PostExecutionReview(
        review_id="rev_1",
        debate_id="debate_1",
        task_id="t1",
        reviewer_role="auditor",
        challenge_type=challenge_type,
        finding="Output differs from spec",
        severity=severity,
        recommendation="re_execute",
    )


class TestArbitrationEngineExtended:
    def test_confidence_lower_bound_clamped(self, tmp_path: Path) -> None:
        """LLM returns confidence=-0.5, verify decision.confidence == 0.0."""
        response = json.dumps({
            "selected_candidate_id": "c1",
            "confidence": -0.5,
            "reasoning": "negative confidence edge",
            "merge_notes": "clamped",
        })
        engine = _make_engine(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
        )
        decision = engine.arbitrate(
            bundle, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )
        assert decision.confidence == 0.0
        assert decision.selected_candidate_id == "c1"

    def test_post_execution_reviews_in_bundle(self, tmp_path: Path) -> None:
        """Bundle with post_execution_reviews triggers LLM; reviews appear in prompt."""
        captured_requests: list[Any] = []

        def capturing_factory() -> MagicMock:
            provider = MagicMock()

            def capture_generate(request: Any) -> SimpleNamespace:
                captured_requests.append(request)
                return SimpleNamespace(
                    content=[{
                        "type": "text",
                        "text": json.dumps({
                            "selected_candidate_id": "c1",
                            "confidence": 0.9,
                            "reasoning": "reviewed",
                            "merge_notes": "ok",
                        }),
                    }]
                )

            provider.generate.side_effect = capture_generate
            return provider

        engine = ArbitrationEngine(capturing_factory, default_model="test-model")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        review = _make_review(severity="high", challenge_type="benchmark_interpretation")
        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
            reviews=[review],
        )

        decision = engine.arbitrate(
            bundle, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )

        # LLM was called
        assert len(captured_requests) == 1

        # The review content should appear in the user prompt
        prompt_text = captured_requests[0].messages[0]["content"]
        assert "Post-Execution Reviews" in prompt_text
        assert "benchmark_interpretation" in prompt_text
        assert "Output differs from spec" in prompt_text
        assert "re_execute" in prompt_text

        assert decision.selected_candidate_id == "c1"
        assert decision.confidence == 0.9

    def test_arbitrate_creates_step_and_attempt(self, tmp_path: Path) -> None:
        """Verify store.create_step and store.create_step_attempt are called for 2+ eligible."""
        response = json.dumps({
            "selected_candidate_id": "c1",
            "confidence": 0.8,
            "reasoning": "good",
            "merge_notes": "proceed",
        })
        engine = _make_engine(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        # Wrap create_step and create_step_attempt to track calls
        original_create_step = store.create_step
        original_create_attempt = store.create_step_attempt
        step_calls: list[dict[str, Any]] = []
        attempt_calls: list[dict[str, Any]] = []

        def tracking_create_step(**kwargs: Any) -> Any:
            step_calls.append(kwargs)
            return original_create_step(**kwargs)

        def tracking_create_attempt(**kwargs: Any) -> Any:
            attempt_calls.append(kwargs)
            return original_create_attempt(**kwargs)

        store.create_step = tracking_create_step  # type: ignore[assignment]
        store.create_step_attempt = tracking_create_attempt  # type: ignore[assignment]

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
        )
        decision = engine.arbitrate(
            bundle, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )

        assert len(step_calls) == 1
        assert step_calls[0]["task_id"] == "t1"
        assert step_calls[0]["kind"] == "verify"
        assert step_calls[0]["title"] == "deliberation_arbitration"

        assert len(attempt_calls) == 1
        assert attempt_calls[0]["task_id"] == "t1"
        assert attempt_calls[0]["executor_mode"] == "deliberation_arbitrator"

        assert decision.selected_candidate_id == "c1"

    def test_arbitrate_stores_artifact_on_success(self, tmp_path: Path) -> None:
        """Verify artifact with type deliberation_llm_arbitration is stored."""
        response = json.dumps({
            "selected_candidate_id": "c2",
            "confidence": 0.75,
            "reasoning": "solid plan",
            "merge_notes": "go ahead",
        })
        engine = _make_engine(response)
        store = KernelStore(tmp_path / "state.db")
        arts_path = tmp_path / "artifacts"
        arts = ArtifactStore(arts_path)
        pool = _make_pool()

        # Wrap store_json to capture what gets stored
        original_store_json = arts.store_json
        stored_payloads: list[dict[str, Any]] = []

        def tracking_store_json(payload: Any) -> Any:
            stored_payloads.append(payload)
            return original_store_json(payload)

        arts.store_json = tracking_store_json  # type: ignore[assignment]

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
        )
        decision = engine.arbitrate(
            bundle, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )

        assert decision.selected_candidate_id == "c2"
        assert len(stored_payloads) == 1

        artifact = stored_payloads[0]
        assert artifact["artifact_type"] == "deliberation_llm_arbitration"
        assert artifact["debate_id"] == "debate_1"
        assert artifact["selected_candidate_id"] == "c2"
        assert artifact["confidence"] == 0.75
        assert artifact["escalation_required"] is False

    def test_fallback_merge_notes_contain_marker(self, tmp_path: Path) -> None:
        """Verify [fallback] appears in merge_notes when LLM fails."""
        def failing_factory() -> MagicMock:
            p = MagicMock()
            p.generate.side_effect = RuntimeError("LLM down")
            return p

        engine = ArbitrationEngine(failing_factory, default_model="test-model")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
        )
        decision = engine.arbitrate(
            bundle, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )

        assert decision.selected_candidate_id == "c1"
        assert "[fallback]" in decision.merge_notes
        assert "engineer" in decision.merge_notes
        assert decision.confidence == 0.5
        assert decision.escalation_required is False
