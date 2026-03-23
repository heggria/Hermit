"""Unit tests for the FaultInjector."""

from __future__ import annotations

import pytest

from hermit.kernel.verification.assurance.injection import FaultInjector
from hermit.kernel.verification.assurance.models import (
    INJ_APPROVAL_QUEUE,
    INJ_QUEUE_DISPATCH,
    INJ_TOOL_PRE_CALL,
    FaultSpec,
)

# ------------------------------------------------------------------
# Harness-mode guard
# ------------------------------------------------------------------


class TestHarnessModeGuard:
    def test_rejects_default_mode(self) -> None:
        with pytest.raises(RuntimeError, match="harness mode"):
            FaultInjector()

    def test_rejects_explicit_false(self) -> None:
        with pytest.raises(RuntimeError, match="harness mode"):
            FaultInjector(harness_mode=False)

    def test_accepts_harness_mode(self) -> None:
        injector = FaultInjector(harness_mode=True)
        assert injector.get_armed() == []


# ------------------------------------------------------------------
# Arm / disarm
# ------------------------------------------------------------------


class TestArmDisarm:
    def test_arm_returns_handle(self) -> None:
        injector = FaultInjector(harness_mode=True)
        spec = FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once")
        handle = injector.arm(spec)

        assert handle.handle_id.startswith("fault-")
        assert handle.fault_spec is spec
        assert handle.triggered is False
        assert handle.trigger_count == 0

    def test_arm_invalid_cardinality(self) -> None:
        injector = FaultInjector(harness_mode=True)
        spec = FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="never")
        with pytest.raises(ValueError, match="Invalid cardinality"):
            injector.arm(spec)

    def test_armed_listed(self) -> None:
        injector = FaultInjector(harness_mode=True)
        h1 = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))
        h2 = injector.arm(FaultSpec(injection_point=INJ_TOOL_PRE_CALL, cardinality="repeated"))

        armed = injector.get_armed()
        ids = {h.handle_id for h in armed}
        assert h1.handle_id in ids
        assert h2.handle_id in ids
        assert len(armed) == 2

    def test_disarm_removes(self) -> None:
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))
        assert len(injector.get_armed()) == 1

        injector.disarm(handle)
        assert injector.get_armed() == []

    def test_disarm_idempotent(self) -> None:
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))
        injector.disarm(handle)
        # Second disarm should not raise
        injector.disarm(handle)
        assert injector.get_armed() == []

    def test_disarm_all(self) -> None:
        injector = FaultInjector(harness_mode=True)
        injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))
        injector.arm(FaultSpec(injection_point=INJ_TOOL_PRE_CALL, cardinality="repeated"))
        assert len(injector.get_armed()) == 2

        injector.disarm_all()
        assert injector.get_armed() == []


# ------------------------------------------------------------------
# Trigger – once
# ------------------------------------------------------------------


class TestTriggerOnce:
    def test_first_trigger_succeeds(self) -> None:
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))

        assert injector.trigger(handle) is True
        assert handle.triggered is True
        assert handle.trigger_count == 1

    def test_second_trigger_fails(self) -> None:
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))

        injector.trigger(handle)
        assert injector.trigger(handle) is False
        assert handle.trigger_count == 1

    def test_disarmed_handle_does_not_trigger(self) -> None:
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))
        injector.disarm(handle)

        assert injector.trigger(handle) is False


# ------------------------------------------------------------------
# Trigger – repeated
# ------------------------------------------------------------------


class TestTriggerRepeated:
    def test_triggers_every_time(self) -> None:
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="repeated"))

        for _i in range(5):
            assert injector.trigger(handle) is True
        assert handle.trigger_count == 5
        assert handle.triggered is True


# ------------------------------------------------------------------
# Trigger – probabilistic
# ------------------------------------------------------------------


class TestTriggerProbabilistic:
    def test_deterministic_with_seed(self) -> None:
        """Same seed must produce the same sequence of trigger outcomes."""
        results_a: list[bool] = []
        results_b: list[bool] = []

        for results in (results_a, results_b):
            injector = FaultInjector(harness_mode=True)
            handle = injector.arm(
                FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="probabilistic"),
                seed=42,
            )
            for _ in range(20):
                results.append(injector.trigger(handle))

        assert results_a == results_b

    def test_some_triggers_some_not(self) -> None:
        """With enough attempts, probabilistic should produce a mix."""
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(
            FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="probabilistic"),
            seed=12345,
        )
        outcomes = [injector.trigger(handle) for _ in range(100)]
        assert any(outcomes), "Expected at least one True"
        assert not all(outcomes), "Expected at least one False"

    def test_trigger_count_matches_successes(self) -> None:
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(
            FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="probabilistic"),
            seed=99,
        )
        outcomes = [injector.trigger(handle) for _ in range(50)]
        assert handle.trigger_count == sum(outcomes)


