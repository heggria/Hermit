"""Tests for promote_memories_via_kernel — policy deny and capability grant error paths."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.plugins.builtin.hooks.memory.hooks_promotion import promote_memories_via_kernel
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry


def _settings(tmp_path: Path) -> SimpleNamespace:
    """Build a minimal settings object."""
    db_path = tmp_path / "kernel.db"
    artifacts_dir = tmp_path / "artifacts"
    memory_file = tmp_path / "memory" / "memories.md"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text("")
    return SimpleNamespace(
        kernel_db_path=str(db_path),
        kernel_artifacts_dir=str(artifacts_dir),
        memory_file=str(memory_file),
    )


def _entry(category: str = "user_preference", content: str = "test memory") -> MemoryEntry:
    return MemoryEntry(
        category=category,
        content=content,
        confidence=0.8,
    )


def test_promote_returns_false_when_policy_denies(tmp_path: Path) -> None:
    """When PolicyEngine.evaluate returns verdict='deny', promotion returns False."""
    settings = _settings(tmp_path)
    engine = MagicMock()
    entry = _entry()

    from hermit.kernel.policy.models.models import PolicyDecision, PolicyObligations

    deny_policy = PolicyDecision(
        verdict="deny",
        action_class="memory_write",
        obligations=PolicyObligations(),
        risk_level="low",
    )

    with patch(
        "hermit.kernel.policy.PolicyEngine.evaluate",
        return_value=deny_policy,
    ):
        # Lines 169-170: policy.verdict == "deny" → finalize + return False
        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-1",
            messages=[{"role": "user", "content": "hello"}],
            used_keywords=set(),
            new_entries=[entry],
            mode="session_end",
        )

    assert result is False


def test_promote_returns_false_when_approval_required(tmp_path: Path) -> None:
    """When policy requires approval, promotion returns False."""
    settings = _settings(tmp_path)
    engine = MagicMock()
    entry = _entry()

    from hermit.kernel.policy.models.models import PolicyDecision, PolicyObligations

    require_approval_policy = PolicyDecision(
        verdict="allow",
        action_class="memory_write",
        obligations=PolicyObligations(require_approval=True),
        risk_level="medium",
    )

    with patch(
        "hermit.kernel.policy.PolicyEngine.evaluate",
        return_value=require_approval_policy,
    ):
        # Lines 169-170: obligations.require_approval → finalize + return False
        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-2",
            messages=[{"role": "user", "content": "hello"}],
            used_keywords=set(),
            new_entries=[entry],
            mode="session_end",
        )

    assert result is False


def test_promote_returns_false_on_capability_grant_error(tmp_path: Path) -> None:
    """When CapabilityGrantService.enforce raises, promotion returns False."""
    settings = _settings(tmp_path)
    engine = MagicMock()
    entry = _entry()

    from hermit.kernel.authority.grants import CapabilityGrantError

    def _enforce_raises(*args, **kwargs):
        raise CapabilityGrantError(code="expired", message="Grant expired")

    with patch(
        "hermit.kernel.authority.grants.CapabilityGrantService.enforce",
        side_effect=_enforce_raises,
    ):
        # Lines 233-234, 249, 257-259: CapabilityGrantError → event + fail + return False
        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-3",
            messages=[{"role": "user", "content": "hello"}],
            used_keywords=set(),
            new_entries=[entry],
            mode="session_end",
        )

    assert result is False


def test_promote_returns_false_for_missing_settings() -> None:
    """When kernel_db_path or kernel_artifacts_dir is missing, returns False."""
    engine = MagicMock()
    entry = _entry()

    # No kernel_db_path
    settings = SimpleNamespace(kernel_db_path=None, kernel_artifacts_dir="/tmp/art")
    result = promote_memories_via_kernel(
        engine,
        settings,
        session_id="s",
        messages=[],
        used_keywords=set(),
        new_entries=[entry],
        mode="x",
    )
    assert result is False


def test_promote_returns_false_for_empty_entries() -> None:
    """When new_entries is empty, returns False."""
    engine = MagicMock()
    settings = SimpleNamespace(kernel_db_path="/tmp/db", kernel_artifacts_dir="/tmp/art")
    result = promote_memories_via_kernel(
        engine,
        settings,
        session_id="s",
        messages=[],
        used_keywords=set(),
        new_entries=[],
        mode="x",
    )
    assert result is False
