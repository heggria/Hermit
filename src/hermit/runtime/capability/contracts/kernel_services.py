"""Kernel-plugin decoupling layer.

Provides a Protocol ABC and a concrete registry so that plugins can access
kernel services (store, artifact store, task controller, etc.) without
importing kernel internals directly.  The kernel registers its service
instances at startup; plugins retrieve them by name through the registry.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import structlog

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Protocol – what a kernel service provider must look like
# ---------------------------------------------------------------------------


@runtime_checkable
class KernelServiceProvider(Protocol):
    """Protocol that any kernel-side service provider must satisfy.

    Implementations live in the kernel layer; the runtime and plugin layers
    depend only on this protocol, never on concrete kernel types.
    """

    def get_store(self) -> Any:
        """Return the primary ledger / journal store."""
        ...

    def get_artifact_store(self) -> Any:
        """Return the artifact store (lineage, claims, evidence)."""
        ...

    def get_task_controller(self) -> Any:
        """Return the task controller for task lifecycle management."""
        ...


# ---------------------------------------------------------------------------
# Concrete registry
# ---------------------------------------------------------------------------


class KernelServiceRegistry:
    """Concrete registry that holds kernel service references for plugin access.

    The kernel populates this registry during startup.  Plugins look up
    services by well-known name strings, keeping the dependency arrow from
    plugins → registry (runtime layer) rather than plugins → kernel.

    The registry is intentionally **not** a singleton; the owning runtime
    component controls its lifetime and passes it where needed.
    """

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    # -- mutators (kernel-side) ---------------------------------------------

    def register(self, name: str, service: Any) -> None:
        """Register a kernel service under *name*.

        Raises ``ValueError`` if *name* is already registered.  Use
        :meth:`replace` for intentional overwrites (e.g. during testing).
        """
        if not name:
            raise ValueError("Service name must be a non-empty string.")
        if name in self._services:
            raise ValueError(
                f"Service '{name}' is already registered. Use replace() for intentional overwrites."
            )
        self._services[name] = service
        log.debug("kernel service registered: %s", name)

    def replace(self, name: str, service: Any) -> None:
        """Replace an existing service registration (useful in tests)."""
        if not name:
            raise ValueError("Service name must be a non-empty string.")
        self._services[name] = service
        log.debug("kernel service replaced: %s", name)

    # -- accessors (plugin-side) --------------------------------------------

    def get(self, name: str) -> Any:
        """Return the service registered under *name*.

        Raises ``KeyError`` when the service has not been registered.
        """
        try:
            return self._services[name]
        except KeyError:
            raise KeyError(
                f"Kernel service '{name}' is not registered. Available services: "
                + (", ".join(sorted(self._services)) or "(none)")
            ) from None

    def has(self, name: str) -> bool:
        """Return ``True`` if a service is registered under *name``."""
        return name in self._services

    @property
    def registered_names(self) -> frozenset[str]:
        """Snapshot of all currently registered service names."""
        return frozenset(self._services)
