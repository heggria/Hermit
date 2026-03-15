from __future__ import annotations

from hermit.identity.models import PrincipalRecord

__all__ = ["PrincipalRecord", "PrincipalService"]


def __getattr__(name: str):
    if name != "PrincipalService":
        raise AttributeError(name)
    from hermit.identity.service import PrincipalService

    return PrincipalService
