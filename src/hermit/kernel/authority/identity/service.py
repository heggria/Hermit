from __future__ import annotations

from hermit.kernel.authority.identity.models import PrincipalRecord
from hermit.kernel.ledger.journal.store import KernelStore

# Channels that are treated as first-class principal types when the actor name
# is not a reserved system identity ("kernel" or "user").  Using a named
# constant avoids scattering the same magic set across the codebase and makes
# future additions (e.g. a new adapter) a single-line change.
_CHANNEL_PRINCIPAL_TYPES: frozenset[str] = frozenset(
    {"scheduler", "webhook", "feishu", "cli", "chat"}
)

# Reserved actor names that always map to system/user principal types and must
# never be overridden by the channel.
_SYSTEM_ACTOR_NAMES: frozenset[str] = frozenset({"kernel", "user"})


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
        """Resolve a free-form actor string to a :class:`PrincipalRecord`.

        Resolution rules (applied in order):

        1. A *None* or blank actor defaults to ``"kernel"``.
        2. The actor ``"kernel"`` maps to ``principal_type="system"``; any
           other actor maps to ``principal_type="user"`` by default.
        3. If *source_channel* is a recognised external channel (see
           ``_CHANNEL_PRINCIPAL_TYPES``) **and** the actor is not a reserved
           system name, the channel string itself becomes the
           ``principal_type``.  This lets policy rules distinguish, say, a
           Feishu user from a CLI user even when both supply the same display
           name.
        """
        name = str(actor or "kernel").strip() or "kernel"
        principal_type = "system" if name == "kernel" else "user"
        if source_channel in _CHANNEL_PRINCIPAL_TYPES and name not in _SYSTEM_ACTOR_NAMES:
            principal_type = source_channel
        return self.resolve(
            principal_type=principal_type,
            display_name=name,
            source_channel=source_channel,
            external_ref=name,
        )
