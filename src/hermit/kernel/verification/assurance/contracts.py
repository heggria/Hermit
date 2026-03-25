"""Contract engine for the Trace-Contract-Driven Assurance System.

Evaluates trace contracts against single events (runtime) or full traces
(post_run) using a recursive predicate algebra.
"""

from __future__ import annotations

import operator
from collections import Counter
from typing import Any

import structlog

from hermit.kernel.verification.assurance.models import (
    ContractViolation,
    TraceContractSpec,
    TraceEnvelope,
    _id,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Default TTL for bounded_stuck (seconds)
# ---------------------------------------------------------------------------

_DEFAULT_STUCK_TTL_SECONDS: float = 600.0

# ---------------------------------------------------------------------------
# Comparison operators for the `count` predicate
# ---------------------------------------------------------------------------

_CMP_OPS: dict[str, Any] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    ">": operator.gt,
    "<": operator.lt,
}


class AssuranceContractEngine:
    """Evaluate trace contracts against runtime events or full traces.

    Contracts are registered via :meth:`register` or loaded automatically by
    :meth:`_register_builtins`.  Evaluation is split into two modes:

    * **runtime** -- checked on each incoming event individually.
    * **post_run** -- checked once against the full ordered trace.
    """

    def __init__(self) -> None:
        self._contracts: dict[str, TraceContractSpec] = {}
        self._register_builtins()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: TraceContractSpec) -> None:
        """Register (or replace) a contract specification."""
        self._contracts[spec.contract_id] = spec
        log.debug("contract.registered", contract_id=spec.contract_id, mode=spec.mode)

    # ------------------------------------------------------------------
    # Runtime evaluation (single event)
    # ------------------------------------------------------------------

    def evaluate_runtime(
        self,
        envelope: TraceEnvelope,
        *,
        context: dict[str, Any] | None = None,
    ) -> list[ContractViolation]:
        """Check all runtime-mode contracts against a single event.

        *context* may carry extra metadata (e.g. ``prior_envelopes``) used
        by predicate helpers.
        """
        prior: list[TraceEnvelope] = []
        if context and "prior_envelopes" in context:
            prior = context["prior_envelopes"]

        # Build the full trace visible at evaluation time: prior events + current.
        trace_so_far = [*prior, envelope]

        violations: list[ContractViolation] = []
        for spec in self._contracts.values():
            if spec.mode not in ("runtime", "both"):
                continue

            if not self._scope_matches(spec, envelope):
                continue

            passed = self.evaluate_predicate(
                spec.assert_expr,
                trace_so_far,
                current=envelope,
            )
            if not passed:
                violations.append(
                    ContractViolation(
                        violation_id=_id("cv"),
                        contract_id=spec.contract_id,
                        severity=spec.severity,
                        mode="runtime",
                        task_id=envelope.task_id,
                        event_id=envelope.trace_id,
                        evidence={
                            "event_type": envelope.event_type,
                            "event_seq": envelope.event_seq,
                        },
                        remediation_hint=spec.remediation_hint,
                    )
                )
        return violations

    # ------------------------------------------------------------------
    # Post-run evaluation (full trace)
    # ------------------------------------------------------------------

    def evaluate_post_run(
        self,
        envelopes: list[TraceEnvelope],
        *,
        task_id: str | None = None,
    ) -> list[ContractViolation]:
        """Check all post_run-mode contracts against the full trace."""
        effective_task_id = task_id or (envelopes[0].task_id if envelopes else "unknown")

        violations: list[ContractViolation] = []
        for spec in self._contracts.values():
            if spec.mode not in ("post_run", "both"):
                continue

            passed = self.evaluate_predicate(spec.assert_expr, envelopes)
            if not passed:
                violations.append(
                    ContractViolation(
                        violation_id=_id("cv"),
                        contract_id=spec.contract_id,
                        severity=spec.severity,
                        mode="post_run",
                        task_id=effective_task_id,
                        evidence={"trace_length": len(envelopes)},
                        remediation_hint=spec.remediation_hint,
                    )
                )
        return violations

    # ------------------------------------------------------------------
    # Predicate algebra
    # ------------------------------------------------------------------

    def evaluate_predicate(
        self,
        predicate: dict[str, Any],
        envelopes: list[TraceEnvelope],
        *,
        current: TraceEnvelope | None = None,
    ) -> bool:
        """Recursively evaluate a predicate dict against a trace.

        Supported operators:
            all, any, not, exists, count, before, after, eq, in_set,
            has_field, custom.
        """
        if not predicate:
            return True

        # ---- Combinators ----
        if "all" in predicate:
            return all(
                self.evaluate_predicate(p, envelopes, current=current) for p in predicate["all"]
            )

        if "any" in predicate:
            return any(
                self.evaluate_predicate(p, envelopes, current=current) for p in predicate["any"]
            )

        if "not" in predicate:
            return not self.evaluate_predicate(predicate["not"], envelopes, current=current)

        # ---- Existence ----
        if "exists" in predicate:
            target = predicate["exists"]
            return any(e.event_type == target for e in envelopes)

        # ---- Count ----
        if "count" in predicate:
            spec = predicate["count"]
            event_type = spec["event"]
            op_str = spec["op"]
            value = spec["value"]
            actual = sum(1 for e in envelopes if e.event_type == event_type)
            cmp_fn = _CMP_OPS.get(op_str)
            if cmp_fn is None:
                log.warning("predicate.unknown_op", op=op_str)
                return False
            return bool(cmp_fn(actual, value))

        # ---- Ordering: before ----
        if "before" in predicate:
            spec = predicate["before"]
            return self._check_ordering(envelopes, spec["event1"], spec["event2"])

        # ---- Ordering: after ----
        if "after" in predicate:
            spec = predicate["after"]
            return self._check_ordering(envelopes, spec["event2"], spec["event1"])

        # ---- Field equality ----
        if "eq" in predicate:
            spec = predicate["eq"]
            return self._field_eq(current, spec["field"], spec["value"])

        # ---- Field membership ----
        if "in_set" in predicate:
            spec = predicate["in_set"]
            return self._field_in_set(current, spec["field"], spec["values"])

        # ---- has_field (check non-None field on current) ----
        if "has_field" in predicate:
            return self._has_field(current, predicate["has_field"])

        # ---- No duplicate pairs ----
        if "no_duplicate_pairs" in predicate:
            spec = predicate["no_duplicate_pairs"]
            return self._no_duplicate_pairs(envelopes, spec["field1"], spec["field2"])

        # ---- bounded_gap (wallclock gap check) ----
        if "bounded_gap" in predicate:
            spec = predicate["bounded_gap"]
            return self._bounded_gap(
                envelopes, spec["event_type"], spec.get("max_seconds", _DEFAULT_STUCK_TTL_SECONDS)
            )

        # ---- every (apply sub-predicate to all matching events) ----
        if "every" in predicate:
            spec = predicate["every"]
            event_type = spec["event"]
            sub = spec["pred"]
            matching = [e for e in envelopes if e.event_type == event_type]
            if not matching:
                return True
            return all(self.evaluate_predicate(sub, envelopes, current=e) for e in matching)

        log.warning("predicate.unsupported", keys=list(predicate.keys()))
        return False

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_ordering(envelopes: list[TraceEnvelope], first_type: str, second_type: str) -> bool:
        """Return True if *first_type* appears before *second_type* by event_seq."""
        first_seq: int | None = None
        second_seq: int | None = None
        for e in envelopes:
            if e.event_type == first_type and first_seq is None:
                first_seq = e.event_seq
            if e.event_type == second_type and second_seq is None:
                second_seq = e.event_seq
        if first_seq is None or second_seq is None:
            return False
        return first_seq < second_seq

    @staticmethod
    def _field_eq(envelope: TraceEnvelope | None, field: str, value: Any) -> bool:
        if envelope is None:
            return False
        actual = getattr(envelope, field, None)
        return actual == value

    @staticmethod
    def _field_in_set(envelope: TraceEnvelope | None, field: str, values: list[Any]) -> bool:
        if envelope is None:
            return False
        actual = getattr(envelope, field, None)
        return actual in values

    @staticmethod
    def _has_field(envelope: TraceEnvelope | None, field: str) -> bool:
        if envelope is None:
            return False
        val = getattr(envelope, field, None)
        return val is not None

    @staticmethod
    def _no_duplicate_pairs(envelopes: list[TraceEnvelope], field1: str, field2: str) -> bool:
        """Return True if no two envelopes share the same (field1, field2) pair."""
        seen: Counter[tuple[Any, Any]] = Counter()
        for e in envelopes:
            v1 = getattr(e, field1, None)
            v2 = getattr(e, field2, None)
            if v1 is None or v2 is None:
                continue
            pair = (v1, v2)
            seen[pair] += 1
            if seen[pair] > 1:
                return False
        return True

    @staticmethod
    def _bounded_gap(
        envelopes: list[TraceEnvelope],
        event_type: str,
        max_seconds: float,
    ) -> bool:
        """Return True if no step runs longer than *max_seconds*.

        Measures wallclock gap between consecutive events of *event_type*.
        """
        timestamps = sorted(e.wallclock_at for e in envelopes if e.event_type == event_type)
        for i in range(1, len(timestamps)):
            if timestamps[i] - timestamps[i - 1] > max_seconds:
                return False
        return True

    # ------------------------------------------------------------------
    # Scope matching
    # ------------------------------------------------------------------

    @staticmethod
    def _scope_matches(spec: TraceContractSpec, envelope: TraceEnvelope) -> bool:
        """Check whether the contract scope applies to the given envelope.

        If ``scope`` is empty the contract applies to everything.  Otherwise
        the scope dict may contain:

        * ``event_types`` -- list of event types the contract applies to.
        * ``action_class`` -- action classes (checked against payload).
        """
        if not spec.scope:
            return True

        if "event_types" in spec.scope and envelope.event_type not in spec.scope["event_types"]:
            return False

        if "action_class" in spec.scope:
            action = envelope.payload.get("action_class")
            if action not in spec.scope["action_class"]:
                return True  # scope doesn't restrict this event

        return True

    # ------------------------------------------------------------------
    # Built-in contracts
    # ------------------------------------------------------------------

    def _register_builtins(self) -> None:
        """Register the standard built-in contracts."""

        # 1. task.lifecycle (post_run, blocker)
        self.register(
            TraceContractSpec(
                contract_id="task.lifecycle",
                mode="post_run",
                severity="blocker",
                assert_expr={
                    "all": [
                        {"exists": "task.created"},
                        {
                            "any": [
                                {"exists": "task.completed"},
                                {"exists": "task.failed"},
                            ]
                        },
                    ]
                },
                remediation_hint=(
                    "Task trace must contain a task.created event and at least "
                    "one task.completed or task.failed event."
                ),
            )
        )

        # 2. approval.gating (runtime, blocker)
        self.register(
            TraceContractSpec(
                contract_id="approval.gating",
                scope={"event_types": ["tool_call.start"]},
                mode="runtime",
                severity="blocker",
                assert_expr={
                    "before": {
                        "event1": "approval.granted",
                        "event2": "tool_call.start",
                    }
                },
                remediation_hint=(
                    "approval.granted must appear in the trace before tool_call.start."
                ),
            )
        )

        # 3. side_effect.authorization (runtime, blocker)
        self.register(
            TraceContractSpec(
                contract_id="side_effect.authorization",
                scope={"event_types": ["tool_call.start"]},
                mode="runtime",
                severity="blocker",
                assert_expr={"has_field": "grant_ref"},
                remediation_hint=("tool_call.start events must carry a grant_ref."),
            )
        )

        # 4. receipt.linkage (post_run, high)
        self.register(
            TraceContractSpec(
                contract_id="receipt.linkage",
                mode="post_run",
                severity="high",
                assert_expr={
                    "every": {
                        "event": "receipt.issued",
                        "pred": {
                            "all": [
                                {"has_field": "decision_ref"},
                                {"has_field": "grant_ref"},
                            ]
                        },
                    }
                },
                remediation_hint=(
                    "Every receipt.issued event must carry decision_ref and grant_ref."
                ),
            )
        )

        # 5. no_duplicate_execution (post_run, blocker)
        self.register(
            TraceContractSpec(
                contract_id="no_duplicate_execution",
                mode="post_run",
                severity="blocker",
                assert_expr={
                    "no_duplicate_pairs": {
                        "field1": "step_attempt_id",
                        "field2": "receipt_ref",
                    }
                },
                remediation_hint=(
                    "No two events may share the same (step_attempt_id, receipt_ref) pair."
                ),
            )
        )

        # 6. bounded_stuck (post_run, high)
        self.register(
            TraceContractSpec(
                contract_id="bounded_stuck",
                mode="post_run",
                severity="high",
                assert_expr={
                    "bounded_gap": {
                        "event_type": "tool_call.start",
                        "max_seconds": _DEFAULT_STUCK_TTL_SECONDS,
                    }
                },
                remediation_hint=(
                    "No step should run longer than the configured TTL. "
                    "Check for hung tool calls or missing receipts."
                ),
            )
        )

        # 7. workspace.isolation (runtime, blocker)
        self.register(
            TraceContractSpec(
                contract_id="workspace.isolation",
                scope={"event_types": ["tool_call.start"]},
                mode="runtime",
                severity="blocker",
                assert_expr={"has_field": "lease_ref"},
                remediation_hint=(
                    "tool_call.start events must carry a lease_ref for workspace isolation."
                ),
            )
        )

        log.debug("contracts.builtins_registered", count=len(self._contracts))
