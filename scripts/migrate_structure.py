#!/usr/bin/env python3
"""Hermit directory restructure migration script.

Moves files from flat/shallow layout to domain-driven architecture:
  core/, kernel/, plugin/, provider/, builtin/, storage/ ->
  surfaces/, runtime/, kernel/, infra/, plugins/, apps/

Uses git mv to preserve history. Rewrites all imports.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "hermit"
TESTS = ROOT / "tests"

# ---------------------------------------------------------------------------
# 1. FILE MOVES: old relative path (under src/hermit/) -> new relative path
# ---------------------------------------------------------------------------

FILE_MOVES: dict[str, str] = {
    # Top-level modules
    "main.py": "surfaces/cli/main.py",
    "config.py": "runtime/assembly/config.py",
    "context.py": "runtime/assembly/context.py",
    "i18n.py": "infra/system/i18n.py",
    "logging.py": "runtime/observation/logging/setup.py",
    "autostart.py": "surfaces/cli/autostart.py",
    "executables.py": "infra/system/executables.py",
    # core/ -> runtime/
    "core/runner.py": "runtime/control/runner/runner.py",
    "core/session.py": "runtime/control/lifecycle/session.py",
    "core/tools.py": "runtime/capability/registry/tools.py",
    "core/sandbox.py": "infra/system/sandbox.py",
    "core/budgets.py": "runtime/control/lifecycle/budgets.py",
    "core/orchestrator.py": "runtime/control/dispatch/orchestrator.py",
    # plugin/ -> runtime/capability/
    "plugin/base.py": "runtime/capability/contracts/base.py",
    "plugin/hooks.py": "runtime/capability/contracts/hooks.py",
    "plugin/rules.py": "runtime/capability/contracts/rules.py",
    "plugin/skills.py": "runtime/capability/contracts/skills.py",
    "plugin/config.py": "runtime/capability/loader/config.py",
    "plugin/loader.py": "runtime/capability/loader/loader.py",
    "plugin/manager.py": "runtime/capability/registry/manager.py",
    "plugin/mcp_client.py": "runtime/capability/resolver/mcp_client.py",
    # provider/ -> runtime/provider_host/
    "provider/runtime.py": "runtime/provider_host/execution/runtime.py",
    "provider/services.py": "runtime/provider_host/execution/services.py",
    "provider/contracts.py": "runtime/provider_host/shared/contracts.py",
    "provider/messages.py": "runtime/provider_host/shared/messages.py",
    "provider/profiles.py": "runtime/provider_host/shared/profiles.py",
    "provider/images.py": "runtime/provider_host/shared/images.py",
    "provider/providers/claude.py": "runtime/provider_host/llm/claude.py",
    "provider/providers/codex.py": "runtime/provider_host/llm/codex.py",
    # storage/ -> infra/
    "storage/store.py": "infra/storage/store.py",
    "storage/atomic.py": "infra/storage/atomic.py",
    "storage/lock.py": "infra/locking/lock.py",
    # identity/, capabilities/, workspaces/ -> kernel/authority/
    "identity/models.py": "kernel/authority/identity/models.py",
    "identity/service.py": "kernel/authority/identity/service.py",
    "capabilities/models.py": "kernel/authority/grants/models.py",
    "capabilities/service.py": "kernel/authority/grants/service.py",
    "workspaces/models.py": "kernel/authority/workspaces/models.py",
    "workspaces/service.py": "kernel/authority/workspaces/service.py",
    # kernel/ -> kernel/task/
    "kernel/models.py": "kernel/task/models/records.py",
    "kernel/controller.py": "kernel/task/services/controller.py",
    "kernel/topics.py": "kernel/task/services/topics.py",
    "kernel/ingress_router.py": "kernel/task/services/ingress_router.py",
    "kernel/planning.py": "kernel/task/services/planning.py",
    "kernel/outcomes.py": "kernel/task/state/outcomes.py",
    "kernel/continuation.py": "kernel/task/state/continuation.py",
    "kernel/control_intents.py": "kernel/task/state/control_intents.py",
    "kernel/projections.py": "kernel/task/projections/projections.py",
    "kernel/conversation_projection.py": "kernel/task/projections/conversation.py",
    "kernel/progress_summary.py": "kernel/task/projections/progress_summary.py",
    # kernel/ -> kernel/context/
    "kernel/context.py": "kernel/context/models/context.py",
    "kernel/context_compiler.py": "kernel/context/compiler/compiler.py",
    "kernel/memory_governance.py": "kernel/context/memory/governance.py",
    "kernel/memory_text.py": "kernel/context/memory/text.py",
    "kernel/knowledge.py": "kernel/context/memory/knowledge.py",
    "kernel/provider_input.py": "kernel/context/injection/provider_input.py",
    # kernel/ -> kernel/policy/
    "kernel/approvals.py": "kernel/policy/approvals/approvals.py",
    "kernel/approval_copy.py": "kernel/policy/approvals/approval_copy.py",
    "kernel/decisions.py": "kernel/policy/approvals/decisions.py",
    "kernel/authorization_plans.py": "kernel/policy/permits/authorization_plans.py",
    "kernel/policy/engine.py": "kernel/policy/evaluators/engine.py",
    "kernel/policy/derivation.py": "kernel/policy/evaluators/derivation.py",
    "kernel/policy/models.py": "kernel/policy/models/models.py",
    "kernel/policy/rules.py": "kernel/policy/guards/rules.py",
    "kernel/policy/fingerprint.py": "kernel/policy/guards/fingerprint.py",
    "kernel/policy/merge.py": "kernel/policy/guards/merge.py",
    "kernel/policy/tool_spec_adapter.py": "kernel/policy/guards/tool_spec_adapter.py",
    # kernel/ -> kernel/execution/
    "kernel/executor.py": "kernel/execution/executor/executor.py",
    "kernel/dispatch.py": "kernel/execution/coordination/dispatch.py",
    "kernel/observation.py": "kernel/execution/coordination/observation.py",
    "kernel/contracts.py": "kernel/execution/controller/contracts.py",
    "kernel/execution_contracts.py": "kernel/execution/controller/execution_contracts.py",
    "kernel/supervision.py": "kernel/execution/controller/supervision.py",
    "kernel/reconcile.py": "kernel/execution/recovery/reconcile.py",
    "kernel/reconciliations.py": "kernel/execution/recovery/reconciliations.py",
    "kernel/git_worktree.py": "kernel/execution/suspension/git_worktree.py",
    # kernel/ -> kernel/artifacts/
    "kernel/artifacts.py": "kernel/artifacts/models/artifacts.py",
    "kernel/claim_manifest.py": "kernel/artifacts/lineage/claim_manifest.py",
    "kernel/claims.py": "kernel/artifacts/lineage/claims.py",
    "kernel/evidence_cases.py": "kernel/artifacts/lineage/evidence_cases.py",
    # kernel/ -> kernel/verification/
    "kernel/receipts.py": "kernel/verification/receipts/receipts.py",
    "kernel/proofs.py": "kernel/verification/proofs/proofs.py",
    "kernel/rollbacks.py": "kernel/verification/rollbacks/rollbacks.py",
    # kernel/ -> kernel/ledger/
    "kernel/store.py": "kernel/ledger/journal/store.py",
    "kernel/store_ledger.py": "kernel/ledger/events/store_ledger.py",
    "kernel/store_projection.py": "kernel/ledger/projections/store_projection.py",
    "kernel/store_records.py": "kernel/ledger/journal/store_records.py",
    "kernel/store_scheduler.py": "kernel/ledger/journal/store_scheduler.py",
    "kernel/store_support.py": "kernel/ledger/journal/store_support.py",
    "kernel/store_tasks.py": "kernel/ledger/journal/store_tasks.py",
    "kernel/store_types.py": "kernel/ledger/journal/store_types.py",
    "kernel/store_v2.py": "kernel/ledger/journal/store_v2.py",
    # companion/ -> apps/companion/
    "companion/appbundle.py": "apps/companion/appbundle.py",
    "companion/control.py": "apps/companion/control.py",
    "companion/menubar.py": "apps/companion/menubar.py",
    # builtin/ -> plugins/builtin/ (categorized)
    "builtin/feishu/__init__.py": "plugins/builtin/adapters/feishu/__init__.py",
    "builtin/feishu/_client.py": "plugins/builtin/adapters/feishu/_client.py",
    "builtin/feishu/adapter.py": "plugins/builtin/adapters/feishu/adapter.py",
    "builtin/feishu/hooks.py": "plugins/builtin/adapters/feishu/hooks.py",
    "builtin/feishu/normalize.py": "plugins/builtin/adapters/feishu/normalize.py",
    "builtin/feishu/plugin.toml": "plugins/builtin/adapters/feishu/plugin.toml",
    "builtin/feishu/reaction.py": "plugins/builtin/adapters/feishu/reaction.py",
    "builtin/feishu/reply.py": "plugins/builtin/adapters/feishu/reply.py",
    "builtin/feishu/tools.py": "plugins/builtin/adapters/feishu/tools.py",
    "builtin/webhook/hooks.py": "plugins/builtin/hooks/webhook/hooks.py",
    "builtin/webhook/models.py": "plugins/builtin/hooks/webhook/models.py",
    "builtin/webhook/plugin.toml": "plugins/builtin/hooks/webhook/plugin.toml",
    "builtin/webhook/server.py": "plugins/builtin/hooks/webhook/server.py",
    "builtin/webhook/tools.py": "plugins/builtin/hooks/webhook/tools.py",
    "builtin/scheduler/__init__.py": "plugins/builtin/hooks/scheduler/__init__.py",
    "builtin/scheduler/engine.py": "plugins/builtin/hooks/scheduler/engine.py",
    "builtin/scheduler/hooks.py": "plugins/builtin/hooks/scheduler/hooks.py",
    "builtin/scheduler/models.py": "plugins/builtin/hooks/scheduler/models.py",
    "builtin/scheduler/plugin.toml": "plugins/builtin/hooks/scheduler/plugin.toml",
    "builtin/scheduler/tools.py": "plugins/builtin/hooks/scheduler/tools.py",
    "builtin/memory/__init__.py": "plugins/builtin/hooks/memory/__init__.py",
    "builtin/memory/engine.py": "plugins/builtin/hooks/memory/engine.py",
    "builtin/memory/hooks.py": "plugins/builtin/hooks/memory/hooks.py",
    "builtin/memory/plugin.toml": "plugins/builtin/hooks/memory/plugin.toml",
    "builtin/memory/types.py": "plugins/builtin/hooks/memory/types.py",
    "builtin/image_memory/engine.py": "plugins/builtin/hooks/image_memory/engine.py",
    "builtin/image_memory/hooks.py": "plugins/builtin/hooks/image_memory/hooks.py",
    "builtin/image_memory/plugin.toml": "plugins/builtin/hooks/image_memory/plugin.toml",
    "builtin/image_memory/types.py": "plugins/builtin/hooks/image_memory/types.py",
    "builtin/orchestrator/__init__.py": "plugins/builtin/subagents/orchestrator/__init__.py",
    "builtin/orchestrator/hooks.py": "plugins/builtin/subagents/orchestrator/hooks.py",
    "builtin/orchestrator/plugin.toml": "plugins/builtin/subagents/orchestrator/plugin.toml",
    "builtin/orchestrator/state.py": "plugins/builtin/subagents/orchestrator/state.py",
    "builtin/orchestrator/subagents.py": "plugins/builtin/subagents/orchestrator/subagents.py",
    "builtin/github/__init__.py": "plugins/builtin/mcp/github/__init__.py",
    "builtin/github/mcp.py": "plugins/builtin/mcp/github/mcp.py",
    "builtin/github/plugin.toml": "plugins/builtin/mcp/github/plugin.toml",
    "builtin/mcp_loader/__init__.py": "plugins/builtin/mcp/mcp_loader/__init__.py",
    "builtin/mcp_loader/mcp.py": "plugins/builtin/mcp/mcp_loader/mcp.py",
    "builtin/mcp_loader/plugin.toml": "plugins/builtin/mcp/mcp_loader/plugin.toml",
    "builtin/grok/plugin.toml": "plugins/builtin/tools/grok/plugin.toml",
    "builtin/grok/search.py": "plugins/builtin/tools/grok/search.py",
    "builtin/grok/tools.py": "plugins/builtin/tools/grok/tools.py",
    "builtin/web_tools/__init__.py": "plugins/builtin/tools/web_tools/__init__.py",
    "builtin/web_tools/fetch.py": "plugins/builtin/tools/web_tools/fetch.py",
    "builtin/web_tools/plugin.toml": "plugins/builtin/tools/web_tools/plugin.toml",
    "builtin/web_tools/search.py": "plugins/builtin/tools/web_tools/search.py",
    "builtin/web_tools/tools.py": "plugins/builtin/tools/web_tools/tools.py",
    "builtin/computer_use/__init__.py": "plugins/builtin/tools/computer_use/__init__.py",
    "builtin/computer_use/actions.py": "plugins/builtin/tools/computer_use/actions.py",
    "builtin/computer_use/plugin.toml": "plugins/builtin/tools/computer_use/plugin.toml",
    "builtin/computer_use/tools.py": "plugins/builtin/tools/computer_use/tools.py",
    "builtin/compact/commands.py": "plugins/builtin/bundles/compact/commands.py",
    "builtin/compact/plugin.toml": "plugins/builtin/bundles/compact/plugin.toml",
    "builtin/planner/commands.py": "plugins/builtin/bundles/planner/commands.py",
    "builtin/planner/plugin.toml": "plugins/builtin/bundles/planner/plugin.toml",
    "builtin/usage/commands.py": "plugins/builtin/bundles/usage/commands.py",
    "builtin/usage/plugin.toml": "plugins/builtin/bundles/usage/plugin.toml",
}

# Skills subdirectory moves (non-Python files, moved with git mv on the directory)
SKILLS_MOVES: dict[str, str] = {
    "builtin/feishu/skills": "plugins/builtin/adapters/feishu/skills",
    "builtin/webhook/skills": "plugins/builtin/hooks/webhook/skills",
    "builtin/scheduler/skills": "plugins/builtin/hooks/scheduler/skills",
    "builtin/memory/skills": "plugins/builtin/hooks/memory/skills",
    "builtin/image_memory/skills": "plugins/builtin/hooks/image_memory/skills",
    "builtin/orchestrator/skills": "plugins/builtin/subagents/orchestrator/skills",
    "builtin/github/skills": "plugins/builtin/mcp/github/skills",
    "builtin/grok/skills": "plugins/builtin/tools/grok/skills",
    "builtin/web_tools/skills": "plugins/builtin/tools/web_tools/skills",
    "builtin/computer_use/skills": "plugins/builtin/tools/computer_use/skills",
}

# Locales directory moves (non-Python, git mv the directory)
LOCALE_MOVES: dict[str, str] = {
    "locales": "infra/system/locales",
}

# Non-Python files in builtin that need moving
EXTRA_FILE_MOVES: dict[str, str] = {
    "builtin/AGENTS.md": "plugins/builtin/AGENTS.md",
}

# ---------------------------------------------------------------------------
# 2. IMPORT REWRITE MAP: old module path -> new module path
# ---------------------------------------------------------------------------


def build_import_map() -> dict[str, str]:
    """Build map from old hermit.X module path to new hermit.Y module path."""
    m: dict[str, str] = {}

    for old_rel, new_rel in FILE_MOVES.items():
        if not old_rel.endswith(".py"):
            continue
        # Convert file path to module path
        old_mod = "hermit." + old_rel[:-3].replace("/", ".")
        new_mod = "hermit." + new_rel[:-3].replace("/", ".")
        m[old_mod] = new_mod

    # Also handle package-level imports (directories with __init__.py)
    # Old packages that are being reorganized
    package_rewrites = {
        # core package -> individual targets (no single replacement, handled by file moves)
        # plugin package -> runtime.capability
        "hermit.runtime.capability": "hermit.runtime.capability",
        # provider package
        "hermit.runtime.provider_host": "hermit.runtime.provider_host",
        "hermit.runtime.provider_host.llm": "hermit.runtime.provider_host.llm",
        # storage package
        "hermit.infra.storage": "hermit.infra.storage",
        # companion package
        "hermit.apps.companion": "hermit.apps.companion",
        # identity/capabilities/workspaces
        "hermit.kernel.authority.identity": "hermit.kernel.authority.identity",
        "hermit.kernel.authority.grants": "hermit.kernel.authority.grants",
        "hermit.kernel.authority.workspaces": "hermit.kernel.authority.workspaces",
        # kernel.policy sub-package (the policy __init__.py itself moves)
        "hermit.kernel.policy": "hermit.kernel.policy",  # stays but internals move
    }
    m.update(package_rewrites)

    # Builtin rewrites
    builtin_category_map = {
        "feishu": "adapters",
        "webhook": "hooks",
        "scheduler": "hooks",
        "memory": "hooks",
        "image_memory": "hooks",
        "orchestrator": "subagents",
        "github": "mcp",
        "mcp_loader": "mcp",
        "grok": "tools",
        "web_tools": "tools",
        "computer_use": "tools",
        "compact": "bundles",
        "planner": "bundles",
        "usage": "bundles",
    }
    for plugin_name, category in builtin_category_map.items():
        old_prefix = f"hermit.builtin.{plugin_name}"
        new_prefix = f"hermit.plugins.builtin.{category}.{plugin_name}"
        m[old_prefix] = new_prefix

    return m


IMPORT_MAP = build_import_map()

# ---------------------------------------------------------------------------
# 3. TEST FILE MOVES
# ---------------------------------------------------------------------------

UNIT_TESTS = [
    "test_autostart.py",
    "test_config.py",
    "test_context.py",
    "test_i18n.py",
    "test_executables.py",
    "test_compact_commands.py",
    "test_context_compiler.py",
    "test_flatten_dict.py",
    "test_codex_provider.py",
    "test_codex_provider_extra.py",
    "test_claude_provider_extra.py",
    "test_companion_menubar.py",
    "test_docs_alignment.py",
    "test_grok_search.py",
    "test_image_memory_plugin.py",
    "test_install_scripts.py",
    "test_kernel_permits.py",
    "test_kernel_store_tasks_support.py",
    "test_memory_engine.py",
    "test_memory_governance.py",
    "test_memory_hooks.py",
    "test_memory_schema.py",
    "test_misc_coverage.py",
    "test_observation_and_client_extra.py",
    "test_planner_kernel.py",
    "test_plugin_manager.py",
    "test_plugin_manager_extra.py",
    "test_policy_derivation.py",
    "test_provider_images.py",
    "test_provider_input_compiler.py",
    "test_session.py",
    "test_sync_docs_to_wiki.py",
    "test_tools.py",
    "test_feishu_reply_extra.py",
    "test_feishu_hooks_and_reaction_extra.py",
    "test_feishu_tools_extra.py",
    "test_cli_error_branches.py",
    "test_web_tools_and_computer_use.py",
]

INTEGRATION_TESTS = [
    "test_cli.py",
    "test_cli_more.py",
    "test_build_macos_dmg.py",
    "test_companion_appbundle.py",
    "test_companion_control.py",
    "test_companion_control_extra.py",
    "test_context7_plugin.py",
    "test_dispatch_result.py",
    "test_github_plugin.py",
    "test_kernel_context_and_memory_services.py",
    "test_kernel_coverage_boost.py",
    "test_kernel_dispatch_and_controller_extra.py",
    "test_kernel_services_extra.py",
    "test_kernel_topics_and_projections_extra.py",
    "test_main_mcp_helpers.py",
    "test_mcp.py",
    "test_provider_runtime_extra.py",
    "test_provider_runtime_services.py",
    "test_runner_async_extra.py",
    "test_runner_extra.py",
    "test_scheduler.py",
    "test_scheduler_dispatch.py",
    "test_scheduler_webhook_tools_extra.py",
    "test_task_kernel_controller.py",
    "test_task_kernel_policy_executor.py",
    "test_task_kernel_reconcile.py",
    "test_task_kernel_runner_misc.py",
    "test_task_kernel_runtime.py",
    "test_webhook_search_and_hooks_extra.py",
    "test_webhook_server.py",
    "test_feishu_adapter_extra.py",
    "test_feishu_dispatcher_adapter_messages.py",
    "test_feishu_dispatcher_cards_and_actions.py",
    "test_feishu_dispatcher_lifecycle.py",
    "test_feishu_dispatcher_normalize_runner.py",
    "test_feishu_dispatcher_reactions.py",
]

FIXTURE_FILES = [
    "task_kernel_support.py",
    "feishu_dispatcher_support.py",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True, **kwargs)


def git_mv(old: str, new: str) -> None:
    old_path = SRC / old
    new_path = SRC / new
    if not old_path.exists():
        print(f"  SKIP (not found): {old}")
        return
    new_path.parent.mkdir(parents=True, exist_ok=True)
    # If target already exists (e.g. auto-created __init__.py), remove it first
    if new_path.exists():
        new_path.unlink()
    run(["git", "mv", str(old_path), str(new_path)])


def ensure_init(directory: Path) -> None:
    """Create __init__.py if it doesn't exist."""
    init = directory / "__init__.py"
    if not init.exists():
        init.write_text("")


