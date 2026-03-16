from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_docs_to_wiki.py"


def _load_sync_docs_module():
    spec = importlib.util.spec_from_file_location("sync_docs_to_wiki", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_docs_to_wiki = _load_sync_docs_module()


def run_sync(manifest_path: Path, wiki_dir: Path) -> SimpleNamespace:
    try:
        sync_docs_to_wiki.sync_docs_to_wiki(
            manifest_path=manifest_path,
            wiki_dir=wiki_dir,
            repo_url="https://github.com/example/Hermit",
            default_branch="main",
        )
    except sync_docs_to_wiki.SyncError as exc:
        return SimpleNamespace(returncode=1, stderr=str(exc), stdout="")
    return SimpleNamespace(returncode=0, stderr="", stdout="")


def write_manifest(manifest_path: Path, payload: dict[str, str]) -> None:
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_sync_writes_manifest_pages_and_sidebar(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    manifest_path = tmp_path / "wiki_manifest.json"
    write_manifest(
        manifest_path,
        {
            "Home": "docs/wiki-home.md",
            "Why-Hermit": "docs/why-hermit.md",
            "Roadmap": "docs/roadmap.md",
        },
    )

    result = run_sync(manifest_path, wiki_dir)

    assert result.returncode == 0, result.stderr
    assert (wiki_dir / "Home.md").exists()
    assert (wiki_dir / "Why-Hermit.md").exists()
    assert (wiki_dir / "Roadmap.md").exists()
    sidebar = (wiki_dir / "_Sidebar.md").read_text(encoding="utf-8")
    assert sidebar == (
        "## Hermit Wiki\n\n- [Home](Home)\n- [Why-Hermit](Why-Hermit)\n- [Roadmap](Roadmap)\n"
    )


def test_sync_sidebar_includes_expanded_manifest_entries_in_order(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    manifest_path = tmp_path / "wiki_manifest.json"
    write_manifest(
        manifest_path,
        {
            "Home": "docs/wiki-home.md",
            "Getting-Started": "docs/getting-started.md",
            "Task-Lifecycle": "docs/task-lifecycle.md",
            "Operator-Guide": "docs/operator-guide.md",
            "FAQ": "docs/faq.md",
        },
    )

    result = run_sync(manifest_path, wiki_dir)

    assert result.returncode == 0, result.stderr
    sidebar = (wiki_dir / "_Sidebar.md").read_text(encoding="utf-8")
    assert sidebar == (
        "## Hermit Wiki\n\n"
        "- [Home](Home)\n"
        "- [Getting-Started](Getting-Started)\n"
        "- [Task-Lifecycle](Task-Lifecycle)\n"
        "- [Operator-Guide](Operator-Guide)\n"
        "- [FAQ](FAQ)\n"
    )


def test_sync_rewrites_mirrored_and_repo_links(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    manifest_path = tmp_path / "wiki_manifest.json"
    write_manifest(
        manifest_path,
        {
            "Home": "docs/wiki-home.md",
            "Getting-Started": "docs/getting-started.md",
        },
    )

    result = run_sync(manifest_path, wiki_dir)

    assert result.returncode == 0, result.stderr
    content = (wiki_dir / "Home.md").read_text(encoding="utf-8")
    assert (
        "Canonical source: [docs/wiki-home.md](https://github.com/example/Hermit/blob/main/docs/wiki-home.md)"
        in content
    )
    assert "[Getting started](Getting-Started)" in content
    assert "[Repository README](https://github.com/example/Hermit/blob/main/README.md)" in content
    assert (
        "[Documentation map](https://github.com/example/Hermit/blob/main/README.md#documentation-map)"
        in content
    )


def test_sync_rewrites_anchors_for_mirrored_pages(tmp_path: Path) -> None:
    docs_dir = ROOT / "docs"
    source_path = docs_dir / "wiki-home-anchor-test.md"
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    manifest_path = tmp_path / "wiki_manifest.json"
    write_manifest(
        manifest_path,
        {
            "Home": "docs/wiki-home-anchor-test.md",
            "Roadmap": "docs/roadmap.md",
        },
    )
    source_path.write_text("[Roadmap anchor](./roadmap.md#status)\n", encoding="utf-8")

    try:
        result = run_sync(manifest_path, wiki_dir)
        assert result.returncode == 0, result.stderr
        content = (wiki_dir / "Home.md").read_text(encoding="utf-8")
        assert "[Roadmap anchor](Roadmap#status)" in content
    finally:
        if source_path.exists():
            source_path.unlink()


def test_sync_fails_when_manifest_source_is_missing(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    manifest_path = tmp_path / "wiki_manifest.json"
    write_manifest(manifest_path, {"Home": "docs/does-not-exist.md"})

    result = run_sync(manifest_path, wiki_dir)

    assert result.returncode != 0
    assert "Missing source file for wiki page 'Home': docs/does-not-exist.md" in result.stderr


def test_sync_fails_when_title_contains_invalid_characters(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    manifest_path = tmp_path / "wiki_manifest.json"
    write_manifest(manifest_path, {"Bad Title": "docs/wiki-home.md"})

    result = run_sync(manifest_path, wiki_dir)

    assert result.returncode != 0
    assert "Invalid wiki page title 'Bad Title'" in result.stderr
