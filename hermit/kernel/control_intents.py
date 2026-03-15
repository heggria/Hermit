from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ControlIntent:
    action: str
    target_id: str = ""
    reason: str = ""


_APPROVE_RE = re.compile(r"^(?:/task\s+approve|批准|approve)\s+([a-z0-9_]+)$", re.IGNORECASE)
_APPROVE_ONCE_RE = re.compile(r"^(?:批准一次|approve_once)\s+([a-z0-9_]+)$", re.IGNORECASE)
_APPROVE_MUTABLE_WORKSPACE_RE = re.compile(
    r"^(?:批准可变工作区|approve_mutable_workspace|approve-mutable-workspace)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_DENY_RE = re.compile(r"^(?:/task\s+deny|拒绝|deny)\s+([a-z0-9_]+)(?:\s+(.+))?$", re.IGNORECASE)

_TASK_CASE_RE = re.compile(
    r"^(?:/task\s+case|task\s+case|查看任务|查看task|看看任务|看看task|显示任务|show\s+task|任务详情|task详情)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_TASK_SWITCH_RE = re.compile(
    r"^(?:切到任务|切换到任务|切到|切换到|focus\s+task|switch\s+task|continue\s+task|继续任务)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_TASK_EVENTS_RE = re.compile(
    r"^(?:/task\s+events|task\s+events|查看事件|查看任务事件|show\s+events)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_TASK_RECEIPTS_RE = re.compile(
    r"^(?:/task\s+receipts|task\s+receipts|查看收据|查看任务收据|show\s+receipts)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_TASK_PROOF_RE = re.compile(
    r"^(?:/task\s+proof|task\s+proof|查看证明|查看proof|show\s+proof)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_TASK_PROOF_EXPORT_RE = re.compile(
    r"^(?:/task\s+proof-export|导出证明|导出proof|export\s+proof)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_ROLLBACK_WITH_ID_RE = re.compile(
    r"^(?:/task\s+rollback|task\s+rollback|回滚|撤销|rollback)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_TASK_GRANT_REVOKE_RE = re.compile(
    r"^(?:撤销能力授权|撤销授权|撤销capability|revoke\s+capability)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_SCHEDULE_HISTORY_RE = re.compile(
    r"^(?:查看定时历史|查看调度历史|schedule\s+history|调度历史)\s*([a-z0-9_]+)?$",
    re.IGNORECASE,
)
_SCHEDULE_ENABLE_RE = re.compile(
    r"^(?:启用定时任务|启用调度|schedule\s+enable)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_SCHEDULE_DISABLE_RE = re.compile(
    r"^(?:禁用定时任务|禁用调度|schedule\s+disable)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)
_SCHEDULE_REMOVE_RE = re.compile(
    r"^(?:删除定时任务|删除调度|移除定时任务|schedule\s+remove)\s+([a-z0-9_]+)$",
    re.IGNORECASE,
)

_LOWER_HELP_TEXTS = {
    "help",
    "帮助",
    "看看帮助",
    "显示帮助",
    "有哪些命令",
    "有什么命令",
    "可用命令",
}
_LOWER_NEW_TEXTS = {
    "新开会话",
    "开启新会话",
    "新建会话",
    "重置会话",
    "清空当前会话",
}
_LOWER_HISTORY_TEXTS = {
    "history",
    "查看历史",
    "会话历史",
    "看看历史",
    "当前会话历史",
}
_LOWER_TASK_LIST_TEXTS = {
    "任务列表",
    "列出任务",
    "看看任务列表",
    "查看任务列表",
    "最近任务",
}
_LOWER_CASE_LATEST_TEXTS = {
    "看看这个任务",
    "看下这个任务",
    "查看这个任务",
    "当前任务详情",
    "看看当前任务",
    "查看当前任务",
    "看下case",
    "看看case",
    "show case",
}
_LOWER_EVENTS_LATEST_TEXTS = {
    "看看这个任务的事件",
    "查看这个任务的事件",
    "当前任务事件",
    "看下事件",
}
_LOWER_RECEIPTS_LATEST_TEXTS = {
    "看看这个任务的收据",
    "查看这个任务的收据",
    "当前任务收据",
    "最近收据",
}
_LOWER_PROOF_LATEST_TEXTS = {
    "看看这个任务的证明",
    "查看这个任务的证明",
    "当前任务证明",
    "看下proof",
}
_LOWER_PROOF_EXPORT_LATEST_TEXTS = {
    "导出这个任务的证明",
    "导出这个任务的proof",
    "导出当前任务证明",
}
_LOWER_PLAN_ENTER_TEXTS = {
    "进入规划模式",
    "开始规划",
    "先规划一下",
    "先计划一下",
    "先给个计划",
    "plan current task",
}
_LOWER_PLAN_CONFIRM_TEXTS = {
    "开始执行",
    "执行吧",
    "确认执行",
    "按计划执行",
    "run the plan",
    "execute the plan",
}
_LOWER_PLAN_EXIT_TEXTS = {
    "退出规划模式",
    "关闭规划模式",
    "结束规划模式",
    "plan off",
}
_LOWER_ROLLBACK_LATEST_TEXTS = {
    "回滚这次操作",
    "回滚这次写入",
    "撤销这次操作",
    "撤销最近一次操作",
    "rollback this",
    "rollback latest",
}
_LOWER_GRANT_LIST_TEXTS = {
    "查看授权",
    "查看能力授权",
    "能力授权列表",
    "查看capability",
    "capability列表",
}
_LOWER_SCHEDULE_LIST_TEXTS = {
    "定时任务列表",
    "调度列表",
    "查看定时任务",
    "查看调度",
    "schedule list",
}
_PENDING_APPROVE_TEXT = {
    "开始执行",
    "执行吧",
    "确认执行",
    "继续执行",
    "approve",
    "通过",
    "批准",
    "同意",
}


def parse_control_intent(
    text: str,
    *,
    pending_approval_id: str | None = None,
    latest_task_id: str | None = None,
    latest_receipt_id: str | None = None,
) -> ControlIntent | None:
    stripped = text.strip()
    lowered = stripped.lower()
    if not stripped:
        return None

    match = _APPROVE_RE.match(stripped)
    if match:
        return ControlIntent("approve_once", match.group(1))
    match = _APPROVE_ONCE_RE.match(stripped)
    if match:
        return ControlIntent("approve_once", match.group(1))
    match = _APPROVE_MUTABLE_WORKSPACE_RE.match(stripped)
    if match:
        return ControlIntent("approve_mutable_workspace", match.group(1))
    match = _DENY_RE.match(stripped)
    if match:
        return ControlIntent("deny", match.group(1), match.group(2) or "")
    if pending_approval_id and stripped in _PENDING_APPROVE_TEXT:
        return ControlIntent("approve_once", pending_approval_id)

    if lowered in _LOWER_HELP_TEXTS:
        return ControlIntent("show_help")
    if lowered in _LOWER_NEW_TEXTS:
        return ControlIntent("new_session")
    if lowered in _LOWER_HISTORY_TEXTS:
        return ControlIntent("show_history")
    if lowered in _LOWER_TASK_LIST_TEXTS:
        return ControlIntent("task_list")

    match = _TASK_SWITCH_RE.match(stripped)
    if match:
        return ControlIntent("focus_task", match.group(1), "explicit_task_switch")

    match = _TASK_CASE_RE.match(stripped)
    if match:
        return ControlIntent("case", match.group(1))
    if latest_task_id and lowered in _LOWER_CASE_LATEST_TEXTS:
        return ControlIntent("case", latest_task_id)

    match = _TASK_EVENTS_RE.match(stripped)
    if match:
        return ControlIntent("task_events", match.group(1))
    if latest_task_id and lowered in _LOWER_EVENTS_LATEST_TEXTS:
        return ControlIntent("task_events", latest_task_id)

    match = _TASK_RECEIPTS_RE.match(stripped)
    if match:
        return ControlIntent("task_receipts", match.group(1))
    if latest_task_id and lowered in _LOWER_RECEIPTS_LATEST_TEXTS:
        return ControlIntent("task_receipts", latest_task_id)

    match = _TASK_PROOF_RE.match(stripped)
    if match:
        return ControlIntent("task_proof", match.group(1))
    if latest_task_id and lowered in _LOWER_PROOF_LATEST_TEXTS:
        return ControlIntent("task_proof", latest_task_id)

    match = _TASK_PROOF_EXPORT_RE.match(stripped)
    if match:
        return ControlIntent("task_proof_export", match.group(1))
    if latest_task_id and lowered in _LOWER_PROOF_EXPORT_LATEST_TEXTS:
        return ControlIntent("task_proof_export", latest_task_id)

    if lowered in _LOWER_PLAN_ENTER_TEXTS:
        return ControlIntent("plan_enter", latest_task_id or "")
    if latest_task_id and lowered in _LOWER_PLAN_CONFIRM_TEXTS:
        return ControlIntent("plan_confirm", latest_task_id)
    if latest_task_id and lowered in _LOWER_PLAN_EXIT_TEXTS:
        return ControlIntent("plan_exit", latest_task_id)

    match = _ROLLBACK_WITH_ID_RE.match(stripped)
    if match:
        return ControlIntent("rollback", match.group(1))
    if latest_receipt_id and lowered in _LOWER_ROLLBACK_LATEST_TEXTS:
        return ControlIntent("rollback", latest_receipt_id)

    if lowered in _LOWER_GRANT_LIST_TEXTS:
        return ControlIntent("capability_list")
    match = _TASK_GRANT_REVOKE_RE.match(stripped)
    if match:
        return ControlIntent("capability_revoke", match.group(1))

    if lowered in _LOWER_SCHEDULE_LIST_TEXTS:
        return ControlIntent("schedule_list")
    match = _SCHEDULE_HISTORY_RE.match(stripped)
    if match:
        return ControlIntent("schedule_history", (match.group(1) or "").strip())
    match = _SCHEDULE_ENABLE_RE.match(stripped)
    if match:
        return ControlIntent("schedule_enable", match.group(1))
    match = _SCHEDULE_DISABLE_RE.match(stripped)
    if match:
        return ControlIntent("schedule_disable", match.group(1))
    match = _SCHEDULE_REMOVE_RE.match(stripped)
    if match:
        return ControlIntent("schedule_remove", match.group(1))

    if (
        lowered
        in {
            "重建这个任务的projection",
            "重建当前任务projection",
            "重建这个任务投影",
            "重建当前任务投影",
        }
        and latest_task_id
    ):
        return ControlIntent("projection_rebuild", latest_task_id)
    if lowered in {"重建所有projection", "重建所有投影"}:
        return ControlIntent("projection_rebuild_all")

    return None
