from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from hermit.builtin.memory.engine import MemoryEngine
from hermit.kernel.models import ConversationRecord, TaskRecord

_BRANCH_MARKERS = (
    "顺便",
    "另外",
    "再查一下",
    "再问一下",
    "顺手",
)
_CONTINUE_MARKERS = (
    "继续",
    "接着",
    "然后",
    "补充",
    "补充一点",
    "补充说明",
    "说明",
    "加上",
    "再加",
    "改成",
    "改为",
    "放到",
    "发到",
    "写到",
    "去掉",
    "删掉",
    "保留",
    "就按",
    "按照",
    "extra note",
    "follow up",
)
_AMBIGUOUS_MARKERS = (
    "这个",
    "那个",
    "这份",
    "这条",
    "上面",
    "上一条",
    "刚才",
)
_ARTIFACT_REF_RE = re.compile(r"\bartifact_[a-z0-9]{6,}\b", re.IGNORECASE)
_RECEIPT_REF_RE = re.compile(r"\breceipt_[a-z0-9]{6,}\b", re.IGNORECASE)
_ABSOLUTE_PATH_RE = re.compile(r"(?:~|/)[\w./-]+")


@dataclass(frozen=True)
class CandidateScore:
    task_id: str
    score: float
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BindingDecision:
    resolution: Literal[
        "control",
        "approval",
        "append_note",
        "fork_child",
        "start_new_root",
        "chat_only",
        "pending_disambiguation",
    ]
    chosen_task_id: str | None = None
    parent_task_id: str | None = None
    confidence: float = 0.0
    margin: float = 0.0
    candidates: list[CandidateScore] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)


