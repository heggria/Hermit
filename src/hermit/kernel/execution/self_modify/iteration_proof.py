"""Iteration Proof — hash-chained proof bundles for self-iteration pipelines.

Provides a verifiable proof chain for each self-improvement iteration,
linking every phase output (research, spec, decomposition, execution,
benchmark, lessons) into a single hash-chained bundle. This makes
iteration outcomes durable and independently verifiable.

The proof follows the same hash-chaining philosophy as the task proof
system (``verification/proofs/``), but operates at iteration granularity
rather than per-receipt granularity.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from hermit.kernel.ledger.journal.store_support import (
    canonical_json,
    sha256_hex,
)

__all__ = [
    "IterationProofBundle",
    "build_iteration_proof",
    "export_iteration_proof",
    "verify_iteration_proof",
]

logger = structlog.get_logger()

_SCHEMA_VERSION = "iteration.proof/v1"


def _now_ts() -> float:
    return datetime.now(UTC).timestamp()


def _hash_phase_data(data: Any) -> str:
    """Compute SHA-256 of arbitrary phase data via canonical JSON."""
    if data is None:
        return sha256_hex(canonical_json(None))
    return sha256_hex(canonical_json(data))


# ---------------------------------------------------------------------------
# Proof bundle model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IterationProofBundle:
    """Hash-chained proof bundle for a self-improvement iteration.

    Collects SHA-256 hashes of every phase output and chain-links them
    into a single ``chain_hash`` that covers the entire iteration
    lifecycle: research -> spec -> decomposition -> execution ->
    benchmark -> lessons.
    """

    schema: str = _SCHEMA_VERSION
    iteration_id: str = ""
    spec_id: str = ""
    goal: str = ""
    result: str = ""  # "accepted" | "rejected" | "accepted_with_followups"

    # Per-phase content hashes
    research_findings_hash: str = ""
    spec_content_hash: str = ""
    decomposition_plan_hash: str = ""
    execution_receipt_hashes: list[str] = field(default_factory=list)
    benchmark_result_hash: str = ""
    lessons_hash: str = ""

    # Aggregated chain hash: SHA-256 of all phase hashes concatenated
    chain_hash: str = ""

    created_at: float = field(default_factory=_now_ts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "iteration_id": self.iteration_id,
            "spec_id": self.spec_id,
            "goal": self.goal,
            "result": self.result,
            "research_findings_hash": self.research_findings_hash,
            "spec_content_hash": self.spec_content_hash,
            "decomposition_plan_hash": self.decomposition_plan_hash,
            "execution_receipt_hashes": list(self.execution_receipt_hashes),
            "benchmark_result_hash": self.benchmark_result_hash,
            "lessons_hash": self.lessons_hash,
            "chain_hash": self.chain_hash,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _compute_chain_hash(
    *,
    research_findings_hash: str,
    spec_content_hash: str,
    decomposition_plan_hash: str,
    execution_receipt_hashes: list[str],
    benchmark_result_hash: str,
    lessons_hash: str,
) -> str:
    """Chain-link all phase hashes into a single SHA-256 digest.

    Concatenation order is the pipeline order: research -> spec ->
    decompose -> execute -> benchmark -> learn.  The execution receipt
    hashes are sorted and joined to ensure determinism regardless of
    completion order.
    """
    parts = [
        research_findings_hash,
        spec_content_hash,
        decomposition_plan_hash,
        "|".join(sorted(execution_receipt_hashes)),
        benchmark_result_hash,
        lessons_hash,
    ]
    return sha256_hex("+".join(parts))


def build_iteration_proof(
    store: object,
    iteration_id: str,
    *,
    result: str = "",
    lane_artifacts: dict[str, Any] | None = None,
) -> IterationProofBundle:
    """Construct an IterationProofBundle from store metadata.

    Reads the spec_backlog entry for *iteration_id*, extracts each
    phase's output from the metadata, computes per-phase SHA-256 hashes,
    and chain-links them.

    Args:
        store: A KernelStore (or compatible object with spec_backlog CRUD).
        iteration_id: The iteration to build a proof for.
        result: Iteration outcome string (accepted/rejected/...).
        lane_artifacts: Optional pre-collected lane artifact dict to
            include in the decomposition hash.

    Returns:
        A frozen IterationProofBundle.

    Raises:
        KeyError: If the iteration is not found in the store.
    """
    entry = _find_entry(store, iteration_id)
    if entry is None:
        raise KeyError(f"Iteration not found: {iteration_id}")

    meta = _parse_metadata(entry)
    spec_id = entry.get("spec_id", "")
    goal = meta.get("goal") or entry.get("goal", "")

    # Phase data extraction from metadata
    research_data = meta.get("research_findings") or meta.get("research_report", {})
    spec_data = meta.get("spec_content") or meta.get("generated_spec", {})
    decomposition_data = meta.get("decomposition_plan") or meta.get("task_plan", {})
    if lane_artifacts:
        decomposition_data = {
            "plan": decomposition_data,
            "lane_artifacts": lane_artifacts,
        }
    execution_receipts = meta.get("execution_receipts", [])
    benchmark_data = meta.get("benchmark_results", {})
    lessons_data = meta.get("lessons") or meta.get("lesson_pack", {})

    # Compute per-phase hashes
    research_hash = _hash_phase_data(research_data)
    spec_hash = _hash_phase_data(spec_data)
    decomposition_hash = _hash_phase_data(decomposition_data)
    receipt_hashes = [_hash_phase_data(r) for r in execution_receipts] if execution_receipts else []
    benchmark_hash = _hash_phase_data(benchmark_data)
    lessons_hash = _hash_phase_data(lessons_data)

    chain_hash = _compute_chain_hash(
        research_findings_hash=research_hash,
        spec_content_hash=spec_hash,
        decomposition_plan_hash=decomposition_hash,
        execution_receipt_hashes=receipt_hashes,
        benchmark_result_hash=benchmark_hash,
        lessons_hash=lessons_hash,
    )

    bundle = IterationProofBundle(
        iteration_id=iteration_id,
        spec_id=spec_id,
        goal=goal,
        result=result,
        research_findings_hash=research_hash,
        spec_content_hash=spec_hash,
        decomposition_plan_hash=decomposition_hash,
        execution_receipt_hashes=receipt_hashes,
        benchmark_result_hash=benchmark_hash,
        lessons_hash=lessons_hash,
        chain_hash=chain_hash,
    )

    logger.info(
        "iteration_proof.built",
        iteration_id=iteration_id,
        spec_id=spec_id,
        chain_hash=chain_hash[:16],
        phase_count=6,
        receipt_count=len(receipt_hashes),
    )

    return bundle


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def verify_iteration_proof(proof: IterationProofBundle) -> dict[str, Any]:
    """Verify a proof bundle's chain integrity by recomputing the chain hash.

    Returns a dict with ``valid`` (bool) and diagnostic fields.
    """
    recomputed = _compute_chain_hash(
        research_findings_hash=proof.research_findings_hash,
        spec_content_hash=proof.spec_content_hash,
        decomposition_plan_hash=proof.decomposition_plan_hash,
        execution_receipt_hashes=proof.execution_receipt_hashes,
        benchmark_result_hash=proof.benchmark_result_hash,
        lessons_hash=proof.lessons_hash,
    )

    valid = recomputed == proof.chain_hash
    result: dict[str, Any] = {
        "valid": valid,
        "iteration_id": proof.iteration_id,
        "spec_id": proof.spec_id,
        "chain_hash": proof.chain_hash,
        "recomputed_chain_hash": recomputed,
        "phase_hashes": {
            "research_findings": proof.research_findings_hash,
            "spec_content": proof.spec_content_hash,
            "decomposition_plan": proof.decomposition_plan_hash,
            "execution_receipts": proof.execution_receipt_hashes,
            "benchmark_result": proof.benchmark_result_hash,
            "lessons": proof.lessons_hash,
        },
    }

    if not valid:
        logger.warning(
            "iteration_proof.verification_failed",
            iteration_id=proof.iteration_id,
            expected=proof.chain_hash[:16],
            actual=recomputed[:16],
        )
    else:
        logger.info(
            "iteration_proof.verified",
            iteration_id=proof.iteration_id,
            chain_hash=proof.chain_hash[:16],
        )

    return result


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _resolve_proof_dir() -> Path:
    """Resolve the iteration proof export directory.

    Uses ``$HERMIT_BASE_DIR/kernel/artifacts/iteration_proofs/`` when the
    env var is set, otherwise falls back to ``~/.hermit/kernel/artifacts/
    iteration_proofs/``.
    """
    base = os.environ.get("HERMIT_BASE_DIR", "")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".hermit"
    return root / "kernel" / "artifacts" / "iteration_proofs"


def export_iteration_proof(
    proof: IterationProofBundle,
    *,
    output_dir: Path | None = None,
) -> Path:
    """Export a proof bundle as JSON to the iteration proofs directory.

    Args:
        proof: The proof bundle to export.
        output_dir: Override for the export directory. Defaults to
            ``$HERMIT_BASE_DIR/kernel/artifacts/iteration_proofs/``.

    Returns:
        The Path to the written JSON file.
    """
    target_dir = output_dir or _resolve_proof_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{proof.iteration_id}.proof.json"
    filepath = target_dir / filename

    payload = proof.to_dict()
    # Include verification result alongside the proof
    verification = verify_iteration_proof(proof)
    payload["verification"] = verification

    filepath.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "iteration_proof.exported",
        iteration_id=proof.iteration_id,
        path=str(filepath),
        chain_hash=proof.chain_hash[:16],
        verified=verification["valid"],
    )

    return filepath


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_entry(store: object, iteration_id: str) -> dict | None:
    """Find a spec_backlog entry by iteration_id.

    Mirrors IterationKernel._find_entry() logic: tries direct spec_id
    lookup first, then scans metadata.iteration_id.
    """
    get_spec_entry = getattr(store, "get_spec_entry", None)
    if get_spec_entry is not None:
        entry = get_spec_entry(iteration_id)
        if entry is not None:
            return entry

    list_spec_backlog = getattr(store, "list_spec_backlog", None)
    if list_spec_backlog is None:
        return None

    entries = list_spec_backlog(limit=500)
    for e in entries:
        raw = e.get("metadata")
        if not raw:
            continue
        try:
            meta = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(meta, dict) and meta.get("iteration_id") == iteration_id:
            return e

    return None


def _parse_metadata(entry: dict) -> dict:
    """Parse the JSON metadata from a spec_backlog entry."""
    raw = entry.get("metadata")
    if not raw:
        return {}
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}
