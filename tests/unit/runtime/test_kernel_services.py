"""Tests for src/hermit/runtime/capability/contracts/kernel_services.py"""

from __future__ import annotations

import pytest

from hermit.runtime.capability.contracts.kernel_services import (
    KernelServiceProvider,
    KernelServiceRegistry,
)

# ---------------------------------------------------------------------------
# KernelServiceProvider Protocol
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Satisfies the KernelServiceProvider protocol."""

    def get_store(self):
        return "store"

    def get_artifact_store(self):
        return "artifact_store"

    def get_task_controller(self):
        return "task_controller"


class _PartialProvider:
    """Missing methods — does NOT satisfy the protocol."""

    def get_store(self):
        return "store"


class TestKernelServiceProviderProtocol:
    def test_valid_provider_is_instance(self) -> None:
        assert isinstance(_FakeProvider(), KernelServiceProvider)

    def test_invalid_provider_is_not_instance(self) -> None:
        assert not isinstance(_PartialProvider(), KernelServiceProvider)

    def test_non_object_is_not_instance(self) -> None:
        assert not isinstance("string", KernelServiceProvider)


# ---------------------------------------------------------------------------
# KernelServiceRegistry
# ---------------------------------------------------------------------------


class TestKernelServiceRegistryRegister:
    def test_register_and_get(self) -> None:
        reg = KernelServiceRegistry()
        reg.register("store", "my_store")
        assert reg.get("store") == "my_store"

    def test_register_empty_name_raises(self) -> None:
        reg = KernelServiceRegistry()
        with pytest.raises(ValueError, match="non-empty"):
            reg.register("", "value")

    def test_register_duplicate_raises(self) -> None:
        reg = KernelServiceRegistry()
        reg.register("store", "v1")
        with pytest.raises(ValueError, match="already registered"):
            reg.register("store", "v2")

    def test_register_multiple_services(self) -> None:
        reg = KernelServiceRegistry()
        reg.register("a", 1)
        reg.register("b", 2)
        assert reg.get("a") == 1
        assert reg.get("b") == 2


class TestKernelServiceRegistryReplace:
    def test_replace_existing(self) -> None:
        reg = KernelServiceRegistry()
        reg.register("store", "v1")
        reg.replace("store", "v2")
        assert reg.get("store") == "v2"

    def test_replace_nonexistent_creates(self) -> None:
        reg = KernelServiceRegistry()
        reg.replace("new", "value")
        assert reg.get("new") == "value"

    def test_replace_empty_name_raises(self) -> None:
        reg = KernelServiceRegistry()
        with pytest.raises(ValueError, match="non-empty"):
            reg.replace("", "value")


class TestKernelServiceRegistryGet:
    def test_get_missing_raises_with_available_names(self) -> None:
        reg = KernelServiceRegistry()
        reg.register("alpha", 1)
        reg.register("beta", 2)
        with pytest.raises(KeyError, match="alpha") as exc_info:
            reg.get("missing")
        # Available services should be listed
        assert "beta" in str(exc_info.value)

    def test_get_missing_empty_registry(self) -> None:
        reg = KernelServiceRegistry()
        with pytest.raises(KeyError, match="\\(none\\)"):
            reg.get("missing")


class TestKernelServiceRegistryHas:
    def test_has_existing(self) -> None:
        reg = KernelServiceRegistry()
        reg.register("store", "v")
        assert reg.has("store") is True

    def test_has_missing(self) -> None:
        reg = KernelServiceRegistry()
        assert reg.has("store") is False


class TestKernelServiceRegistryRegisteredNames:
    def test_empty_registry(self) -> None:
        reg = KernelServiceRegistry()
        assert reg.registered_names == frozenset()

    def test_populated_registry(self) -> None:
        reg = KernelServiceRegistry()
        reg.register("a", 1)
        reg.register("b", 2)
        assert reg.registered_names == frozenset({"a", "b"})

    def test_returns_frozen_snapshot(self) -> None:
        reg = KernelServiceRegistry()
        reg.register("a", 1)
        names = reg.registered_names
        reg.register("b", 2)
        # Snapshot should not reflect later additions
        assert "b" not in names
