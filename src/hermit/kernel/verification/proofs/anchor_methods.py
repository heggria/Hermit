from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from hermit.kernel.ledger.journal.store_support import sha256_hex as _sha256_hex
from hermit.kernel.verification.proofs.anchoring import (
    AnchorMethod,
    AnchorVerification,
    AnchorVerificationStatus,
    ProofAnchor,
)


class LocalLogAnchor(AnchorMethod):
    """Append proof hashes to a local JSONL file with hash chaining."""

    method_name: str = "local_log"

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path

    def _read_last_anchor_hash(self) -> str:
        """Read the prev_anchor_hash from the last line of the log."""
        if not self._log_path.exists():
            return ""
        text = self._log_path.read_text(encoding="utf-8").strip()
        if not text:
            return ""
        last_line = text.split("\n")[-1]
        try:
            json.loads(last_line)  # validate JSON
            return _sha256_hex(last_line)
        except json.JSONDecodeError:
            return ""

    def anchor(self, task_id: str, proof_hash: str) -> ProofAnchor:
        prev_anchor_hash = self._read_last_anchor_hash()
        anchored_at = time.time()
        entry = {
            "proof_hash": proof_hash,
            "task_id": task_id,
            "anchored_at": anchored_at,
            "prev_anchor_hash": prev_anchor_hash,
        }
        entry_line = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(entry_line + "\n")
        anchor_ref = _sha256_hex(entry_line)
        return ProofAnchor(
            proof_hash=proof_hash,
            anchor_method="local_log",
            anchor_ref=anchor_ref,
            anchored_at=anchored_at,
            anchor_payload=entry,
        )

    def verify(self, anchor: ProofAnchor, proof_hash: str) -> AnchorVerification:
        if anchor.proof_hash != proof_hash:
            return AnchorVerification(
                status=AnchorVerificationStatus.INVALID,
                message="Proof hash does not match anchor",
                proof_hash=proof_hash,
                anchor=anchor,
            )
        if not self._log_path.exists():
            return AnchorVerification(
                status=AnchorVerificationStatus.UNKNOWN,
                message="Anchor log file not found",
                proof_hash=proof_hash,
                anchor=anchor,
            )
        text = self._log_path.read_text(encoding="utf-8").strip()
        for line in text.split("\n"):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("proof_hash") == proof_hash:
                return AnchorVerification(
                    status=AnchorVerificationStatus.VALID,
                    message="Anchor verified in local log",
                    proof_hash=proof_hash,
                    anchor=anchor,
                )
        return AnchorVerification(
            status=AnchorVerificationStatus.INVALID,
            message="Proof hash not found in anchor log",
            proof_hash=proof_hash,
            anchor=anchor,
        )


class GitNoteAnchor(AnchorMethod):
    """Write proof hash as a git note on HEAD commit."""

    method_name: str = "git_note"

    def __init__(self, repo_path: Path | None = None) -> None:
        self._repo_path = repo_path

    def _run_git(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = ["git"]
        if self._repo_path is not None:
            cmd.extend(["-C", str(self._repo_path)])
        cmd.extend(args)
        return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)

    def anchor(self, task_id: str, proof_hash: str) -> ProofAnchor:
        anchored_at = time.time()
        note_body = json.dumps(
            {"proof_hash": proof_hash, "task_id": task_id, "anchored_at": anchored_at},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result = self._run_git("notes", "--ref=hermit-proofs", "add", "-f", "-m", note_body, "HEAD")
        if result.returncode != 0:
            raise RuntimeError(f"git notes add failed: {result.stderr.strip()}")
        # Read back commit hash for the anchor ref
        head_result = self._run_git("rev-parse", "HEAD")
        commit_hash = head_result.stdout.strip() if head_result.returncode == 0 else "unknown"
        return ProofAnchor(
            proof_hash=proof_hash,
            anchor_method="git_note",
            anchor_ref=commit_hash,
            anchored_at=anchored_at,
            anchor_payload={"commit": commit_hash, "note_body": note_body},
        )

    def verify(self, anchor: ProofAnchor, proof_hash: str) -> AnchorVerification:
        if anchor.proof_hash != proof_hash:
            return AnchorVerification(
                status=AnchorVerificationStatus.INVALID,
                message="Proof hash does not match anchor",
                proof_hash=proof_hash,
                anchor=anchor,
            )
        commit = anchor.anchor_payload.get("commit", anchor.anchor_ref)
        result = self._run_git("notes", "--ref=hermit-proofs", "show", commit)
        if result.returncode != 0:
            return AnchorVerification(
                status=AnchorVerificationStatus.UNKNOWN,
                message=f"Could not read git note: {result.stderr.strip()}",
                proof_hash=proof_hash,
                anchor=anchor,
            )
        try:
            note_data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return AnchorVerification(
                status=AnchorVerificationStatus.INVALID,
                message="Git note is not valid JSON",
                proof_hash=proof_hash,
                anchor=anchor,
            )
        if note_data.get("proof_hash") == proof_hash:
            return AnchorVerification(
                status=AnchorVerificationStatus.VALID,
                message="Anchor verified via git note",
                proof_hash=proof_hash,
                anchor=anchor,
            )
        return AnchorVerification(
            status=AnchorVerificationStatus.INVALID,
            message="Proof hash in git note does not match",
            proof_hash=proof_hash,
            anchor=anchor,
        )