def collect_all_target_dirs() -> set[Path]:
    """Collect all unique target directories that need __init__.py files."""
    dirs: set[Path] = set()
    for new_rel in FILE_MOVES.values():
        if not new_rel.endswith(".py"):
            continue
        # All intermediate packages need __init__.py
        parts = Path(new_rel).parent.parts
        for i in range(1, len(parts) + 1):
            dirs.add(SRC / Path(*parts[:i]))
    return dirs


# ---------------------------------------------------------------------------
# Import rewriting
# ---------------------------------------------------------------------------


def rewrite_imports_in_file(filepath: Path) -> int:
    """Rewrite imports in a single file. Returns number of replacements made."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return 0

    original = content
    changes = 0

    # Sort by longest prefix first to avoid partial matches
    sorted_map = sorted(IMPORT_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)

    for old_mod, new_mod in sorted_map:
        if old_mod == new_mod:
            continue

        # Pattern 1: from hermit.old.path import X
        pattern_from = re.compile(
            r"(?<![.\w])from\s+" + re.escape(old_mod) + r"(?=\s+import\b|\s*\()"
        )
        content = pattern_from.sub(f"from {new_mod}", content)

        # Pattern 2: import hermit.old.path
        pattern_import = re.compile(r"(?<![.\w])import\s+" + re.escape(old_mod) + r"(?=\s|$|,)")
        content = pattern_import.sub(f"import {new_mod}", content)

        # Pattern 3: String references in dicts/tuples (like kernel __init__.py _EXPORTS)
        # Match "hermit.old.path" in quotes
        pattern_str = re.compile(r'(["\'])' + re.escape(old_mod) + r"(\1)")
        content = pattern_str.sub(rf"\g<1>{new_mod}\g<2>", content)

        # Pattern 4: f-string or format string references like hermit.builtin.X
        # (handled by the string pattern above when in quotes)

    if content != original:
        filepath.write_text(content, encoding="utf-8")
        changes = 1

    return changes


def rewrite_test_support_imports(filepath: Path) -> int:
    """Rewrite test support module imports (tests.X -> tests.fixtures.X)."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return 0

    original = content

    for fixture in FIXTURE_FILES:
        module_name = fixture[:-3]  # strip .py
        content = content.replace(
            f"from tests.{module_name}",
            f"from tests.fixtures.{module_name}",
        )

    if content != original:
        filepath.write_text(content, encoding="utf-8")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Special-case fixes
