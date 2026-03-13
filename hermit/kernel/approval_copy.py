from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Any, Callable

from hermit.i18n import resolve_locale, tr

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

    def __init__(
        self,
        formatter: Formatter | None = None,
        *,
        formatter_timeout_ms: int = 500,
        locale: str | None = None,
    ) -> None:
        self._formatter = formatter
        self._formatter_timeout_ms = formatter_timeout_ms
        self._locale = resolve_locale(locale) if locale else None

    def _t(self, message_key: str, *, default: str | None = None, **kwargs: object) -> str:
        return tr(message_key, locale=resolve_locale(self._locale), default=default, **kwargs)

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
        return self._t(
            "kernel.approval.blocked_message",
            summary=copy.summary,
            detail_block=detail_block,
            approval_id=approval_id,
        )

    def model_prompt(self, requested_action: dict[str, Any], approval_id: str) -> str:
        copy = self.describe(requested_action, approval_id=approval_id)
        return self._t(
            "kernel.approval.model_prompt",
            summary=copy.summary,
            approval_id=approval_id,
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
            "outside_workspace": bool(requested_action.get("outside_workspace")),
        }

    def _template_copy(self, facts: dict[str, Any]) -> ApprovalCopy:
        command = facts["command_preview"]
        paths = facts["target_paths"]
        hosts = facts["network_hosts"]
        packet_title = facts["title"]
        risk = facts["risk_level"]

        if command:
            if "git push" in command.lower():
                return ApprovalCopy(
                    title=self._t("kernel.approval.template.push.title"),
                    summary=self._t("kernel.approval.template.push.summary"),
                    detail=self._t("kernel.approval.template.push.detail"),
                )
            if any(token in command.lower() for token in ("rm ", "trash ", "del ")):
                return ApprovalCopy(
                    title=self._t("kernel.approval.template.delete.title"),
                    summary=self._t("kernel.approval.template.delete.summary"),
                    detail=self._t("kernel.approval.template.delete.detail"),
                )
            return ApprovalCopy(
                title=self._t("kernel.approval.template.command.title"),
                summary=self._t("kernel.approval.template.command.summary"),
                detail=self._t("kernel.approval.template.command.detail"),
            )

        if paths:
            if len(paths) == 1:
                path = paths[0]
                if any(token in path for token in (".env", "/.ssh/", "/.gnupg/", "/Library/")):
                    return ApprovalCopy(
                        title=self._t("kernel.approval.template.sensitive_file.title"),
                        summary=self._t("kernel.approval.template.sensitive_file.summary", path=path),
                        detail=self._t("kernel.approval.template.sensitive_file.detail"),
                    )
                if facts.get("outside_workspace"):
                    return ApprovalCopy(
                        title=self._t("kernel.approval.template.outside_workspace.title"),
                        summary=self._t("kernel.approval.template.outside_workspace.summary", path=path),
                        detail=self._t("kernel.approval.template.outside_workspace.detail"),
                    )
                return ApprovalCopy(
                    title=self._t("kernel.approval.template.single_file.title"),
                    summary=self._t("kernel.approval.template.single_file.summary", path=path),
                    detail=self._t("kernel.approval.template.single_file.detail"),
                )
            return ApprovalCopy(
                title=self._t("kernel.approval.template.multi_file.title"),
                summary=self._t("kernel.approval.template.multi_file.summary", count=len(paths)),
                detail=self._t("kernel.approval.template.multi_file.detail"),
            )

        if hosts:
            if len(hosts) == 1:
                summary = self._t("kernel.approval.template.network.summary.single", host=hosts[0])
            else:
                summary = self._t("kernel.approval.template.network.summary.multiple", count=len(hosts))
            return ApprovalCopy(
                title=self._t("kernel.approval.template.network.title"),
                summary=summary,
                detail=self._t("kernel.approval.template.network.detail"),
            )

        if packet_title:
            summary = facts["packet_summary"] or self._t("kernel.approval.template.packet.summary_default")
            detail = self._t("kernel.approval.template.packet.detail", risk=risk)
            return ApprovalCopy(title=packet_title, summary=summary, detail=detail)

        summary = self._t(
            "kernel.approval.template.fallback.summary",
            tool_name=facts["tool_name"] or self._t("kernel.approval.template.fallback.unknown_tool"),
        )
        detail = self._t("kernel.approval.template.fallback.detail", risk=risk)
        return ApprovalCopy(
            title=self._t("kernel.approval.template.fallback.title"),
            summary=summary,
            detail=detail,
        )
