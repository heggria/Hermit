from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermit.kernel.policy.evaluators.engine import PolicyEngine
    from hermit.kernel.policy.guards.fingerprint import build_action_fingerprint
    from hermit.kernel.policy.guards.rules import POLICY_RULES_VERSION
    from hermit.kernel.policy.models.models import (
        ActionRequest,
        PolicyDecision,
        PolicyObligations,
        PolicyReason,
    )

__all__ = [
    "POLICY_RULES_VERSION",
    "ActionRequest",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyObligations",
    "PolicyReason",
    "build_action_fingerprint",
]

_EXPORTS = {
    "ActionRequest": ("hermit.kernel.policy.models.models", "ActionRequest"),
    "POLICY_RULES_VERSION": ("hermit.kernel.policy.guards.rules", "POLICY_RULES_VERSION"),
    "PolicyDecision": ("hermit.kernel.policy.models.models", "PolicyDecision"),
    "PolicyEngine": ("hermit.kernel.policy.evaluators.engine", "PolicyEngine"),
    "PolicyObligations": ("hermit.kernel.policy.models.models", "PolicyObligations"),
    "PolicyReason": ("hermit.kernel.policy.models.models", "PolicyReason"),
    "build_action_fingerprint": (
        "hermit.kernel.policy.guards.fingerprint",
        "build_action_fingerprint",
    ),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
