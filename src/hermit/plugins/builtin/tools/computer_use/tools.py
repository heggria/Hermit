from __future__ import annotations

from hermit.plugins.builtin.tools.computer_use import actions
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.registry.tools import ToolSpec


def register(ctx: PluginContext) -> None:
    for tool in _all_tools():
        ctx.add_tool(tool)


def _all_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="computer_screenshot",
            description=(
                "Inspect the current macOS screen when visible UI state matters and "
                "no native tool or API can provide the needed context."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=actions.screenshot,
            readonly=True,
            action_class="read_local",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        ),
        ToolSpec(
            name="computer_click",
            description=(
                "Last-resort desktop automation: click at screen coordinates. "
                "Prefer native app/API tools when available."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Screen x coordinate."},
                    "y": {"type": "integer", "description": "Screen y coordinate."},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button. Default: left.",
                    },
                    "double": {
                        "type": "boolean",
                        "description": "Whether to double click. Default: false.",
                    },
                },
                "required": ["x", "y"],
            },
            handler=actions.click,
            readonly=False,
            action_class="execute_command",
            risk_hint="critical",
            requires_receipt=True,
        ),
        ToolSpec(
            name="computer_type",
            description=(
                "Last-resort desktop automation: type text into the focused macOS "
                "application. Prefer native app/API tools when available."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type."},
                },
                "required": ["text"],
            },
            handler=actions.type_text,
            readonly=False,
            action_class="execute_command",
            risk_hint="critical",
            requires_receipt=True,
        ),
        ToolSpec(
            name="computer_key",
            description=(
                "Last-resort desktop automation: press a keyboard shortcut or special "
                "key, for example 'cmd+c', 'return', 'escape', or 'cmd+space'. "
                "Prefer native app/API tools when available."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key or shortcut to press."},
                },
                "required": ["key"],
            },
            handler=actions.press_key,
            readonly=False,
            action_class="execute_command",
            risk_hint="critical",
            requires_receipt=True,
        ),
        ToolSpec(
            name="computer_move",
            description=(
                "Last-resort desktop automation: move the mouse cursor to screen "
                "coordinates. Prefer native app/API tools when available."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Screen x coordinate."},
                    "y": {"type": "integer", "description": "Screen y coordinate."},
                },
                "required": ["x", "y"],
            },
            handler=actions.move,
            readonly=False,
            action_class="execute_command",
            risk_hint="critical",
            requires_receipt=True,
        ),
        ToolSpec(
            name="computer_scroll",
            description=(
                "Last-resort desktop automation: scroll at screen coordinates in a "
                "given direction. Prefer native app/API tools when available."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Screen x coordinate."},
                    "y": {"type": "integer", "description": "Screen y coordinate."},
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": "Scroll direction.",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Scroll amount. Default: 3.",
                    },
                },
                "required": ["x", "y", "direction"],
            },
            handler=actions.scroll,
            readonly=False,
            action_class="execute_command",
            risk_hint="critical",
            requires_receipt=True,
        ),
        ToolSpec(
            name="computer_get_screen_size",
            description="Get the main macOS screen size in pixels.",
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=actions.get_screen_size,
            readonly=True,
            action_class="read_local",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        ),
        ToolSpec(
            name="computer_open_app",
            description=(
                "Last-resort desktop automation: open a macOS application by name "
                "using `open -a`. Prefer native app/API tools when available."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Application name, for example 'Safari' or 'Notes'.",
                    },
                },
                "required": ["app_name"],
            },
            handler=actions.open_app,
            readonly=False,
            action_class="execute_command",
            risk_hint="critical",
            requires_receipt=True,
        ),
    ]
