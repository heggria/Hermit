"""Tests for anchor_methods.py — covers missing lines for LocalLogAnchor and GitNoteAnchor."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermit.kernel.verification.proofs.anchor_methods import GitNoteAnchor, LocalLogAnchor
from hermit.kernel.verification.proofs.anchoring import (
    AnchorVerificationStatus,
    ProofAnchor,
)


class TestLocalLogAnchorReadLastAnchorHash:
    """Cover lines 31, 36-37: empty file returns '', invalid JSON returns ''."""

    def test_empty_file_returns_empty_string(self, tmp_path: Path) -> None:
        log_path = tmp_path / "anchors.jsonl"
        log_path.write_text("", encoding="utf-8")
        anchor = LocalLogAnchor(log_path)
        assert anchor._read_last_anchor_hash() == ""

    def test_invalid_json_returns_empty_string(self, tmp_path: Path) -> None:
        log_path = tmp_path / "anchors.jsonl"
        log_path.write_text("not valid json\n", encoding="utf-8")
        anchor = LocalLogAnchor(log_path)
        assert anchor._read_last_anchor_hash() == ""


class TestLocalLogAnchorVerifyMismatch:
    """Cover line 63: proof_hash mismatch returns INVALID."""

    def test_verify_proof_hash_mismatch(self, tmp_path: Path) -> None:
        log_path = tmp_path / "anchors.jsonl"
        anchor_method = LocalLogAnchor(log_path)
        proof_anchor = anchor_method.anchor("task_1", "real_hash")

        # Verify with a different proof_hash
        result = anchor_method.verify(proof_anchor, "wrong_hash")
        assert result.status == AnchorVerificationStatus.INVALID
        assert "does not match" in result.message


class TestLocalLogAnchorVerifyLogMissing:
    """Cover line 70: log file does not exist returns UNKNOWN."""

    def test_verify_missing_log_returns_unknown(self, tmp_path: Path) -> None:
        log_path = tmp_path / "missing.jsonl"
        anchor_method = LocalLogAnchor(log_path)
        proof_anchor = ProofAnchor(
            proof_hash="somehash",
            anchor_method="local_log",
            anchor_ref="ref",
            anchored_at=1.0,
        )
        result = anchor_method.verify(proof_anchor, "somehash")
        assert result.status == AnchorVerificationStatus.UNKNOWN
        assert "not found" in result.message


class TestLocalLogAnchorVerifyHashNotInLog:
    """Cover lines 80-81: JSON valid but proof_hash not matching any entry."""

    def test_verify_hash_not_found_in_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "anchors.jsonl"
        # Write a valid entry with a different proof_hash
        entry = json.dumps({"proof_hash": "other_hash", "task_id": "t1", "anchored_at": 1.0})
        log_path.write_text(entry + "\n", encoding="utf-8")

        anchor_method = LocalLogAnchor(log_path)
        proof_anchor = ProofAnchor(
            proof_hash="missing_hash",
            anchor_method="local_log",
            anchor_ref="ref",
            anchored_at=1.0,
        )
        result = anchor_method.verify(proof_anchor, "missing_hash")
        assert result.status == AnchorVerificationStatus.INVALID
        assert "not found" in result.message


class TestGitNoteAnchorErrors:
    """Cover lines 122, 136, 145, 153-154."""

    def test_anchor_raises_on_git_failure(self) -> None:
        """Cover line 122: git notes add failure raises RuntimeError."""
        git_anchor = GitNoteAnchor(repo_path=Path("/tmp/fake-repo"))
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="fatal: not a git repository"
        )
        with (
            patch("subprocess.run", return_value=mock_result),
            pytest.raises(RuntimeError, match="git notes add failed"),
        ):
            git_anchor.anchor("task_1", "somehash")

    def test_verify_proof_hash_mismatch(self) -> None:
        """Cover line 136: verify with mismatched proof_hash."""
        git_anchor = GitNoteAnchor()
        proof_anchor = ProofAnchor(
            proof_hash="original",
            anchor_method="git_note",
            anchor_ref="abc123",
            anchored_at=1.0,
            anchor_payload={"commit": "abc123"},
        )
        result = git_anchor.verify(proof_anchor, "different_hash")
        assert result.status == AnchorVerificationStatus.INVALID
        assert "does not match" in result.message

    def test_verify_git_note_show_fails(self) -> None:
        """Cover line 145: git notes show fails returns UNKNOWN."""
        git_anchor = GitNoteAnchor(repo_path=Path("/tmp/fake-repo"))
        proof_anchor = ProofAnchor(
            proof_hash="somehash",
            anchor_method="git_note",
            anchor_ref="abc123",
            anchored_at=1.0,
            anchor_payload={"commit": "abc123"},
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no note found"
        )
        with patch("subprocess.run", return_value=mock_result):
            result = git_anchor.verify(proof_anchor, "somehash")
        assert result.status == AnchorVerificationStatus.UNKNOWN
        assert "Could not read git note" in result.message

    def test_verify_git_note_invalid_json(self) -> None:
        """Cover lines 153-154: git note is not valid JSON."""
        git_anchor = GitNoteAnchor(repo_path=Path("/tmp/fake-repo"))
        proof_anchor = ProofAnchor(
            proof_hash="somehash",
            anchor_method="git_note",
            anchor_ref="abc123",
            anchored_at=1.0,
            anchor_payload={"commit": "abc123"},
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json at all", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result):
            result = git_anchor.verify(proof_anchor, "somehash")
        assert result.status == AnchorVerificationStatus.INVALID
        assert "not valid JSON" in result.message
