"""Decompose plugin hooks — placeholder for metaloop activation."""

from __future__ import annotations

from hermit.runtime.capability.contracts.base import PluginContext


def register(ctx: PluginContext) -> None:
    """Register decompose hooks.

    Currently a placeholder — the decompose plugin is activated via
    governed tools rather than lifecycle hooks.  Future metaloop
    integration will register PRE_RUN hooks here.
    """
