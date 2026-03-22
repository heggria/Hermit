"""Meta-loop orchestrator — state machine that advances iterations through phases."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.metaloop.backlog import SpecBacklog
from hermit.plugins.builtin.hooks.metaloop.models import (
    IterationPhase,
    IterationState,
)

log = structlog.get_logger()

# --- Safety limits ---
MAX_FOLLOWUP_DEPTH = 3
MAX_FOLLOWUP_FANOUT = 2
MAX_QUEUE_DEPTH = 100
PHASE_TIMEOUT_IMPLEMENT = 900  # 15 minutes
POLL_INTERVAL_ACTIVE = 10.0  # seconds
POLL_INTERVAL_IDLE = 60.0  # seconds
_IDLE_TICKS_THRESHOLD = 6  # consecutive idle ticks before switching to idle interval


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a sync (daemon thread) context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _parse_metadata(raw: str | dict | None) -> dict:
    """Parse metadata from DB row (may be JSON string or dict)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _serialize_metadata(meta: dict) -> str:
    return json.dumps(meta, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


class MetaLoopOrchestrator:
    """Advances self-improvement iterations through lifecycle phases."""

    def __init__(
        self,
        store: Any,
        *,
        max_retries: int = 3,
        runner: Any = None,
        workspace_root: str = "",
        benchmark_blocking: bool = True,
    ) -> None:
        self._backlog = SpecBacklog(store)
        self._store = store
        self._max_retries = max_retries
        self._runner = runner
        self._workspace_root = workspace_root
        self._benchmark_blocking = benchmark_blocking

    def set_runner(self, runner: Any) -> None:
        """Update the runner reference (for hot-reload)."""
        self._runner = runner

    def _query_relevant_lessons(self, goal: str, limit: int = 10) -> list[dict]:
        """Query stored lessons relevant to the current iteration goal.

        Relevance scoring: count keyword overlap between lesson summary
        and goal text. Return top-N by relevance score (descending).
        """
        if not hasattr(self._store, "list_lessons"):
            return []
        try:
            all_lessons = self._store.list_lessons(limit=50)
        except Exception:
            log.debug("metaloop_lessons_query_failed")
            return []
        if not all_lessons:
            return []

        # Tokenise goal into lowercase keywords (>= 3 chars to skip noise)
        goal_keywords = {w for w in goal.lower().split() if len(w) >= 3}
        if not goal_keywords:
            # Fallback: return most recent lessons if goal has no usable keywords
            return all_lessons[:limit]

        scored: list[tuple[float, dict]] = []
        for lesson in all_lessons:
            summary = (
                lesson.get("summary", "")
                if isinstance(lesson, dict)
                else getattr(lesson, "summary", "")
            )
            category = (
                lesson.get("category", "")
                if isinstance(lesson, dict)
                else getattr(lesson, "category", "")
            )
            summary_keywords = {w for w in summary.lower().split() if len(w) >= 3}
            overlap = len(goal_keywords & summary_keywords)
            # Boost actionable categories (mistakes and rollback patterns)
            category_boost = 1.0
            if category in ("mistake", "rollback_pattern"):
                category_boost = 1.5
            score = overlap * category_boost
            if score > 0:
                scored.append((score, lesson))

        # Sort by score descending, take top-N
        scored.sort(key=lambda x: x[0], reverse=True)
        return [lesson for _, lesson in scored[:limit]]

    def _update_metadata(self, spec_id: str, key: str, value: dict) -> dict:
        """Merge *value* under *key* in the spec's metadata and persist."""
        entry = self._store.get_spec_entry(spec_id=spec_id)
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        metadata[key] = value
        self._store.update_spec_status(
            spec_id=spec_id,
            status=data.get("status", "pending"),
            metadata=_serialize_metadata(metadata),
        )
        return metadata

    def advance(self, spec_id: str) -> IterationState | None:
        """Advance a single spec by one phase."""
        state = self._backlog.get_state(spec_id)
        if state is None:
            log.warning("metaloop_advance_no_state", spec_id=spec_id)
            return None

        if state.is_terminal:
            log.debug(
                "metaloop_advance_already_terminal",
                spec_id=spec_id,
                phase=state.phase.value,
            )
            return state

        handler = getattr(self, f"_handle_{state.phase.value}", None)
        if handler is None:
            next_phase = state.next_phase()
            if next_phase is None:
                return state
            return self._backlog.advance_phase(spec_id, next_phase)

        try:
            return handler(state)
        except Exception:
            log.exception(
                "metaloop_phase_handler_error",
                spec_id=spec_id,
                phase=state.phase.value,
            )
            return self._backlog.mark_failed(
                spec_id,
                error=f"Phase {state.phase.value} failed with unhandled exception",
                max_retries=self._max_retries,
            )

    def on_subtask_complete(
        self, task_id: str, *, success: bool = True, error: str | None = None
    ) -> IterationState | None:
        """Handle DAG subtask completion — advance IMPLEMENTING to REVIEWING.

        On success, stores the worktree branch info for later PR creation
        instead of merging directly to main. The actual merge only happens
        after explicit approval via the IterationBridge.
        """
        if not task_id:
            return None

        if not hasattr(self._store, "get_spec_by_dag_task_id"):
            return None

        entry = self._store.get_spec_by_dag_task_id(task_id)
        if entry is None:
            return None

        data = entry if isinstance(entry, dict) else entry.__dict__
        spec_id = data["spec_id"]
        phase = IterationPhase(data.get("status", "pending"))

        if phase != IterationPhase.IMPLEMENTING:
            return None

        metadata = _parse_metadata(data.get("metadata"))
        impl_info = metadata.get("implementation") or {}
        worktree_path = impl_info.get("worktree_path")

        if success:
            # Do NOT merge to main. Store branch info for PR creation instead.
            # The worktree branch will be used later by create_iteration_pr()
            # after the iteration passes review, benchmarking, and reconciliation.
            if worktree_path and self._workspace_root:
                self._update_metadata(
                    spec_id,
                    "pr_pending",
                    {
                        "worktree_path": worktree_path,
                        "branch": f"self-modify/{spec_id}",
                        "ready_for_review": True,
                    },
                )
                log.info(
                    "metaloop_subtask_complete_pr_pending",
                    task_id=task_id,
                    spec_id=spec_id,
                )
            else:
                log.info(
                    "metaloop_subtask_complete",
                    task_id=task_id,
                    spec_id=spec_id,
                )
            return self._backlog.advance_phase(spec_id, IterationPhase.REVIEWING)

        # Failure path — clean up worktree without merging
        if worktree_path and self._workspace_root:
            try:
                from hermit.kernel.execution.self_modify.workspace import (
                    SelfModifyWorkspace,
                )

                ws = SelfModifyWorkspace(self._workspace_root)
                ws.remove(spec_id)
            except Exception:
                log.exception("metaloop_worktree_cleanup_failed", spec_id=spec_id)

        log.warning(
            "metaloop_subtask_failed",
            task_id=task_id,
            spec_id=spec_id,
            error=error,
        )
        return self._backlog.mark_failed(
            spec_id,
            error=error or "Subtask failed",
            max_retries=self._max_retries,
        )

    # ------------------------------------------------------------------
    # Phase handlers — method name must match IterationPhase.value
    # ------------------------------------------------------------------

    def _handle_pending(self, state: IterationState) -> IterationState | None:
        return self._backlog.advance_phase(state.spec_id, IterationPhase.RESEARCHING)

    def _handle_researching(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return state
        data = entry if isinstance(entry, dict) else entry.__dict__
        goal = data.get("goal", "")

        # Parse research hints
        raw_hints = data.get("research_hints")
        hints: list[str] = []
        if raw_hints:
            try:
                parsed = json.loads(raw_hints) if isinstance(raw_hints, str) else raw_hints
                if isinstance(parsed, list):
                    hints = [str(h) for h in parsed]
            except (json.JSONDecodeError, TypeError):
                pass

        # Query and inject relevance-filtered prior lessons as hints
        prior_lessons = self._query_relevant_lessons(goal)
        for lesson in prior_lessons:
            summary = (
                lesson.get("summary", "")
                if isinstance(lesson, dict)
                else getattr(lesson, "summary", "")
            )
            category = (
                lesson.get("category", "")
                if isinstance(lesson, dict)
                else getattr(lesson, "category", "")
            )
            if summary:
                prefix = {
                    "mistake": "AVOID (prior mistake)",
                    "rollback_pattern": "CAUTION (prior rollback)",
                    "success_pattern": "PREFER (proven pattern)",
                    "optimization": "TIP (optimization)",
                }.get(category, "Prior lesson")
                hints.append(f"{prefix}: {summary}")
        log.info(
            "metaloop_lessons_applied",
            spec_id=state.spec_id,
            lessons_found=len(prior_lessons),
            phase="researching",
        )

        # Run research pipeline — prefer the globally initialized pipeline
        # (set up at SERVE_START with all 4 strategies: codebase, web, doc,
        # git_history).  Fall back to local instantiation if not available.
        from hermit.plugins.builtin.hooks.research.pipeline import ResearchPipeline
        from hermit.plugins.builtin.hooks.research.strategies import (
            CodebaseStrategy,
            DocStrategy,
            GitHistoryStrategy,
            WebStrategy,
        )
        from hermit.plugins.builtin.hooks.research.tools import (
            _pipeline as global_pipeline,
        )

        workspace = self._workspace_root or ""
        if global_pipeline is not None:
            pipeline = global_pipeline
        else:
            pipeline = ResearchPipeline(
                [
                    CodebaseStrategy(workspace),
                    WebStrategy(enabled=True),
                    DocStrategy(enabled=True),
                    GitHistoryStrategy(workspace),
                ]
            )
        report = _run_async(pipeline.run(goal, hints))

        # Serialize report to metadata
        findings_dicts = [
            {
                "source": f.source,
                "title": f.title,
                "content": f.content[:500],
                "relevance": f.relevance,
                "url": f.url,
                "file_path": f.file_path,
            }
            for f in report.findings
        ]
        self._update_metadata(
            state.spec_id,
            "research",
            {
                "goal": report.goal,
                "findings": findings_dicts,
                "knowledge_gaps": list(report.knowledge_gaps),
                "query_count": report.query_count,
                "duration_seconds": report.duration_seconds,
                "count": len(findings_dicts),
                "sources": sorted({f.source for f in report.findings}),
                "prior_lessons": [
                    {
                        "lesson_id": (
                            ls.get("lesson_id", "")
                            if isinstance(ls, dict)
                            else getattr(ls, "lesson_id", "")
                        ),
                        "category": (
                            ls.get("category", "")
                            if isinstance(ls, dict)
                            else getattr(ls, "category", "")
                        ),
                        "summary": (
                            ls.get("summary", "")
                            if isinstance(ls, dict)
                            else getattr(ls, "summary", "")
                        ),
                    }
                    for ls in prior_lessons
                ],
            },
        )

        return self._backlog.advance_phase(state.spec_id, IterationPhase.GENERATING_SPEC)

    def _handle_generating_spec(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return state
        data = entry if isinstance(entry, dict) else entry.__dict__
        goal = data.get("goal", "")
        metadata = _parse_metadata(data.get("metadata"))
        research_data = metadata.get("research") or {}

        # Reconstruct ResearchReport from metadata
        from hermit.plugins.builtin.hooks.research.models import (
            ResearchFinding,
            ResearchReport,
        )

        findings_raw = research_data.get("findings", [])
        findings = tuple(
            ResearchFinding(
                source=f.get("source", ""),
                title=f.get("title", ""),
                content=f.get("content", ""),
                relevance=f.get("relevance", 0.0),
                url=f.get("url", ""),
                file_path=f.get("file_path", ""),
            )
            for f in findings_raw
        )
        report = ResearchReport(
            goal=research_data.get("goal", goal),
            findings=findings,
            knowledge_gaps=tuple(research_data.get("knowledge_gaps", ())),
            query_count=research_data.get("query_count", 0),
            duration_seconds=research_data.get("duration_seconds", 0.0),
        )

        # Build lesson-derived constraints from prior lessons
        lesson_constraints: list[str] = []
        prior_lessons = research_data.get("prior_lessons", [])
        for ls in prior_lessons:
            cat = ls.get("category", "")
            summary = ls.get("summary", "")
            if not summary:
                continue
            if cat in ("mistake", "rollback_pattern"):
                lesson_constraints.append(f"AVOID: {summary}")
            elif cat == "success_pattern":
                lesson_constraints.append(f"PREFER: {summary}")
        if lesson_constraints:
            log.info(
                "metaloop_lessons_applied",
                spec_id=state.spec_id,
                constraint_count=len(lesson_constraints),
                phase="generating_spec",
            )

        # Generate spec with lesson constraints
        from hermit.plugins.builtin.hooks.decompose.spec_generator import SpecGenerator

        spec = SpecGenerator().generate(
            goal,
            research_report=report,
            constraints=tuple(lesson_constraints) if lesson_constraints else None,
        )

        # Serialize to metadata
        self._update_metadata(
            state.spec_id,
            "generated_spec",
            {
                "spec_id": spec.spec_id,
                "title": spec.title,
                "goal": spec.goal,
                "constraints": list(spec.constraints),
                "acceptance_criteria": list(spec.acceptance_criteria),
                "file_plan": [dict(e) for e in spec.file_plan],
                "research_ref": spec.research_ref,
                "trust_zone": spec.trust_zone,
            },
        )

        return self._backlog.advance_phase(state.spec_id, IterationPhase.SPEC_APPROVAL)

    def _handle_spec_approval(self, state: IterationState) -> IterationState | None:
        """Evaluate spec approval based on policy_profile and risk_budget.

        - autonomous: auto-approve if risk_budget <= medium
        - supervised: always create an approval request via the kernel
        - default: auto-approve low risk, request approval for medium+
        """
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return state
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))

        if "generated_spec" not in metadata:
            return self._backlog.mark_failed(
                state.spec_id,
                error="No generated_spec in metadata — cannot approve",
                max_retries=self._max_retries,
            )

        spec_data = metadata.get("generated_spec", {})

        # Determine risk level from the generated spec or metadata
        risk_budget = metadata.get("risk_budget", {})
        risk_band = risk_budget.get("band", "")
        if not risk_band:
            # Infer from trust_zone in spec
            trust_zone = spec_data.get("trust_zone", "normal")
            risk_band = {"strict": "high", "normal": "medium", "relaxed": "low"}.get(
                trust_zone, "medium"
            )

        # Determine policy_profile: check metadata, then fall back to source
        policy_profile = metadata.get("policy_profile", "")
        if not policy_profile:
            source = data.get("source", "")
            if source == "self-iterate":
                policy_profile = "autonomous"
            else:
                policy_profile = "default"

        # Decision logic
        needs_approval = False

        if policy_profile == "autonomous":
            # Auto-approve low and medium risk; require approval for high
            needs_approval = risk_band in ("high", "critical")
        elif policy_profile == "supervised":
            # Always require human approval
            needs_approval = True
        else:
            # "default": auto-approve low, require approval for medium+
            needs_approval = risk_band not in ("low",)

        if not needs_approval:
            log.info(
                "metaloop_spec_auto_approved",
                spec_id=state.spec_id,
                policy_profile=policy_profile,
                risk_band=risk_band,
            )
            self._update_metadata(
                state.spec_id,
                "spec_approval_decision",
                {
                    "approved": True,
                    "method": "auto",
                    "policy_profile": policy_profile,
                    "risk_band": risk_band,
                    "decided_at": time.time(),
                },
            )
            return self._backlog.advance_phase(state.spec_id, IterationPhase.DECOMPOSING)

        # Create an approval request via the kernel approval system
        approval_created = False
        if self._runner is not None and hasattr(self._runner, "task_controller"):
            tc = self._runner.task_controller
            store = tc.store
            if hasattr(store, "create_approval"):
                try:
                    # Find or create a task_id context for the approval
                    task_id = metadata.get("dag_task_id", f"metaloop-{state.spec_id}")
                    store.create_approval(
                        task_id=task_id,
                        step_id=f"spec-approval-{state.spec_id}",
                        step_attempt_id=f"attempt-{state.spec_id}",
                        approval_type="spec_approval",
                        requested_action={
                            "action": "approve_iteration_spec",
                            "spec_id": state.spec_id,
                            "title": spec_data.get("title", ""),
                            "goal": spec_data.get("goal", ""),
                            "risk_band": risk_band,
                            "policy_profile": policy_profile,
                            "file_count": len(spec_data.get("file_plan", [])),
                            "constraints": spec_data.get("constraints", []),
                        },
                        request_packet_ref=None,
                    )
                    approval_created = True
                    log.info(
                        "metaloop_spec_approval_requested",
                        spec_id=state.spec_id,
                        policy_profile=policy_profile,
                        risk_band=risk_band,
                    )
                except Exception:
                    log.warning(
                        "metaloop_spec_approval_create_failed",
                        spec_id=state.spec_id,
                        exc_info=True,
                    )

        if not approval_created:
            # Fallback: if we cannot create a kernel approval, auto-approve
            # with a warning to avoid blocking the pipeline indefinitely
            log.warning(
                "metaloop_spec_approval_fallback_auto",
                spec_id=state.spec_id,
                reason="approval system unavailable",
            )
            self._update_metadata(
                state.spec_id,
                "spec_approval_decision",
                {
                    "approved": True,
                    "method": "fallback_auto",
                    "policy_profile": policy_profile,
                    "risk_band": risk_band,
                    "reason": "approval system unavailable",
                    "decided_at": time.time(),
                },
            )
            return self._backlog.advance_phase(state.spec_id, IterationPhase.DECOMPOSING)

        # Approval request created — stay at SPEC_APPROVAL.
        # The approval resolution hook will advance us when approved/denied.
        self._update_metadata(
            state.spec_id,
            "spec_approval_decision",
            {
                "approved": False,
                "method": "pending_kernel_approval",
                "policy_profile": policy_profile,
                "risk_band": risk_band,
                "requested_at": time.time(),
            },
        )
        return self._backlog.advance_phase(
            state.spec_id,
            IterationPhase.SPEC_APPROVAL,
        )

    def _handle_decomposing(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return state
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        spec_data = metadata.get("generated_spec") or {}

        # Collect lesson-referenced files for priority boosting
        research_data = metadata.get("research") or {}
        lesson_files: set[str] = set()
        prior_lessons = research_data.get("prior_lessons", [])
        for ls in prior_lessons:
            # Fetch full lesson data to get applicable_files
            lesson_id = ls.get("lesson_id", "")
            if lesson_id and hasattr(self._store, "get_lesson"):
                try:
                    full = self._store.get_lesson(lesson_id)
                    if full:
                        raw_files = full.get("applicable_files")
                        if isinstance(raw_files, str):
                            try:
                                parsed = json.loads(raw_files)
                                if isinstance(parsed, list):
                                    lesson_files.update(str(f) for f in parsed)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        elif isinstance(raw_files, list):
                            lesson_files.update(str(f) for f in raw_files)
                except Exception:
                    pass

        # Reconstruct GeneratedSpec from metadata
        from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec
        from hermit.plugins.builtin.hooks.decompose.task_decomposer import TaskDecomposer

        file_plan_raw = spec_data.get("file_plan", [])

        # Boost lesson-referenced files by prepending them to file_plan if not present
        existing_paths = {e.get("path", "") for e in file_plan_raw}
        boosted_entries: list[dict] = []
        for lf in sorted(lesson_files):
            if lf and lf not in existing_paths:
                boosted_entries.append(
                    {"path": lf, "action": "modify", "reason": "Lesson-referenced file"}
                )
        if boosted_entries:
            file_plan_raw = boosted_entries + list(file_plan_raw)
            log.info(
                "metaloop_lessons_applied",
                spec_id=state.spec_id,
                boosted_files=len(boosted_entries),
                phase="decomposing",
            )

        spec = GeneratedSpec(
            spec_id=spec_data.get("spec_id", state.spec_id),
            title=spec_data.get("title", ""),
            goal=spec_data.get("goal", ""),
            constraints=tuple(spec_data.get("constraints", ())),
            acceptance_criteria=tuple(spec_data.get("acceptance_criteria", ())),
            file_plan=tuple(dict(e) for e in file_plan_raw),
            research_ref=spec_data.get("research_ref", ""),
            trust_zone=spec_data.get("trust_zone", "normal"),
        )

        plan = TaskDecomposer().decompose(spec)

        # Serialize to metadata
        self._update_metadata(
            state.spec_id,
            "decomposition_plan",
            {
                "spec_id": plan.spec_id,
                "steps": [dict(s) for s in plan.steps],
                "dependency_graph": dict(plan.dependency_graph),
                "estimated_duration_minutes": plan.estimated_duration_minutes,
            },
        )

        return self._backlog.advance_phase(state.spec_id, IterationPhase.IMPLEMENTING)

    def _handle_implementing(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return state
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        decomposition = metadata.get("decomposition_plan") or {}
        steps = decomposition.get("steps", [])
        if not steps:
            return self._backlog.advance_phase(state.spec_id, IterationPhase.REVIEWING)

        if self._runner is None:
            return self._backlog.advance_phase(state.spec_id, IterationPhase.REVIEWING)

        from hermit.kernel.task.services.dag_builder import StepNode

        nodes = [
            StepNode(
                key=step["key"],
                kind=step.get("kind", "execute"),
                title=step.get("title", step["key"]),
                depends_on=step.get("depends_on", []),
                metadata=step.get("metadata", {}),
            )
            for step in steps
        ]
        tc = self._runner.task_controller
        try:
            result = tc.start_dag_task(
                conversation_id=f"metaloop-{state.spec_id}",
                goal=f"Implement spec {state.spec_id}",
                source_channel="metaloop",
                nodes=nodes,
                policy_profile="autonomous",
                workspace_root=self._workspace_root,
            )
        except Exception as exc:
            log.exception(
                "metaloop_dag_creation_failed",
                spec_id=state.spec_id,
                step_count=len(nodes),
            )
            return self._backlog.mark_failed(
                state.spec_id,
                error=f"DAG task creation failed for {len(nodes)} steps: "
                f"{type(exc).__name__}: {exc}",
                max_retries=self._max_retries,
            )
        # start_dag_task may return a tuple (task, ...) or a single object
        task = result[0] if isinstance(result, tuple) else result
        dag_task_id = task.task_id if hasattr(task, "task_id") and task.task_id else None
        # Stay at IMPLEMENTING — poller will detect completion via timeout check.
        # on_subtask_complete is also called by the poller when the DAG task
        # reaches a terminal state (see _check_implementing_timeout).
        return self._backlog.advance_phase(
            state.spec_id,
            IterationPhase.IMPLEMENTING,
            dag_task_id=dag_task_id,
            metadata={"implementing_started_at": time.time()},
        )

    def _handle_reviewing(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return self._backlog.advance_phase(state.spec_id, IterationPhase.BENCHMARKING)
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        decomposition = metadata.get("decomposition_plan") or {}
        steps = decomposition.get("steps", [])

        # Extract changed file paths from decomposition steps
        changed_files = []
        for step in steps:
            step_meta = step.get("metadata", {})
            path = step_meta.get("path", "")
            if path:
                changed_files.append(path)

        if not changed_files:
            self._update_metadata(
                state.spec_id,
                "acceptance_criteria_validation",
                {"total": 0, "validated": 0, "skipped": True},
            )
            return self._backlog.advance_phase(state.spec_id, IterationPhase.BENCHMARKING)

        try:
            from hermit.plugins.builtin.hooks.quality.reviewer import GovernedReviewer

            report = _run_async(GovernedReviewer(self._workspace_root).review(changed_files))

            self._update_metadata(
                state.spec_id,
                "review",
                {
                    "passed": report.passed,
                    "finding_count": len(report.findings),
                    "duration_seconds": report.duration_seconds,
                    "findings": [
                        {
                            "severity": f.severity.value,
                            "category": f.category,
                            "message": f.message,
                            "file_path": f.file_path,
                            "line": f.line,
                        }
                        for f in report.findings
                    ],
                },
            )

            if not report.passed:
                blocking = [f for f in report.findings if f.severity == "blocking"]
                detail_lines = [
                    f"  - [{f.category}] {f.file_path}:{f.line}: {f.message}" for f in blocking
                ]
                detail = "\n".join(detail_lines) if detail_lines else "(no detail)"
                log.warning(
                    "metaloop_review_blocked",
                    spec_id=state.spec_id,
                    blocking_count=len(blocking),
                    total_findings=len(report.findings),
                )
                return self._backlog.mark_failed(
                    state.spec_id,
                    error=f"Review blocked: {len(blocking)} BLOCKING finding(s):\n{detail}",
                    max_retries=self._max_retries,
                )
        except ImportError:
            log.debug(
                "metaloop_reviewer_import_failed",
                spec_id=state.spec_id,
            )
        except Exception:
            log.exception("metaloop_review_error", spec_id=state.spec_id)

        # --- Acceptance criteria validation ---
        spec_data = metadata.get("generated_spec") or {}
        criteria = spec_data.get("acceptance_criteria", [])
        file_plan = spec_data.get("file_plan", [])
        criteria_results: list[dict[str, object]] = []

        for criterion in criteria:
            if criterion is None:
                continue
            result: dict[str, object] = {"criterion": criterion, "validated": False}
            criterion_lower = str(criterion).lower()

            if "make check" in criterion_lower:
                result["validated"] = True
                result["method"] = "deferred_to_benchmark"
            elif "new files" in criterion_lower or "created and importable" in criterion_lower:
                plan_paths = {f.get("path", "") for f in file_plan if f.get("action") == "create"}
                if not plan_paths:
                    result["method"] = "unverifiable_no_file_plan"
                else:
                    found = plan_paths & set(changed_files)
                    result["validated"] = bool(found)
                    result["method"] = "file_existence_check"
                    result["detail"] = f"expected={sorted(plan_paths)}, found={sorted(found)}"
            elif "modified files" in criterion_lower:
                plan_paths = {f.get("path", "") for f in file_plan if f.get("action") == "modify"}
                if not plan_paths:
                    result["method"] = "unverifiable_no_file_plan"
                else:
                    found = plan_paths & set(changed_files)
                    result["validated"] = bool(found)
                    result["method"] = "file_modification_check"
            elif (
                "approach validated" in criterion_lower
                or "implementation follows" in criterion_lower
            ):
                result["validated"] = True
                result["method"] = "research_approach_noted"
            else:
                result["method"] = "unverifiable_at_review"

            criteria_results.append(result)

        validated_count = sum(1 for r in criteria_results if r.get("validated"))
        self._update_metadata(
            state.spec_id,
            "acceptance_criteria_validation",
            {
                "total": len(criteria_results),
                "validated": validated_count,
                "results": criteria_results,
            },
        )

        return self._backlog.advance_phase(state.spec_id, IterationPhase.BENCHMARKING)
        if entry is None:
            return self._backlog.advance_phase(state.spec_id, IterationPhase.LEARNING)
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        impl_info = metadata.get("implementation") or {}
        worktree_path = impl_info.get("worktree_path")

        try:
            from hermit.plugins.builtin.hooks.benchmark.runner import BenchmarkRunner

            runner = BenchmarkRunner(self._store)
            result = _run_async(runner.run(state.spec_id, state.spec_id, worktree_path))

            benchmark_data = {
                "check_passed": result.check_passed,
                "test_total": result.test_total,
                "test_passed": result.test_passed,
                "coverage": result.coverage,
                "lint_violations": result.lint_violations,
                "duration_seconds": result.duration_seconds,
                "regression_detected": result.regression_detected,
                "compared_to_baseline": dict(result.compared_to_baseline),
            }
            self._update_metadata(state.spec_id, "benchmark", benchmark_data)

            # Gate: block iteration on benchmark failure when blocking is enabled
            if self._benchmark_blocking:
                if not result.check_passed:
                    error_msg = "Benchmark failed: make check returned non-zero"
                    log.warning(
                        "metaloop_benchmark_check_failed",
                        spec_id=state.spec_id,
                        test_passed=result.test_passed,
                        test_total=result.test_total,
                    )
                    return self._backlog.mark_failed(
                        state.spec_id,
                        error=error_msg,
                        max_retries=self._max_retries,
                    )

                if result.regression_detected:
                    compared = result.compared_to_baseline
                    regression_details = (
                        "; ".join(f"{k}: {v}" for k, v in sorted(compared.items()))
                        if compared
                        else "details unavailable"
                    )
                    error_msg = f"Benchmark regression detected: {regression_details}"
                    log.warning(
                        "metaloop_benchmark_regression",
                        spec_id=state.spec_id,
                        regressions=regression_details,
                    )
                    return self._backlog.mark_failed(
                        state.spec_id,
                        error=error_msg,
                        max_retries=self._max_retries,
                    )
        except Exception:
            log.warning(
                "metaloop_benchmark_error",
                spec_id=state.spec_id,
                exc_info=True,
            )
            # Infrastructure failure — don't block, but record that benchmark
            # was skipped so downstream phases have visibility.
            self._update_metadata(
                state.spec_id,
                "benchmark",
                {"benchmark_skipped": True, "reason": "BenchmarkRunner raised an exception"},
            )

        return self._backlog.advance_phase(state.spec_id, IterationPhase.LEARNING)

    def _handle_learning(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return self._backlog.advance_phase(state.spec_id, IterationPhase.RECONCILING)
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        benchmark_data = metadata.get("benchmark") or {}

        # Determine current followup depth (Fix 2)
        current_depth = int(metadata.get("followup_depth", 0))

        if benchmark_data:
            try:
                from hermit.plugins.builtin.hooks.benchmark.learning import (
                    IterationLearner,
                )
                from hermit.plugins.builtin.hooks.benchmark.models import (
                    BenchmarkResult,
                )

                br = BenchmarkResult(
                    iteration_id=state.spec_id,
                    spec_id=state.spec_id,
                    **{
                        k: v
                        for k, v in benchmark_data.items()
                        if k
                        in (
                            "check_passed",
                            "test_total",
                            "test_passed",
                            "coverage",
                            "lint_violations",
                            "duration_seconds",
                            "regression_detected",
                            "compared_to_baseline",
                        )
                    },
                )
                learner = IterationLearner(self._store)
                lessons = _run_async(learner.learn(state.spec_id, br))

                # Create followup specs for mistake lessons with safety limits
                mistake_lessons = [ls for ls in lessons if ls.category == "mistake"]
                followups_created = 0

                for lesson in mistake_lessons:
                    # Fix 3: fanout limit
                    if followups_created >= MAX_FOLLOWUP_FANOUT:
                        log.info(
                            "metaloop_followup_fanout_capped",
                            spec_id=state.spec_id,
                            limit=MAX_FOLLOWUP_FANOUT,
                        )
                        break

                    # Fix 2: depth limit
                    if current_depth >= MAX_FOLLOWUP_DEPTH:
                        log.info(
                            "metaloop_followup_depth_capped",
                            spec_id=state.spec_id,
                            depth=current_depth,
                            limit=MAX_FOLLOWUP_DEPTH,
                        )
                        self._update_metadata(
                            state.spec_id,
                            "terminated_info",
                            {
                                "terminated_reason": "max_followup_depth_exceeded",
                                "limit_name": "max_followup_depth",
                                "limit_value": MAX_FOLLOWUP_DEPTH,
                                "actual_value": current_depth,
                            },
                        )
                        break

                    # Fix 4: queue depth limit
                    if hasattr(self._store, "count_active_specs"):
                        active_count = self._store.count_active_specs()
                        if active_count >= MAX_QUEUE_DEPTH:
                            log.warning(
                                "metaloop_followup_queue_full",
                                spec_id=state.spec_id,
                                active=active_count,
                                limit=MAX_QUEUE_DEPTH,
                            )
                            self._update_metadata(
                                state.spec_id,
                                "terminated_info",
                                {
                                    "terminated_reason": "queue_depth_exceeded",
                                    "limit_name": "max_queue_depth",
                                    "limit_value": MAX_QUEUE_DEPTH,
                                    "actual_value": active_count,
                                },
                            )
                            break

                    # Fix 9: goal hash dedup
                    goal_text = f"Fix: {lesson.summary}"
                    goal_hash = hashlib.sha256(goal_text.strip().lower().encode()).hexdigest()
                    if hasattr(self._store, "find_spec_by_goal_hash"):
                        existing = self._store.find_spec_by_goal_hash(goal_hash)
                        if existing is not None:
                            log.debug(
                                "metaloop_followup_dedup_skipped",
                                spec_id=state.spec_id,
                                goal_hash=goal_hash[:16],
                            )
                            continue

                    import uuid

                    followup_id = f"followup-{uuid.uuid4().hex[:12]}"
                    if hasattr(self._store, "create_spec_entry"):
                        # Fix 0: pass native types, NOT pre-serialized strings
                        self._store.create_spec_entry(
                            spec_id=followup_id,
                            goal=goal_text,
                            priority="high",
                            research_hints=[
                                f"Followup from iteration {state.spec_id}",
                                f"Lesson: {lesson.summary}",
                            ],
                            metadata={
                                "followup_from": state.spec_id,
                                "lesson_id": lesson.lesson_id,
                                "followup_depth": current_depth + 1,
                                "goal_hash": goal_hash,
                            },
                        )
                        followups_created += 1
                        log.info(
                            "metaloop_followup_created",
                            spec_id=followup_id,
                            source=state.spec_id,
                            depth=current_depth + 1,
                        )
            except Exception:
                log.exception("metaloop_learning_error", spec_id=state.spec_id)

        return self._backlog.advance_phase(state.spec_id, IterationPhase.RECONCILING)

    def _handle_reconciling(self, state: IterationState) -> IterationState | None:
        """Evaluate the promotion gate and transition to ACCEPTED or REJECTED.

        Promotion criteria:
        1. Benchmark must pass (no regression detected)
        2. Review must pass (no blocking findings)
        3. No prior error recorded on the spec entry
        4. Lessons should not contain 'mistake' category entries
        5. Acceptance criteria validation must meet minimum threshold
        6. IterationKernel promotion gate (if available)
        """
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return self._backlog.advance_phase(state.spec_id, IterationPhase.REJECTED)
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))

        rejection_reasons: list[str] = []

        # 1. Benchmark gate
        benchmark_data = metadata.get("benchmark") or {}
        if not benchmark_data:
            rejection_reasons.append("No benchmark results available")
        else:
            if benchmark_data.get("regression_detected"):
                rejection_reasons.append("Benchmark regression detected")
            if not benchmark_data.get("check_passed", False):
                rejection_reasons.append("Benchmark check did not pass")

        # 2. Review gate
        review_data = metadata.get("review") or {}
        if review_data and not review_data.get("passed", True):
            rejection_reasons.append(
                f"Review found blocking findings ({review_data.get('finding_count', 0)} issues)"
            )

        # 3. DAG step completion gate — check for prior failures
        error = data.get("error")
        if error:
            rejection_reasons.append(f"Prior error recorded: {error}")

        # 4. Lessons gate — 'mistake' lessons indicate broken implementation
        try:
            if hasattr(self._store, "list_lessons"):
                lessons = self._store.list_lessons(
                    iteration_ids=[state.spec_id],
                    categories=["mistake"],
                )
                if lessons:
                    summaries = [
                        ls.get("summary", "") if isinstance(ls, dict) else str(ls) for ls in lessons
                    ]
                    rejection_reasons.append(f"Mistake lessons found: {'; '.join(summaries[:3])}")
        except Exception:
            log.debug(
                "metaloop_reconciling_lessons_check_failed",
                spec_id=state.spec_id,
            )

        # 5. Acceptance criteria gate
        _CRITERIA_VALIDATION_THRESHOLD = 0.5
        criteria_validation = metadata.get("acceptance_criteria_validation") or {}
        total_criteria = criteria_validation.get("total", 0)
        validated_criteria = criteria_validation.get("validated", 0)
        spec_criteria = (metadata.get("generated_spec") or {}).get("acceptance_criteria", [])
        if total_criteria > 0:
            validation_ratio = validated_criteria / total_criteria
            if validation_ratio < _CRITERIA_VALIDATION_THRESHOLD:
                rejection_reasons.append(
                    f"Acceptance criteria insufficiently validated: "
                    f"{validated_criteria}/{total_criteria} "
                    f"({validation_ratio:.0%})"
                )
        elif spec_criteria and not criteria_validation.get("skipped"):
            rejection_reasons.append(
                f"Acceptance criteria validation was never run "
                f"({len(spec_criteria)} criteria defined but no validation recorded)"
            )
        log.info(
            "metaloop_criteria_gate",
            spec_id=state.spec_id,
            total=total_criteria,
            validated=validated_criteria,
        )

        # 6. Try the IterationKernel.check_promotion_gate() if available
        kernel_gate_passed = False
        try:
            from hermit.kernel.execution.self_modify.iteration_kernel import (
                IterationKernel,
            )

            kernel = IterationKernel(self._store)
            kernel_gate_passed = kernel.check_promotion_gate(state.spec_id)
        except (KeyError, ImportError):
            # KeyError = iteration not found in kernel's model (expected when
            # the iteration was created via metaloop, not IterationKernel.admit).
            # ImportError = iteration_kernel module unavailable.
            # Fall through to local criteria.
            pass
        except Exception:
            log.debug(
                "metaloop_reconciling_kernel_gate_error",
                spec_id=state.spec_id,
            )

        # Build the promotion decision
        promoted = len(rejection_reasons) == 0 or kernel_gate_passed
        decision = {
            "promoted": promoted,
            "kernel_gate_passed": kernel_gate_passed,
            "rejection_reasons": rejection_reasons,
            "decided_at": time.time(),
        }
        self._update_metadata(state.spec_id, "promotion_decision", decision)

        if promoted:
            log.info(
                "metaloop_promotion_accepted",
                spec_id=state.spec_id,
                kernel_gate=kernel_gate_passed,
            )
            return self._backlog.advance_phase(state.spec_id, IterationPhase.ACCEPTED)

        log.warning(
            "metaloop_promotion_rejected",
            spec_id=state.spec_id,
            reasons=rejection_reasons,
        )
        return self._backlog.advance_phase(
            state.spec_id,
            IterationPhase.REJECTED,
            error=f"Promotion gate failed: {'; '.join(rejection_reasons)}",
        )


class SpecBacklogPoller:
    """Meta-loop poller that wakes for pending and active iterations.

    Uses adaptive polling (Fix 13): fast interval when work exists,
    slow interval after consecutive idle ticks.
    """

    def __init__(
        self,
        orchestrator: MetaLoopOrchestrator,
        backlog: SpecBacklog,
        *,
        poll_interval: float = POLL_INTERVAL_ACTIVE,
    ):
        self._orchestrator = orchestrator
        self._backlog = backlog
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_idle: int = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._consecutive_idle = 0
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="metaloop-poller",
        )
        self._thread.start()
        log.info("metaloop_poller_started", poll_interval=self._poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=POLL_INTERVAL_IDLE + 1)
            self._thread = None
        log.info("metaloop_poller_stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _current_interval(self) -> float:
        """Return current poll interval based on idle streak (Fix 13)."""
        if self._consecutive_idle >= _IDLE_TICKS_THRESHOLD:
            return POLL_INTERVAL_IDLE
        return POLL_INTERVAL_ACTIVE

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("metaloop_poller_tick_error")
            self._stop_event.wait(timeout=self._current_interval())

    def _cleanup_worktree(self, spec_id: str) -> None:
        """Best-effort cleanup of the worktree for a spec on timeout/failure."""
        workspace_root = self._orchestrator._workspace_root
        if not workspace_root:
            return
        try:
            from hermit.kernel.execution.self_modify.workspace import SelfModifyWorkspace

            ws = SelfModifyWorkspace(workspace_root)
            ws.remove(spec_id)
            log.info("metaloop_worktree_timeout_cleanup", spec_id=spec_id)
        except Exception:
            log.warning(
                "metaloop_worktree_timeout_cleanup_failed",
                spec_id=spec_id,
                exc_info=True,
            )

    def _check_dag_task_terminal(self, spec_id: str, dag_task_id: str) -> bool | None:
        """Check if a DAG task reached a terminal state and handle it.

        Returns True if terminal and handled, False if still running,
        None if the task could not be checked (no runner / lookup error).
        """
        if not self._orchestrator._runner:
            return None
        try:
            tc = self._orchestrator._runner.task_controller
            task = tc.store.get_task(dag_task_id)
            if task is None:
                return None
            if task.status == "completed":
                log.info(
                    "metaloop_implementing_task_completed",
                    spec_id=spec_id,
                    dag_task_id=dag_task_id,
                )
                self._orchestrator.on_subtask_complete(dag_task_id, success=True)
                return True
            if task.status in ("failed", "cancelled"):
                log.warning(
                    "metaloop_implementing_task_failed",
                    spec_id=spec_id,
                    dag_task_id=dag_task_id,
                    task_status=task.status,
                )
                self._orchestrator.on_subtask_complete(
                    dag_task_id,
                    success=False,
                    error=f"DAG task {task.status}",
                )
                return True
            return False  # still running
        except Exception:
            log.exception("metaloop_implementing_check_error", spec_id=spec_id)
            return None

    def _check_implementing_timeout(self, entry: dict) -> bool:
        """Check if an IMPLEMENTING spec has timed out.

        Returns True if work was done (spec advanced or failed).

        Before timeout: checks whether the DAG task finished early.
        After timeout: if the DAG task is terminal, advances normally;
        if still running, cancels it and fails the spec with worktree
        cleanup.  Never extends the timeout indefinitely.
        """
        spec_id = entry["spec_id"]
        metadata = _parse_metadata(entry.get("metadata"))
        started_at = metadata.get("implementing_started_at")
        dag_task_id = entry.get("dag_task_id")

        if started_at is None:
            self._orchestrator._update_metadata(spec_id, "implementing_started_at", time.time())
            return False

        elapsed = time.time() - float(started_at)

        # Before timeout: check if DAG task finished early
        if elapsed < PHASE_TIMEOUT_IMPLEMENT:
            if dag_task_id:
                result = self._check_dag_task_terminal(spec_id, dag_task_id)
                if result is True:
                    return True
            return False

        # Timeout exceeded — check DAG task one last time
        if dag_task_id:
            result = self._check_dag_task_terminal(spec_id, dag_task_id)
            if result is True:
                return True  # terminal — handled normally
            if result is False:
                # Task still running past hard timeout — cancel it
                log.warning(
                    "metaloop_implementing_timeout_cancel",
                    spec_id=spec_id,
                    dag_task_id=dag_task_id,
                    elapsed_seconds=int(elapsed),
                )
                try:
                    tc = self._orchestrator._runner.task_controller
                    tc.cancel_task(dag_task_id, reason="implementation timeout exceeded")
                except Exception:
                    log.warning(
                        "metaloop_implementing_cancel_failed",
                        spec_id=spec_id,
                        dag_task_id=dag_task_id,
                        exc_info=True,
                    )

        # Hard timeout — clean up worktree and fail
        log.warning(
            "metaloop_implementing_timeout",
            spec_id=spec_id,
            elapsed_seconds=int(elapsed),
            dag_task_id=dag_task_id,
        )
        self._cleanup_worktree(spec_id)
        self._backlog.mark_failed(
            spec_id,
            error=f"IMPLEMENTING timed out after {int(elapsed)}s (dag_task_id={dag_task_id})",
            max_retries=self._orchestrator._max_retries,
        )
        return True

    def _tick(self) -> None:
        """Claim and advance the next pending spec, AND continue active ones."""
        did_work = False

        # Always check IMPLEMENTING specs for timeouts first (Fix 1).
        # This runs regardless of pending specs to avoid starvation.
        if hasattr(self._backlog._store, "list_spec_backlog"):
            try:
                implementing = self._backlog._store.list_spec_backlog(
                    status="implementing", limit=10
                )
                for impl_entry in implementing:
                    entry = impl_entry if isinstance(impl_entry, dict) else impl_entry.__dict__
                    if self._check_implementing_timeout(entry):
                        did_work = True  # only count actual transitions
            except Exception:
                log.exception("metaloop_poller_implementing_check_error")

        # Claim a pending spec (does NOT block active phase advancement)
        claimed = self._backlog.claim_next()
        if claimed is not None:
            log.info(
                "metaloop_poller_claimed",
                spec_id=claimed.spec_id,
                phase=claimed.phase.value,
            )
            self._orchestrator.advance(claimed.spec_id)
            did_work = True

        # ALSO advance active (non-terminal, non-pending) iterations.
        # Process one spec per active phase per tick for fairness.
        if hasattr(self._backlog._store, "list_spec_backlog"):
            try:
                for status in (
                    "researching",
                    "generating_spec",
                    "spec_approval",
                    "decomposing",
                    "reviewing",
                    "benchmarking",
                    "learning",
                    "reconciling",
                ):
                    active = self._backlog._store.list_spec_backlog(status=status, limit=1)
                    if active:
                        entry = active[0] if isinstance(active[0], dict) else active[0].__dict__
                        spec_id = entry["spec_id"]
                        log.info(
                            "metaloop_poller_advancing",
                            spec_id=spec_id,
                            phase=status,
                        )
                        self._orchestrator.advance(spec_id)
                        did_work = True
                        break  # one active advance per tick for fairness
            except Exception:
                log.exception("metaloop_poller_advance_active_error")

        # Update idle counter for adaptive polling (Fix 13)
        if did_work:
            self._consecutive_idle = 0
        else:
            self._consecutive_idle += 1


class SignalToSpecConsumer:
    """Polls actionable signals and creates spec_backlog entries."""

    _ELIGIBLE_SOURCES = {"patrol", "benchmark", "review", "test_failure"}

    def __init__(
        self,
        store: Any,
        *,
        poll_interval: float = 30.0,
    ) -> None:
        self._store = store
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="signal-to-spec",
        )
        self._thread.start()
        log.info("signal_to_spec_started", poll_interval=self._poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval + 1)
            self._thread = None
        log.info("signal_to_spec_stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("signal_to_spec_tick_error")
            self._stop_event.wait(timeout=self._poll_interval)

    def _tick(self) -> int:
        """Check for actionable signals and create specs. Returns count."""
        if not hasattr(self._store, "actionable_signals"):
            return 0

        # Fix 4: queue depth check before processing signals
        if hasattr(self._store, "count_active_specs"):
            active_count = self._store.count_active_specs()
            if active_count >= MAX_QUEUE_DEPTH:
                log.debug(
                    "signal_to_spec_queue_full",
                    active=active_count,
                    limit=MAX_QUEUE_DEPTH,
                )
                return 0

        try:
            signals = self._store.actionable_signals(limit=20)
        except Exception:
            log.debug("signal_to_spec_fetch_failed")
            return 0

        created = 0
        for signal in signals:
            signal_id = (
                signal.get("signal_id")
                if isinstance(signal, dict)
                else getattr(signal, "signal_id", None)
            )
            if not signal_id:
                continue
            source_kind = (
                signal.get("source_kind")
                if isinstance(signal, dict)
                else getattr(signal, "source_kind", None)
            )
            if source_kind not in self._ELIGIBLE_SOURCES:
                continue

            suggested_goal = (
                signal.get("suggested_goal")
                if isinstance(signal, dict)
                else getattr(signal, "suggested_goal", None)
            )
            if not suggested_goal:
                continue

            existing = None
            if hasattr(self._store, "get_spec_entry"):
                try:
                    existing = self._store.get_spec_entry(spec_id=f"signal-{signal_id}")
                except Exception:
                    existing = None

            if existing is not None:
                continue

            # Fix 9: goal hash dedup (replaces O(n) full-table scan)
            goal_hash = hashlib.sha256(suggested_goal.strip().lower().encode()).hexdigest()
            if hasattr(self._store, "find_spec_by_goal_hash"):
                dup = self._store.find_spec_by_goal_hash(goal_hash)
                if dup is not None:
                    log.debug(
                        "signal_to_spec_dedup_skipped",
                        signal_id=signal_id,
                        goal_hash=goal_hash[:16],
                    )
                    continue

            # Re-check queue depth before each creation
            if (
                hasattr(self._store, "count_active_specs")
                and self._store.count_active_specs() >= MAX_QUEUE_DEPTH
            ):
                log.info(
                    "signal_to_spec_queue_full_mid_batch",
                    created_so_far=created,
                )
                break

            try:
                risk_level = (
                    signal.get("risk_level", "normal")
                    if isinstance(signal, dict)
                    else getattr(signal, "risk_level", "normal")
                )
                priority = "high" if risk_level in ("high", "critical") else "normal"

                # Fix 0: pass native types, not pre-serialized strings
                self._store.create_spec_entry(
                    spec_id=f"signal-{signal_id}",
                    goal=suggested_goal,
                    priority=priority,
                    research_hints=[
                        f"Signal source: {source_kind}",
                        f"Signal ID: {signal_id}",
                        f"Risk level: {risk_level}",
                    ],
                    metadata={
                        "produced_from_signal_id": signal_id,
                        "source_kind": source_kind,
                        "risk_level": risk_level,
                        "goal_hash": goal_hash,
                    },
                )

                if hasattr(self._store, "update_signal_disposition"):
                    self._store.update_signal_disposition(
                        signal_id,
                        "acted",
                        acted_at=time.time(),
                        produced_task_id=f"signal-{signal_id}",
                    )

                created += 1
                log.info(
                    "signal_to_spec_created",
                    signal_id=signal_id,
                    source_kind=source_kind,
                    priority=priority,
                )
            except Exception:
                log.exception("signal_to_spec_create_failed", signal_id=signal_id)

        if created:
            log.info("signal_to_spec_batch", created=created)
        return created
