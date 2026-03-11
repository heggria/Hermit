from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from hermit.core.sandbox import CommandSandbox
from hermit.storage import atomic_write


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    readonly: bool = False  # True = no side effects; safe to call in plan mode

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def call(self, name: str, payload: dict[str, Any]) -> Any:
        return self.get(name).handler(payload)

    def anthropic_tools(self, readonly_only: bool = False) -> list[dict[str, Any]]:
        tools = self._tools.values()
        if readonly_only:
            tools = (t for t in tools if t.readonly)  # type: ignore[assignment]
        return [tool.to_anthropic_schema() for tool in tools]


def _safe_path(root_dir: Path, relative_path: str) -> Path:
    path = (root_dir / relative_path).resolve()
    if root_dir.resolve() not in path.parents and path != root_dir.resolve():
        raise ValueError(f"Path escapes workspace: {relative_path}")
    return path


def create_builtin_tool_registry(
    root_dir: Path,
    sandbox: CommandSandbox,
    config_root_dir: Optional[Path] = None,
) -> ToolRegistry:
    root_dir = root_dir.resolve()
    registry = ToolRegistry()

    def read_file(payload: dict[str, Any]) -> str:
        path = _safe_path(root_dir, str(payload["path"]))
        return path.read_text(encoding="utf-8")

    def write_file(payload: dict[str, Any]) -> str:
        path = _safe_path(root_dir, str(payload["path"]))
        atomic_write(path, str(payload["content"]))
        return "ok"

    def bash(payload: dict[str, Any]) -> dict[str, Any]:
        result = sandbox.run(str(payload["command"]))
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }

    registry.register(
        ToolSpec(
            name="read_file",
            description="Read a UTF-8 text file inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=read_file,
            readonly=True,
        )
    )
    registry.register(
        ToolSpec(
            name="write_file",
            description="Write a UTF-8 text file inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        )
    )
    registry.register(
        ToolSpec(
            name="bash",
            description="Run a shell command inside the workspace sandbox.",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            handler=bash,
        )
    )

    if config_root_dir is not None:
        config_root_dir = config_root_dir.resolve()

        def read_hermit_file(payload: dict[str, Any]) -> str:
            path = _safe_path(config_root_dir, str(payload["path"]))
            if not path.exists():
                return f"File not found: {path.relative_to(config_root_dir)}"
            if path.is_dir():
                return f"Path is a directory: {path.relative_to(config_root_dir)}"
            return path.read_text(encoding="utf-8")

        def write_hermit_file(payload: dict[str, Any]) -> str:
            path = _safe_path(config_root_dir, str(payload["path"]))
            atomic_write(path, str(payload["content"]))
            return "ok"

        def list_hermit_files(payload: dict[str, Any]) -> list[str]:
            relative_root = str(payload.get("path", "."))
            path = _safe_path(config_root_dir, relative_root)
            if not path.exists():
                return []
            if path.is_file():
                return [str(path.relative_to(config_root_dir))]
            entries = []
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                suffix = "/" if child.is_dir() else ""
                entries.append(f"{child.relative_to(config_root_dir)}{suffix}")
            return entries

        registry.register(
            ToolSpec(
                name="read_hermit_file",
                description="Read a UTF-8 text file inside the Hermit config directory.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                handler=read_hermit_file,
                readonly=True,
            )
        )
        registry.register(
            ToolSpec(
                name="write_hermit_file",
                description="Write a UTF-8 text file inside the Hermit config directory.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                handler=write_hermit_file,
            )
        )
        registry.register(
            ToolSpec(
                name="list_hermit_files",
                description="List files or directories inside the Hermit config directory.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": [],
                },
                handler=list_hermit_files,
                readonly=True,
            )
        )
    return registry


def serialize_tool_result(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return value
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
