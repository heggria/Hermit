from __future__ import annotations

from hermit.builtin.computer_use import actions
from hermit.core.tools import ToolSpec
from hermit.plugin.base import PluginContext


def register(ctx: PluginContext) -> None:
    for tool in _all_tools():
        ctx.add_tool(tool)


def _all_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="computer_screenshot",
            description=(
                "Capture the current macOS screen and return a PNG screenshot as "
                "Anthropic vision-compatible base64 image content."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=actions.screenshot,
            readonly=False,
        ),
        ToolSpec(
            name="computer_click",
            description=(
                "Click at screen coordinates. Supports left/right/middle click and double click."
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
        ),
        ToolSpec(
            name="computer_type",
            description="Type text into the focused macOS application.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type."},
                },
                "required": ["text"],
            },
            handler=actions.type_text,
            readonly=False,
        ),
        ToolSpec(
            name="computer_key",
            description=(
                "Press a keyboard shortcut or special key, for example 'cmd+c', "
                "'return', 'escape', or 'cmd+space'."
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
        ),
        ToolSpec(
            name="computer_move",
            description="Move the mouse cursor to screen coordinates.",
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
        ),
        ToolSpec(
            name="computer_scroll",
            description="Scroll at screen coordinates in a given direction.",
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
        ),
        ToolSpec(
            name="computer_get_screen_size",
            description="Get the main macOS screen size in pixels.",
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=actions.get_screen_size,
            readonly=False,
        ),
        ToolSpec(
            name="computer_open_app",
            description="Open a macOS application by name using `open -a`.",
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
        ),
    ]