# ---------------------------------------------------------------------------


def fix_plugin_loader() -> None:
    """Fix hardcoded builtin module path in loader.py and add recursive discovery."""
    loader_path = SRC / "runtime" / "capability" / "loader" / "loader.py"
    if not loader_path.exists():
        print("  WARN: loader.py not found at expected path")
        return

    content = loader_path.read_text(encoding="utf-8")

    # Fix the hardcoded module path derivation in _invoke_entry
    old_invoke = '''            dir_name = Path(
                manifest.plugin_dir if manifest.plugin_dir is not None else plugin_dir
            ).name
            full_module = f"hermit.builtin.{dir_name}.{module_name}"'''

    new_invoke = '''            # Derive module path from plugin_dir relative to package root
            _plugin_path = Path(
                manifest.plugin_dir if manifest.plugin_dir is not None else plugin_dir
            )
            _pkg_root = Path(__file__).resolve().parents[4]  # src/hermit
            try:
                _rel = _plugin_path.resolve().relative_to(_pkg_root)
                full_module = "hermit." + ".".join(_rel.parts) + "." + module_name
            except ValueError:
                full_module = f"hermit.plugins.builtin.{_plugin_path.name}.{module_name}"'''

    content = content.replace(old_invoke, new_invoke)

    # Fix discover_plugins to recurse into subcategory directories
    old_discover = """def discover_plugins(*search_dirs: Path) -> List[PluginManifest]:
    manifests: List[PluginManifest] = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for child in sorted(search_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest = parse_manifest(child)
            if manifest is not None:
                manifests.append(manifest)
    return manifests"""

    new_discover = """def discover_plugins(*search_dirs: Path) -> List[PluginManifest]:
    manifests: List[PluginManifest] = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for child in sorted(search_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest = parse_manifest(child)
            if manifest is not None:
                manifests.append(manifest)
            else:
                # Recurse into category subdirectories (adapters/, hooks/, etc.)
                for grandchild in sorted(child.iterdir()):
                    if not grandchild.is_dir():
                        continue
                    sub_manifest = parse_manifest(grandchild)
                    if sub_manifest is not None:
                        manifests.append(sub_manifest)
    return manifests"""

    content = content.replace(old_discover, new_discover)

    loader_path.write_text(content, encoding="utf-8")
    print("  Fixed plugin loader (module path derivation + recursive discovery)")