# ------------------------------------------------------------------
# check_trigger – condition matching
# ------------------------------------------------------------------


class TestCheckTrigger:
    def test_matches_injection_point_and_condition(self) -> None:
        injector = FaultInjector(harness_mode=True)
        spec = FaultSpec(
            injection_point=INJ_QUEUE_DISPATCH,
            trigger_condition={"event": "tool_call.start"},
            cardinality="once",
        )
        handle = injector.arm(spec)

        matched = injector.check_trigger(
            INJ_QUEUE_DISPATCH,
            {"event": "tool_call.start", "tool": "bash"},
        )
        assert len(matched) == 1
        assert matched[0].handle_id == handle.handle_id

    def test_no_match_wrong_injection_point(self) -> None:
        injector = FaultInjector(harness_mode=True)
        injector.arm(
            FaultSpec(
                injection_point=INJ_QUEUE_DISPATCH,
                trigger_condition={"event": "tool_call.start"},
                cardinality="once",
            )
        )

        matched = injector.check_trigger(
            INJ_TOOL_PRE_CALL,
            {"event": "tool_call.start"},
        )
        assert matched == []

    def test_no_match_wrong_condition(self) -> None:
        injector = FaultInjector(harness_mode=True)
        injector.arm(
            FaultSpec(
                injection_point=INJ_QUEUE_DISPATCH,
                trigger_condition={"event": "tool_call.start"},
                cardinality="once",
            )
        )

        matched = injector.check_trigger(
            INJ_QUEUE_DISPATCH,
            {"event": "approval.granted"},
        )
        assert matched == []

    def test_empty_condition_matches_everything(self) -> None:
        injector = FaultInjector(harness_mode=True)
        handle = injector.arm(
            FaultSpec(
                injection_point=INJ_QUEUE_DISPATCH,
                trigger_condition={},
                cardinality="once",
            )
        )

        matched = injector.check_trigger(
            INJ_QUEUE_DISPATCH,
            {"event": "anything", "extra": True},
        )
        assert len(matched) == 1
        assert matched[0].handle_id == handle.handle_id

    def test_multi_key_condition_requires_all(self) -> None:
        injector = FaultInjector(harness_mode=True)
        injector.arm(
            FaultSpec(
                injection_point=INJ_QUEUE_DISPATCH,
                trigger_condition={"event": "tool_call.start", "tool": "bash"},
                cardinality="once",
            )
        )

        # Missing "tool" key
        assert (
            injector.check_trigger(
                INJ_QUEUE_DISPATCH,
                {"event": "tool_call.start"},
            )
            == []
        )

        # Both present and matching
        assert (
            len(
                injector.check_trigger(
                    INJ_QUEUE_DISPATCH,
                    {"event": "tool_call.start", "tool": "bash"},
                )
            )
            == 1
        )

    def test_returns_multiple_matches(self) -> None:
        injector = FaultInjector(harness_mode=True)
        h1 = injector.arm(
            FaultSpec(
                injection_point=INJ_QUEUE_DISPATCH,
                trigger_condition={"event": "tool_call.start"},
                cardinality="once",
            )
        )
        h2 = injector.arm(
            FaultSpec(
                injection_point=INJ_QUEUE_DISPATCH,
                trigger_condition={"event": "tool_call.start"},
                cardinality="repeated",
            )
        )

        matched = injector.check_trigger(
            INJ_QUEUE_DISPATCH,
            {"event": "tool_call.start"},
        )
        ids = {h.handle_id for h in matched}
        assert h1.handle_id in ids
        assert h2.handle_id in ids


# ------------------------------------------------------------------
# get_triggered / get_armed
# ------------------------------------------------------------------


class TestIntrospection:
    def test_get_triggered_empty_initially(self) -> None:
        injector = FaultInjector(harness_mode=True)
        injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))
        assert injector.get_triggered() == []

    def test_get_triggered_after_fire(self) -> None:
        injector = FaultInjector(harness_mode=True)
        h1 = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))
        h2 = injector.arm(FaultSpec(injection_point=INJ_APPROVAL_QUEUE, cardinality="once"))

        injector.trigger(h1)

        triggered = injector.get_triggered()
        assert len(triggered) == 1
        assert triggered[0].handle_id == h1.handle_id

        # h2 not triggered
        assert h2.handle_id not in {h.handle_id for h in triggered}

    def test_get_armed_includes_triggered_and_untriggered(self) -> None:
        injector = FaultInjector(harness_mode=True)
        h1 = injector.arm(FaultSpec(injection_point=INJ_QUEUE_DISPATCH, cardinality="once"))
        h2 = injector.arm(FaultSpec(injection_point=INJ_APPROVAL_QUEUE, cardinality="once"))

        injector.trigger(h1)

        armed = injector.get_armed()
        ids = {h.handle_id for h in armed}
        assert h1.handle_id in ids
        assert h2.handle_id in ids
        assert len(armed) == 2
