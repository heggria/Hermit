"""Harness-only fault injection for the assurance system.

FaultInjector is gated behind ``harness_mode=True`` so it can never
be instantiated accidentally in production code.  It manages armed
faults, evaluates trigger conditions against runtime events, and
enforces cardinality semantics (once / repeated / probabilistic).
"""

from __future__ import annotations

import random

import structlog

from hermit.kernel.verification.assurance.models import FaultHandle, FaultSpec, _id

log = structlog.get_logger()

_VALID_CARDINALITIES = frozenset({"once", "repeated", "probabilistic"})


class FaultInjector:
    """Register, trigger, and manage fault injections.

    Must be instantiated with ``harness_mode=True``; raises
    ``RuntimeError`` otherwise.
    """

    def __init__(self, *, harness_mode: bool = False) -> None:
        if not harness_mode:
            raise RuntimeError("FaultInjector can only be used in harness mode")
        self._armed: dict[str, FaultHandle] = {}
        self._rng = random.Random()

    # ------------------------------------------------------------------
    # Arming / disarming
    # ------------------------------------------------------------------

    def arm(self, spec: FaultSpec, *, seed: int | None = None) -> FaultHandle:
        """Register a fault and return a handle to it.

        If *seed* is provided, the internal RNG is re-seeded.  This is
        primarily useful for ``probabilistic`` cardinality to guarantee
        deterministic behaviour in tests.
        """
        if spec.cardinality not in _VALID_CARDINALITIES:
            raise ValueError(
                f"Invalid cardinality {spec.cardinality!r}; "
                f"expected one of {sorted(_VALID_CARDINALITIES)}"
            )

        if seed is not None:
            self._rng = random.Random(seed)

        handle = FaultHandle(
            handle_id=_id("fault"),
            fault_spec=spec,
        )
        self._armed[handle.handle_id] = handle
        log.debug(
            "fault_armed",
            handle_id=handle.handle_id,
            injection_point=spec.injection_point,
            cardinality=spec.cardinality,
        )
        return handle

    def disarm(self, handle: FaultHandle) -> None:
        """Remove a previously armed fault."""
        removed = self._armed.pop(handle.handle_id, None)
        if removed is not None:
            log.debug("fault_disarmed", handle_id=handle.handle_id)

    def disarm_all(self) -> None:
        """Remove every armed fault."""
        count = len(self._armed)
        self._armed.clear()
        log.debug("faults_disarmed_all", count=count)

    # ------------------------------------------------------------------
    # Triggering
    # ------------------------------------------------------------------

    def trigger(self, handle: FaultHandle, *, context: dict | None = None) -> bool:
        """Attempt to fire an armed fault.

        Returns ``True`` if the fault fired, ``False`` otherwise.

        Cardinality rules:
        * **once** -- only the first trigger succeeds.
        * **repeated** -- every trigger succeeds.
        * **probabilistic** -- fires with 50 % probability (seeded RNG).
        """
        if handle.handle_id not in self._armed:
            return False

        spec = handle.fault_spec
        cardinality = spec.cardinality

        if cardinality == "once":
            if handle.triggered:
                return False
            handle.triggered = True
            handle.trigger_count += 1
            log.debug("fault_triggered", handle_id=handle.handle_id, cardinality="once")
            return True

        if cardinality == "repeated":
            handle.triggered = True
            handle.trigger_count += 1
            log.debug(
                "fault_triggered",
                handle_id=handle.handle_id,
                cardinality="repeated",
                count=handle.trigger_count,
            )
            return True

        if cardinality == "probabilistic":
            if self._rng.random() < 0.5:
                handle.triggered = True
                handle.trigger_count += 1
                log.debug(
                    "fault_triggered",
                    handle_id=handle.handle_id,
                    cardinality="probabilistic",
                    count=handle.trigger_count,
                )
                return True
            return False

        return False  # pragma: no cover – invalid cardinality caught at arm()

    def check_trigger(self, injection_point: str, event: dict) -> list[FaultHandle]:
        """Return armed faults whose injection_point and trigger_condition match *event*.

        A trigger_condition matches when every key-value pair in the
        condition dict is present (with equal value) in *event*.
        """
        matched: list[FaultHandle] = []
        for handle in list(self._armed.values()):
            spec = handle.fault_spec
            if spec.injection_point != injection_point:
                continue
            if _condition_matches(spec.trigger_condition, event):
                matched.append(handle)
        return matched

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_armed(self) -> list[FaultHandle]:
        """Return all currently armed faults (whether triggered or not)."""
        return list(self._armed.values())

    def get_triggered(self) -> list[FaultHandle]:
        """Return armed faults that have fired at least once."""
        return [h for h in self._armed.values() if h.triggered]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _condition_matches(condition: dict, event: dict) -> bool:
    """Return ``True`` when every key-value pair in *condition* exists in *event*."""
    return all(key in event and event[key] == value for key, value in condition.items())
