from __future__ import annotations

import re
from pathlib import Path

from hermit.kernel.artifacts.lineage.claims import repository_claim_status

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_docs_describe_kernel_first_positioning() -> None:
    readme = _read("README.md")
    architecture = _read("docs/architecture.md")
    pyproject = _read("pyproject.toml")

    assert "OS kernel treats process execution" in readme
    assert "governed agent kernel" in architecture
    assert "governed agent kernel" in pyproject


def test_public_docs_state_v0_1_is_target_not_completion_claim() -> None:
    readme = _read("README.md")
    architecture = _read("docs/architecture.md")

    assert "Kernel Spec v0.1" in readme
    assert "Conformance Matrix" in readme
    assert "governed agent kernel" in architecture


def test_public_docs_call_out_governance_hard_cut_and_proof_boundary() -> None:
    readme = _read("README.md")
    architecture = _read("docs/architecture.md")

    assert "governed execution" in readme
    assert "approvals" in readme
    assert "Fail-closed" in architecture
    assert "proof bundle" in architecture


def test_public_entry_points_share_macos_brand_icon() -> None:
    readme = _read("README.md")
    readme_zh = _read("README.zh-CN.md")
    wiki_home = _read("docs/wiki-home.md")
    docs_home_template = _read("docs/overrides/home.html")
    mkdocs = _read("mkdocs.yml")

    assert (ROOT / "docs/assets/hermit-macos-icon.svg").exists()
    assert "./docs/assets/hermit-macos-icon.svg" in readme
    assert "./docs/assets/hermit-macos-icon.svg" in readme_zh
    assert (
        "raw.githubusercontent.com/heggria/Hermit/main/docs/assets/hermit-macos-icon.svg"
        in wiki_home
    )
    assert "hermit-landing" in docs_home_template
    assert "logo: assets/hermit-macos-icon.svg" in mkdocs
    assert "favicon: assets/hermit-macos-icon.svg" in mkdocs


def test_conformance_matrix_tracks_exit_criteria_and_claim_boundary() -> None:
    matrix = _read("docs/kernel-conformance-matrix-v0.1.md")

    assert "Spec exit criterion" in matrix
    assert "No direct model-to-tool execution bypass" in matrix
    assert "Contract-sensitive retries invalidate stale contract" in matrix
    assert "The repo can now gate and surface claims through code" in matrix


def test_status_docs_reflect_current_claimable_profiles() -> None:
    readme = _read("README.md")
    roadmap = _read("docs/roadmap.md")
    checklist = _read("docs/kernel-spec-v0.1-section-checklist.md")
    matrix = _read("docs/kernel-conformance-matrix-v0.1.md")

    assert "close to a claimable alpha kernel" not in readme
    assert "close to claimable as an alpha kernel" not in roadmap
    assert "claimable through the conformance matrix and `task claim-status`" in matrix
    assert "Repository-level claim status as of 2026-03-15" in checklist
    assert "Compatibility debt / caution" in checklist


def test_live_docs_do_not_reintroduce_legacy_permit_surface_names() -> None:
    targets = [
        "README.md",
        "docs/architecture.md",
        "docs/demo-flows.md",
        "docs/governance.md",
        "docs/receipts-and-proofs.md",
        "docs/status-and-compatibility.md",
        "docs/task-lifecycle.md",
        "docs/use-cases.md",
        "docs/why-hermit.md",
    ]
    banned_phrases = [
        "ExecutionPermit",
        "PathGrant",
        "approve_always_directory",
        "scoped permit or grant",
        "permit or grant references",
        "permits and grants",
        "path grant enforcement",
    ]

    for path in targets:
        content = _read(path)
        for phrase in banned_phrases:
            assert phrase not in content, f"{phrase!r} unexpectedly present in {path}"


def test_conformance_matrix_rows_match_claim_manifest(monkeypatch) -> None:
    from hermit.kernel.artifacts.lineage import claims as _claims_mod
    from hermit.kernel.artifacts.lineage.claim_manifest import CLAIM_ROWS

    # Mock the expensive semantic probe results to avoid spinning up multiple
    # SQLite databases and full kernel stacks for each of the 12+ probes.
    # The test validates label/status alignment between the markdown matrix
    # and the claim manifest, so we use a fast synthetic probe result.
    fast_probe_results = {
        str(row["id"]): {"status": "implemented", "evaluation": "mock"}
        for row in CLAIM_ROWS
        if str(row["id"]) != "signed_proofs"
    }
    monkeypatch.setattr(
        _claims_mod,
        "_semantic_probe_results",
        lambda **kwargs: fast_probe_results,
    )

    matrix = _read("docs/kernel-conformance-matrix-v0.1.md")
    rows = re.findall(r"^\| ([^|]+) \| `([^`]+)` \|", matrix, re.MULTILINE)
    observed = {label.strip(): status for label, status in rows}
    derived = {row["label"]: row["status"] for row in repository_claim_status()["rows"]}

    # Only check labels that appear in both the markdown matrix and the claim manifest.
    # The matrix exit criteria table is a curated subset; the manifest may include
    # additional rows that are surfaced only through `task claim-status`.
    common_labels = set(observed) & set(derived)
    assert common_labels, "No overlapping labels between matrix and claim manifest"
    for label in common_labels:
        assert observed[label] == derived[label], (
            f"Status mismatch for {label!r}: matrix={observed[label]!r}, manifest={derived[label]!r}"
        )
