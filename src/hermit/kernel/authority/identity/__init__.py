from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hermit.kernel.authority.identity.models import PrincipalRecord

if TYPE_CHECKING:
    from hermit.kernel.authority.identity.service import PrincipalService

__all__ = ["PrincipalRecord", "PrincipalService"]


def __getattr__(name: str) -> Any:
    if name != "PrincipalService":
        raise AttributeError(name)
    from hermit.kernel.authority.identity.service import PrincipalService

    return PrincipalService
