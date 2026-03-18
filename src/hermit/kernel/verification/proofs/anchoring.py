from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from hermit.kernel.ledger.journal.store_support import canonical_json as _canonical_json
from hermit.kernel.ledger.journal.store_support import sha256_hex as _sha256_hex


class AnchorVerificationStatus(Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass
class ProofAnchor:
    proof_hash: str
    anchor_method: str
    anchor_ref: str
    anchored_at: float
    anchor_payload: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class AnchorVerification:
    status: AnchorVerificationStatus
    message: str
    proof_hash: str
    anchor: ProofAnchor | None = None


class AnchorMethod:
    """Base class for anchor methods."""

    method_name: str = "base"

    def anchor(self, task_id: str, proof_hash: str) -> ProofAnchor:
        raise NotImplementedError

    def verify(self, anchor: ProofAnchor, proof_hash: str) -> AnchorVerification:
        raise NotImplementedError


class AnchorService:
    """Service for anchoring proof hashes to external stores."""

    def __init__(self, methods: dict[str, AnchorMethod] | None = None) -> None:
        self._methods: dict[str, AnchorMethod] = methods or {}

    def register_method(self, name: str, method: AnchorMethod) -> None:
        self._methods[name] = method

    @staticmethod
    def compute_proof_hash(proof_summary: dict[str, Any]) -> str:
        """Compute SHA-256 of canonical JSON proof summary."""
        return _sha256_hex(_canonical_json(proof_summary))

    def anchor_proof(
        self,
        task_id: str,
        proof_summary: dict[str, Any],
        method: str = "local_log",
    ) -> ProofAnchor:
        """Anchor a proof summary using the specified method."""
        if method not in self._methods:
            raise ValueError(f"Unknown anchor method: {method}")
        proof_hash = self.compute_proof_hash(proof_summary)
        return self._methods[method].anchor(task_id, proof_hash)

    def verify_anchor(self, anchor: ProofAnchor) -> AnchorVerification:
        """Verify an anchor against its stored record."""
        method_name = anchor.anchor_method
        if method_name not in self._methods:
            return AnchorVerification(
                status=AnchorVerificationStatus.UNKNOWN,
                message=f"Anchor method not available: {method_name}",
                proof_hash=anchor.proof_hash,
                anchor=anchor,
            )
        return self._methods[method_name].verify(anchor, anchor.proof_hash)
