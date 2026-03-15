from __future__ import annotations

from hermit.capabilities.models import CapabilityGrantRecord

__all__ = ["CapabilityGrantError", "CapabilityGrantRecord", "CapabilityGrantService"]


def __getattr__(name: str):
    if name not in {"CapabilityGrantError", "CapabilityGrantService"}:
        raise AttributeError(name)
    from hermit.capabilities.service import CapabilityGrantError, CapabilityGrantService

    return {
        "CapabilityGrantError": CapabilityGrantError,
        "CapabilityGrantService": CapabilityGrantService,
    }[name]
