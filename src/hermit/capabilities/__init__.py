from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hermit.capabilities.models import CapabilityGrantRecord

if TYPE_CHECKING:
    from hermit.capabilities.service import CapabilityGrantError, CapabilityGrantService

__all__ = ["CapabilityGrantError", "CapabilityGrantRecord", "CapabilityGrantService"]


def __getattr__(name: str) -> Any:
    if name not in {"CapabilityGrantError", "CapabilityGrantService"}:
        raise AttributeError(name)
    from hermit.capabilities.service import CapabilityGrantError, CapabilityGrantService

    return {
        "CapabilityGrantError": CapabilityGrantError,
        "CapabilityGrantService": CapabilityGrantService,
    }[name]
