from __future__ import annotations

import concurrent.futures
import datetime
from dataclasses import dataclass
from typing import Any, Callable

from hermit.i18n import resolve_locale, tr

Formatter = Callable[[dict[str, Any]], dict[str, str] | str | None]


@dataclass
class ApprovalSection:
    title: str
    items: tuple[str, ...] = ()


@dataclass
class ApprovalCopy:
    title: str
    summary: str
    detail: str
    sections: tuple[ApprovalSection, ...] = ()


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

    def build_canonical_copy(
        self, requested_action: dict[str, Any], approval_id: str | None = None
    ) -> dict[str, Any]:
        copy = self.describe(requested_action, approval_id=approval_id)
        payload: dict[str, Any] = {
            "title": copy.title,
            "summary": copy.summary,
            "detail": copy.detail,
        }
        if copy.sections:
            payload["sections"] = [
                {
                    "title": section.title,
                    "items": list(section.items),
                }
                for section in copy.sections
            ]
        return payload

    def describe(
        self, requested_action: dict[str, Any], approval_id: str | None = None
    ) -> ApprovalCopy:
        facts = self._facts(requested_action, approval_id=approval_id)
        display_copy = dict(requested_action.get("display_copy", {}) or {})
        if display_copy:
            resolved = self._copy_from_mapping(display_copy)
            if resolved is not None:
                return self._ensure_sections(resolved, facts)
        formatted = self._format_with_optional_formatter(facts)
        if formatted is not None:
            return self._ensure_sections(formatted, facts)
        return self._template_copy(facts)

    def resolve_copy(
        self, requested_action: dict[str, Any], approval_id: str | None = None
    ) -> ApprovalCopy:
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
            payload = future.result(timeout=max(0.001, self._formatter_timeout_ms / 1000))
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
            return ApprovalCopy(
                title=title,
                summary=summary,
                detail=detail or summary,
                sections=self._sections_from_mapping(payload.get("sections")),
            )
        return None

    def _sections_from_mapping(self, raw_sections: Any) -> tuple[ApprovalSection, ...]:
        if not isinstance(raw_sections, list):
            return ()
        sections: list[ApprovalSection] = []
        for raw_section in raw_sections:
            if not isinstance(raw_section, dict):
                continue
            title = str(raw_section.get("title", "")).strip()
            raw_items = raw_section.get("items")
            if not title or not isinstance(raw_items, list):
                continue
            items = tuple(str(item).strip() for item in raw_items if str(item).strip())
            if items:
                sections.append(ApprovalSection(title=title, items=items))
        return tuple(sections)

    def _ensure_sections(self, copy: ApprovalCopy, facts: dict[str, Any]) -> ApprovalCopy:
        if copy.sections:
            return copy
        sections = self._sections_for_facts(facts)
        if not sections:
            return copy
        return ApprovalCopy(
            title=copy.title,
            summary=copy.summary,
            detail=copy.detail,
            sections=sections,
        )

    def _facts(
        self, requested_action: dict[str, Any], *, approval_id: str | None
    ) -> dict[str, Any]:
        packet = dict(requested_action.get("approval_packet", {}) or {})
        contract_packet = dict(requested_action.get("contract_packet", {}) or {})
        target_paths = [str(path) for path in requested_action.get("target_paths", [])]
        network_hosts = [str(host) for host in requested_action.get("network_hosts", [])]
        command_preview = str(requested_action.get("command_preview", "") or "").strip()
        tool_name = str(requested_action.get("tool_name", "") or "").strip()
        risk_level = str(
            requested_action.get("risk_level", "") or packet.get("risk_level", "") or "high"
        )
        tool_input = requested_action.get("tool_input")
        return {
            "approval_id": approval_id or "",
            "title": str(packet.get("title", "")).strip(),
            "packet_summary": str(packet.get("summary", "")).strip(),
            "tool_name": tool_name,
            "tool_input": dict(tool_input) if isinstance(tool_input, dict) else tool_input,
            "action_class": str(requested_action.get("action_class", "") or "").strip(),
            "reason": str(requested_action.get("reason", "") or "").strip(),
            "risk_level": risk_level,
            "target_paths": target_paths,
            "network_hosts": network_hosts,
            "command_preview": command_preview,
            "resource_scopes": [
                str(scope) for scope in requested_action.get("resource_scopes", [])
            ],
            "outside_workspace": bool(requested_action.get("outside_workspace")),
            "contract_packet": contract_packet,
            "contract_ref": str(requested_action.get("contract_ref", "") or "").strip(),
            "evidence_case_ref": str(requested_action.get("evidence_case_ref", "") or "").strip(),
            "authorization_plan_ref": str(
                requested_action.get("authorization_plan_ref", "") or ""
            ).strip(),
        }

    def _template_copy(self, facts: dict[str, Any]) -> ApprovalCopy:
        scheduler_copy = self._scheduler_copy(facts)
        if scheduler_copy is not None:
            return scheduler_copy

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
                        summary=self._t(
                            "kernel.approval.template.sensitive_file.summary", path=path
                        ),
                        detail=self._t("kernel.approval.template.sensitive_file.detail"),
                    )
                if facts.get("outside_workspace"):
                    return ApprovalCopy(
                        title=self._t("kernel.approval.template.outside_workspace.title"),
                        summary=self._t(
                            "kernel.approval.template.outside_workspace.summary", path=path
                        ),
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
                summary = self._t(
                    "kernel.approval.template.network.summary.multiple", count=len(hosts)
                )
            return ApprovalCopy(
                title=self._t("kernel.approval.template.network.title"),
                summary=summary,
                detail=self._t("kernel.approval.template.network.detail"),
            )

        if packet_title:
            summary = facts["packet_summary"] or self._t(
                "kernel.approval.template.packet.summary_default"
            )
            detail = self._t("kernel.approval.template.packet.detail", risk=risk)
            return ApprovalCopy(title=packet_title, summary=summary, detail=detail)

        summary = self._t(
            "kernel.approval.template.fallback.summary",
            tool_name=facts["tool_name"]
            or self._t("kernel.approval.template.fallback.unknown_tool"),
        )
        detail = self._t("kernel.approval.template.fallback.detail", risk=risk)
        return ApprovalCopy(
            title=self._t("kernel.approval.template.fallback.title"),
            summary=summary,
            detail=detail,
        )

    def _sections_for_facts(self, facts: dict[str, Any]) -> tuple[ApprovalSection, ...]:
        tool_name = str(facts.get("tool_name", "") or "").strip()
        contract_sections = self._contract_sections(facts)
        if tool_name.startswith("schedule_"):
            return tuple(list(contract_sections) + list(self._scheduler_sections(facts)))
        return contract_sections

    def _contract_sections(self, facts: dict[str, Any]) -> tuple[ApprovalSection, ...]:
        contract_packet = dict(facts.get("contract_packet", {}) or {})
        if not contract_packet:
            return ()
        sections: list[ApprovalSection] = []

        objective = str(contract_packet.get("objective", "") or "").strip()
        expected_effects = [
            str(effect).strip()
            for effect in contract_packet.get("expected_effects", [])
            if str(effect).strip()
        ]
        contract_items: list[str] = []
        if objective:
            contract_items.append(f"Objective: {objective}")
        if expected_effects:
            contract_items.append(f"Expected effects: {', '.join(expected_effects[:4])}")
        rollback_expectation = str(contract_packet.get("rollback_expectation", "") or "").strip()
        if rollback_expectation:
            contract_items.append(f"Rollback expectation: {rollback_expectation}")
        if contract_items:
            sections.append(ApprovalSection(title="Contract", items=tuple(contract_items)))

        evidence = dict(contract_packet.get("evidence_sufficiency", {}) or {})
        evidence_items: list[str] = []
        status = str(evidence.get("status", "") or "").strip()
        score = evidence.get("score")
        if status:
            evidence_items.append(f"Sufficiency: {status}")
        if score is not None:
            evidence_items.append(
                f"Score: {score:.2f}" if isinstance(score, (int, float)) else f"Score: {score}"
            )
        gaps = [
            str(item).strip() for item in evidence.get("unresolved_gaps", []) if str(item).strip()
        ]
        if gaps:
            evidence_items.append(f"Open gaps: {', '.join(gaps[:4])}")
        if evidence_items:
            sections.append(ApprovalSection(title="Evidence", items=tuple(evidence_items)))

        authority_items: list[str] = []
        approval_route = str(contract_packet.get("approval_route", "") or "").strip()
        authority_scope = dict(contract_packet.get("authority_scope", {}) or {})
        if approval_route:
            authority_items.append(f"Approval route: {approval_route}")
        resource_scope = authority_scope.get("resource_scope")
        if isinstance(resource_scope, list) and resource_scope:
            authority_items.append(
                f"Authority scope: {', '.join(str(scope).strip() for scope in resource_scope[:4] if str(scope).strip())}"
            )
        current_gaps = [
            str(item).strip()
            for item in contract_packet.get("current_gaps", [])
            if str(item).strip()
        ]
        if current_gaps:
            authority_items.append(f"Revalidation gaps: {', '.join(current_gaps[:4])}")
        drift_expiry = contract_packet.get("drift_expiry")
        if drift_expiry:
            authority_items.append(f"Drift expiry: {drift_expiry}")
        if authority_items:
            sections.append(ApprovalSection(title="Authority", items=tuple(authority_items)))

        return tuple(sections)

    def _scheduler_copy(self, facts: dict[str, Any]) -> ApprovalCopy | None:
        tool_name = str(facts.get("tool_name", "") or "").strip()
        if tool_name == "schedule_create":
            tool_input = self._scheduler_input(facts)
            name = str(tool_input.get("name", "")).strip() or self._t(
                "kernel.approval.scheduler.item.name_unknown"
            )
            timing = self._describe_scheduler_timing(tool_input)
            return ApprovalCopy(
                title=self._t("kernel.approval.scheduler.create.title"),
                summary=self._t(
                    "kernel.approval.scheduler.create.summary", name=name, timing=timing
                ),
                detail=self._t("kernel.approval.scheduler.create.detail"),
                sections=self._scheduler_sections(facts),
            )
        if tool_name == "schedule_update":
            tool_input = self._scheduler_input(facts)
            job_id = str(tool_input.get("job_id", "")).strip() or self._t(
                "kernel.approval.scheduler.item.job_unknown"
            )
            return ApprovalCopy(
                title=self._t("kernel.approval.scheduler.update.title"),
                summary=self._t("kernel.approval.scheduler.update.summary", job_id=job_id),
                detail=self._t("kernel.approval.scheduler.update.detail"),
                sections=self._scheduler_sections(facts),
            )
        if tool_name == "schedule_delete":
            tool_input = self._scheduler_input(facts)
            job_id = str(tool_input.get("job_id", "")).strip() or self._t(
                "kernel.approval.scheduler.item.job_unknown"
            )
            return ApprovalCopy(
                title=self._t("kernel.approval.scheduler.delete.title"),
                summary=self._t("kernel.approval.scheduler.delete.summary", job_id=job_id),
                detail=self._t("kernel.approval.scheduler.delete.detail"),
                sections=self._scheduler_sections(facts),
            )
        return None

    def _scheduler_sections(self, facts: dict[str, Any]) -> tuple[ApprovalSection, ...]:
        tool_name = str(facts.get("tool_name", "") or "").strip()
        tool_input = self._scheduler_input(facts)
        detail_items: list[str] = []
        reason_items: list[str] = []

        if tool_name == "schedule_create":
            name = str(tool_input.get("name", "")).strip()
            prompt = self._summarize_text(str(tool_input.get("prompt", "")).strip(), limit=120)
            timing = self._describe_scheduler_timing(tool_input)
            if name:
                detail_items.append(self._t("kernel.approval.scheduler.item.name", name=name))
            if timing:
                detail_items.append(self._t("kernel.approval.scheduler.item.timing", timing=timing))
            if prompt:
                detail_items.append(self._t("kernel.approval.scheduler.item.prompt", prompt=prompt))
            reason_items.append(
                self._scheduler_reason(
                    facts,
                    default_key="kernel.approval.scheduler.create.reason",
                )
            )
        elif tool_name == "schedule_update":
            job_id = str(tool_input.get("job_id", "")).strip()
            if job_id:
                detail_items.append(self._t("kernel.approval.scheduler.item.job_id", job_id=job_id))
            name = str(tool_input.get("name", "")).strip()
            if name:
                detail_items.append(self._t("kernel.approval.scheduler.item.name_new", name=name))
            prompt = self._summarize_text(str(tool_input.get("prompt", "")).strip(), limit=120)
            if prompt:
                detail_items.append(
                    self._t("kernel.approval.scheduler.item.prompt_new", prompt=prompt)
                )
            if "enabled" in tool_input:
                detail_items.append(
                    self._t(
                        "kernel.approval.scheduler.item.enabled_state",
                        state=self._t(
                            "kernel.approval.scheduler.item.enabled"
                            if bool(tool_input.get("enabled"))
                            else "kernel.approval.scheduler.item.disabled"
                        ),
                    )
                )
            if str(tool_input.get("cron_expr", "")).strip():
                detail_items.append(
                    self._t(
                        "kernel.approval.scheduler.item.timing_new",
                        timing=self._describe_scheduler_timing(
                            {
                                "schedule_type": "cron",
                                "cron_expr": tool_input.get("cron_expr"),
                            }
                        ),
                    )
                )
            reason_items.append(
                self._scheduler_reason(
                    facts,
                    default_key="kernel.approval.scheduler.update.reason",
                )
            )
        elif tool_name == "schedule_delete":
            job_id = str(tool_input.get("job_id", "")).strip()
            if job_id:
                detail_items.append(self._t("kernel.approval.scheduler.item.job_id", job_id=job_id))
            detail_items.append(self._t("kernel.approval.scheduler.delete.effect"))
            reason_items.append(
                self._scheduler_reason(
                    facts,
                    default_key="kernel.approval.scheduler.delete.reason",
                )
            )

        sections: list[ApprovalSection] = []
        if detail_items:
            sections.append(
                ApprovalSection(
                    title=self._t("kernel.approval.section.details"),
                    items=tuple(item for item in detail_items if item),
                )
            )
        if reason_items:
            sections.append(
                ApprovalSection(
                    title=self._t("kernel.approval.section.reason"),
                    items=tuple(item for item in reason_items if item),
                )
            )
        return tuple(section for section in sections if section.items)

    def _scheduler_input(self, facts: dict[str, Any]) -> dict[str, Any]:
        tool_input = facts.get("tool_input")
        return dict(tool_input) if isinstance(tool_input, dict) else {}

    def _scheduler_reason(self, facts: dict[str, Any], *, default_key: str) -> str:
        reason = str(facts.get("reason", "") or "").strip()
        return reason or self._t(default_key)

    def _describe_scheduler_timing(self, tool_input: dict[str, Any]) -> str:
        schedule_type = str(tool_input.get("schedule_type", "")).strip()
        if schedule_type == "once":
            when = self._format_datetime_text(str(tool_input.get("once_at", "")).strip())
            if when:
                return self._t("kernel.approval.scheduler.timing.once", when=when)
        if schedule_type == "interval":
            seconds = self._safe_int(tool_input.get("interval_seconds"))
            if seconds and seconds > 0:
                return self._t(
                    "kernel.approval.scheduler.timing.interval",
                    interval=self._format_interval(seconds),
                )
        if schedule_type == "cron":
            cron_expr = str(tool_input.get("cron_expr", "")).strip()
            if cron_expr:
                next_run = self._next_cron_run_text(cron_expr)
                if next_run:
                    return self._t(
                        "kernel.approval.scheduler.timing.cron_with_next",
                        cron_expr=cron_expr,
                        next_run=next_run,
                    )
                return self._t("kernel.approval.scheduler.timing.cron", cron_expr=cron_expr)
        return self._t("kernel.approval.scheduler.timing.unknown")

    def _next_cron_run_text(self, cron_expr: str) -> str | None:
        try:
            from croniter import croniter

            now = datetime.datetime.now().astimezone()
            next_run = croniter(cron_expr, now).get_next(datetime.datetime)
        except Exception:
            return None
        return self._format_datetime_value(next_run)

    def _format_datetime_text(self, value: str) -> str:
        if not value:
            return ""
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.datetime.fromisoformat(normalized)
        except ValueError:
            return value
        return self._format_datetime_value(parsed)

    @staticmethod
    def _format_datetime_value(value: datetime.datetime) -> str:
        if value.tzinfo is None:
            return value.strftime("%Y-%m-%d %H:%M")
        return value.astimezone().strftime("%Y-%m-%d %H:%M")

    def _format_interval(self, seconds: int) -> str:
        if seconds % 3600 == 0:
            hours = seconds // 3600
            return self._t(
                "kernel.approval.scheduler.interval.hour"
                if hours == 1
                else "kernel.approval.scheduler.interval.hours",
                count=hours,
            )
        if seconds % 60 == 0:
            minutes = seconds // 60
            return self._t(
                "kernel.approval.scheduler.interval.minute"
                if minutes == 1
                else "kernel.approval.scheduler.interval.minutes",
                count=minutes,
            )
        return self._t(
            "kernel.approval.scheduler.interval.second"
            if seconds == 1
            else "kernel.approval.scheduler.interval.seconds",
            count=seconds,
        )

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _summarize_text(value: str, *, limit: int) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: max(0, limit - 3)].rstrip()}..."