def fix_builtin_dir_refs() -> None:
    """Fix all references to Path(__file__).parent / 'builtin' in main.py and services.py."""

    # Fix main.py: Path(__file__).parent / "builtin" -> package root / "plugins/builtin"
    main_path = SRC / "surfaces" / "cli" / "main.py"
    if main_path.exists():
        content = main_path.read_text(encoding="utf-8")
        # Replace all occurrences of the builtin dir pattern
        content = content.replace(
            'Path(__file__).parent / "builtin"',
            'Path(__file__).resolve().parents[2] / "plugins" / "builtin"',
        )
        main_path.write_text(content, encoding="utf-8")
        print("  Fixed builtin dir refs in main.py")

    # Fix services.py: Path(__file__).resolve().parents[1] / "builtin"
    services_path = SRC / "runtime" / "provider_host" / "execution" / "services.py"
    if services_path.exists():
        content = services_path.read_text(encoding="utf-8")
        content = content.replace(
            'Path(__file__).resolve().parents[1] / "builtin"',
            'Path(__file__).resolve().parents[4] / "plugins" / "builtin"',
        )
        services_path.write_text(content, encoding="utf-8")
        print("  Fixed builtin dir refs in services.py")


def fix_kernel_init() -> None:
    """Rewrite kernel/__init__.py with new module paths."""
    init_path = SRC / "kernel" / "__init__.py"
    if not init_path.exists():
        print("  WARN: kernel/__init__.py not found")
        return

    new_content = """from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermit.kernel.policy.approvals.approval_copy import ApprovalCopyService
    from hermit.kernel.policy.approvals.approvals import ApprovalService
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.context.models.context import CompiledProviderInput, TaskExecutionContext
    from hermit.kernel.task.services.controller import TaskController
    from hermit.kernel.task.projections.conversation import ConversationProjectionService
    from hermit.kernel.execution.executor.executor import ToolExecutionResult, ToolExecutor
    from hermit.kernel.context.memory.knowledge import BeliefService, MemoryRecordService
    from hermit.kernel.task.services.planning import PlanningService
    from hermit.kernel.policy import PolicyDecision, PolicyEngine
    from hermit.kernel.task.projections.projections import ProjectionService
    from hermit.kernel.verification.proofs.proofs import ProofService
    from hermit.kernel.verification.receipts.receipts import ReceiptService
    from hermit.kernel.verification.rollbacks.rollbacks import RollbackService
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.execution.controller.supervision import SupervisionService

__all__ = [
    "ApprovalService",
    "ApprovalCopyService",
    "ArtifactStore",
    "KernelStore",
    "PolicyDecision",
    "PolicyEngine",
    "ProofService",
    "ProjectionService",
    "RollbackService",
    "ReceiptService",
    "BeliefService",
    "MemoryRecordService",
    "PlanningService",
    "ConversationProjectionService",
    "SupervisionService",
    "TaskController",
    "CompiledProviderInput",
    "TaskExecutionContext",
    "ToolExecutionResult",
    "ToolExecutor",
]

_EXPORTS = {
    "ApprovalCopyService": ("hermit.kernel.policy.approvals.approval_copy", "ApprovalCopyService"),
    "ApprovalService": ("hermit.kernel.policy.approvals.approvals", "ApprovalService"),
    "ArtifactStore": ("hermit.kernel.artifacts.models.artifacts", "ArtifactStore"),
    "CompiledProviderInput": ("hermit.kernel.context.models.context", "CompiledProviderInput"),
    "TaskExecutionContext": ("hermit.kernel.context.models.context", "TaskExecutionContext"),
    "ConversationProjectionService": (
        "hermit.kernel.task.projections.conversation",
        "ConversationProjectionService",
    ),
    "TaskController": ("hermit.kernel.task.services.controller", "TaskController"),
    "ToolExecutionResult": ("hermit.kernel.execution.executor.executor", "ToolExecutionResult"),
    "ToolExecutor": ("hermit.kernel.execution.executor.executor", "ToolExecutor"),
    "PolicyDecision": ("hermit.kernel.policy", "PolicyDecision"),
    "PolicyEngine": ("hermit.kernel.policy", "PolicyEngine"),
    "ProofService": ("hermit.kernel.verification.proofs.proofs", "ProofService"),
    "ProjectionService": ("hermit.kernel.task.projections.projections", "ProjectionService"),
    "RollbackService": ("hermit.kernel.verification.rollbacks.rollbacks", "RollbackService"),
    "ReceiptService": ("hermit.kernel.verification.receipts.receipts", "ReceiptService"),
    "KernelStore": ("hermit.kernel.ledger.journal.store", "KernelStore"),
    "SupervisionService": (
        "hermit.kernel.execution.controller.supervision",
        "SupervisionService",
    ),
    "BeliefService": ("hermit.kernel.context.memory.knowledge", "BeliefService"),
    "MemoryRecordService": ("hermit.kernel.context.memory.knowledge", "MemoryRecordService"),
    "PlanningService": ("hermit.kernel.task.services.planning", "PlanningService"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
"""
    init_path.write_text(new_content, encoding="utf-8")
    print("  Rewrote kernel/__init__.py with new module paths")


