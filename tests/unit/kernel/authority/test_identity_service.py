"""Tests for hermit.kernel.authority.identity.service — PrincipalService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermit.kernel.authority.identity.models import PrincipalRecord
from hermit.kernel.authority.identity.service import PrincipalService


def _make_principal(**overrides) -> PrincipalRecord:
    defaults = {
        "principal_id": "p-1",
        "principal_type": "system",
        "display_name": "kernel",
        "source_channel": None,
        "external_ref": None,
    }
    defaults.update(overrides)
    return PrincipalRecord(**defaults)


def _make_service() -> tuple[PrincipalService, MagicMock]:
    store = MagicMock()
    store.ensure_principal.return_value = _make_principal()
    return PrincipalService(store), store


class TestPrincipalServiceResolve:
    def test_resolve_delegates_to_store(self) -> None:
        svc, store = _make_service()
        svc.resolve(
            principal_type="user",
            display_name="alice",
            source_channel="cli",
            external_ref="alice-ref",
        )
        store.ensure_principal.assert_called_once_with(
            principal_type="user",
            display_name="alice",
            source_channel="cli",
            external_ref="alice-ref",
        )

    def test_resolve_returns_principal_record(self) -> None:
        svc, store = _make_service()
        expected = _make_principal(principal_id="p-99")
        store.ensure_principal.return_value = expected
        result = svc.resolve(principal_type="user", display_name="bob")
        assert result is expected

    def test_resolve_passes_none_defaults(self) -> None:
        svc, store = _make_service()
        svc.resolve(principal_type="system", display_name="kernel")
        store.ensure_principal.assert_called_once_with(
            principal_type="system",
            display_name="kernel",
            source_channel=None,
            external_ref=None,
        )


class TestPrincipalServiceResolveName:
    def test_none_actor_resolves_to_kernel(self) -> None:
        svc, store = _make_service()
        svc.resolve_name(None)
        store.ensure_principal.assert_called_once()
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["display_name"] == "kernel"
        assert call_kwargs["principal_type"] == "system"

    def test_empty_string_actor_resolves_to_kernel(self) -> None:
        svc, store = _make_service()
        svc.resolve_name("")
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["display_name"] == "kernel"
        assert call_kwargs["principal_type"] == "system"

    def test_whitespace_only_actor_resolves_to_kernel(self) -> None:
        svc, store = _make_service()
        svc.resolve_name("   ")
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["display_name"] == "kernel"
        assert call_kwargs["principal_type"] == "system"

    def test_kernel_actor_is_system_type(self) -> None:
        svc, store = _make_service()
        svc.resolve_name("kernel")
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["principal_type"] == "system"

    def test_regular_user_is_user_type(self) -> None:
        svc, store = _make_service()
        svc.resolve_name("alice")
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["principal_type"] == "user"
        assert call_kwargs["display_name"] == "alice"

    @pytest.mark.parametrize(
        "channel",
        ["scheduler", "webhook", "feishu", "cli", "chat"],
    )
    def test_special_channel_overrides_type(self, channel: str) -> None:
        svc, store = _make_service()
        svc.resolve_name("some-actor", source_channel=channel)
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["principal_type"] == channel

    def test_kernel_actor_with_special_channel_stays_system(self) -> None:
        svc, store = _make_service()
        svc.resolve_name("kernel", source_channel="scheduler")
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["principal_type"] == "system"

    def test_user_actor_with_special_channel_stays_user(self) -> None:
        svc, store = _make_service()
        svc.resolve_name("user", source_channel="feishu")
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["principal_type"] == "user"

    def test_unknown_channel_defaults_to_user(self) -> None:
        svc, store = _make_service()
        svc.resolve_name("bob", source_channel="unknown")
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["principal_type"] == "user"

    def test_resolve_name_passes_source_channel_and_external_ref(self) -> None:
        svc, store = _make_service()
        svc.resolve_name("alice", source_channel="cli")
        call_kwargs = store.ensure_principal.call_args.kwargs
        assert call_kwargs["source_channel"] == "cli"
        assert call_kwargs["external_ref"] == "alice"


class TestIdentityInitGetattr:
    def test_getattr_principal_service(self) -> None:
        from hermit.kernel.authority.identity import PrincipalService as PS

        assert PS is PrincipalService

    def test_getattr_unknown_raises(self) -> None:
        import hermit.kernel.authority.identity as mod

        with pytest.raises(AttributeError):
            _ = mod.NoSuchThing  # type: ignore[attr-defined]
