from __future__ import annotations

import base64
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from hermit.infra.system.i18n import tr

_TMP_PREFIX = "hermit_screenshot_"
_CLICLICK = "cliclick"

_SPECIAL_KEY_CODES = {
    "backspace": 51,
    "delete": 51,
    "down": 125,
    "end": 119,
    "enter": 76,
    "escape": 53,
    "esc": 53,
    "forwarddelete": 117,
    "home": 115,
    "left": 123,
    "pagedown": 121,
    "pageup": 116,
    "return": 36,
    "right": 124,
    "space": 49,
    "tab": 48,
    "up": 126,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}

_MODIFIER_FLAGS = {
    "cmd": "command down",
    "command": "command down",
    "ctrl": "control down",
    "control": "control down",
    "opt": "option down",
    "option": "option down",
    "shift": "shift down",
}

_ACCESSIBILITY_ERROR_MARKERS = (
    "1002",
    "assistive access",
    "not allowed assistive access",
    "not permitted to send keystrokes",
    "not allowed to send keystrokes",
    "not allowed to control",
    tr("tools.computer_use.error.no_key_send"),
    tr("tools.computer_use.error.no_control"),
)


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise RuntimeError(detail)
    return result


def _run_osascript(script: str, argv: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    args = ["osascript", "-e", script]
    if argv:
        args.extend(argv)
    return _run_command(args)


def _tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _require_int(payload: dict[str, Any], key: str) -> int:
    if key not in payload:
        raise RuntimeError(f"{key} is required")
    try:
        return int(payload[key])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{key} must be an integer") from exc


def _ok(info: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True}
    if info:
        result["info"] = info
    return result


def _is_accessibility_error(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker.lower() in lowered for marker in _ACCESSIBILITY_ERROR_MARKERS)


def _desktop_automation_error(
    action: str,
    detail: str,
    *,
    used_osascript: bool = False,
    suggest_cliclick: bool = False,
) -> RuntimeError:
    if _is_accessibility_error(detail):
        message = (
            f"macOS blocked desktop automation while trying to {action}. "
            "Grant Accessibility access to the app running Hermit "
            "(for example Codex, Terminal, iTerm, or Python) in "
            "System Settings > Privacy & Security > Accessibility."
        )
        if suggest_cliclick:
            message += (
                " Hermit is currently using osascript/System Events for this action. "
                "Installing cliclick makes clicks, typing, moving, and scrolling more reliable, "
                "but Accessibility permission is still required."
            )
        return RuntimeError(f"{message} Original error: {detail}")

    if used_osascript and suggest_cliclick:
        return RuntimeError(
            f"Desktop automation failed while trying to {action}. "
            "Hermit fell back to osascript/System Events because cliclick is not installed. "
            "Install cliclick for more reliable clicks, typing, moving, and scrolling. "
            f"Original error: {detail}"
        )

    return RuntimeError(detail)


def screenshot(_: dict[str, Any]) -> dict[str, Any]:
    path = Path("/tmp") / f"{_TMP_PREFIX}{time.time_ns()}.png"
    if not _tool_exists("screencapture"):
        raise RuntimeError("screencapture is not available")
    try:
        _run_command(["screencapture", "-x", "-t", "png", str(path)])
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": data,
            },
        }
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def click(payload: dict[str, Any]) -> dict[str, Any]:
    x = _require_int(payload, "x")
    y = _require_int(payload, "y")
    button = str(payload.get("button", "left")).strip().lower()
    double = bool(payload.get("double", False))
    verb = "double click" if double else "right click" if button == "right" else "click"

    if _tool_exists(_CLICLICK):
        action = "dc" if double else {"left": "c", "right": "rc", "middle": "mc"}.get(button)
        if action is None:
            raise RuntimeError(f"unsupported button: {button}")
        try:
            _run_command([_CLICLICK, f"{action}:{x},{y}"])
        except RuntimeError as exc:
            raise _desktop_automation_error(f"{verb} at {x},{y}", str(exc)) from exc
        return _ok(f"clicked at {x},{y}")

    if button not in {"left", "right"}:
        raise RuntimeError("middle click requires cliclick")

    script = f'tell application "System Events" to {verb} at {{{x}, {y}}}'
    try:
        _run_osascript(script)
    except RuntimeError as exc:
        raise _desktop_automation_error(
            f"{verb} at {x},{y}",
            str(exc),
            used_osascript=True,
            suggest_cliclick=True,
        ) from exc
    return _ok(f"{verb} at {x},{y}")


