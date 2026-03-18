from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermit.kernel.verification.proofs.anchor_methods import GitNoteAnchor, LocalLogAnchor
from hermit.kernel.verification.proofs.anchoring import (
    AnchorService,
    AnchorVerificationStatus,
    ProofAnchor,
)


@pytest.fixture
def sample_proof_summary() -> dict:
    return {
        "task_id": "task-001",
        "chain_verification": {"valid": True, "head_hash": "abc123"},
        "receipt_count": 2,
    }


@pytest.fixture
def local_log_path(tmp_path: Path) -> Path:
    return tmp_path / "proof-anchors.jsonl"


@pytest.fixture
def anchor_service(local_log_path: Path) -> AnchorService:
    svc = AnchorService()
    svc.register_method("local_log", LocalLogAnchor(local_log_path))
    return svc


class TestAnchorService:
    def test_compute_proof_hash_deterministic(self, sample_proof_summary: dict) -> None:
        h1 = AnchorService.compute_proof_hash(sample_proof_summary)
        h2 = AnchorService.compute_proof_hash(sample_proof_summary)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_unknown_method_raises(self, sample_proof_summary: dict) -> None:
        svc = AnchorService()
        with pytest.raises(ValueError, match="Unknown anchor method"):
            svc.anchor_proof("task-001", sample_proof_summary, method="nonexistent")


class TestLocalLogAnchor:
    def test_writes_jsonl_entry(
        self,
        anchor_service: AnchorService,
        sample_proof_summary: dict,
        local_log_path: Path,
    ) -> None:
        anchor = anchor_service.anchor_proof("task-001", sample_proof_summary, method="local_log")
        assert local_log_path.exists()
        lines = local_log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["proof_hash"] == anchor.proof_hash
        assert entry["task_id"] == "task-001"
        assert entry["prev_anchor_hash"] == ""

    def test_anchor_chain_links(
        self,
        anchor_service: AnchorService,
        local_log_path: Path,
    ) -> None:
        summary1 = {"task_id": "task-001", "data": "first"}
        summary2 = {"task_id": "task-002", "data": "second"}
        anchor_service.anchor_proof("task-001", summary1, method="local_log")
        anchor_service.anchor_proof("task-002", summary2, method="local_log")

        lines = local_log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        entry1 = json.loads(lines[0])
        entry2 = json.loads(lines[1])
        # First entry has empty prev_anchor_hash
        assert entry1["prev_anchor_hash"] == ""
        # Second entry links to hash of first entry line
        assert entry2["prev_anchor_hash"] != ""
        # The prev_anchor_hash should be the SHA-256 of the first line
        from hermit.kernel.ledger.journal.store_support import sha256_hex

        assert entry2["prev_anchor_hash"] == sha256_hex(lines[0])

    def test_verify_anchor_valid(
        self,
        anchor_service: AnchorService,
        sample_proof_summary: dict,
    ) -> None:
        anchor = anchor_service.anchor_proof("task-001", sample_proof_summary, method="local_log")
        verification = anchor_service.verify_anchor(anchor)
        assert verification.status == AnchorVerificationStatus.VALID
        assert "verified" in verification.message.lower()

    def test_verify_anchor_invalid_tampered_hash(
        self,
        anchor_service: AnchorService,
        sample_proof_summary: dict,
    ) -> None:
        anchor = anchor_service.anchor_proof("task-001", sample_proof_summary, method="local_log")
        tampered = ProofAnchor(
            proof_hash="tampered_hash_value",
            anchor_method="local_log",
            anchor_ref=anchor.anchor_ref,
            anchored_at=anchor.anchored_at,
            anchor_payload=anchor.anchor_payload,
        )
        verification = anchor_service.verify_anchor(tampered)
        assert verification.status == AnchorVerificationStatus.INVALID


class TestGitNoteAnchor:
    def test_git_note_anchor_writes_and_reads(self) -> None:
        git_anchor = GitNoteAnchor(repo_path=Path("/tmp/fake-repo"))

        mock_add = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        mock_rev = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abc123def456\n", stderr=""
        )
        mock_show = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {"proof_hash": "testhash", "task_id": "task-001", "anchored_at": 1.0},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            stderr="",
        )

        with patch("subprocess.run") as mock_run:
            # anchor() calls git notes add then git rev-parse
            mock_run.side_effect = [mock_add, mock_rev]
            anchor = git_anchor.anchor("task-001", "testhash")

        assert anchor.proof_hash == "testhash"
        assert anchor.anchor_method == "git_note"
        assert anchor.anchor_ref == "abc123def456"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = mock_show
            verification = git_anchor.verify(anchor, "testhash")

        assert verification.status == AnchorVerificationStatus.VALID

    def test_git_note_anchor_verify_mismatch(self) -> None:
        git_anchor = GitNoteAnchor(repo_path=Path("/tmp/fake-repo"))
        anchor = ProofAnchor(
            proof_hash="original_hash",
            anchor_method="git_note",
            anchor_ref="abc123",
            anchored_at=1.0,
            anchor_payload={"commit": "abc123"},
        )
        # Return a note with a different proof_hash
        mock_show = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"proof_hash": "different_hash"}),
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_show):
            verification = git_anchor.verify(anchor, "original_hash")
        assert verification.status == AnchorVerificationStatus.INVALID


class TestAnchorVerificationUnknownMethod:
    def test_verify_unknown_method_returns_unknown(self) -> None:
        svc = AnchorService()
        anchor = ProofAnchor(
            proof_hash="somehash",
            anchor_method="nonexistent",
            anchor_ref="ref",
            anchored_at=1.0,
        )
        verification = svc.verify_anchor(anchor)
        assert verification.status == AnchorVerificationStatus.UNKNOWN
        assert "not available" in verification.message
