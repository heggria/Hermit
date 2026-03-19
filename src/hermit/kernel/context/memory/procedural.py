from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

_REVIEW_FAILURE_THRESHOLD = 0.7


@dataclass
class ProceduralRecord:
    """A reusable procedure extracted from memory."""

    procedure_id: str
    trigger_pattern: str
    steps: list[str]
    confidence: float = 0.5
    source_memory_ids: list[str] = field(default_factory=lambda: list[str]())
    success_count: int = 0
    failure_count: int = 0
    status: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0


class ProceduralMemoryService:
    """Extracts and manages procedural (how-to) memories.

    Detects "how-to" patterns in memory text and extracts
    structured procedures with trigger patterns and steps.
    """

    def extract_procedure(
        self,
        memory: MemoryRecord,
    ) -> ProceduralRecord | None:
        """Detect and extract a procedure from memory text.

        Looks for patterns like:
        - "To do X, first Y then Z"
        - Step 1/2/3 sequences
        - "run X, then Y"
        """
        text = memory.claim_text
        steps = self._extract_steps(text)
        if not steps:
            return None

        trigger = self._extract_trigger(text)
        if not trigger:
            return None

        return ProceduralRecord(
            procedure_id=f"proc-{uuid.uuid4().hex[:12]}",
            trigger_pattern=trigger,
            steps=steps,
            confidence=memory.confidence,
            source_memory_ids=[memory.memory_id],
            created_at=time.time(),
            updated_at=time.time(),
        )

    def match_procedures(
        self,
        query: str,
        store: KernelStore,
        *,
        limit: int = 5,
    ) -> list[ProceduralRecord]:
        """Find procedures whose trigger patterns match the query."""
        procedures = self._load_all_procedures(store)
        query_lower = query.lower()

        matched: list[tuple[ProceduralRecord, float]] = []
        for proc in procedures:
            if proc.status != "active":
                continue
            score = self._trigger_match_score(query_lower, proc.trigger_pattern)
            if score > 0:
                matched.append((proc, score))

        matched.sort(key=lambda x: x[1], reverse=True)
        return [proc for proc, _ in matched[:limit]]

    def reinforce(
        self,
        procedure_id: str,
        success: bool,
        store: KernelStore,
    ) -> None:
        """Update success/failure count. Flag for review if failure rate > 70%."""
        proc = self._load_procedure(procedure_id, store)
        if proc is None:
            return

        if success:
            proc.success_count += 1
        else:
            proc.failure_count += 1

        total = proc.success_count + proc.failure_count
        failure_rate = proc.failure_count / total if total > 0 else 0.0

        new_status = proc.status
        if failure_rate > _REVIEW_FAILURE_THRESHOLD and total >= 3:
            new_status = "review"
            log.warning(
                "procedural_flagged_for_review",
                procedure_id=procedure_id,
                failure_rate=failure_rate,
            )

        self._save_procedure(proc, store, status=new_status)

    def save_procedure(
        self,
        proc: ProceduralRecord,
        store: KernelStore,
    ) -> None:
        """Save a new or updated procedure to the store."""
        self._save_procedure(proc, store)

    def _extract_steps(self, text: str) -> list[str]:
        """Extract ordered steps from text."""
        # Pattern 1: "Step N: ..."
        step_matches = re.findall(
            r"(?:step\s+\d+[:.]\s*)(.+?)(?=step\s+\d+|$)", text, re.IGNORECASE
        )
        if len(step_matches) >= 2:
            return [s.strip().rstrip(".") for s in step_matches if s.strip()]

        # Pattern 2: "first X, then Y, then Z"
        first_then = re.findall(
            r"(?:first|首先)[,:\s]+(.+?)(?:,\s*then|，\s*然后|$)",
            text,
            re.IGNORECASE,
        )
        then_parts = re.findall(
            r"(?:then|然后)[,:\s]+(.+?)(?:,\s*then|，\s*然后|[.。]|$)",
            text,
            re.IGNORECASE,
        )
        if first_then and then_parts:
            steps = [first_then[0].strip()] + [t.strip() for t in then_parts]
            return [s.rstrip(".") for s in steps if s.strip()]

        # Pattern 3: numbered list "1. X 2. Y"
        numbered = re.findall(r"\d+[.)]\s*(.+?)(?=\d+[.)]|$)", text)
        if len(numbered) >= 2:
            return [s.strip().rstrip(".") for s in numbered if s.strip()]

        return []

    @staticmethod
    def _extract_trigger(text: str) -> str:
        """Extract the trigger condition/context from procedural text."""
        # "To do X, ..." or "When X, ..." or "For X, ..."
        patterns = [
            r"(?:to\s+)(.+?)(?:,|:)",
            r"(?:when\s+)(.+?)(?:,|:)",
            r"(?:for\s+)(.+?)(?:,|:)",
            r"(?:if\s+)(.+?)(?:,|:)",
        ]
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                trigger = match.group(1).strip()
                if 3 <= len(trigger) <= 100:
                    return trigger.lower()

        # Fallback: first meaningful phrase
        words = text.split()[:8]
        return " ".join(words).lower() if words else ""

    @staticmethod
    def _trigger_match_score(query: str, trigger: str) -> float:
        """Score how well a query matches a trigger pattern."""
        if not trigger or not query:
            return 0.0
        trigger_lower = trigger.lower()
        if trigger_lower in query:
            return 1.0
        query_tokens = set(query.split())
        trigger_tokens = set(trigger_lower.split())
        if not trigger_tokens:
            return 0.0
        overlap = len(query_tokens & trigger_tokens)
        return overlap / len(trigger_tokens) if overlap >= 2 else 0.0

    def _load_all_procedures(self, store: KernelStore) -> list[ProceduralRecord]:
        """Load all procedures from the store."""
        _ensure_procedural_schema(store)
        conn = store._get_conn()  # pyright: ignore[reportPrivateUsage]
        with store._event_chain_lock:  # pyright: ignore[reportPrivateUsage]
            rows = conn.execute(
                "SELECT * FROM procedural_memories WHERE status != 'deleted'"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _load_procedure(self, procedure_id: str, store: KernelStore) -> ProceduralRecord | None:
        _ensure_procedural_schema(store)
        conn = store._get_conn()  # pyright: ignore[reportPrivateUsage]
        with store._event_chain_lock:  # pyright: ignore[reportPrivateUsage]
            row = conn.execute(
                "SELECT * FROM procedural_memories WHERE procedure_id = ?",
                (procedure_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def _save_procedure(
        self,
        proc: ProceduralRecord,
        store: KernelStore,
        *,
        status: str | None = None,
    ) -> None:
        _ensure_procedural_schema(store)
        now = time.time()
        conn = store._get_conn()  # pyright: ignore[reportPrivateUsage]
        with store._event_chain_lock, conn:  # pyright: ignore[reportPrivateUsage]
            conn.execute(
                """
                INSERT INTO procedural_memories
                    (procedure_id, trigger_pattern, steps_json, confidence,
                     source_memory_ids_json, success_count, failure_count,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(procedure_id) DO UPDATE SET
                    trigger_pattern = excluded.trigger_pattern,
                    steps_json = excluded.steps_json,
                    confidence = excluded.confidence,
                    source_memory_ids_json = excluded.source_memory_ids_json,
                    success_count = excluded.success_count,
                    failure_count = excluded.failure_count,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    proc.procedure_id,
                    proc.trigger_pattern,
                    json.dumps(proc.steps),
                    proc.confidence,
                    json.dumps(proc.source_memory_ids),
                    proc.success_count,
                    proc.failure_count,
                    status or proc.status,
                    proc.created_at or now,
                    now,
                ),
            )

    @staticmethod
    def _row_to_record(row: Any) -> ProceduralRecord:

        return ProceduralRecord(
            procedure_id=str(row["procedure_id"]),
            trigger_pattern=str(row["trigger_pattern"]),
            steps=json.loads(row["steps_json"]) if row["steps_json"] else [],
            confidence=float(row["confidence"]),
            source_memory_ids=json.loads(row["source_memory_ids_json"])
            if row["source_memory_ids_json"]
            else [],
            success_count=int(row["success_count"]),
            failure_count=int(row["failure_count"]),
            status=str(row["status"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )


def _ensure_procedural_schema(store: KernelStore) -> None:
    """Create procedural_memories table if it doesn't exist."""
    conn = store._get_conn()  # pyright: ignore[reportPrivateUsage]
    with store._event_chain_lock, conn:  # pyright: ignore[reportPrivateUsage]
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS procedural_memories (
                procedure_id TEXT PRIMARY KEY,
                trigger_pattern TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source_memory_ids_json TEXT NOT NULL DEFAULT '[]',
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )


__all__ = ["ProceduralMemoryService", "ProceduralRecord"]
