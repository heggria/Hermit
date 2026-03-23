"""SteeringProtocol — lifecycle management for mid-execution steering directives."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from hermit.kernel.signals.models import SteeringDirective

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()


class SteeringProtocol:
    """Manages the lifecycle of steering directives."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def issue(self, directive: SteeringDirective) -> SteeringDirective:
        """Persist directive and emit steering.issued event."""
        self._store.create_steering(directive)
        self._emit_event("steering.issued", directive)
        self._mark_input_dirty(directive.task_id)
        return directive

    def acknowledge(self, directive_id: str) -> None:
        """Mark as acknowledged and emit event."""
        sig = self._store.get_signal(directive_id)
        if sig is None:
            return
        self._store.update_steering_disposition(directive_id, "acknowledged")
        directive = SteeringDirective.from_signal(sig)
        directive.disposition = "acknowledged"
        self._emit_event("steering.acknowledged", directive)

    def apply(self, directive_id: str) -> None:
        """Mark as applied and record applied_at."""
        sig = self._store.get_signal(directive_id)
        if sig is None:
            return
        now = time.time()
        self._store.update_steering_disposition(directive_id, "applied", applied_at=now)
        directive = SteeringDirective.from_signal(sig)
        directive.disposition = "applied"
        self._emit_event("steering.applied", directive)

    def reject(self, directive_id: str, reason: str = "") -> None:
        """Mark as rejected and emit event with reason."""
        sig = self._store.get_signal(directive_id)
        if sig is None:
            return
        self._store.update_steering_disposition(directive_id, "rejected")
        directive = SteeringDirective.from_signal(sig)
        directive.disposition = "rejected"
        self._emit_event("steering.rejected", directive, extra={"reason": reason})

    def supersede(self, old_id: str, new: SteeringDirective) -> SteeringDirective:
        """Mark old as superseded, set new's supersedes_id, then issue new."""
        sig = self._store.get_signal(old_id)
        if sig is not None:
            self._store.update_steering_disposition(old_id, "superseded")
            old_directive = SteeringDirective.from_signal(sig)
            old_directive.disposition = "superseded"
            self._emit_event(
                "steering.superseded", old_directive, extra={"superseded_by": new.directive_id}
            )
        new.supersedes_id = old_id
        return self.issue(new)

    def active_for_task(self, task_id: str) -> list[SteeringDirective]:
        """Return directives in pending/acknowledged/applied states."""
        return self._store.active_steerings_for_task(task_id)

    def _emit_event(
        self,
        event_type: str,
        directive: SteeringDirective,
        extra: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "directive_id": directive.directive_id,
            "steering_type": directive.steering_type,
            "directive": directive.directive,
            "disposition": directive.disposition,
            "issued_by": directive.issued_by,
        }
        if extra:
            payload.update(extra)
        self._store.append_event(
            event_type=event_type,
            entity_type="steering",
            entity_id=directive.directive_id,
            task_id=directive.task_id or None,
            actor=directive.issued_by,
            payload=payload,
        )

    def _mark_input_dirty(self, task_id: str) -> None:
        """Set input_dirty on the active step attempt for this task."""
        try:
            attempts = self._store.list_step_attempts(task_id=task_id, limit=1)
            if not attempts:
                return
            latest_attempt = attempts[0]
            ctx = dict(latest_attempt.context or {})
            ctx["input_dirty"] = True
            self._store.update_step_attempt(latest_attempt.step_attempt_id, context=ctx)
            self._store.append_event(
                event_type="step_attempt.input_dirty",
                entity_type="step_attempt",
                entity_id=latest_attempt.step_attempt_id,
                task_id=task_id,
                actor="steering",
                payload={"reason": "steering_directive_issued"},
            )
        except Exception:
            log.debug("steering_directive_error", exc_info=True)
