"""Policy-domain enums for verdict and action_class strings.

Using ``str``-subclassing enums (``StrEnum``-style via ``str, Enum``) means
every enum value *is* a plain string, so:

- Serialisation / JSON dumps work transparently without ``.value`` access.
- Comparisons against legacy string literals remain True.
- ``isinstance(v, str)`` is True, so type annotations that say ``str``
  continue to accept enum members without casts.

Migration strategy
------------------
1. This module defines the canonical constants.
2. Call-sites should import ``Verdict`` / ``ActionClass`` and use the
   symbolic names instead of bare string literals.
3. Dataclass fields (``verdict: str``, ``action_class: str``) stay ``str``
   typed — backward-compatible with serialised store data and external callers.
"""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    """Canonical verdicts produced by the policy engine."""

    ALLOW = "allow"
    ALLOW_WITH_RECEIPT = "allow_with_receipt"
    APPROVAL_REQUIRED = "approval_required"
    PREVIEW_REQUIRED = "preview_required"
    DENY = "deny"

    # Non-policy special verdicts used in specific subsystems
    REQUIRE_APPROVAL = "require_approval"  # legacy alias kept for compatibility
    SELECTED = "selected"  # task-plan selection verdict

    @classmethod
    def _missing_(cls, value: object) -> None:
        valid = ", ".join(f'"{m.value}"' for m in cls)
        raise ValueError(f"{value!r} is not a valid {cls.__name__}. Expected one of: {valid}")


class ActionClass(StrEnum):
    """Canonical action-class identifiers used across the policy and execution layers."""

    # Read-only
    READ_LOCAL = "read_local"
    NETWORK_READ = "network_read"
    EXECUTE_COMMAND_READONLY = "execute_command_readonly"

    # Local writes
    WRITE_LOCAL = "write_local"
    PATCH_FILE = "patch_file"
    MEMORY_WRITE = "memory_write"

    # Process / shell execution
    EXECUTE_COMMAND = "execute_command"

    # Network / external mutations
    NETWORK_WRITE = "network_write"
    CREDENTIALED_API_CALL = "credentialed_api_call"
    EXTERNAL_MUTATION = "external_mutation"

    # VCS / publishing
    VCS_MUTATION = "vcs_mutation"
    PUBLICATION = "publication"

    # Orchestration / lifecycle
    DELEGATE_EXECUTION = "delegate_execution"
    DELEGATE_REASONING = "delegate_reasoning"
    SCHEDULER_MUTATION = "scheduler_mutation"
    APPROVAL_RESOLUTION = "approval_resolution"
    ROLLBACK = "rollback"

    # UI / attachments / infra
    EPHEMERAL_UI_MUTATION = "ephemeral_ui_mutation"
    ATTACHMENT_INGEST = "attachment_ingest"
    PATROL_EXECUTION = "patrol_execution"

    # Fallback
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> None:
        valid = ", ".join(f'"{m.value}"' for m in cls)
        raise ValueError(f"{value!r} is not a valid {cls.__name__}. Expected one of: {valid}")


class ComplexityBand(StrEnum):
    """Task complexity bands for governance intensity adaptation.

    Higher complexity bands trigger more governance stages (witness capture,
    contract synthesis, deliberation, reconciliation). Lower bands skip
    expensive stages to reduce overhead on simple tasks.
    """

    TRIVIAL = "trivial"  # Single-step read-only operations
    SIMPLE = "simple"  # 1-2 steps, low-risk write operations
    MODERATE = "moderate"  # 3-5 steps, mixed read/write
    COMPLEX = "complex"  # DAG tasks, high-risk, supervised
