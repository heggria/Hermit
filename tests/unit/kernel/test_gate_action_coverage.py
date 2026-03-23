"""Exhaustive ActionClass coverage for deliberation trigger logic."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.competition.deliberation_service import DeliberationService
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
from hermit.kernel.ledger.journal.store import KernelStore

# ---------------------------------------------------------------------------
# Action class groupings — must stay in sync with the frozensets in
# deliberation_service.py (_READONLY_ACTIONS, _MEDIUM_RISK_DELIBERATION_ACTIONS,
# _HIGH_RISK_DELIBERATION_ACTIONS).
# ---------------------------------------------------------------------------

# All readonly actions (should NEVER trigger)
_READONLY = [
    "read_local",
    "network_read",
    "execute_command_readonly",
    "delegate_reasoning",
    "ephemeral_ui_mutation",
]

# Medium-risk mutation actions
_MEDIUM_MUTATIONS = [
    "write_local",
    "patch_file",
    "execute_command",
    "network_write",
    "external_mutation",
    "vcs_mutation",
    "publication",
    "rollback",
    "scheduler_mutation",
]

# High-risk-only mutation actions (trigger at high/critical but NOT medium)
_HIGH_ONLY_MUTATIONS = [
    "delegate_execution",
    "approval_resolution",
    "credentialed_api_call",
    "memory_write",
    "attachment_ingest",
    "patrol_execution",
]

# Actions that should never trigger
_NEVER_TRIGGER = ["unknown", "some_future_action", ""]

_ALL_RISK_LEVELS = ["low", "medium", "high", "critical"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_arbitrator(response_text: str | None = None) -> ArbitrationEngine:
    if response_text is None:
        response_text = json.dumps(
            {
                "selected_candidate_id": "placeholder",
                "confidence": 0.8,
                "reasoning": "test",
            }
        )

    def factory() -> Any:
        p = MagicMock()
        p.generate.return_value = SimpleNamespace(content=[{"type": "text", "text": response_text}])
        return p

    return ArbitrationEngine(factory, default_model="test-model")


def _make_service(tmp_path: Path) -> DeliberationService:
    store = KernelStore(tmp_path / "state.db")
    return DeliberationService(store=store, arbitrator=_make_arbitrator())


# ---------------------------------------------------------------------------
# 1. Readonly actions never trigger — any risk level
# ---------------------------------------------------------------------------


class TestReadonlyNeverTriggers:
    """Readonly actions must return False regardless of risk level."""

    @pytest.mark.parametrize("action", _READONLY, ids=lambda a: f"action={a}")
    @pytest.mark.parametrize("risk", _ALL_RISK_LEVELS, ids=lambda r: f"risk={r}")
    def test_readonly_never_triggers(self, tmp_path: Path, action: str, risk: str) -> None:
        svc = _make_service(tmp_path)
        result = svc.should_deliberate(risk_level=risk, action_class=action)
        assert result is False, f"Readonly action {action!r} must not trigger at risk={risk!r}"


# ---------------------------------------------------------------------------
# 2. Medium mutations: trigger at medium/high/critical, not at low
# ---------------------------------------------------------------------------


class TestMediumMutationsTrigger:
    """Medium-risk mutation actions trigger at medium, high, and critical risk."""

    @pytest.mark.parametrize("action", _MEDIUM_MUTATIONS, ids=lambda a: f"action={a}")
    @pytest.mark.parametrize("risk", ["medium", "high", "critical"], ids=lambda r: f"risk={r}")
    def test_triggers_at_medium_and_above(self, tmp_path: Path, action: str, risk: str) -> None:
        svc = _make_service(tmp_path)
        result = svc.should_deliberate(risk_level=risk, action_class=action)
        assert result is True, f"Mutation action {action!r} should trigger at risk={risk!r}"

    @pytest.mark.parametrize("action", _MEDIUM_MUTATIONS, ids=lambda a: f"action={a}")
    def test_does_not_trigger_at_low(self, tmp_path: Path, action: str) -> None:
        svc = _make_service(tmp_path)
        result = svc.should_deliberate(risk_level="low", action_class=action)
        assert result is False, f"Mutation action {action!r} must not trigger at risk='low'"


# ---------------------------------------------------------------------------
# 3. High-only mutations: trigger at high/critical, not at medium/low
# ---------------------------------------------------------------------------


class TestHighOnlyMutations:
    """High-risk-only mutation actions trigger at high and critical only."""

    @pytest.mark.parametrize("action", _HIGH_ONLY_MUTATIONS, ids=lambda a: f"action={a}")
    @pytest.mark.parametrize("risk", ["high", "critical"], ids=lambda r: f"risk={r}")
    def test_triggers_at_high_and_critical(self, tmp_path: Path, action: str, risk: str) -> None:
        svc = _make_service(tmp_path)
        result = svc.should_deliberate(risk_level=risk, action_class=action)
        assert result is True, f"High-only action {action!r} should trigger at risk={risk!r}"

    @pytest.mark.parametrize("action", _HIGH_ONLY_MUTATIONS, ids=lambda a: f"action={a}")
    @pytest.mark.parametrize("risk", ["medium", "low"], ids=lambda r: f"risk={r}")
    def test_does_not_trigger_at_medium_or_low(
        self, tmp_path: Path, action: str, risk: str
    ) -> None:
        svc = _make_service(tmp_path)
        result = svc.should_deliberate(risk_level=risk, action_class=action)
        assert result is False, f"High-only action {action!r} must not trigger at risk={risk!r}"


# ---------------------------------------------------------------------------
# 4. Never-trigger actions — unknown, empty, and unrecognised strings
# ---------------------------------------------------------------------------


class TestNeverTriggerActions:
    """Unknown, empty, and unrecognised action strings never trigger."""

    @pytest.mark.parametrize("action", _NEVER_TRIGGER, ids=lambda a: f"action={a!r}")
    @pytest.mark.parametrize("risk", _ALL_RISK_LEVELS, ids=lambda r: f"risk={r}")
    def test_never_triggers(self, tmp_path: Path, action: str, risk: str) -> None:
        svc = _make_service(tmp_path)
        result = svc.should_deliberate(risk_level=risk, action_class=action)
        assert result is False, f"Action {action!r} must not trigger at risk={risk!r}"


# ---------------------------------------------------------------------------
# 5. Static method consistency — check_deliberation_needed matches
#    should_deliberate for a representative cross-section
# ---------------------------------------------------------------------------


class TestStaticMethodConsistency:
    """Verify check_deliberation_needed mirrors should_deliberate for all groups."""

    # Build a representative sample that covers every group × every risk level.
    _REPRESENTATIVE_PAIRS: list[tuple[str, str]] = [
        # Readonly
        *((risk, action) for risk in _ALL_RISK_LEVELS for action in _READONLY),
        # Medium mutations
        *((risk, action) for risk in _ALL_RISK_LEVELS for action in _MEDIUM_MUTATIONS),
        # High-only mutations
        *((risk, action) for risk in _ALL_RISK_LEVELS for action in _HIGH_ONLY_MUTATIONS),
        # Never-trigger
        *((risk, action) for risk in _ALL_RISK_LEVELS for action in _NEVER_TRIGGER),
    ]

    @pytest.mark.parametrize(
        ("risk", "action"),
        _REPRESENTATIVE_PAIRS,
        ids=lambda pair: f"{pair}" if isinstance(pair, str) else None,
    )
    def test_static_matches_instance(self, tmp_path: Path, risk: str, action: str) -> None:
        svc = _make_service(tmp_path)
        instance_result = svc.should_deliberate(risk_level=risk, action_class=action)
        static_result = DeliberationService.check_deliberation_needed(
            risk_level=risk, action_class=action
        )
        assert static_result == instance_result, (
            f"Static/instance mismatch for risk={risk!r}, action={action!r}: "
            f"static={static_result}, instance={instance_result}"
        )
