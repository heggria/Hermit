from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.context import CompiledProviderInput, TaskExecutionContext, WorkingStateSnapshot
from hermit.kernel.context_compiler import ContextCompiler
from hermit.kernel.conversation_projection import ConversationProjectionService
from hermit.kernel.planning import PlanningService
from hermit.kernel.projections import ProjectionService
from hermit.kernel.store import KernelStore
from hermit.kernel.store_support import _sha256_hex

_CODE_BLOCK_RE = re.compile(r"```(?P<lang>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)
_LONG_PROSE_CHAR_THRESHOLD = 4096
_LONG_PROSE_LINE_THRESHOLD = 80
_INLINE_EXCERPT_LIMIT = 800
_SESSION_TIME_RE = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)
_TAG_RE = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)


def _trim(text: str, limit: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return cleaned[:limit]
    return cleaned[: limit - 1].rstrip() + "…"


def _strip_runtime_markup(text: str) -> str:
    cleaned = str(text or "")
    cleaned = _SESSION_TIME_RE.sub("", cleaned)
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
    return cleaned.strip()


class ProviderInputCompiler:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.context_compiler = ContextCompiler(artifact_store=artifact_store)
        self.task_projections = ProjectionService(store)
        self.conversation_projections = ConversationProjectionService(store, artifact_store)
        self.planning = PlanningService(store, artifact_store)

    def normalize_ingress(
        self,
        *,
        task_context: TaskExecutionContext,
        raw_text: str,
        final_prompt: str,
    ) -> dict[str, Any]:
        artifact_refs: list[str] = []
        detected_payload_kinds: list[str] = []
        inline_source = _strip_runtime_markup(raw_text) or _strip_runtime_markup(final_prompt)
        normalized_prompt = inline_source
        original_hash = _sha256_hex(raw_text or final_prompt or "")

        for index, match in enumerate(_CODE_BLOCK_RE.finditer(inline_source), start=1):
            code = match.group("body")
            language = match.group("lang").strip()
            artifact_ref = self._store_ingress_artifact(
                task_context=task_context,
                payload={
                    "kind": "ingress.payload/v1",
                    "payload_kind": "code_block",
                    "language": language,
                    "content": code,
                    "block_index": index,
                    "original_input_hash": original_hash,
                },
                metadata={
                    "conversation_id": task_context.conversation_id,
                    "payload_kind": "code_block",
                    "language": language,
                    "block_index": index,
                    "original_input_hash": original_hash,
                },
            )
            if artifact_ref is not None:
                artifact_refs.append(artifact_ref)
                detected_payload_kinds.append("code_block")
                replacement = (
                    f"[artifact:{artifact_ref}] fenced code block"
                    + (f" ({language})" if language else "")
                )
                normalized_prompt = normalized_prompt.replace(match.group(0), replacement, 1)

        if (
            len(inline_source) > _LONG_PROSE_CHAR_THRESHOLD
            or inline_source.count("\n") + 1 > _LONG_PROSE_LINE_THRESHOLD
        ):
            artifact_ref = self._store_ingress_artifact(
                task_context=task_context,
                payload={
                    "kind": "ingress.payload/v1",
                    "payload_kind": "long_text",
                    "content": inline_source,
                    "original_input_hash": original_hash,
                },
                metadata={
                    "conversation_id": task_context.conversation_id,
                    "payload_kind": "long_text",
                    "original_input_hash": original_hash,
                },
            )
            if artifact_ref is not None:
                artifact_refs.append(artifact_ref)
                detected_payload_kinds.append("long_text")

        inline_excerpt = _trim(normalized_prompt, _INLINE_EXCERPT_LIMIT)
        return {
            "ingress_artifact_refs": artifact_refs,
            "inline_excerpt": inline_excerpt,
            "detected_payload_kinds": list(dict.fromkeys(detected_payload_kinds)),
            "original_input_hash": original_hash,
            "normalized_prompt": normalized_prompt,
        }

    def compile(
        self,
        *,
        task_context: TaskExecutionContext,
        final_prompt: str,
        raw_text: str,
    ) -> CompiledProviderInput:
        normalized = self.normalize_ingress(
            task_context=task_context,
            raw_text=raw_text,
            final_prompt=final_prompt,
        )
        self._update_attempt_ingress_metadata(task_context.step_attempt_id, normalized)

        projection_payload = self.conversation_projections.ensure(task_context.conversation_id)
        task_projection = self.task_projections.ensure_task_projection(task_context.task_id)
        planning_state = self.planning.state_for_task(task_context.task_id)
        task = self.store.get_task(task_context.task_id)
        step = self.store.get_step(task_context.step_id)
        notes = self._recent_notes(task_context.task_id)

        pack = self.context_compiler.compile(
            context=task_context,
            working_state=WorkingStateSnapshot(
                goal_summary=_trim(raw_text or final_prompt, 400),
                open_loops=[_trim(item, 200) for item in projection_payload.get("open_loops", [])[:8]],
                recent_results=[
                    _trim(str(task_projection.get("topic", {}).get("summary", "") or ""), 200),
                ]
                if task_projection.get("topic")
                else [],
                planning_mode=bool(planning_state.planning_mode),
                candidate_plan_refs=list(planning_state.candidate_plan_refs),
                selected_plan_ref=str(planning_state.selected_plan_ref or ""),
                plan_status=str(planning_state.plan_status or "none"),
            ),
            beliefs=self.store.list_beliefs(task_id=task_context.task_id, status="active", limit=200),
            memories=self.store.list_memory_records(
                status="active",
                conversation_id=task_context.conversation_id,
                limit=500,
            ),
            query=normalized["inline_excerpt"],
            task_summary={
                "task_id": task.task_id if task is not None else task_context.task_id,
                "title": getattr(task, "title", ""),
                "goal": getattr(task, "goal", ""),
                "status": getattr(task, "status", ""),
            },
            step_summary={
                "step_id": step.step_id if step is not None else task_context.step_id,
                "kind": getattr(step, "kind", ""),
                "status": getattr(step, "status", ""),
            },
            policy_summary={"policy_profile": task_context.policy_profile},
            planning_state=asdict(planning_state),
            recent_notes=notes,
            relevant_artifact_refs=self._relevant_artifact_refs(task_context, normalized["ingress_artifact_refs"]),
            ingress_artifact_refs=list(normalized["ingress_artifact_refs"]),
            session_projection_ref=projection_payload.get("artifact_ref"),
        )
        context_pack_ref = self._store_context_pack(task_context=task_context, pack=pack)
        compiled_text = self._render_message(
            projection_payload=projection_payload,
            context_pack=pack.to_payload(),
            current_request=normalized["inline_excerpt"],
            normalized_prompt=normalized["normalized_prompt"],
            ingress_artifact_refs=normalized["ingress_artifact_refs"],
        )
        return CompiledProviderInput(
            messages=[{"role": "user", "content": compiled_text}],
            context_pack_ref=context_pack_ref,
            ingress_artifact_refs=list(normalized["ingress_artifact_refs"]),
            session_projection_ref=projection_payload.get("artifact_ref"),
            source_mode="compiled",
        )

    def _recent_notes(self, task_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for event in reversed(self.store.list_events(task_id=task_id, limit=200)):
            if event["event_type"] != "task.note.appended":
                continue
            payload = dict(event["payload"])
            items.append(
                {
                    "event_seq": int(event["event_seq"]),
                    "inline_excerpt": _trim(
                        str(payload.get("inline_excerpt") or payload.get("raw_text") or ""),
                        240,
                    ),
                }
            )
            if len(items) >= 5:
                break
        return items

    def _relevant_artifact_refs(self, task_context: TaskExecutionContext, ingress_artifact_refs: list[str]) -> list[str]:
        refs: list[str] = []
        for artifact_ref in ingress_artifact_refs:
            if artifact_ref not in refs:
                refs.append(artifact_ref)
        for artifact_ref in self.planning.latest_plan_artifact_refs(task_context.task_id, limit=3):
            if artifact_ref not in refs:
                refs.append(artifact_ref)
        for artifact in reversed(self.store.list_artifacts(task_id=task_context.task_id, limit=30)):
            if artifact.kind.startswith("context.pack/"):
                continue
            if artifact.artifact_id not in refs:
                refs.append(artifact.artifact_id)
            if len(refs) >= 10:
                break
        return refs

    def _store_ingress_artifact(
        self,
        *,
        task_context: TaskExecutionContext,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str | None:
        if self.artifact_store is None:
            return None
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=task_context.task_id,
            step_id=task_context.step_id,
            kind="ingress.payload/v1",
            uri=uri,
            content_hash=content_hash,
            producer="provider_input",
            retention_class="task",
            trust_tier="observed",
            metadata=metadata,
        )
        return artifact.artifact_id

    def _store_context_pack(self, *, task_context: TaskExecutionContext, pack: Any) -> str | None:
        if not pack.artifact_uri:
            return None
        artifact = self.store.create_artifact(
            task_id=task_context.task_id,
            step_id=task_context.step_id,
            kind="context.pack/v2",
            uri=str(pack.artifact_uri),
            content_hash=str(pack.artifact_hash or pack.pack_hash),
            producer="provider_input",
            retention_class="audit",
            trust_tier="derived",
            metadata={"pack_hash": pack.pack_hash, "conversation_id": task_context.conversation_id},
        )
        self.store.append_event(
            event_type="context.pack.compiled",
            entity_type="task",
            entity_id=task_context.task_id,
            task_id=task_context.task_id,
            step_id=task_context.step_id,
            actor="kernel",
            payload={"artifact_ref": artifact.artifact_id, "pack_hash": pack.pack_hash, "kind": "context.pack/v2"},
        )
        return artifact.artifact_id

    def _update_attempt_ingress_metadata(self, step_attempt_id: str, normalized: dict[str, Any]) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context or {})
        ingress = dict(context.get("ingress_metadata", {}) or {})
        ingress.update(
            {
                "ingress_artifact_refs": list(normalized["ingress_artifact_refs"]),
                "inline_excerpt": normalized["inline_excerpt"],
                "detected_payload_kinds": list(normalized["detected_payload_kinds"]),
                "original_input_hash": normalized["original_input_hash"],
            }
        )
        context["ingress_metadata"] = ingress
        self.store.update_step_attempt(step_attempt_id, context=context)

    def _render_message(
        self,
        *,
        projection_payload: dict[str, Any],
        context_pack: dict[str, Any],
        current_request: str,
        normalized_prompt: str,
        ingress_artifact_refs: list[str],
    ) -> str:
        lines = [
            "<conversation_projection>",
            projection_payload.get("summary", ""),
            "</conversation_projection>",
            "",
            "<context_pack>",
            f"task={context_pack.get('task_summary', {})}",
            f"step={context_pack.get('step_summary', {})}",
            f"policy={context_pack.get('policy_summary', {})}",
            f"working_state={context_pack.get('working_state', {})}",
            f"selected_beliefs={context_pack.get('selected_beliefs', [])}",
            f"retrieval_memory={context_pack.get('retrieval_memory', [])}",
            f"relevant_artifact_refs={context_pack.get('relevant_artifact_refs', [])}",
            f"ingress_artifact_refs={context_pack.get('ingress_artifact_refs', [])}",
            f"session_projection_ref={context_pack.get('session_projection_ref')}",
            "</context_pack>",
            "",
            "<current_request>",
            current_request,
            "</current_request>",
        ]
        if ingress_artifact_refs:
            lines.extend(
                [
                    "",
                    "<artifact_usage>",
                    "Large payloads were materialized as artifacts. Use the listed refs instead of assuming the full original text is inline.",
                    f"artifact_refs={ingress_artifact_refs}",
                    "</artifact_usage>",
                ]
            )
        if normalized_prompt and normalized_prompt != current_request:
            lines.extend(["", "<normalized_prompt>", _trim(normalized_prompt, 1600), "</normalized_prompt>"])
        return "\n".join(str(line) for line in lines if line is not None)


__all__ = ["ProviderInputCompiler"]
