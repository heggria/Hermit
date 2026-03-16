"""Compatibility /plan command routed to kernel-native planning."""

from __future__ import annotations

from typing import Any

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.capability.contracts.base import CommandSpec


def _locale_for_runner(runner: Any = None) -> str:
    settings = getattr(getattr(runner, "pm", None), "settings", None)
    return resolve_locale(getattr(settings, "locale", None))


def _t(  # pyright: ignore[reportUnusedFunction]
    message_key: str,
    *,
    runner: Any = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    return tr(message_key, locale=_locale_for_runner(runner), default=default, **kwargs)


def _cmd_plan(runner: Any, session_id: str, text: str) -> Any:
    parts = text.strip().split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    if subcommand == "confirm":
        return runner.dispatch_control_action(session_id, action="plan_confirm", target_id="")
    if subcommand == "off":
        return runner.dispatch_control_action(session_id, action="plan_exit", target_id="")
    return runner.dispatch_control_action(session_id, action="plan_enter", target_id="")


def register(ctx: Any) -> None:
    ctx.add_command(
        CommandSpec(
            name="/plan",
            help_text="kernel.planner.command.help",
            handler=_cmd_plan,
        )
    )


__all__ = ["_cmd_plan", "register"]
