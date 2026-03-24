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
    # REQUIRE_APPROVAL is a legacy alias for APPROVAL_REQUIRED.
    # Both share the same underlying string value ("approval_required"), so
    # they compare equal and serialise identically — use APPROVAL_REQUIRED in
    # new code.
    REQUIRE_APPROVAL = "approval_required"  # true alias; same value as APPROVAL_REQUIRED
    SELECTED = "selected"  # task-plan selection verdict


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
