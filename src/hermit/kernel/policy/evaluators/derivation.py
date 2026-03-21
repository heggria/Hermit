from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any, cast

from hermit.kernel.policy.models.models import ActionRequest

_SENSITIVE_PREFIXES = (
    ".env",
    ".ssh",
    ".gnupg",
    "Library/",
)
_SENSITIVE_ABS_PREFIXES = (
    "/etc",
    "/usr",
    "/Library",
    "/System",
)
_SHELL_SEGMENT_SEPARATORS = {"&&", "||", ";", "|", "&"}
_PYTHON_WRITE_RE = re.compile(
    r"""(?ix)
    (
        \.write_text\(
        |\.write_bytes\(
        |\.mkdir\(
        |\.touch\(
        |\bopen\([^)]*,\s*['"](?:w|a|x|wb|ab|xb|w\+|a\+|x\+)['"]
        |\bos\.remove\(
        |\bos\.unlink\(
        |\bshutil\.rmtree\(
        |\.unlink\(
    )
    """
)
_PATHLIB_HOME_RE = re.compile(r"""Path\.home\(\)\s*((?:/\s*['"][^'"]+['"]\s*)+)""")
_PATHLIB_LITERAL_RE = re.compile(
    r"""Path\(\s*['"]([^'"]+)['"]\s*\)\s*((?:/\s*['"][^'"]+['"]\s*)*)"""
)
_PATHLIB_SEGMENT_RE = re.compile(r"""['"]([^'"]+)['"]""")


def derive_command_observables(command: str, *, workspace_root: str = "") -> dict[str, object]:
    lowered = command.lower()
    observables: dict[str, object] = {
        "command_preview": command,
        "command_flags": {
            "writes_disk": any(
                token in command for token in (">", ">>", "tee ", "mv ", "cp ", "touch ", "mkdir ")
            )
            or bool(_PYTHON_WRITE_RE.search(command)),
            "deletes_files": "rm " in lowered
            or "trash " in lowered
            or any(
                token in lowered for token in (".unlink(", "os.remove(", "os.unlink(", "rmtree(")
            ),
            "sudo": "sudo " in lowered,
            "curl_pipe_sh": "curl" in lowered and "| sh" in lowered,
            "git_push": "git push" in lowered,
            "network_access": any(
                token in lowered for token in ("curl ", "wget ", "http://", "https://")
            ),
        },
        "network_hosts": _extract_hosts(command),
        "target_paths": _extract_command_paths(command, workspace_root=workspace_root),
    }
    if "git push" in lowered:
        observables["vcs_operation"] = "git_push"
    elif "git commit" in lowered:
        observables["vcs_operation"] = "git_commit"
    elif "git checkout" in lowered:
        observables["vcs_operation"] = "git_checkout"
    return observables


def derive_request(request: ActionRequest) -> ActionRequest:
    derived = dict(request.derived)
    _raw_tool_input: Any = request.tool_input
    tool_input: dict[str, Any] = (
        cast(dict[str, Any], _raw_tool_input) if isinstance(_raw_tool_input, dict) else {}
    )
    workspace_root = str(request.context.get("workspace_root", "") or "")
    if request.tool_name in {
        "read_file",
        "write_file",
        "write_hermit_file",
        "read_hermit_file",
        "list_hermit_files",
    }:
        target = str(tool_input.get("path", "")).strip()
        if target:
            target_path = _resolve_target(target, workspace_root)
            derived["target_paths"] = [target_path]
            derived["sensitive_paths"] = (
                [target_path] if _is_sensitive_path(target_path, workspace_root) else []
            )
            outside_workspace = bool(
                workspace_root and not _inside_workspace(target_path, workspace_root)
            )
            derived["outside_workspace"] = outside_workspace
            if outside_workspace:
                derived["outside_workspace_roots"] = [_outside_workspace_root(target_path)]
                derived["grant_candidate_prefix"] = _grant_candidate_prefix(target_path)
            kernel_paths = [
                p for p in derived.get("target_paths", []) if _is_kernel_path(p, workspace_root)
            ]
            if kernel_paths:
                derived["kernel_paths"] = kernel_paths
    if request.tool_name == "bash" or request.action_class in {"execute_command", "vcs_mutation"}:
        command = str(tool_input.get("command", "")).strip()
        if command:
            derived.update(derive_command_observables(command, workspace_root=workspace_root))
            # Workspace boundary enforcement for shell command target paths
            cmd_target_paths: list[str] = list(derived.get("target_paths", []))  # type: ignore[arg-type]
            if workspace_root and cmd_target_paths:
                outside_paths = [
                    p for p in cmd_target_paths if not _inside_workspace(p, workspace_root)
                ]
                if outside_paths:
                    derived["outside_workspace"] = True
                    derived["outside_workspace_roots"] = list(
                        dict.fromkeys(_outside_workspace_root(p) for p in outside_paths)
                    )
                    derived["sensitive_paths"] = [
                        p for p in outside_paths if _is_sensitive_path(p, workspace_root)
                    ]
    request.derived = derived
    return request


