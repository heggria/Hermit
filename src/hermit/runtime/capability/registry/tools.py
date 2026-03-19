from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from hermit.infra.storage import atomic_write
from hermit.infra.system.i18n import localize_schema, resolve_locale, tr
from hermit.runtime.provider_host.execution.sandbox import CommandSandbox

ToolHandler = Callable[[dict[str, Any]], Any]
_RISK_HINTS = {"low", "medium", "high", "critical"}


class ToolGovernanceError(ValueError):
    """Raised when a tool spec is missing required governance metadata."""


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    description_key: str | None = None
    readonly: bool = False  # True = no side effects; safe to call in plan mode
    action_class: str | None = None
    resource_scope_hint: str | list[str] | None = None
    idempotent: bool = False
    risk_hint: str | None = None
    requires_receipt: bool | None = None
    supports_preview: bool = False
    result_is_internal_context: bool = False

    def __post_init__(self) -> None:
        self._validate_governance()

    def _validate_governance(self) -> None:
        action_class = str(self.action_class or "").strip()
        if not action_class:
            raise ToolGovernanceError(f"Tool '{self.name}' must declare action_class explicitly.")
        if self.risk_hint is not None and self.risk_hint not in _RISK_HINTS:
            raise ToolGovernanceError(
                f"Tool '{self.name}' has unsupported risk_hint '{self.risk_hint}'."
            )
        if self.readonly:
            if self.requires_receipt is not False:
                raise ToolGovernanceError(
                    f"Readonly tool '{self.name}' must declare requires_receipt=False."
                )
            return
        if self.risk_hint is None:
            raise ToolGovernanceError(
                f"Mutating tool '{self.name}' must declare risk_hint explicitly."
            )
        if self.requires_receipt is None:
            raise ToolGovernanceError(
                f"Mutating tool '{self.name}' must declare requires_receipt explicitly."
            )


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

    def list_tools(self, readonly_only: bool = False) -> list[ToolSpec]:
        tools = self._tools.values()
        if readonly_only:
            tools = (t for t in tools if t.readonly)  # type: ignore[assignment]
        return list(tools)


def localize_tool_spec(tool: ToolSpec, *, locale: str | None = None) -> ToolSpec:
    resolved_locale = resolve_locale(locale)
    return replace(
        tool,
        description=tr(
            tool.description_key or "",
            locale=resolved_locale,
            default=tool.description,
        )
        if tool.description_key
        else tool.description,
        input_schema=localize_schema(tool.input_schema, locale=resolved_locale),
        description_key=None,
    )


def _safe_path(root_dir: Path, relative_path: str) -> Path:
    path = (root_dir / relative_path).resolve()
    if root_dir.resolve() not in path.parents and path != root_dir.resolve():
        raise ValueError(f"Path escapes workspace: {relative_path}")
    return path


