"""Task-level pattern learning from completed tasks.

Aggregates step-level execution patterns across completed tasks to identify
recurring multi-step sequences.  Patterns are stored as ``task_pattern``
memory records and can be retrieved as reference information for subsequent
similar tasks.

Patterns with fewer than 2 steps are not stored (too generic).
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Pattern descriptor
# ---------------------------------------------------------------------------


@dataclass
class TaskPattern:
    """Describes a recurring multi-step execution pattern."""

    pattern_fingerprint: str
    step_fingerprints: list[str] = field(default_factory=list[str])
    step_descriptions: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    goal_keywords: list[str] = field(default_factory=list[str])
    invocation_count: int = 0
    success_count: int = 0
    success_rate: float = 0.0
    source_task_refs: list[str] = field(default_factory=list[str])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "to",
        "for",
        "of",
        "in",
        "on",
        "at",
        "is",
        "it",
        "this",
        "that",
        "with",
        "from",
        "by",
    }
)

_KEYWORD_PATTERN = re.compile(r"[a-z0-9_]+")


def _extract_keywords(goal: str) -> list[str]:
    """Extract meaningful keywords from a task goal string."""
    words = _KEYWORD_PATTERN.findall(goal.lower())
    return sorted({w for w in words if w not in _STOP_WORDS and len(w) > 1})


def _step_fingerprint(action_class: str, tool_name: str) -> str:
    """Stable fingerprint for a single step."""
    raw = f"{action_class}:{tool_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _pattern_fingerprint(step_fingerprints: list[str]) -> str:
    """Stable fingerprint for an ordered sequence of steps."""
    raw = "|".join(step_fingerprints)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Learner service
# ---------------------------------------------------------------------------


class TaskPatternLearner:
    """Learns multi-step execution patterns from completed tasks."""

    def __init__(self, store: KernelStore) -> None:
        self.store = store

    def learn_from_completed_task(self, task_id: str) -> MemoryRecord | None:
        """Extract an execution pattern from a completed task.

        Returns the created or reinforced ``MemoryRecord``
        (``memory_kind="task_pattern"``) or ``None`` when the task is not
        eligible (e.g. fewer than 2 satisfied steps).
        """
        # Gather all satisfied step attempts for this task
        attempts = self.store.list_step_attempts(task_id=task_id, status="succeeded", limit=100)
        if len(attempts) < 2:
            return None

        # Build step descriptions from execution contracts
        step_descs: list[dict[str, str]] = []
        step_fps: list[str] = []

        for attempt in sorted(attempts, key=lambda a: a.started_at or 0.0):
            contract_ref = attempt.execution_contract_ref
            if not contract_ref:
                continue
            contract = self.store.get_execution_contract(contract_ref)
            if contract is None:
                continue
            action_class = str(contract.success_criteria.get("action_class", ""))
            tool_name = str(contract.success_criteria.get("tool_name", ""))
            if not action_class:
                continue
            fp = _step_fingerprint(action_class, tool_name)
            step_fps.append(fp)
            step_descs.append({"action_class": action_class, "tool_name": tool_name})

        if len(step_fps) < 2:
            return None

        fingerprint = _pattern_fingerprint(step_fps)

        # Extract goal keywords from the task
        task = self.store.get_task(task_id)
        goal = str(getattr(task, "goal", "") or "") if task else ""
        keywords = _extract_keywords(goal)

        # Check for existing pattern
        existing = self._find_pattern_by_fingerprint(fingerprint)
        if existing is not None:
            sa = dict(existing.structured_assertion or {})
            invocation_count = int(sa.get("invocation_count", 0)) + 1
            success_count = int(sa.get("success_count", 0)) + 1
            sa["invocation_count"] = invocation_count
            sa["success_count"] = success_count
            sa["success_rate"] = success_count / invocation_count if invocation_count > 0 else 0.0
            source_refs = list(sa.get("source_task_refs", []))
            if task_id not in source_refs:
                source_refs.append(task_id)
                if len(source_refs) > 10:
                    source_refs = source_refs[-10:]
            sa["source_task_refs"] = source_refs
            # Merge keywords
            existing_kw = set(sa.get("goal_keywords", []))
            existing_kw.update(keywords)
            sa["goal_keywords"] = sorted(existing_kw)

            self.store.update_memory_record(
                existing.memory_id,
                structured_assertion=sa,
                validation_basis=f"task_completed:{task_id}",
                last_validated_at=time.time(),
            )
            self.store.append_event(
                event_type="task_pattern.reinforced",
                entity_type="memory_record",
                entity_id=existing.memory_id,
                task_id=task_id,
                actor="kernel",
                payload={
                    "fingerprint": fingerprint,
                    "invocation_count": invocation_count,
                },
            )
            log.debug(
                "task_pattern.reinforced",
                memory_id=existing.memory_id,
                fingerprint=fingerprint,
            )
            return existing

        structured_assertion: dict[str, Any] = {
            "pattern_fingerprint": fingerprint,
            "step_fingerprints": step_fps,
            "step_descriptions": step_descs,
            "goal_keywords": keywords,
            "invocation_count": 1,
            "success_count": 1,
            "success_rate": 1.0,
            "source_task_refs": [task_id],
        }

        steps_summary = " → ".join(d["action_class"] for d in step_descs[:5])
        memory = self.store.create_memory_record(
            task_id=task_id,
            conversation_id=None,
            category="task_pattern",
            claim_text=f"Task pattern: {steps_summary}",
            structured_assertion=structured_assertion,
            scope_kind="global",
            scope_ref="",
            promotion_reason="task_completed",
            retention_class="durable_template",
            status="active",
            confidence=0.6,
            trust_tier="durable",
            evidence_refs=[task_id],
            memory_kind="task_pattern",
            validation_basis=f"task_completed:{task_id}",
            last_validated_at=time.time(),
        )

        self.store.append_event(
            event_type="task_pattern.learned",
            entity_type="memory_record",
            entity_id=memory.memory_id,
            task_id=task_id,
            actor="kernel",
            payload={
                "fingerprint": fingerprint,
                "step_count": len(step_fps),
                "steps_summary": steps_summary,
            },
        )

        log.info(
            "task_pattern.learned",
            memory_id=memory.memory_id,
            fingerprint=fingerprint,
            step_count=len(step_fps),
        )
        return memory

    def find_matching_pattern(self, goal: str) -> TaskPattern | None:
        """Find the best-matching task pattern for a given goal.

        Returns ``None`` if no pattern matches well enough.
        """
        keywords = _extract_keywords(goal)
        if not keywords:
            return None

        patterns = self._active_patterns()
        if not patterns:
            return None

        best: MemoryRecord | None = None
        best_score = 0.0

        for record in patterns:
            sa = dict(record.structured_assertion or {})
            pattern_kw = set(sa.get("goal_keywords", []))
            if not pattern_kw:
                continue
            # Jaccard similarity on keywords
            query_set = set(keywords)
            intersection = query_set & pattern_kw
            union = query_set | pattern_kw
            similarity = len(intersection) / len(union) if union else 0.0
            if similarity > best_score and similarity >= 0.3:
                best_score = similarity
                best = record

        if best is None:
            return None

        sa = dict(best.structured_assertion or {})
        return TaskPattern(
            pattern_fingerprint=str(sa.get("pattern_fingerprint", "")),
            step_fingerprints=list(sa.get("step_fingerprints", [])),
            step_descriptions=list(sa.get("step_descriptions", [])),
            goal_keywords=list(sa.get("goal_keywords", [])),
            invocation_count=int(sa.get("invocation_count", 0)),
            success_count=int(sa.get("success_count", 0)),
            success_rate=float(sa.get("success_rate", 0.0)),
            source_task_refs=list(sa.get("source_task_refs", [])),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _active_patterns(self) -> list[MemoryRecord]:
        all_active = self.store.list_memory_records(status="active", limit=500)
        return [r for r in all_active if r.memory_kind == "task_pattern"]

    def _find_pattern_by_fingerprint(self, fingerprint: str) -> MemoryRecord | None:
        for record in self._active_patterns():
            sa = dict(record.structured_assertion or {})
            if str(sa.get("pattern_fingerprint", "")) == fingerprint:
                return record
        return None