class IngressRouter:
    def __init__(self, store: Any) -> None:
        self.store = store

    def bind(
        self,
        *,
        conversation: ConversationRecord | None,
        open_tasks: list[TaskRecord],
        normalized_text: str,
        explicit_task_ref: str | None = None,
        reply_to_task_id: str | None = None,
        pending_approval_task_id: str | None = None,
    ) -> BindingDecision:
        cleaned = self._normalize(normalized_text)
        if explicit_task_ref:
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=explicit_task_ref,
                confidence=1.0,
                margin=1.0,
                reason_codes=["explicit_task_ref"],
            )
        if reply_to_task_id:
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=reply_to_task_id,
                confidence=1.0,
                margin=1.0,
                reason_codes=["reply_target"],
            )
        if pending_approval_task_id and self._looks_like_approval_followup(cleaned):
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=pending_approval_task_id,
                confidence=0.98,
                margin=0.98,
                reason_codes=["pending_approval_correlation"],
            )
        structural = self._resolve_structural_binding(open_tasks=open_tasks, text=cleaned)
        if structural is not None:
            return structural
        if not open_tasks:
            return BindingDecision(
                resolution="start_new_root",
                confidence=0.2,
                reason_codes=["no_open_tasks"],
            )
        if conversation is not None and conversation.focus_task_id and self._looks_like_focus_followup(cleaned):
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=conversation.focus_task_id,
                confidence=0.92,
                margin=0.92,
                reason_codes=["focus_followup_marker"],
            )

        scored: list[CandidateScore] = []
        focus_task_id = conversation.focus_task_id if conversation is not None else None
        for task in open_tasks:
            score, reasons = self._score_task(task, cleaned, focus_task_id=focus_task_id)
            if score <= 0:
                continue
            scored.append(CandidateScore(task_id=task.task_id, score=score, reason_codes=reasons))
        scored.sort(key=lambda item: item.score, reverse=True)

        if self._has_branch_marker(cleaned):
            parent = focus_task_id or (scored[0].task_id if scored else open_tasks[0].task_id)
            return BindingDecision(
                resolution="fork_child",
                parent_task_id=parent,
                confidence=0.72 if scored else 0.6,
                margin=(scored[0].score - scored[1].score) if len(scored) > 1 else (scored[0].score if scored else 0.0),
                candidates=scored[:5],
                reason_codes=["branch_marker"],
            )

        if not scored:
            return BindingDecision(
                resolution="start_new_root",
                confidence=0.35,
                candidates=[],
                reason_codes=["no_candidate_match"],
            )

        best = scored[0]
        runner_up = scored[1] if len(scored) > 1 else None
        margin = best.score - runner_up.score if runner_up is not None else best.score
        if best.score >= 0.95 and margin >= 0.05:
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=best.task_id,
                confidence=min(1.0, best.score),
                margin=margin,
                candidates=scored[:5],
                reason_codes=list(best.reason_codes),
            )
        if best.score >= 0.75 and margin < 0.05:
            return BindingDecision(
                resolution="pending_disambiguation",
                confidence=best.score,
                margin=margin,
                candidates=scored[:5],
                reason_codes=["ambiguous_top_candidates"],
            )
        if best.score >= 0.8:
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=best.task_id,
                confidence=best.score,
                margin=margin,
                candidates=scored[:5],
                reason_codes=list(best.reason_codes),
            )
        return BindingDecision(
            resolution="start_new_root",
            confidence=0.4,
            margin=margin,
            candidates=scored[:5],
            reason_codes=["weak_candidate_match"],
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(str(text or "").split()).strip()

    @staticmethod
    def _looks_like_approval_followup(text: str) -> bool:
        return any(marker in text for marker in ("执行", "批准", "approve", "草稿", "不要发", "改成"))

    @staticmethod
    def _looks_like_focus_followup(text: str) -> bool:
        return any(marker in text for marker in _CONTINUE_MARKERS) or any(
            marker in text for marker in _AMBIGUOUS_MARKERS
        )

    @staticmethod
    def _has_branch_marker(text: str) -> bool:
        return any(marker in text for marker in _BRANCH_MARKERS)

    def _resolve_structural_binding(self, *, open_tasks: list[TaskRecord], text: str) -> BindingDecision | None:
        task_ids = {task.task_id for task in open_tasks}
        artifact_targets = {
            artifact.task_id
            for artifact_id in self._artifact_refs(text)
            if (artifact := self.store.get_artifact(artifact_id)) is not None
            and artifact.task_id in task_ids
        }
        receipt_targets = {
            receipt.task_id
            for receipt_id in self._receipt_refs(text)
            if (receipt := self.store.get_receipt(receipt_id)) is not None
            and receipt.task_id in task_ids
        }
        direct_targets = {str(task_id) for task_id in artifact_targets | receipt_targets if task_id}
        if len(direct_targets) == 1:
            chosen = next(iter(direct_targets))
            reason_codes: list[str] = []
            if artifact_targets:
                reason_codes.append("artifact_ref_match")
            if receipt_targets:
                reason_codes.append("receipt_ref_match")
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=chosen,
                confidence=1.0,
                margin=1.0,
                reason_codes=reason_codes,
            )
        if len(direct_targets) > 1:
            return BindingDecision(
                resolution="pending_disambiguation",
                confidence=0.99,
                margin=0.0,
                candidates=[
                    CandidateScore(task_id=task_id, score=1.0, reason_codes=["conflicting_reference_target"])
                    for task_id in sorted(direct_targets)
                ],
                reason_codes=["conflicting_reference_targets"],
            )

        workspace_targets = self._workspace_targets(open_tasks=open_tasks, text=text)
        if len(workspace_targets) == 1:
            task_id, score = workspace_targets[0]
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=task_id,
                confidence=score,
                margin=score,
                reason_codes=["workspace_path_match"],
            )
        if len(workspace_targets) > 1:
            workspace_targets.sort(key=lambda item: item[1], reverse=True)
            best_task_id, best_score = workspace_targets[0]
            runner_up_score = workspace_targets[1][1]
            if best_score - runner_up_score >= 0.1:
                return BindingDecision(
                    resolution="append_note",
                    chosen_task_id=best_task_id,
                    confidence=best_score,
                    margin=best_score - runner_up_score,
                    reason_codes=["workspace_path_match"],
                )
        return None

    def _score_task(self, task: TaskRecord, text: str, *, focus_task_id: str | None) -> tuple[float, list[str]]:
        reasons: list[str] = []
        score = 0.0
        if focus_task_id and task.task_id == focus_task_id:
            score += 0.35
            reasons.append("focus_task")
        if self._task_references_artifact(task.task_id, text):
            score += 0.75
            reasons.append("artifact_ref_match")
        if self._task_references_receipt(task.task_id, text):
            score += 0.75
            reasons.append("receipt_ref_match")
        if self._task_matches_workspace_path(task.task_id, text):
            score += 0.55
            reasons.append("workspace_path_match")
        context_texts = [str(task.title or ""), str(task.goal or "")]
        for event in reversed(self.store.list_events(task_id=task.task_id, limit=50)):
            if event["event_type"] != "task.note.appended":
                continue
            payload = dict(event["payload"] or {})
            note_text = str(payload.get("inline_excerpt") or payload.get("raw_text") or "").strip()
            if note_text:
                context_texts.append(note_text)
            if len(context_texts) >= 6:
                break
        query_tokens = {token for token in MemoryEngine._topic_tokens(text) if len(token) >= 2}
        has_continue_marker = any(marker in text for marker in _CONTINUE_MARKERS)
        has_ambiguous_marker = any(marker in text for marker in _AMBIGUOUS_MARKERS)
        if has_continue_marker:
            score += 0.2
            reasons.append("continue_marker")
        if has_ambiguous_marker:
            score += 0.15
            reasons.append("ambiguous_marker")
        for candidate_text in context_texts:
            if not candidate_text:
                continue
            if MemoryEngine._shares_topic(candidate_text, text):
                score += 0.35
                reasons.append("topic_overlap")
                break
            candidate_tokens = {token for token in MemoryEngine._topic_tokens(candidate_text) if len(token) >= 2}
            if query_tokens & candidate_tokens:
                score += 0.3
                reasons.append("token_overlap")
                break
            if any(token in candidate_text for token in query_tokens):
                score += 0.2
                reasons.append("substring_overlap")
                break
        return min(score, 1.0), reasons

    @staticmethod
    def _artifact_refs(text: str) -> list[str]:
        return list(dict.fromkeys(match.lower() for match in _ARTIFACT_REF_RE.findall(text)))

    @staticmethod
    def _receipt_refs(text: str) -> list[str]:
        return list(dict.fromkeys(match.lower() for match in _RECEIPT_REF_RE.findall(text)))

    @staticmethod
    def _path_refs(text: str) -> list[str]:
        refs = []
        for match in _ABSOLUTE_PATH_RE.findall(text):
            candidate = str(match or "").strip().rstrip(".,:;)]}>\"'")
            if candidate:
                refs.append(candidate)
        return list(dict.fromkeys(refs))

    def _workspace_targets(self, *, open_tasks: list[TaskRecord], text: str) -> list[tuple[str, float]]:
        paths = self._path_refs(text)
        if not paths:
            return []
        targets: list[tuple[str, float]] = []
        for task in open_tasks:
            workspace_root = self._task_workspace_root(task.task_id)
            if not workspace_root:
                continue
            root = self._normalized_path(workspace_root)
            if not root:
                continue
            root_prefix = f"{root.rstrip('/')}/"
            best_score = 0.0
            for path in paths:
                normalized = self._normalized_path(path)
                if not normalized:
                    continue
                if normalized == root or normalized.startswith(root_prefix):
                    # Prefer the most specific workspace root when multiple tasks share a prefix.
                    best_score = max(best_score, min(0.97, 0.82 + min(len(root), 200) / 1000))
            if best_score > 0:
                targets.append((task.task_id, best_score))
        return targets

    def _task_references_artifact(self, task_id: str, text: str) -> bool:
        refs = set(self._artifact_refs(text))
        if not refs:
            return False
        for artifact in self.store.list_artifacts(task_id=task_id, limit=40):
            if artifact.artifact_id.lower() in refs:
                return True
        return False

    def _task_references_receipt(self, task_id: str, text: str) -> bool:
        refs = set(self._receipt_refs(text))
        if not refs:
            return False
        for receipt in self.store.list_receipts(task_id=task_id, limit=20):
            if receipt.receipt_id.lower() in refs:
                return True
        return False

    def _task_matches_workspace_path(self, task_id: str, text: str) -> bool:
        workspace_root = self._task_workspace_root(task_id)
        if not workspace_root:
            return False
        root = self._normalized_path(workspace_root)
        if not root:
            return False
        root_prefix = f"{root.rstrip('/')}/"
        for path in self._path_refs(text):
            normalized = self._normalized_path(path)
            if not normalized:
                continue
            if normalized == root or normalized.startswith(root_prefix):
                return True
        return False

    def _task_workspace_root(self, task_id: str) -> str:
        for attempt in self.store.list_step_attempts(task_id=task_id, limit=5):
            workspace_root = str((attempt.context or {}).get("workspace_root", "") or "").strip()
            if workspace_root:
                return workspace_root
        return ""

    @staticmethod
    def _normalized_path(path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        try:
            return str(Path(raw).expanduser().resolve()).replace("\\", "/")
        except OSError:
            return raw.replace("\\", "/")


__all__ = ["BindingDecision", "CandidateScore", "IngressRouter"]
