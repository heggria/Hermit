from __future__ import annotations

from hermit.kernel.authority.identity.models import PrincipalRecord
from hermit.kernel.ledger.journal.store import KernelStore

# Channels that double as principal types when the actor name is not a
# reserved system identifier.  Keeping this in one place means adding a
# new channel only requires a single update here.
_CHANNEL_PRINCIPAL_TYPES: frozenset[str] = frozenset(
    {"scheduler", "webhook", "feishu", "cli", "chat"}
)

# Display names that are reserved for internal/system principals and must
# not be overridden by a channel-derived principal type.
_RESERVED_SYSTEM_NAMES: frozenset[str] = frozenset({"kernel", "user"})


class PrincipalService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store

    def resolve(
        self,
        *,
        principal_type: str,
        display_name: str,
        source_channel: str | None = None,
        external_ref: str | None = None,
    ) -> PrincipalRecord:
        return self.store.ensure_principal(
            principal_type=principal_type,
            display_name=display_name,
            source_channel=source_channel,
            external_ref=external_ref,
        )

    def resolve_name(
        self, actor: str | None, *, source_channel: str | None = None
    ) -> PrincipalRecord:
        """Resolve an actor string to a :class:`PrincipalRecord`.

        Coercion rules (applied in order):
        1. ``None`` or whitespace-only *actor* values are treated as
           ``"kernel"`` (the internal system principal).
        2. If *source_channel* is one of the recognised channel types
           **and** the resolved name is not a reserved system name, the
           channel is used as the ``principal_type`` so that
           channel-specific actors (e.g. a Feishu user) are grouped
           correctly.
        3. Otherwise ``"system"`` is used for ``"kernel"`` and ``"user"``
           is used for all other names.
        """
        name = str(actor or "kernel").strip() or "kernel"
        principal_type = "system" if name == "kernel" else "user"
        if source_channel in _CHANNEL_PRINCIPAL_TYPES and name not in _RESERVED_SYSTEM_NAMES:
            principal_type = source_channel
        return self.resolve(
            principal_type=principal_type,
            display_name=name,
            source_channel=source_channel,
            external_ref=name,
        )
