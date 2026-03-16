from __future__ import annotations

from hermit.identity.models import PrincipalRecord
from hermit.kernel.store import KernelStore


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
        name = str(actor or "kernel").strip() or "kernel"
        principal_type = "system" if name == "kernel" else "user"
        if source_channel in {"scheduler", "webhook", "feishu", "cli", "chat"} and name not in {
            "kernel",
            "user",
        }:
            principal_type = source_channel
        return self.resolve(
            principal_type=principal_type,
            display_name=name,
            source_channel=source_channel,
            external_ref=name,
        )