_KERNEL_SEGMENT = f"{os.sep}src{os.sep}hermit{os.sep}kernel{os.sep}"


def _is_kernel_path(path: str, workspace_root: str) -> bool:
    """Check if path falls within the kernel source tree.

    Uses workspace_root when available, and falls back to checking whether the
    resolved path contains a ``src/hermit/kernel/`` segment so that the guard
    still fires when the runtime workspace is a subdirectory of the repository.
    """
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return False
    if workspace_root:
        try:
            kernel_prefix = str(Path(workspace_root).resolve() / "src" / "hermit" / "kernel")
        except OSError:
            kernel_prefix = ""
        if kernel_prefix and resolved.startswith(kernel_prefix):
            return True
    return _KERNEL_SEGMENT in resolved


def _resolve_target(target: str, workspace_root: str) -> str:
    try:
        candidate = Path(target).expanduser()
        if candidate.is_absolute():
            return str(candidate.resolve())
        if workspace_root:
            return str((Path(workspace_root).expanduser().resolve() / candidate).resolve())
        return str(candidate.resolve())
    except OSError:
        return target


def _inside_workspace(path: str, workspace_root: str) -> bool:
    try:
        candidate = Path(path).resolve()
        root = Path(workspace_root).resolve()
    except OSError:
        return False
    return candidate == root or root in candidate.parents


def _is_sensitive_path(path: str, workspace_root: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith(_SENSITIVE_ABS_PREFIXES):
        return True
    if workspace_root and normalized.startswith(workspace_root.replace("\\", "/")):
        rel = normalized[len(workspace_root.replace("\\", "/")) :].lstrip("/")
        return any(rel == prefix or rel.startswith(prefix) for prefix in _SENSITIVE_PREFIXES)
    return any(part in normalized for part in ("/.ssh/", "/.gnupg/", "/.aws/"))


def _outside_workspace_root(path: str) -> str:
    candidate = Path(path).expanduser()
    home = Path.home().resolve()
    try:
        resolved = candidate.resolve()
    except OSError:
        return str(candidate)
    if resolved == home or home in resolved.parents:
        parts = resolved.parts
        home_parts = home.parts
        if len(parts) > len(home_parts):
            return str(Path(*parts[: len(home_parts) + 1]))
        return str(home)
    return resolved.anchor or str(resolved)


def _grant_candidate_prefix(path: str) -> str:
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    return str(resolved.parent)


def _extract_hosts(command: str) -> list[str]:
    hosts: list[str] = []
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if token.startswith(("http://", "https://")):
            host = token.split("://", 1)[1].split("/", 1)[0]
            hosts.append(host)
    return list(dict.fromkeys(hosts))


def _extract_command_paths(command: str, *, workspace_root: str) -> list[str]:
    paths: list[str] = []
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return paths

    segment: list[str] = []
    for token in tokens:
        if token in _SHELL_SEGMENT_SEPARATORS:
            paths.extend(_extract_segment_paths(segment, workspace_root=workspace_root))
            segment = []
            continue
        segment.append(token)
    paths.extend(_extract_segment_paths(segment, workspace_root=workspace_root))
    paths.extend(_extract_embedded_python_paths(command, workspace_root=workspace_root))
    return list(dict.fromkeys(path for path in paths if path))


def _extract_segment_paths(tokens: list[str], *, workspace_root: str) -> list[str]:
    if not tokens:
        return []
    paths: list[str] = []
    command_name = tokens[0].lower()
    if command_name in {"touch", "mkdir", "rm"}:
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            paths.append(_resolve_target(token, workspace_root))
    elif command_name in {"cp", "mv"}:
        candidates = [token for token in tokens[1:] if not token.startswith("-")]
        if candidates:
            paths.append(_resolve_target(candidates[-1], workspace_root))

    for index, token in enumerate(tokens[:-1]):
        if token in {">", ">>"}:
            paths.append(_resolve_target(tokens[index + 1], workspace_root))
    return paths


def _extract_embedded_python_paths(command: str, *, workspace_root: str) -> list[str]:
    paths: list[str] = []
    for raw_suffix in _PATHLIB_HOME_RE.findall(command):
        parts = _PATHLIB_SEGMENT_RE.findall(raw_suffix)
        if parts:
            paths.append(str((Path.home() / Path(*parts)).resolve()))

    for base, raw_suffix in _PATHLIB_LITERAL_RE.findall(command):
        base_path = Path(_resolve_target(base, workspace_root))
        parts = _PATHLIB_SEGMENT_RE.findall(raw_suffix)
        candidate = base_path / Path(*parts) if parts else base_path
        try:
            paths.append(str(candidate.expanduser().resolve()))
        except OSError:
            paths.append(str(candidate))
    return list(dict.fromkeys(paths))
