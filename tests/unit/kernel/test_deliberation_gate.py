"""Tests for confidence-based gating in the deliberation gate.

Verifies that ``should_deliberate`` and ``check_deliberation_needed`` correctly
gate actions based on ActionClass and risk level.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.competition.deliberation_service import (
    DeliberationService,
)
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
from hermit.kernel.ledger.journal.store import KernelStore


def _make_arbitrator(response_text: str | None = None) -> ArbitrationEngine:
    if response_text is None:
        response_text = json.dumps({
            "selected_candidate_id": "placeholder",
            "confidence": 0.8,
            "reasoning": "test",
        })

    def factory() -> Any:
        p = MagicMock()
        p.generate.return_value = SimpleNamespace(
            content=[{"type": "text", "text": response_text}]
        )
        return p

    return ArbitrationEngine(factory, default_model="test-model")


def _make_service(tmp_path: Path) -> DeliberationService:
    store = KernelStore(tmp_path / "state.db")
    return DeliberationService(store=store, arbitrator=_make_arbitrator())


# -- ActionClass-based trigger logic (instance method) -----------------------


class TestShouldDeliberateActionClass:
    """Verify the ActionClass-based trigger logic in ``should_deliberate``."""

    def test_readonly_actions_never_trigger(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        readonly_actions = [
            "read_local",
            "network_read",
            "execute_command_readonly",
            "delegate_reasoning",
        ]
        for risk in ("low", "medium", "high", "critical"):
            for action in readonly_actions:
                assert svc.should_deliberate(risk_level=risk, action_class=action) is False, (
                    f"Expected False for readonly action={action!r} at risk={risk!r}"
                )

    def test_mutation_at_critical_triggers(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        mutation_actions = ["execute_command", "write_local", "patch_file", "rollback"]
        for action in mutation_actions:
            assert svc.should_deliberate(risk_level="critical", action_class=action) is True, (
                f"Expected True for mutation action={action!r} at risk='critical'"
            )

    def test_mutation_at_medium_triggers(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        assert svc.should_deliberate(risk_level="medium", action_class="execute_command") is True
        assert svc.should_deliberate(risk_level="medium", action_class="write_local") is True

    def test_unknown_action_never_triggers(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        for risk in ("low", "medium", "high", "critical"):
            assert svc.should_deliberate(risk_level=risk, action_class="unknown") is False
            assert svc.should_deliberate(risk_level=risk, action_class="some_future_action") is False

    def test_low_risk_never_triggers(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        actions = [
            "execute_command",
            "write_local",
            "patch_file",
            "rollback",
            "read_local",
            "delegate_execution",
            "approval_resolution",
        ]
        for action in actions:
            assert svc.should_deliberate(risk_level="low", action_class=action) is False, (
                f"Expected False for action={action!r} at risk='low'"
            )

    def test_orchestration_actions_at_high_risk(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        orchestration_actions = ["delegate_execution", "approval_resolution"]
        for action in orchestration_actions:
            assert svc.should_deliberate(risk_level="high", action_class=action) is True, (
                f"Expected True for orchestration action={action!r} at risk='high'"
            )
            assert svc.should_deliberate(risk_level="medium", action_class=action) is False, (
                f"Expected False for orchestration action={action!r} at risk='medium'"
            )


# -- Static check_deliberation_needed ----------------------------------------


class TestStaticCheckDeliberationNeeded:
    """Verify that the static method mirrors the instance method results."""

    @pytest.mark.parametrize(
        ("risk", "action"),
        [
            # Readonly - never triggers
            ("critical", "read_local"),
            ("high", "network_read"),
            ("medium", "execute_command_readonly"),
            ("low", "delegate_reasoning"),
            # Mutations that trigger
            ("critical", "execute_command"),
            ("high", "write_local"),
            ("medium", "patch_file"),
            ("high", "rollback"),
            ("critical", "delegate_execution"),
            ("high", "approval_resolution"),
            # Low risk - never triggers
            ("low", "execute_command"),
            ("low", "write_local"),
            # Unknown action
            ("critical", "unknown"),
            ("medium", "some_future_action"),
            # Orchestration at medium - does not trigger
            ("medium", "delegate_execution"),
            ("medium", "approval_resolution"),
        ],
    )
    def test_matches_instance_method(
        self, tmp_path: Path, risk: str, action: str
    ) -> None:
        svc = _make_service(tmp_path)
        instance_result = svc.should_deliberate(risk_level=risk, action_class=action)
        static_result = DeliberationService.check_deliberation_needed(
            risk_level=risk, action_class=action
        )
        assert static_result == instance_result, (
            f"Static and instance mismatch for risk={risk!r}, action={action!r}: "
            f"static={static_result}, instance={instance_result}"
        )