def type_text(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text", ""))
    if not text:
        raise RuntimeError("text is required")

    if _tool_exists(_CLICLICK):
        try:
            _run_command([_CLICLICK, f"t:{text}"])
        except RuntimeError as exc:
            raise _desktop_automation_error("type text", str(exc)) from exc
        return _ok("text typed")

    script = 'on run argv\ntell application "System Events" to keystroke item 1 of argv\nend run'
    try:
        _run_osascript(script, [text])
    except RuntimeError as exc:
        raise _desktop_automation_error(
            "type text",
            str(exc),
            used_osascript=True,
            suggest_cliclick=True,
        ) from exc
    return _ok("text typed")


def press_key(payload: dict[str, Any]) -> dict[str, Any]:
    raw_key = str(payload.get("key", "")).strip()
    if not raw_key:
        raise RuntimeError("key is required")

    parts = [part.strip().lower() for part in raw_key.split("+") if part.strip()]
    if not parts:
        raise RuntimeError("key is required")

    modifiers = [_MODIFIER_FLAGS[part] for part in parts[:-1] if part in _MODIFIER_FLAGS]
    unknown_modifiers = [part for part in parts[:-1] if part not in _MODIFIER_FLAGS]
    if unknown_modifiers:
        raise RuntimeError(f"unsupported modifier(s): {', '.join(unknown_modifiers)}")

    key = parts[-1]
    key_code = _SPECIAL_KEY_CODES.get(key)
    using_clause = f" using {{{', '.join(modifiers)}}}" if modifiers else ""

    if key_code is not None:
        script = f'tell application "System Events" to key code {key_code}{using_clause}'
        try:
            _run_osascript(script)
        except RuntimeError as exc:
            raise _desktop_automation_error("press a key", str(exc), used_osascript=True) from exc
        return _ok(f"pressed {raw_key}")

    if len(key) == 1:
        quoted = _applescript_string(key)
        script = (
            f'tell application "System Events" to keystroke {quoted}'
            if not modifiers
            else f'tell application "System Events" to keystroke {quoted}{using_clause}'
        )
        try:
            _run_osascript(script)
        except RuntimeError as exc:
            raise _desktop_automation_error("press a key", str(exc), used_osascript=True) from exc
        return _ok(f"pressed {raw_key}")

    raise RuntimeError(f"unsupported key: {raw_key}")


def move(payload: dict[str, Any]) -> dict[str, Any]:
    x = _require_int(payload, "x")
    y = _require_int(payload, "y")

    if _tool_exists(_CLICLICK):
        try:
            _run_command([_CLICLICK, f"m:{x},{y}"])
        except RuntimeError as exc:
            raise _desktop_automation_error(f"move the mouse to {x},{y}", str(exc)) from exc
        return _ok(f"moved to {x},{y}")

    script = f'tell application "System Events" to move mouse to {{{x}, {y}}}'
    try:
        _run_osascript(script)
    except RuntimeError as exc:
        raise _desktop_automation_error(
            f"move the mouse to {x},{y}",
            str(exc),
            used_osascript=True,
            suggest_cliclick=True,
        ) from exc
    return _ok(f"moved to {x},{y}")


def scroll(payload: dict[str, Any]) -> dict[str, Any]:
    x = _require_int(payload, "x")
    y = _require_int(payload, "y")
    direction = str(payload.get("direction", "down")).strip().lower()
    try:
        amount = int(payload.get("amount", 3))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("amount must be an integer") from exc
    if amount <= 0:
        raise RuntimeError("amount must be > 0")

    if _tool_exists(_CLICLICK):
        action = {"up": "wu", "down": "wd", "left": "wl", "right": "wr"}.get(direction)
        if action is None:
            raise RuntimeError(f"unsupported direction: {direction}")
        try:
            _run_command([_CLICLICK, f"m:{x},{y}", f"{action}:{amount}"])
        except RuntimeError as exc:
            raise _desktop_automation_error(f"scroll {direction}", str(exc)) from exc
        return _ok(f"scrolled {direction} at {x},{y}")

    verb = {
        "up": "scroll up",
        "down": "scroll down",
        "left": "scroll left",
        "right": "scroll right",
    }.get(direction)
    if verb is None:
        raise RuntimeError(f"unsupported direction: {direction}")
    script = (
        f'tell application "System Events"\nclick at {{{x}, {y}}}\n{verb} by {amount}\nend tell'
    )
    try:
        _run_osascript(script)
    except RuntimeError as exc:
        raise _desktop_automation_error(
            f"scroll {direction}",
            str(exc),
            used_osascript=True,
            suggest_cliclick=True,
        ) from exc
    return _ok(f"scrolled {direction} at {x},{y}")


def get_screen_size(_: dict[str, Any]) -> dict[str, Any]:
    script = 'tell application "Finder" to get bounds of window of desktop'
    result = _run_osascript(script)
    parts = [part.strip() for part in result.stdout.strip().split(",")]
    if len(parts) != 4:
        raise RuntimeError(f"unexpected screen bounds: {result.stdout.strip()}")
    left, top, right, bottom = (int(part) for part in parts)
    return {
        "width": right - left,
        "height": bottom - top,
    }


def open_app(payload: dict[str, Any]) -> dict[str, Any]:
    app_name = str(payload.get("app_name", "")).strip()
    if not app_name:
        raise RuntimeError("app_name is required")
    if not _tool_exists("open"):
        raise RuntimeError("open is not available")
    _run_command(["open", "-a", app_name])
    return _ok(f"opened {app_name}")
