"""Usage command plugin: show token consumption for the current session."""
from __future__ import annotations

from typing import Any

from hermit.i18n import resolve_locale, tr
from hermit.plugin.base import CommandSpec


def _locale_for_runner(runner: Any = None) -> str:
    settings = getattr(getattr(runner, "pm", None), "settings", None)
    return resolve_locale(getattr(settings, "locale", None))


def _t(
    message_key: str,
    *,
    runner: Any = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    return tr(message_key, locale=_locale_for_runner(runner), default=default, **kwargs)


def _cmd_usage(runner: Any, session_id: str, _text: str) -> Any:
    from hermit.core.runner import DispatchResult

    session = runner.session_manager.get_or_create(session_id)
    user_turns = sum(1 for m in session.messages if m.get("role") == "user")
    lines = [
        _t("kernel.usage.title", runner=runner),
        _t("kernel.usage.input", runner=runner, tokens=session.total_input_tokens),
        _t("kernel.usage.output", runner=runner, tokens=session.total_output_tokens),
        _t("kernel.usage.cache_read", runner=runner, tokens=session.total_cache_read_tokens),
        _t("kernel.usage.cache_write", runner=runner, tokens=session.total_cache_creation_tokens),
        _t("kernel.usage.message_turns", runner=runner, turns=user_turns),
    ]
    return DispatchResult("\n".join(lines), is_command=True)


def register(ctx: Any) -> None:
    ctx.add_command(CommandSpec(
        name="/usage",
        help_text="kernel.usage.command.help",
        handler=_cmd_usage,
    ))