def fix_kernel_policy_init() -> None:
    """Rewrite kernel/policy/__init__.py with new sub-paths."""
    init_path = SRC / "kernel" / "policy" / "__init__.py"
    if not init_path.exists():
        print("  WARN: kernel/policy/__init__.py not found")
        return

    new_content = """from hermit.kernel.policy.evaluators.engine import PolicyEngine
from hermit.kernel.policy.guards.fingerprint import build_action_fingerprint
from hermit.kernel.policy.models.models import (
    ActionRequest,
    PolicyDecision,
    PolicyObligations,
    PolicyReason,
)
from hermit.kernel.policy.guards.rules import POLICY_RULES_VERSION

__all__ = [
    "ActionRequest",
    "POLICY_RULES_VERSION",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyObligations",
    "PolicyReason",
    "build_action_fingerprint",
]
"""
    init_path.write_text(new_content, encoding="utf-8")
    print("  Rewrote kernel/policy/__init__.py with new module paths")


# ---------------------------------------------------------------------------
# Main phases
# ---------------------------------------------------------------------------


def phase1_move_files() -> None:
    """Phase 1: Create directories and move files."""
    print("\n=== Phase 1a: Creating target directories ===")
    target_dirs = collect_all_target_dirs()
    for d in sorted(target_dirs):
        d.mkdir(parents=True, exist_ok=True)
        ensure_init(d)

    # Also create top-level package __init__.py files
    for pkg in [
        "surfaces",
        "surfaces/cli",
        "runtime",
        "runtime/assembly",
        "runtime/observation",
        "runtime/observation/logging",
        "runtime/control",
        "runtime/control/runner",
        "runtime/control/lifecycle",
        "runtime/control/dispatch",
        "runtime/capability",
        "runtime/capability/contracts",
        "runtime/capability/loader",
        "runtime/capability/registry",
        "runtime/capability/resolver",
        "runtime/provider_host",
        "runtime/provider_host/execution",
        "runtime/provider_host/shared",
        "runtime/provider_host/llm",
        "infra",
        "infra/system",
        "infra/storage",
        "infra/locking",
        "kernel/authority",
        "kernel/authority/identity",
        "kernel/authority/grants",
        "kernel/authority/workspaces",
        "kernel/task",
        "kernel/task/models",
        "kernel/task/services",
        "kernel/task/state",
        "kernel/task/projections",
        "kernel/context",
        "kernel/context/models",
        "kernel/context/compiler",
        "kernel/context/memory",
        "kernel/context/injection",
        "kernel/policy/approvals",
        "kernel/policy/permits",
        "kernel/policy/evaluators",
        "kernel/policy/models",
        "kernel/policy/guards",
        "kernel/execution",
        "kernel/execution/executor",
        "kernel/execution/coordination",
        "kernel/execution/controller",
        "kernel/execution/recovery",
        "kernel/execution/suspension",
        "kernel/artifacts",
        "kernel/artifacts/models",
        "kernel/artifacts/lineage",
        "kernel/verification",
        "kernel/verification/receipts",
        "kernel/verification/proofs",
        "kernel/verification/rollbacks",
        "kernel/ledger",
        "kernel/ledger/journal",
        "kernel/ledger/events",
        "kernel/ledger/projections",
        "apps",
        "apps/companion",
        "plugins",
        "plugins/builtin",
        "plugins/builtin/adapters",
        "plugins/builtin/hooks",
        "plugins/builtin/subagents",
        "plugins/builtin/mcp",
        "plugins/builtin/tools",
        "plugins/builtin/bundles",
    ]:
        d = SRC / pkg
        d.mkdir(parents=True, exist_ok=True)
        ensure_init(d)

    print("\n=== Phase 1b: Moving files (git mv) ===")
    for old_rel, new_rel in FILE_MOVES.items():
        git_mv(old_rel, new_rel)

    print("\n=== Phase 1b+: Moving skills directories ===")
    for old_rel, new_rel in SKILLS_MOVES.items():
        old_path = SRC / old_rel
        new_path = SRC / new_rel
        if old_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            run(["git", "mv", str(old_path), str(new_path)])

    print("\n=== Phase 1b++: Moving locale directories ===")
    for old_rel, new_rel in LOCALE_MOVES.items():
        old_path = SRC / old_rel
        new_path = SRC / new_rel
        if old_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            run(["git", "mv", str(old_path), str(new_path)])

    print("\n=== Phase 1b+++: Moving extra files ===")
    for old_rel, new_rel in EXTRA_FILE_MOVES.items():
        git_mv(old_rel, new_rel)


