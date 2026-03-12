from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Any, Callable

Formatter = Callable[[dict[str, Any]], dict[str, str] | str | None]


@dataclass
class ApprovalCopy:
    title: str
    summary: str
    detail: str


class ApprovalCopyService:
    """Render user-facing approval copy from structured action facts.

    The optional formatter hook allows future LLM-based copy generation while
    preserving deterministic template fallback today.
    """

    def __init__(self, formatter: Formatter | None = None, *, formatter_timeout_ms: int = 500) -> None:
        self._formatter = formatter
        self._formatter_timeout_ms = formatter_timeout_ms

    def build_canonical_copy(self, requested_action: dict[str, Any], approval_id: str | None = None) -> dict[str, str]:
        copy = self.describe(requested_action, approval_id=approval_id)
        return {
            "title": copy.title,
            "summary": copy.summary,
            "detail": copy.detail,
        }

    def describe(self, requested_action: dict[str, Any], approval_id: str | None = None) -> ApprovalCopy:
        display_copy = dict(requested_action.get("display_copy", {}) or {})
        if display_copy:
            resolved = self._copy_from_mapping(display_copy)
            if resolved is not None:
                return resolved
        facts = self._facts(requested_action, approval_id=approval_id)
        formatted = self._format_with_optional_formatter(facts)
        if formatted is not None:
            return formatted
        return self._template_copy(facts)

    def resolve_copy(self, requested_action: dict[str, Any], approval_id: str | None = None) -> ApprovalCopy:
        return self.describe(requested_action, approval_id=approval_id)

    def blocked_message(self, requested_action: dict[str, Any], approval_id: str) -> str:
        copy = self.describe(requested_action, approval_id=approval_id)
        detail = copy.detail.strip()
        detail_block = f"\n{detail}" if detail and detail != copy.summary else ""
        return f"{copy.summary}{detail_block}\n\n审批编号：`{approval_id}`。确认后将从当前步骤继续执行。"

    def model_prompt(self, requested_action: dict[str, Any], approval_id: str) -> str:
        copy = self.describe(requested_action, approval_id=approval_id)
        return (
            f"{copy.summary}（审批编号：{approval_id}）。"
            f"请使用 `/task approve {approval_id}`，或直接回复“批准 {approval_id}”继续执行。"
        )

    def _format_with_optional_formatter(self, facts: dict[str, Any]) -> ApprovalCopy | None:
        if self._formatter is None:
            return None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(self._formatter, facts)
            payload = future.result(timeout=max(0.05, self._formatter_timeout_ms / 1000))
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            return None
        executor.shutdown(wait=False, cancel_futures=True)
        if isinstance(payload, dict):
            return self._copy_from_mapping(payload)
        if isinstance(payload, str):
            text = payload.strip()
            if text:
                return ApprovalCopy(title=facts["title"], summary=text, detail=text)
        return None

    def _copy_from_mapping(self, payload: dict[str, Any]) -> ApprovalCopy | None:
        title = str(payload.get("title", "")).strip()
        summary = str(payload.get("summary", "")).strip()
        detail = str(payload.get("detail", "")).strip()
        if title and summary:
            return ApprovalCopy(title=title, summary=summary, detail=detail or summary)
        return None

    def _facts(self, requested_action: dict[str, Any], *, approval_id: str | None) -> dict[str, Any]:
        packet = dict(requested_action.get("approval_packet", {}) or {})
        target_paths = [str(path) for path in requested_action.get("target_paths", [])]
        network_hosts = [str(host) for host in requested_action.get("network_hosts", [])]
        command_preview = str(requested_action.get("command_preview", "") or "").strip()
        tool_name = str(requested_action.get("tool_name", "") or "").strip()
        risk_level = str(requested_action.get("risk_level", "") or packet.get("risk_level", "") or "high")
        return {
            "approval_id": approval_id or "",
            "title": str(packet.get("title", "")).strip(),
            "packet_summary": str(packet.get("summary", "")).strip(),
            "tool_name": tool_name,
            "risk_level": risk_level,
            "target_paths": target_paths,
            "network_hosts": network_hosts,
            "command_preview": command_preview,
            "resource_scopes": [str(scope) for scope in requested_action.get("resource_scopes", [])],
        }

    def _template_copy(self, facts: dict[str, Any]) -> ApprovalCopy:
        command = facts["command_preview"]
        paths = facts["target_paths"]
        hosts = facts["network_hosts"]
        packet_title = facts["title"]
        risk = facts["risk_level"]

        if command:
            if "git push" in command.lower():
                summary = "准备把当前仓库的本地提交推送到远端仓库。"
                detail = "这会更新远端代码状态，可能影响协作者或部署流程；原始命令可在详情中查看。"
                return ApprovalCopy(title="确认推送远端", summary=summary, detail=detail)
            if any(token in command.lower() for token in ("rm ", "trash ", "del ")):
                summary = "准备删除本地文件或目录。"
                detail = "这个操作可能不可恢复，建议先确认删除范围；原始命令可在详情中查看。"
                return ApprovalCopy(title="确认删除操作", summary=summary, detail=detail)
            summary = "准备执行一条会修改当前环境的命令。"
            detail = "这条命令会产生副作用，需要你确认后继续；原始命令可在详情中查看。"
            return ApprovalCopy(title="确认执行命令", summary=summary, detail=detail)

        if paths:
            if len(paths) == 1:
                path = paths[0]
                if any(token in path for token in (".env", "/.ssh/", "/.gnupg/", "/Library/")):
                    summary = f"准备修改敏感文件：`{path}`。"
                    detail = "这可能影响本地配置、凭据或系统行为，需要你确认。"
                    return ApprovalCopy(title="确认修改敏感文件", summary=summary, detail=detail)
                summary = f"准备修改 1 个文件：`{path}`。"
                detail = "变更预览已生成；确认后将继续执行。"
                return ApprovalCopy(title="确认文件修改", summary=summary, detail=detail)
            summary = f"准备修改 {len(paths)} 个文件。"
            detail = "这是一次批量本地变更，建议先确认影响范围。"
            return ApprovalCopy(title="确认批量文件修改", summary=summary, detail=detail)

        if hosts:
            if len(hosts) == 1:
                summary = f"准备调用外部服务 `{hosts[0]}` 并修改远端状态。"
            else:
                summary = f"准备调用 {len(hosts)} 个外部服务并修改远端状态。"
            detail = "这会影响外部系统，需要你明确确认。"
            return ApprovalCopy(title="确认外部系统变更", summary=summary, detail=detail)

        if packet_title:
            summary = facts["packet_summary"] or "准备执行一个需要确认的操作。"
            detail = f"风险等级：{risk}。请确认后继续执行。"
            return ApprovalCopy(title=packet_title, summary=summary, detail=detail)

        summary = f"准备执行操作 `{facts['tool_name'] or 'unknown'}`。"
        detail = f"该操作风险等级为 {risk}，需要你确认后继续。"
        return ApprovalCopy(title="确认继续执行", summary=summary, detail=detail)
