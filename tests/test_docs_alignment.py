from __future__ import annotations

import re
from pathlib import Path

from hermit.kernel.claims import repository_claim_status

ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_docs_describe_kernel_first_positioning() -> None:
    readme = _read("README.md")
    architecture = _read("docs/architecture.md")
    pyproject = _read("pyproject.toml")

    assert "local-first governed agent kernel" in readme
    assert "kernel-first" in architecture
    assert "governed agent kernel" in pyproject


def test_public_docs_state_v0_1_is_target_not_completion_claim() -> None:
    readme = _read("README.md")
    architecture = _read("docs/architecture.md")

    assert "`v0.1` kernel spec" in readme
    assert "target architecture" in readme
    assert "does not treat the `v0.1` kernel spec as fully shipped" in architecture


def test_public_docs_call_out_governance_hard_cut_and_proof_boundary() -> None:
    readme = _read("README.md")
    architecture = _read("docs/architecture.md")

    assert "tool governance" in readme
    assert "Approval resolution" in readme
    assert "fail closed" in architecture
    assert "missing proof coverage" in architecture


def test_public_entry_points_share_macos_brand_icon() -> None:
    readme = _read("README.md")
    readme_zh = _read("README.zh-CN.md")
    wiki_home = _read("docs/wiki-home.md")
    site_index = _read("docs/site/index.html")

    assert (ROOT / "docs/site/assets/hermit-macos-icon.svg").exists()
    assert "./docs/site/assets/hermit-macos-icon.svg" in readme
    assert "./docs/site/assets/hermit-macos-icon.svg" in readme_zh
    assert (
        "raw.githubusercontent.com/heggria/Hermit/main/docs/site/assets/hermit-macos-icon.svg"
        in wiki_home
    )
    assert "./assets/hermit-macos-icon.svg" in site_index
    assert 'rel="icon"' in site_index
    assert 'rel="mask-icon"' in site_index


def test_conformance_matrix_tracks_exit_criteria_and_claim_boundary() -> None:
    matrix = _read("docs/kernel-conformance-matrix-v0.1.md")

    assert "Spec exit criterion" in matrix
    assert "No direct model-to-tool execution bypass" in matrix
    assert "Input drift / witness drift / approval drift use durable re-entry" in matrix
    assert "The repo can now gate and surface claims through code" in matrix


def test_status_docs_reflect_current_claimable_profiles() -> None:
    readme = _read("README.md")
    roadmap = _read("docs/roadmap.md")
    checklist = _read("docs/kernel-spec-v0.1-section-checklist.md")

    assert "close to a claimable alpha kernel" not in readme
    assert "close to claimable as an alpha kernel" not in roadmap
    assert "claimable through the conformance matrix and `task claim-status`" in roadmap
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


def test_conformance_matrix_rows_match_claim_manifest() -> None:
    matrix = _read("docs/kernel-conformance-matrix-v0.1.md")
    rows = re.findall(r"^\| ([^|]+) \| `([^`]+)` \|", matrix, re.MULTILINE)
    observed = {label: status for label, status in rows}
    derived = {row["label"]: row["status"] for row in repository_claim_status()["rows"]}

    for label, status in derived.items():
        assert observed[label] == status
