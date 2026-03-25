from __future__ import annotations

import re
import time
from dataclasses import asdict
from typing import Any, cast

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.compiler.compiler import ContextCompiler
from hermit.kernel.context.memory.anti_pattern import AntiPatternService
from hermit.kernel.context.memory.confidence import ConfidenceDecayService
from hermit.kernel.context.memory.decay import MemoryDecayService
from hermit.kernel.context.memory.embeddings import EmbeddingService
from hermit.kernel.context.memory.episodic import EpisodicMemoryService
from hermit.kernel.context.memory.lineage import MemoryLineageService
from hermit.kernel.context.memory.memory_quality import MemoryQualityService
from hermit.kernel.context.memory.procedural import ProceduralMemoryService
from hermit.kernel.context.memory.reranker import CrossEncoderReranker
from hermit.kernel.context.memory.retrieval import HybridRetrievalService
from hermit.kernel.context.models.context import (
    CompiledProviderInput,
    TaskExecutionContext,
    WorkingStateSnapshot,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.ledger.journal.store_support import sha256_hex as _sha256_hex
from hermit.kernel.task.projections.conversation import ConversationProjectionService
from hermit.kernel.task.projections.projections import ProjectionService
from hermit.kernel.task.services.planning import PlanningService
from hermit.kernel.task.state.continuation import build_continuation_guidance

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

        # Full memory service wiring
        quality_service = MemoryQualityService()
        embedding_service = EmbeddingService()
        confidence_service = ConfidenceDecayService()
        lineage_service = MemoryLineageService()
        reranker = CrossEncoderReranker()

        retrieval_service = HybridRetrievalService(
            quality_service=quality_service,
            embedding_service=embedding_service,
            confidence_service=confidence_service,
            lineage_service=lineage_service,
            reranker=reranker,
        )

        # Episodic and procedural services for context enrichment
        episodic_service = EpisodicMemoryService()
        procedural_service = ProceduralMemoryService()
        anti_pattern_service = AntiPatternService(lineage_service=lineage_service)
        decay_service = MemoryDecayService()

        self.context_compiler = ContextCompiler(
            artifact_store=artifact_store,
            retrieval_service=retrieval_service,
            store=store,
            episodic_service=episodic_service,
            procedural_service=procedural_service,
            anti_pattern_service=anti_pattern_service,
            decay_service=decay_service,
        )
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
                replacement = f"[artifact:{artifact_ref}] fenced code block" + (
                    f" ({language})" if language else ""
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
        carry_forward = self._carry_forward(task_context, task_projection)
        continuation_guidance_obj = build_continuation_guidance(
            current_request=normalized["inline_excerpt"],
            anchor=carry_forward,
        )
        continuation_guidance = (
            continuation_guidance_obj.to_payload() if continuation_guidance_obj.has_anchor else None
        )
        recent_result_summary = _trim(
            str(task_projection.get("topic", {}).get("summary", "") or ""), 200
        )

        blackboard_entries = self._query_blackboard_entries(task_context.task_id)

        pack = self.context_compiler.compile(
            context=task_context,
            working_state=WorkingStateSnapshot(
                goal_summary=_trim(raw_text or final_prompt, 400),
                open_loops=[
                    _trim(item, 200) for item in projection_payload.get("open_loops", [])[:8]
                ],
                recent_results=[recent_result_summary] if recent_result_summary else [],
                planning_mode=bool(planning_state.planning_mode),
                candidate_plan_refs=list(planning_state.candidate_plan_refs),
                selected_plan_ref=str(planning_state.selected_plan_ref or ""),
                plan_status=str(planning_state.plan_status or "none"),
            ),
            beliefs=self.store.list_beliefs(
                task_id=task_context.task_id, status="active", limit=200
            ),
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
            carry_forward=carry_forward,
            continuation_guidance=continuation_guidance,
            recent_notes=notes,
            relevant_artifact_refs=self._relevant_artifact_refs(
                task_context, normalized["ingress_artifact_refs"]
            ),
            ingress_artifact_refs=list(normalized["ingress_artifact_refs"]),
            focus_summary=self._focus_summary(task_context, projection_payload),
            bound_ingress_deltas=self._bound_ingress_deltas(task_context),
            session_projection_ref=projection_payload.get("artifact_ref"),
            blackboard_entries=blackboard_entries,
        )
        context_pack_ref = self._store_context_pack(task_context=task_context, pack=pack)
        working_state_ref = self._store_working_state(
            task_context=task_context,
            pack=pack,
            context_pack_ref=context_pack_ref,
        )
        self.store.update_step_attempt(
            task_context.step_attempt_id,
            context_pack_ref=context_pack_ref,
            working_state_ref=working_state_ref,
            executor_mode="compiled_provider_input",
        )
        pack_payload = pack.to_payload()
        pack_payload["active_steerings"] = self._active_steerings(task_context.task_id)
        # Clear input_dirty now that steerings have been compiled into context
        _attempt = self.store.get_step_attempt(task_context.step_attempt_id)
        self.store.update_step_attempt(
            task_context.step_attempt_id,
            context={
                **(_attempt.context or {} if _attempt else {}),
                "input_dirty": False,
            },
        )
        compiled_text = self._render_message(
            projection_payload=projection_payload,
            context_pack=pack_payload,
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

    def _focus_summary(
        self, task_context: TaskExecutionContext, projection_payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        focus_task_id = str(projection_payload.get("focus_task_id", "") or "").strip()
        if not focus_task_id:
            return None
        focus_reason = str(projection_payload.get("focus_reason", "") or "").strip()
        open_tasks = list(projection_payload.get("open_tasks", []) or [])
        focus_entry = next(
            (
                dict(item)
                for item in open_tasks
                if str(dict(item).get("task_id", "") or "").strip() == focus_task_id
            ),
            None,
        )
        if focus_entry is None:
            task = self.store.get_task(focus_task_id)
            if task is None:
                return None
            focus_entry = {
                "task_id": task.task_id,
                "title": str(task.title or ""),
                "status": str(task.status or ""),
            }
        return {
            "task_id": focus_task_id,
            "title": str(focus_entry.get("title", "") or ""),
            "status": str(focus_entry.get("status", "") or ""),
            "reason": focus_reason,
            "is_current_task": focus_task_id == task_context.task_id,
        }

    def _bound_ingress_deltas(self, task_context: TaskExecutionContext) -> list[dict[str, Any]]:
        attempt = self.store.get_step_attempt(task_context.step_attempt_id)
        if attempt is None:
            return []
        context = dict(attempt.context or {})
        latest_bound_ingress_id = str(context.get("latest_bound_ingress_id", "") or "").strip()
        last_compiled_ingress_id = str(context.get("last_compiled_ingress_id", "") or "").strip()
        if latest_bound_ingress_id and latest_bound_ingress_id == last_compiled_ingress_id:
            return []
        deltas: list[dict[str, Any]] = []
        for ingress in self.store.list_ingresses(task_id=task_context.task_id, limit=20):
            if ingress.status != "bound":
                continue
            if last_compiled_ingress_id and ingress.ingress_id == last_compiled_ingress_id:
                break
            deltas.append(
                {
                    "ingress_id": ingress.ingress_id,
                    "resolution": ingress.resolution,
                    "actor_principal_id": ingress.actor_principal_id,
                    "source_channel": ingress.source_channel,
                    "raw_excerpt": _trim(ingress.raw_text, 240),
                    "prompt_excerpt": _trim(str(ingress.prompt_ref or ""), 240),
                    "reply_to_ref": ingress.reply_to_ref,
                    "quoted_message_ref": ingress.quoted_message_ref,
                    "referenced_artifact_refs": list(ingress.referenced_artifact_refs),
                    "confidence": ingress.confidence,
                    "margin": ingress.margin,
                }
            )
            if len(deltas) >= 5:
                break
        deltas.reverse()
        return deltas

    @staticmethod
    def _carry_forward(
        task_context: TaskExecutionContext, task_projection: dict[str, Any]
    ) -> dict[str, Any] | None:
        ingress_anchor = dict(task_context.ingress_metadata.get("continuation_anchor", {}) or {})
        if ingress_anchor:
            return ingress_anchor
        projection_anchor = dict(
            task_projection.get("projection", {}).get("task", {}).get("continuation_anchor", {})
            or {}
        )
        return projection_anchor or None

    def _relevant_artifact_refs(
        self, task_context: TaskExecutionContext, ingress_artifact_refs: list[str]
    ) -> list[str]:
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

    def _query_blackboard_entries(self, task_id: str) -> list[dict[str, Any]]:
        """Query active blackboard entries for a task, with graceful fallback."""
        if not hasattr(self.store, "query_blackboard_entries"):
            return []
        try:
            entries = self.store.query_blackboard_entries(task_id=task_id, status="active")
            return [
                {
                    "entry_id": e.entry_id,
                    "entry_type": e.entry_type,
                    "content": dict(e.content) if e.content else {},
                    "confidence": e.confidence,
                    "step_id": e.step_id,
                }
                for e in entries
            ]
        except Exception:
            return []

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
            kind="context.pack/v3",
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
            payload={
                "artifact_ref": artifact.artifact_id,
                "pack_hash": pack.pack_hash,
                "kind": "context.pack/v3",
            },
        )
        return artifact.artifact_id

    def _store_working_state(
        self,
        *,
        task_context: TaskExecutionContext,
        pack: Any,
        context_pack_ref: str | None,
    ) -> str | None:
        if self.artifact_store is None:
            return None
        payload = {
            "kind": "working_state/v1",
            "task_id": task_context.task_id,
            "step_id": task_context.step_id,
            "step_attempt_id": task_context.step_attempt_id,
            "working_state": dict(pack.working_state or {}),
            "pack_hash": pack.pack_hash,
        }
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=task_context.task_id,
            step_id=task_context.step_id,
            kind="working_state/v1",
            uri=uri,
            content_hash=content_hash,
            producer="provider_input",
            retention_class="audit",
            trust_tier="derived",
            metadata={"pack_hash": pack.pack_hash, "conversation_id": task_context.conversation_id},
            lineage_ref=context_pack_ref,
        )
        self.store.append_event(
            event_type="working_state.materialized",
            entity_type="step_attempt",
            entity_id=task_context.step_attempt_id,
            task_id=task_context.task_id,
            step_id=task_context.step_id,
            actor="kernel",
            payload={
                "artifact_ref": artifact.artifact_id,
                "pack_hash": pack.pack_hash,
                "kind": "working_state/v1",
            },
        )
        return artifact.artifact_id

    def _update_attempt_ingress_metadata(
        self, step_attempt_id: str, normalized: dict[str, Any]
    ) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context or {})
        was_dirty = bool(context.get("input_dirty"))
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
        context["phase"] = "planning"
        if was_dirty:
            context["input_dirty"] = False
            context["last_recompiled_at"] = time.time()
            context["last_compiled_ingress_id"] = str(
                context.get("latest_bound_ingress_id", "") or ""
            )
        self.store.update_step_attempt(step_attempt_id, context=context)
        if was_dirty:
            self.store.append_event(
                event_type="step_attempt.recompiled",
                entity_type="step_attempt",
                entity_id=step_attempt_id,
                task_id=attempt.task_id,
                step_id=attempt.step_id,
                actor="kernel",
                payload={
                    "step_attempt_id": step_attempt_id,
                    "latest_bound_ingress_id": context.get("latest_bound_ingress_id"),
                    "latest_note_event_seq": context.get("latest_note_event_seq"),
                },
            )

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
            f"carry_forward={context_pack.get('carry_forward')}",
            f"continuation_guidance={context_pack.get('continuation_guidance')}",
            f"selected_beliefs={context_pack.get('selected_beliefs', [])}",
            f"retrieval_memory={context_pack.get('retrieval_memory', [])}",
            f"relevant_artifact_refs={context_pack.get('relevant_artifact_refs', [])}",
            f"ingress_artifact_refs={context_pack.get('ingress_artifact_refs', [])}",
            f"focus_summary={context_pack.get('focus_summary')}",
            f"bound_ingress_deltas={context_pack.get('bound_ingress_deltas', [])}",
            f"session_projection_ref={context_pack.get('session_projection_ref')}",
            "</context_pack>",
        ]
        continuation_guidance: dict[str, Any] = cast(
            dict[str, Any], context_pack.get("continuation_guidance") or {}
        )
        rendered_guidance = self._render_continuation_guidance(continuation_guidance)
        if rendered_guidance:
            lines.extend(
                ["", "<continuation_guidance>", rendered_guidance, "</continuation_guidance>"]
            )
        lines.extend(["", "<current_request>", current_request, "</current_request>"])
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
            lines.extend(
                ["", "<normalized_prompt>", _trim(normalized_prompt, 1600), "</normalized_prompt>"]
            )
        active_steerings: list[dict[str, Any]] = context_pack.get("active_steerings", [])
        if active_steerings:
            lines.extend(["", "<steering_directives>"])
            lines.append(
                "You MUST incorporate these operator steering directives into your response:"
            )
            for s in active_steerings:
                lines.append(
                    f"- [{s.get('directive_id', '?')}] type={s.get('steering_type', '?')}: "
                    f"{s.get('directive', '')}"
                )
            lines.append("</steering_directives>")
        return "\n".join(str(line) for line in lines if line is not None)

    def _active_steerings(self, task_id: str) -> list[dict[str, Any]]:
        """Fetch active steerings for task, auto-acknowledge pending ones."""
        if not hasattr(self.store, "active_steerings_for_task"):
            return []
        directives = self.store.active_steerings_for_task(task_id)
        items: list[dict[str, Any]] = []
        for d in directives:
            if d.disposition == "pending":
                self.store.update_steering_disposition(d.directive_id, "acknowledged")
                d.disposition = "acknowledged"
            items.append(
                {
                    "directive_id": d.directive_id,
                    "steering_type": d.steering_type,
                    "directive": d.directive,
                    "disposition": d.disposition,
                    "issued_by": d.issued_by,
                    "created_at": d.created_at,
                }
            )
        return items

    def check_context_staleness(self, step_attempt_id: str) -> bool:
        """Check whether the compiled context for a step attempt is stale.

        Staleness is detected when new steerings, notes, or ingresses have
        arrived since the last compilation.  When stale, sets ``input_dirty``
        to ``True`` on the step attempt context and returns ``True``.
        """
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return False
        context = dict(attempt.context or {})

        # Already marked dirty by an external signal (steering, controller).
        if context.get("input_dirty"):
            return True

        # Detect new bound ingresses since last compile.
        latest_bound = str(context.get("latest_bound_ingress_id", "") or "").strip()
        last_compiled = str(context.get("last_compiled_ingress_id", "") or "").strip()
        if latest_bound and latest_bound != last_compiled:
            context["input_dirty"] = True
            self.store.update_step_attempt(step_attempt_id, context=context)
            return True

        # Detect new notes since last compile.
        latest_note_seq = context.get("latest_note_event_seq")
        last_compiled_note_seq = context.get("last_compiled_note_event_seq")
        if latest_note_seq is not None and latest_note_seq != last_compiled_note_seq:
            context["input_dirty"] = True
            self.store.update_step_attempt(step_attempt_id, context=context)
            return True

        return False

    def _render_continuation_guidance(self, guidance: dict[str, Any]) -> str:
        if not guidance or not guidance.get("has_anchor"):
            return ""
        mode = str(guidance.get("mode", "") or "plain_new_task")
        anchor_task_id = str(guidance.get("anchor_task_id", "") or "")
        anchor_user_request = _trim(str(guidance.get("anchor_user_request", "") or ""), 240)
        anchor_goal = _trim(str(guidance.get("anchor_goal", "") or ""), 240)
        outcome_summary = _trim(str(guidance.get("outcome_summary", "") or ""), 320)

        lines = [
            f"anchor_task_id={anchor_task_id}",
            "This is a new task with carry-forward context from a completed anchor task.",
        ]
        if anchor_user_request:
            lines.append(f"Anchor original user request: {anchor_user_request}")
        if anchor_goal:
            lines.append(f"Anchor goal: {anchor_goal}")
        if outcome_summary:
            lines.append(f"Anchor outcome summary: {outcome_summary}")

        if mode == "explicit_topic_shift":
            lines.append(
                "The current request explicitly starts a new topic. Ignore the anchor when deciding intent and answer it as a new request."
            )
        elif mode == "strong_topic_shift":
            lines.append(
                "The current request carries strong new semantics and does not match the anchor topic. Treat it as a new topic unless the user clearly refers back to the anchor."
            )
        elif mode == "anchor_correction":
            lines.append(
                "The current request is short and ambiguous or corrective. Prefer interpreting it as a clarification or correction of the anchor task, and do not drift into unrelated semantics."
            )
        else:
            lines.append(
                "Use the anchor as background context, but treat the current request as a normal new task unless the user is clearly clarifying the anchor."
            )
        lines.append(
            "Interpretation priority: explicit topic shift > strong new-topic semantics > anchor clarification/correction > ordinary new task."
        )
        return "\n".join(lines)


__all__ = ["ProviderInputCompiler"]
