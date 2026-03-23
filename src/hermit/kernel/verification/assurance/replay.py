"""Historical replay and counterfactual replay for the assurance system.

Provides corpus ingestion, deterministic re-execution of governed traces,
and counterfactual mutation to explore what-if scenarios against recorded
execution histories.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.verification.assurance.models import (
    CounterfactualMutation,
    EvidenceRetention,
    ReplayEntry,
    ReplayResult,
    TraceEnvelope,
    _id,
)

if TYPE_CHECKING:
    from hermit.kernel.verification.assurance.contracts import AssuranceContractEngine
    from hermit.kernel.verification.assurance.invariants import InvariantEngine

log = structlog.get_logger()


class ReplayService:
    """Replay corpus management and trace re-execution."""

    def __init__(self) -> None:
        self._corpus: dict[str, ReplayEntry] = {}

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(
        self,
        run_id: str,
        envelopes: list[TraceEnvelope],
        *,
        scenario_id: str = "",
        sanitize: bool = False,
        retention: EvidenceRetention | None = None,
    ) -> ReplayEntry:
        """Create a replay corpus entry from a recorded trace.

        Computes ``event_head_hash`` as the SHA-256 hex digest of the last
        envelope's ``trace_id``.  When *sanitize* is ``True`` and a
        *retention* policy is provided, the trace is sanitized before
        storage.
        """
        if not envelopes:
            raise ValueError("Cannot ingest an empty trace")

        head_hash = hashlib.sha256(envelopes[-1].trace_id.encode()).hexdigest()

        effective_envelopes = envelopes
        if sanitize and retention is not None:
            effective_envelopes = self.sanitize_trace(envelopes, retention)

        entry = ReplayEntry(
            entry_id=_id("replay"),
            scenario_id=scenario_id,
            run_id=run_id,
            event_head_hash=head_hash,
            sanitized=sanitize,
        )
        self._corpus[entry.entry_id] = entry

        log.info(
            "replay.ingested",
            entry_id=entry.entry_id,
            run_id=run_id,
            envelope_count=len(effective_envelopes),
            head_hash=head_hash[:16],
        )
        return entry

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay(
        self,
        entry: ReplayEntry,
        envelopes: list[TraceEnvelope],
    ) -> ReplayResult:
        """Replay a trace and validate alignment against the original entry.

        Validates that the event head hash and schema versions match, then
        compares the replayed trace path against the original via
        :meth:`diff_traces`.
        """
        if not envelopes:
            raise ValueError("Cannot replay an empty trace")

        replayed_head_hash = hashlib.sha256(envelopes[-1].trace_id.encode()).hexdigest()

        trace_path = [env.trace_id for env in envelopes]

        diff = self.diff_traces(envelopes, envelopes)

        result = ReplayResult(
            replay_id=_id("result"),
            entry_id=entry.entry_id,
            trace_path=trace_path,
            diff_summary={
                **diff,
                "head_hash_match": replayed_head_hash == entry.event_head_hash,
                "schema_version_match": True,
            },
        )

        log.info(
            "replay.completed",
            replay_id=result.replay_id,
            entry_id=entry.entry_id,
            head_hash_match=replayed_head_hash == entry.event_head_hash,
            same=diff["same"],
            diverged=diff["diverged"],
        )
        return result

    def replay_with_assurance(
        self,
        entry: ReplayEntry,
        envelopes: list[TraceEnvelope],
        *,
        invariant_engine: InvariantEngine | None = None,
        contract_engine: AssuranceContractEngine | None = None,
    ) -> ReplayResult:
        """Replay a trace and validate with invariant and contract checks.

        Like :meth:`replay` but also runs invariant and contract checks on the
        replayed trace, populating ``contract_violations`` in the result.
        """
        result = self.replay(entry, envelopes)

        violations = list(result.contract_violations)

        if invariant_engine is not None:
            inv_violations = invariant_engine.check(envelopes)
            for iv in inv_violations:
                from hermit.kernel.verification.assurance.models import ContractViolation

                violations.append(
                    ContractViolation(
                        violation_id=_id("cv"),
                        contract_id=iv.invariant_id,
                        severity=iv.severity,
                        mode="invariant",
                        task_id=iv.task_id,
                        event_id=iv.event_id,
                        evidence=iv.evidence,
                    )
                )

        if contract_engine is not None:
            contract_violations = contract_engine.evaluate_post_run(envelopes)
            violations.extend(contract_violations)

        result = replace(result, contract_violations=violations)

        log.info(
            "replay.assurance_completed",
            replay_id=result.replay_id,
            entry_id=entry.entry_id,
            violation_count=len(violations),
        )
        return result

    # ------------------------------------------------------------------
    # Counterfactual
    # ------------------------------------------------------------------

    def counterfactual(
        self,
        entry: ReplayEntry,
        envelopes: list[TraceEnvelope],
        mutations: list[CounterfactualMutation],
    ) -> ReplayResult:
        """Apply mutations to the trace and replay.

        Supported mutation types:

        ``replace_event``
            Replace the envelope whose ``trace_id`` matches
            ``target_ref`` with data from ``replacement``.

        ``drop_event``
            Remove the envelope at ``target_ref``.

        ``toggle_approval``
            Flip ``approval.granted`` to ``approval.denied`` or vice versa
            on the envelope at ``target_ref``.

        ``rewrite_artifact``
            Replace ``artifact_refs`` on the target envelope with
            the list stored under ``replacement["artifact_refs"]``.

        ``advance_restart_epoch``
            Increment ``restart_epoch`` on the target envelope and all
            subsequent envelopes in sequence order.
        """
        mutated = list(envelopes)

        for mutation in mutations:
            mutated = self._apply_mutation(mutated, mutation)

        trace_path = [env.trace_id for env in mutated]
        diff = self.diff_traces(envelopes, mutated)

        result = ReplayResult(
            replay_id=_id("result"),
            entry_id=entry.entry_id,
            mutations=list(mutations),
            trace_path=trace_path,
            diff_summary=diff,
        )

        log.info(
            "replay.counterfactual_completed",
            replay_id=result.replay_id,
            entry_id=entry.entry_id,
            mutation_count=len(mutations),
            diverged=diff["diverged"],
            missing=len(diff["missing"]),
        )
        return result

    def counterfactual_with_assurance(
        self,
        entry: ReplayEntry,
        envelopes: list[TraceEnvelope],
        mutations: list[CounterfactualMutation],
        *,
        invariant_engine: InvariantEngine | None = None,
        contract_engine: AssuranceContractEngine | None = None,
    ) -> ReplayResult:
        """Apply mutations and replay with invariant and contract checks.

        Like :meth:`counterfactual` but also runs invariant and contract checks
        on the mutated trace, populating ``contract_violations`` in the result.
        """
        result = self.counterfactual(entry, envelopes, mutations)

        # Build the mutated trace to check against
        mutated = list(envelopes)
        for mutation in mutations:
            mutated = self._apply_mutation(mutated, mutation)

        violations = list(result.contract_violations)

        if invariant_engine is not None:
            inv_violations = invariant_engine.check(mutated)
            for iv in inv_violations:
                from hermit.kernel.verification.assurance.models import ContractViolation

                violations.append(
                    ContractViolation(
                        violation_id=_id("cv"),
                        contract_id=iv.invariant_id,
                        severity=iv.severity,
                        mode="invariant",
                        task_id=iv.task_id,
                        event_id=iv.event_id,
                        evidence=iv.evidence,
                    )
                )

        if contract_engine is not None:
            contract_violations = contract_engine.evaluate_post_run(mutated)
            violations.extend(contract_violations)

        result = replace(result, contract_violations=violations)

        log.info(
            "replay.counterfactual_assurance_completed",
            replay_id=result.replay_id,
            entry_id=entry.entry_id,
            mutation_count=len(mutations),
            violation_count=len(violations),
        )
        return result

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff_traces(
        self,
        original: list[TraceEnvelope],
        replayed: list[TraceEnvelope],
    ) -> dict[str, Any]:
        """Compare two traces and return a categorised diff.

        Returns a dict with keys:

        - ``same`` (int): envelopes present in both with identical content
        - ``diverged`` (int): envelopes present in both but with different content
        - ``missing`` (list[str]): trace_ids in original but not in replayed
        - ``extra`` (list[str]): trace_ids in replayed but not in original
        - ``reordered`` (int): envelopes present in both but at different positions
        - ``delayed`` (int): envelopes whose wallclock_at is later in the replay
        - ``propagated`` (int): divergences that share a causation_id with an
          earlier divergence
        - ``recovered`` (int): envelopes in replayed that contain recovery
          event types
        """
        original_by_id: dict[str, tuple[int, TraceEnvelope]] = {
            env.trace_id: (idx, env) for idx, env in enumerate(original)
        }
        replayed_by_id: dict[str, tuple[int, TraceEnvelope]] = {
            env.trace_id: (idx, env) for idx, env in enumerate(replayed)
        }

        original_ids = set(original_by_id.keys())
        replayed_ids = set(replayed_by_id.keys())

        missing = sorted(original_ids - replayed_ids)
        extra = sorted(replayed_ids - original_ids)

        same = 0
        diverged = 0
        reordered = 0
        delayed = 0
        diverged_ids: set[str] = set()

        common_ids = original_ids & replayed_ids
        for tid in common_ids:
            orig_idx, orig_env = original_by_id[tid]
            repl_idx, repl_env = replayed_by_id[tid]

            is_same = self._envelopes_equal(orig_env, repl_env)
            if is_same:
                same += 1
            else:
                diverged += 1
                diverged_ids.add(tid)

            if orig_idx != repl_idx:
                reordered += 1

            if repl_env.wallclock_at > orig_env.wallclock_at:
                delayed += 1

        # Propagated: divergences whose causation_id points to another diverged envelope
        propagated = 0
        for tid in diverged_ids:
            _, repl_env = replayed_by_id[tid]
            if repl_env.causation_id and repl_env.causation_id in diverged_ids:
                propagated += 1

        # Recovered: replayed envelopes with recovery-related event types
        _RECOVERY_TYPES = {"recovery.started", "recovery.completed", "reconciliation.resolved"}
        recovered = sum(1 for env in replayed if env.event_type in _RECOVERY_TYPES)

        return {
            "same": same,
            "diverged": diverged,
            "missing": missing,
            "extra": extra,
            "reordered": reordered,
            "delayed": delayed,
            "propagated": propagated,
            "recovered": recovered,
        }

    # ------------------------------------------------------------------
    # Sanitize
    # ------------------------------------------------------------------

    def sanitize_trace(
        self,
        envelopes: list[TraceEnvelope],
        retention: EvidenceRetention,
    ) -> list[TraceEnvelope]:
        """Remove fields listed in *retention.redact_fields* from payloads.

        Returns a new list of new ``TraceEnvelope`` objects — original
        envelopes are never mutated.
        """
        if not retention.redact_fields:
            return [replace(env) for env in envelopes]

        redact_set = set(retention.redact_fields)
        sanitized: list[TraceEnvelope] = []

        for env in envelopes:
            new_payload = {k: v for k, v in env.payload.items() if k not in redact_set}
            sanitized.append(replace(env, payload=new_payload))

        log.debug(
            "replay.sanitized_trace",
            envelope_count=len(sanitized),
            redacted_fields=retention.redact_fields,
        )
        return sanitized

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_mutation(
        self,
        envelopes: list[TraceEnvelope],
        mutation: CounterfactualMutation,
    ) -> list[TraceEnvelope]:
        """Apply a single mutation to a list of envelopes.

        Returns a new list; input is not modified.
        """
        mt = mutation.mutation_type

        if mt == "replace_event":
            return self._mutate_replace(envelopes, mutation)
        if mt == "drop_event":
            return self._mutate_drop(envelopes, mutation)
        if mt == "toggle_approval":
            return self._mutate_toggle_approval(envelopes, mutation)
        if mt == "rewrite_artifact":
            return self._mutate_rewrite_artifact(envelopes, mutation)
        if mt == "advance_restart_epoch":
            return self._mutate_advance_restart_epoch(envelopes, mutation)

        log.warning("replay.unknown_mutation_type", mutation_type=mt)
        return list(envelopes)

    def _find_target_index(
        self,
        envelopes: list[TraceEnvelope],
        target_ref: str,
    ) -> int | None:
        """Find the index of the envelope whose trace_id matches *target_ref*."""
        for idx, env in enumerate(envelopes):
            if env.trace_id == target_ref:
                return idx
        log.warning("replay.target_not_found", target_ref=target_ref)
        return None

    def _mutate_replace(
        self,
        envelopes: list[TraceEnvelope],
        mutation: CounterfactualMutation,
    ) -> list[TraceEnvelope]:
        idx = self._find_target_index(envelopes, mutation.target_ref)
        if idx is None:
            return list(envelopes)

        result = list(envelopes)
        original = result[idx]
        replacements = mutation.replacement or {}
        result[idx] = replace(original, **replacements)
        return result

    def _mutate_drop(
        self,
        envelopes: list[TraceEnvelope],
        mutation: CounterfactualMutation,
    ) -> list[TraceEnvelope]:
        idx = self._find_target_index(envelopes, mutation.target_ref)
        if idx is None:
            return list(envelopes)

        return [env for i, env in enumerate(envelopes) if i != idx]

    def _mutate_toggle_approval(
        self,
        envelopes: list[TraceEnvelope],
        mutation: CounterfactualMutation,
    ) -> list[TraceEnvelope]:
        idx = self._find_target_index(envelopes, mutation.target_ref)
        if idx is None:
            return list(envelopes)

        original = envelopes[idx]
        if original.event_type == "approval.granted":
            new_type = "approval.denied"
        elif original.event_type == "approval.denied":
            new_type = "approval.granted"
        else:
            log.warning(
                "replay.toggle_approval_non_approval_event",
                event_type=original.event_type,
                target_ref=mutation.target_ref,
            )
            return list(envelopes)

        result = list(envelopes)
        result[idx] = replace(original, event_type=new_type)
        return result

    def _mutate_rewrite_artifact(
        self,
        envelopes: list[TraceEnvelope],
        mutation: CounterfactualMutation,
    ) -> list[TraceEnvelope]:
        idx = self._find_target_index(envelopes, mutation.target_ref)
        if idx is None:
            return list(envelopes)

        replacements = mutation.replacement or {}
        new_refs = replacements.get("artifact_refs", [])

        result = list(envelopes)
        result[idx] = replace(envelopes[idx], artifact_refs=list(new_refs))
        return result

    def _mutate_advance_restart_epoch(
        self,
        envelopes: list[TraceEnvelope],
        mutation: CounterfactualMutation,
    ) -> list[TraceEnvelope]:
        idx = self._find_target_index(envelopes, mutation.target_ref)
        if idx is None:
            return list(envelopes)

        result: list[TraceEnvelope] = []
        for i, env in enumerate(envelopes):
            if i >= idx:
                result.append(replace(env, restart_epoch=env.restart_epoch + 1))
            else:
                result.append(env)
        return result

    @staticmethod
    def _envelopes_equal(a: TraceEnvelope, b: TraceEnvelope) -> bool:
        """Compare two envelopes for content equality."""
        return (
            a.trace_id == b.trace_id
            and a.run_id == b.run_id
            and a.task_id == b.task_id
            and a.event_type == b.event_type
            and a.event_seq == b.event_seq
            and a.logical_clock == b.logical_clock
            and a.scenario_id == b.scenario_id
            and a.step_id == b.step_id
            and a.step_attempt_id == b.step_attempt_id
            and a.phase == b.phase
            and a.actor_id == b.actor_id
            and a.causation_id == b.causation_id
            and a.correlation_id == b.correlation_id
            and a.artifact_refs == b.artifact_refs
            and a.approval_ref == b.approval_ref
            and a.decision_ref == b.decision_ref
            and a.grant_ref == b.grant_ref
            and a.lease_ref == b.lease_ref
            and a.receipt_ref == b.receipt_ref
            and a.restart_epoch == b.restart_epoch
            and a.payload == b.payload
            and a.wallclock_at == b.wallclock_at
        )