def phase1c_rewrite_imports() -> None:
    """Phase 1c: Rewrite all imports in src/ and tests/."""
    print("\n=== Phase 1c: Rewriting imports ===")
    count = 0
    for root, _, files in os.walk(SRC):
        for f in files:
            if f.endswith(".py"):
                filepath = Path(root) / f
                count += rewrite_imports_in_file(filepath)
    for root, _, files in os.walk(TESTS):
        for f in files:
            if f.endswith(".py"):
                filepath = Path(root) / f
                count += rewrite_imports_in_file(filepath)

    # Also rewrite imports in scripts/*.py
    scripts_dir = ROOT / "scripts"
    for f in scripts_dir.glob("*.py"):
        count += rewrite_imports_in_file(f)

    print(f"  Rewrote imports in {count} files")


def phase1d_special_fixes() -> None:
    """Phase 1d: Apply critical code changes."""
    print("\n=== Phase 1d: Applying special-case fixes ===")
    fix_plugin_loader()
    fix_builtin_dir_refs()
    fix_kernel_init()
    fix_kernel_policy_init()


def phase2_move_tests() -> None:
    """Phase 2: Reorganize test files into subdirectories."""
    print("\n=== Phase 2: Reorganizing tests ===")

    # Create test subdirectories
    for subdir in ["unit", "integration", "fixtures", "scenario", "e2e"]:
        d = TESTS / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("")

    # Move unit tests
    for test_file in UNIT_TESTS:
        old = TESTS / test_file
        new = TESTS / "unit" / test_file
        if old.exists():
            run(["git", "mv", str(old), str(new)])

    # Move integration tests
    for test_file in INTEGRATION_TESTS:
        old = TESTS / test_file
        new = TESTS / "integration" / test_file
        if old.exists():
            run(["git", "mv", str(old), str(new)])

    # Move fixture files
    for fixture_file in FIXTURE_FILES:
        old = TESTS / fixture_file
        new = TESTS / "fixtures" / fixture_file
        if old.exists():
            run(["git", "mv", str(old), str(new)])

    # Rewrite test support imports
    print("  Rewriting test support imports...")
    count = 0
    for root, _, files in os.walk(TESTS):
        for f in files:
            if f.endswith(".py"):
                count += rewrite_test_support_imports(Path(root) / f)
    print(f"  Rewrote test support imports in {count} files")