def _write_path(root_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root_dir / candidate
    return candidate.resolve()


def create_builtin_tool_registry(
    root_dir: Path,
    sandbox: CommandSandbox,
    config_root_dir: Path | None = None,
    locale: str | None = None,
) -> ToolRegistry:
    root_dir = root_dir.resolve()
    resolved_locale = resolve_locale(locale)
    registry = ToolRegistry()

    def read_file(payload: dict[str, Any]) -> str:
        path = _safe_path(root_dir, str(payload["path"]))
        return path.read_text(encoding="utf-8")

    def write_file(payload: dict[str, Any]) -> str:
        path = _write_path(root_dir, str(payload["path"]))
        atomic_write(path, str(payload["content"]))
        return "ok"

    def bash(payload: dict[str, Any]) -> dict[str, Any]:
        result = sandbox.run(payload)
        if isinstance(result, dict):
            return result
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }

    setattr(bash, "_sandbox", sandbox)

    registry.register(
        localize_tool_spec(
            ToolSpec(
                name="read_file",
                description="Read a UTF-8 text file inside the workspace.",
                description_key="tools.core.read_file.description",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description_key": "tools.core.read_file.path",
                        }
                    },
                    "required": ["path"],
                },
                handler=read_file,
                readonly=True,
                action_class="read_local",
                resource_scope_hint=str(root_dir),
                idempotent=True,
                risk_hint="low",
                requires_receipt=False,
            ),
            locale=resolved_locale,
        )
    )
    registry.register(
        localize_tool_spec(
            ToolSpec(
                name="write_file",
                description="Write a UTF-8 text file inside the workspace.",
                description_key="tools.core.write_file.description",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description_key": "tools.core.write_file.path"},
                        "content": {
                            "type": "string",
                            "description_key": "tools.core.write_file.content",
                        },
                    },
                    "required": ["path", "content"],
                },
                handler=write_file,
                action_class="write_local",
                resource_scope_hint=str(root_dir),
                risk_hint="high",
                requires_receipt=True,
                supports_preview=True,
            ),
            locale=resolved_locale,
        )
    )
    registry.register(
        localize_tool_spec(
            ToolSpec(
                name="bash",
                description="Run a shell command inside the workspace sandbox.",
                description_key="tools.core.bash.description",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description_key": "tools.core.bash.command",
                        },
                        "display_name": {"type": "string"},
                        "ready_patterns": {"type": "array", "items": {"type": "object"}},
                        "failure_patterns": {"type": "array", "items": {"type": "object"}},
                        "progress_patterns": {"type": "array", "items": {"type": "object"}},
                        "ready_return": {"type": "boolean"},
                    },
                    "required": ["command"],
                },
                handler=bash,
                action_class="execute_command",
                resource_scope_hint=str(root_dir),
                risk_hint="critical",
                requires_receipt=True,
                supports_preview=True,
            ),
            locale=resolved_locale,
        )
    )

    if config_root_dir is not None:
        config_root_dir = config_root_dir.resolve()

        def read_hermit_file(payload: dict[str, Any]) -> str:
            path = _safe_path(config_root_dir, str(payload["path"]))
            if not path.exists():
                return tr(
                    "tools.core.read_hermit_file.not_found",
                    locale=resolved_locale,
                    default=f"File not found: {path.relative_to(config_root_dir)}",
                    path=path.relative_to(config_root_dir),
                )
            if path.is_dir():
                return tr(
                    "tools.core.read_hermit_file.is_directory",
                    locale=resolved_locale,
                    default=f"Path is a directory: {path.relative_to(config_root_dir)}",
                    path=path.relative_to(config_root_dir),
                )
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
            entries: list[str] = []
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                suffix = "/" if child.is_dir() else ""
                entries.append(f"{child.relative_to(config_root_dir)}{suffix}")
            return entries

        registry.register(
            localize_tool_spec(
                ToolSpec(
                    name="read_hermit_file",
                    description="Read a UTF-8 text file inside the Hermit config directory.",
                    description_key="tools.core.read_hermit_file.description",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description_key": "tools.core.read_hermit_file.path",
                            }
                        },
                        "required": ["path"],
                    },
                    handler=read_hermit_file,
                    readonly=True,
                    action_class="read_local",
                    resource_scope_hint=str(config_root_dir),
                    idempotent=True,
                    risk_hint="low",
                    requires_receipt=False,
                ),
                locale=resolved_locale,
            )
        )
        registry.register(
            localize_tool_spec(
                ToolSpec(
                    name="write_hermit_file",
                    description="Write a UTF-8 text file inside the Hermit config directory.",
                    description_key="tools.core.write_hermit_file.description",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description_key": "tools.core.write_hermit_file.path",
                            },
                            "content": {
                                "type": "string",
                                "description_key": "tools.core.write_hermit_file.content",
                            },
                        },
                        "required": ["path", "content"],
                    },
                    handler=write_hermit_file,
                    action_class="write_local",
                    resource_scope_hint=str(config_root_dir),
                    risk_hint="high",
                    requires_receipt=True,
                    supports_preview=True,
                ),
                locale=resolved_locale,
            )
        )
        registry.register(
            localize_tool_spec(
                ToolSpec(
                    name="list_hermit_files",
                    description="List files or directories inside the Hermit config directory.",
                    description_key="tools.core.list_hermit_files.description",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description_key": "tools.core.list_hermit_files.path",
                            }
                        },
                        "required": [],
                    },
                    handler=list_hermit_files,
                    readonly=True,
                    action_class="read_local",
                    resource_scope_hint=str(config_root_dir),
                    idempotent=True,
                    risk_hint="low",
                    requires_receipt=False,
                ),
                locale=resolved_locale,
            )
        )

    def iteration_summary(payload: dict[str, Any]) -> str:
        task_id = payload.get("task_id", "")
        status = payload.get("status", "unknown")
        changed_files = payload.get("changed_files", [])
        acceptance_results = payload.get("acceptance_results", [])
        summary = {
            "task_id": task_id,
            "status": status,
            "changed_files": changed_files,
            "acceptance_results": acceptance_results,
        }
        return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)

    registry.register(
        localize_tool_spec(
            ToolSpec(
                name="iteration_summary",
                description="Output a structured JSON summary of an iteration result, including task_id, status, changed_files, and acceptance_results. Intended for use in hermit-iterate Phase 4 (PR close loop) to produce a machine-readable summary embeddable in a PR body.",
                description_key="tools.core.iteration_summary.description",
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description_key": "tools.core.iteration_summary.task_id",
                        },
                        "status": {
                            "type": "string",
                            "description_key": "tools.core.iteration_summary.status",
                        },
                        "changed_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description_key": "tools.core.iteration_summary.changed_files",
                        },
                        "acceptance_results": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description_key": "tools.core.iteration_summary.acceptance_results",
                        },
                    },
                    "required": ["task_id", "status"],
                },
                handler=iteration_summary,
                readonly=True,
                action_class="read_local",
                idempotent=True,
                risk_hint="low",
                requires_receipt=False,
            ),
            locale=resolved_locale,
        )
    )
    return registry


def serialize_tool_result(value: Any) -> str | dict[str, Any] | list[Any]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    if isinstance(value, list):
        return cast(list[Any], value)
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