def phase4_cleanup() -> None:
    """Phase 4: Remove empty old directories."""
    print("\n=== Phase 4: Cleaning up old directories ===")

    old_dirs = [
        "core",
        "plugin",
        "provider",
        "provider/providers",
        "storage",
        "builtin",
        "companion",
        "identity",
        "capabilities",
        "workspaces",
        "locales",
    ]
    for d in old_dirs:
        full_path = SRC / d
        if full_path.exists():
            # Remove __init__.py if it's the only file left
            init = full_path / "__init__.py"
            if init.exists():
                remaining = list(full_path.iterdir())
                remaining = [f for f in remaining if f.name != "__pycache__"]
                if len(remaining) <= 1:
                    run(["git", "rm", "-f", str(init)])

            # Remove __pycache__
            pycache = full_path / "__pycache__"
            if pycache.exists():
                import shutil

                shutil.rmtree(pycache)

            # Try to remove the directory if it's empty
            try:
                full_path.rmdir()
                print(f"  Removed {d}/")
            except OSError:
                # Directory not empty - list remaining files
                remaining = list(full_path.rglob("*"))
                if remaining:
                    print(f"  WARN: {d}/ not empty, remaining: {[str(r) for r in remaining[:5]]}")

    # Also clean up builtin subdirectories
    builtin_dir = SRC / "builtin"
    if builtin_dir.exists():
        import shutil

        # Remove all __pycache__ first
        for cache_dir in builtin_dir.rglob("__pycache__"):
            shutil.rmtree(cache_dir)
        # Try to remove all __init__.py in builtin and sub-dirs
        for init_file in sorted(builtin_dir.rglob("__init__.py"), reverse=True):
            try:
                run(["git", "rm", "-f", str(init_file)])
            except subprocess.CalledProcessError:
                init_file.unlink(missing_ok=True)
        # Remove empty dirs bottom-up
        for dirpath in sorted(builtin_dir.rglob("*"), reverse=True):
            if dirpath.is_dir():
                try:
                    dirpath.rmdir()
                except OSError:
                    pass
        try:
            builtin_dir.rmdir()
            print("  Removed builtin/")
        except OSError:
            print("  WARN: builtin/ not fully removed")


def main() -> None:
    print("=" * 60)
    print("Hermit Directory Restructure Migration")
    print("=" * 60)

    # Verify we're in the right place
    if not (ROOT / "pyproject.toml").exists():
        print("ERROR: pyproject.toml not found. Run from project root.")
        sys.exit(1)
    if not SRC.exists():
        print("ERROR: src/hermit/ not found.")
        sys.exit(1)

    phase1_move_files()
    phase1c_rewrite_imports()
    phase1d_special_fixes()
    phase2_move_tests()
    phase4_cleanup()

    print("\n" + "=" * 60)
    print("Migration complete!")
    print("Next steps:")
    print("  1. Update pyproject.toml entry points")
    print("  2. Update shell scripts")
    print("  3. Run: make lint")
    print("  4. Run: make test")
    print("=" * 60)


if __name__ == "__main__":
    main()
