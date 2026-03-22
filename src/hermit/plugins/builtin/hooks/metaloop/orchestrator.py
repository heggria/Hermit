"""Meta-loop orchestrator — 3-phase pipeline that advances iterations through
planning, implementing, and reviewing.

Phase handlers:
  _handle_planning     — research + spec generation + approval + decomposition
  _handle_implementing — DAG task creation, poller handles timeout detection
  _handle_reviewing    — council review + benchmark + learning, with revision loop
"""

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
    MAX_REVISION_CYCLES,
    IterationState,
    PipelinePhase,
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
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


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
    """Advances self-improvement iterations through the 3-phase pipeline:
    PLANNING -> IMPLEMENTING -> REVIEWING -> ACCEPTED/REJECTED.
    """

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_provider(self) -> Any:
        """Clone the LLM provider from the runner's agent."""
        return self._runner.agent.provider.clone()

    def _get_model(self) -> str:
        """Return the model to use for metaloop LLM calls.

        Checks settings on the runner, then falls back to the default haiku
        model for cost-effective metaloop operations.
        """
        if self._runner is not None:
            settings = getattr(self._runner, "settings", None)
            if settings is not None:
                model = getattr(settings, "metaloop_model", None)
                if model:
                    return str(model)
        return _DEFAULT_MODEL

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

        goal_keywords = {w for w in goal.lower().split() if len(w) >= 3}
        if not goal_keywords:
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
            category_boost = 1.5 if category in ("mistake", "rollback_pattern") else 1.0
            score = overlap * category_boost
            if score > 0:
                scored.append((score, lesson))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [lesson for _, lesson in scored[:limit]]

    def _update_metadata(self, spec_id: str, key: str, value: Any) -> dict:
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

    # ------------------------------------------------------------------
    # Advance — dynamic dispatch
    # ------------------------------------------------------------------

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
            log.warning(
                "metaloop_advance_no_handler",
                spec_id=spec_id,
                phase=state.phase.value,
            )
            return state

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

    # ------------------------------------------------------------------
    # Phase 0: PENDING -> PLANNING
    # ------------------------------------------------------------------

    def _handle_pending(self, state: IterationState) -> IterationState | None:
        return self._backlog.advance_phase(state.spec_id, PipelinePhase.PLANNING)

    # ------------------------------------------------------------------
    # Phase 1: PLANNING — research + spec gen + approval + decompose
    # ------------------------------------------------------------------

    def _handle_planning(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return state
        data = entry if isinstance(entry, dict) else entry.__dict__
        goal = data.get("goal", "")
        metadata = _parse_metadata(data.get("metadata"))

        # --- Step 1: Research ---
        research_data = self._run_research(state.spec_id, goal, data, metadata)

        # --- Step 2: Spec generation (LLM with deterministic fallback) ---
        spec_data = self._run_spec_generation(state.spec_id, goal, research_data)
        if spec_data is None:
            return self._backlog.mark_failed(
                state.spec_id,
                error="Spec generation failed",
                max_retries=self._max_retries,
            )

        # --- Step 3: Inline approval check ---
        approval_result = self._check_spec_approval(state.spec_id, spec_data, data)
        if approval_result == "rejected":
            return self._backlog.advance_phase(
                state.spec_id,
                PipelinePhase.REJECTED,
                error="Spec approval denied",
            )
        if approval_result == "pending":
            # Approval request created — stay at PLANNING.
            return state

        # --- Step 4: Decomposition (LLM with deterministic fallback) ---
        self._run_decomposition(state.spec_id, spec_data, research_data)

        return self._backlog.advance_phase(state.spec_id, PipelinePhase.IMPLEMENTING)

    def _run_research(
        self,
        spec_id: str,
        goal: str,
        data: dict,
        metadata: dict,
    ) -> dict:
        """Run the research pipeline and store results in metadata."""
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
            spec_id=spec_id,
            lessons_found=len(prior_lessons),
            phase="planning_research",
        )

        # Run research pipeline — prefer the globally initialized pipeline
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
        if not workspace:
            import os

            workspace = os.environ.get("HERMIT_WORKSPACE_ROOT", "") or os.getcwd()
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
        research_data: dict[str, Any] = {
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
        }
        self._update_metadata(spec_id, "research", research_data)
        return research_data

    def _run_spec_generation(
        self,
        spec_id: str,
        goal: str,
        research_data: dict,
    ) -> dict | None:
        """Generate a spec via LLM, falling back to deterministic generator."""
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

        # Build lesson-derived constraints
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
                spec_id=spec_id,
                constraint_count=len(lesson_constraints),
                phase="planning_spec_gen",
            )

        constraints = tuple(lesson_constraints) if lesson_constraints else None

        # Try LLM spec generator, fall back to deterministic
        try:
            from hermit.plugins.builtin.hooks.decompose.llm_spec_generator import (
                LLMSpecGenerator,
            )

            provider = self._get_provider()
            model = self._get_model()
            spec = LLMSpecGenerator(provider, model=model).generate(goal, report, constraints)
        except Exception:
            log.warning(
                "metaloop_llm_spec_gen_fallback",
                spec_id=spec_id,
                exc_info=True,
            )
            from hermit.plugins.builtin.hooks.decompose.spec_generator import (
                SpecGenerator,
            )

            spec = SpecGenerator().generate(goal, research_report=report, constraints=constraints)

        spec_data: dict[str, Any] = {
            "spec_id": spec.spec_id,
            "title": spec.title,
            "goal": spec.goal,
            "constraints": list(spec.constraints),
            "acceptance_criteria": list(spec.acceptance_criteria),
            "file_plan": [dict(e) for e in spec.file_plan],
            "research_ref": spec.research_ref,
            "trust_zone": spec.trust_zone,
        }
        self._update_metadata(spec_id, "generated_spec", spec_data)
        return spec_data

    def _check_spec_approval(
        self,
        spec_id: str,
        spec_data: dict,
        data: dict,
    ) -> str:
        """Check spec approval inline. Returns 'approved', 'rejected', or 'pending'.

        - autonomous: auto-approve if risk_budget <= medium
        - supervised: always require approval
        - default: auto-approve low risk, request approval for medium+
        """
        # Re-read metadata (it was updated during research and spec gen)
        entry = self._store.get_spec_entry(spec_id=spec_id)
        if entry is None:
            return "approved"
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))

        # Determine risk level
        risk_budget = metadata.get("risk_budget", {})
        risk_band = risk_budget.get("band", "")
        if not risk_band:
            trust_zone = spec_data.get("trust_zone", "normal")
            risk_band = {"strict": "high", "normal": "medium", "relaxed": "low"}.get(
                trust_zone, "medium"
            )

        # Determine policy_profile
        policy_profile = metadata.get("policy_profile", "")
        if not policy_profile:
            source = data.get("source", "")
            policy_profile = "autonomous" if source == "self-iterate" else "default"

        # Decision logic
        needs_approval = False
        if policy_profile == "autonomous":
            needs_approval = risk_band in ("high", "critical")
        elif policy_profile == "supervised":
            needs_approval = True
        else:
            needs_approval = risk_band not in ("low",)

        if not needs_approval:
            log.info(
                "metaloop_spec_auto_approved",
                spec_id=spec_id,
                policy_profile=policy_profile,
                risk_band=risk_band,
            )
            self._update_metadata(
                spec_id,
                "spec_approval_decision",
                {
                    "approved": True,
                    "method": "auto",
                    "policy_profile": policy_profile,
                    "risk_band": risk_band,
                    "decided_at": time.time(),
                },
            )
            return "approved"

        # Create an approval request via the kernel approval system
        approval_created = False
        if self._runner is not None and hasattr(self._runner, "task_controller"):
            tc = self._runner.task_controller
            store = tc.store
            if hasattr(store, "create_approval"):
                try:
                    task_id = metadata.get("dag_task_id", f"metaloop-{spec_id}")
                    store.create_approval(
                        task_id=task_id,
                        step_id=f"spec-approval-{spec_id}",
                        step_attempt_id=f"attempt-{spec_id}",
                        approval_type="spec_approval",
                        requested_action={
                            "action": "approve_iteration_spec",
                            "spec_id": spec_id,
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
                        spec_id=spec_id,
                        policy_profile=policy_profile,
                        risk_band=risk_band,
                    )
                except Exception:
                    log.warning(
                        "metaloop_spec_approval_create_failed",
                        spec_id=spec_id,
                        exc_info=True,
                    )

        if not approval_created:
            log.warning(
                "metaloop_spec_approval_fallback_auto",
                spec_id=spec_id,
                reason="approval system unavailable",
            )
            self._update_metadata(
                spec_id,
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
            return "approved"

        # Approval request created — stay at PLANNING and wait
        self._update_metadata(
            spec_id,
            "spec_approval_decision",
            {
                "approved": False,
                "method": "pending_kernel_approval",
                "policy_profile": policy_profile,
                "risk_band": risk_band,
                "requested_at": time.time(),
            },
        )
        return "pending"

    def _run_decomposition(
        self,
        spec_id: str,
        spec_data: dict,
        research_data: dict,
    ) -> None:
        """Decompose spec into a task DAG via LLM, falling back to deterministic."""
        from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec

        # Collect lesson-referenced files for priority boosting
        lesson_files: set[str] = set()
        prior_lessons = research_data.get("prior_lessons", [])
        for ls in prior_lessons:
            lesson_id = ls.get("lesson_id", "")
            if lesson_id and hasattr(self._store, "get_lesson"):
                try:
                    full = self._store.get_lesson(lesson_id)
                    if full:
                        raw_files = full.get("applicable_files")
                        if isinstance(raw_files, str):
                            try:
                                parsed_files = json.loads(raw_files)
                                if isinstance(parsed_files, list):
                                    lesson_files.update(str(f) for f in parsed_files)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        elif isinstance(raw_files, list):
                            lesson_files.update(str(f) for f in raw_files)
                except Exception:
                    pass

        file_plan_raw = spec_data.get("file_plan", [])

        # Boost lesson-referenced files by prepending them if not present
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
                spec_id=spec_id,
                boosted_files=len(boosted_entries),
                phase="planning_decompose",
            )

        spec = GeneratedSpec(
            spec_id=spec_data.get("spec_id", spec_id),
            title=spec_data.get("title", ""),
            goal=spec_data.get("goal", ""),
            constraints=tuple(spec_data.get("constraints", ())),
            acceptance_criteria=tuple(spec_data.get("acceptance_criteria", ())),
            file_plan=tuple(dict(e) for e in file_plan_raw),
            research_ref=spec_data.get("research_ref", ""),
            trust_zone=spec_data.get("trust_zone", "normal"),
        )

        # Try LLM decomposer, fall back to deterministic
        try:
            from hermit.plugins.builtin.hooks.decompose.llm_task_decomposer import (
                LLMTaskDecomposer,
            )

            provider = self._get_provider()
            model = self._get_model()
            plan = LLMTaskDecomposer(provider, model=model).decompose(spec)
        except Exception:
            log.warning(
                "metaloop_llm_decompose_fallback",
                spec_id=spec_id,
                exc_info=True,
            )
            from hermit.plugins.builtin.hooks.decompose.task_decomposer import (
                TaskDecomposer,
            )

            plan = TaskDecomposer().decompose(spec)

        self._update_metadata(
            spec_id,
            "decomposition_plan",
            {
                "spec_id": plan.spec_id,
                "steps": [dict(s) for s in plan.steps],
                "dependency_graph": dict(plan.dependency_graph),
                "estimated_duration_minutes": plan.estimated_duration_minutes,
            },
        )

    # ------------------------------------------------------------------
    # Phase 2: IMPLEMENTING — create DAG task, poller handles timeout
    # ------------------------------------------------------------------

    def _handle_implementing(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return state
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))

        # If DAG already started (has timestamp), skip — let timeout checker handle it
        if metadata.get("implementing_started_at"):
            return state

        decomposition = metadata.get("decomposition_plan") or {}
        steps = decomposition.get("steps", [])
        if not steps:
            return self._backlog.advance_phase(state.spec_id, PipelinePhase.REVIEWING)

        if self._runner is None:
            return self._backlog.advance_phase(state.spec_id, PipelinePhase.REVIEWING)

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
                error=(
                    f"DAG task creation failed for {len(nodes)} steps: {type(exc).__name__}: {exc}"
                ),
                max_retries=self._max_retries,
            )
        # start_dag_task may return a tuple (task, ...) or a single object
        task = result[0] if isinstance(result, tuple) else result
        dag_task_id = task.task_id if hasattr(task, "task_id") and task.task_id else None
        # Self-transition IMPLEMENTING -> IMPLEMENTING to write dag_task_id + timestamp.
        # on_subtask_complete advances to REVIEWING when the DAG task finishes.
        return self._backlog.advance_phase(
            state.spec_id,
            PipelinePhase.IMPLEMENTING,
            dag_task_id=dag_task_id,
            metadata={"implementing_started_at": time.time()},
        )

    # ------------------------------------------------------------------
    # Phase 3: REVIEWING — council + benchmark + learning + revision loop
    # ------------------------------------------------------------------

    def _handle_reviewing(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return self._backlog.mark_failed(
                state.spec_id,
                error="Spec entry not found during review",
                max_retries=self._max_retries,
            )
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        decomposition = metadata.get("decomposition_plan") or {}
        steps = decomposition.get("steps", [])
        spec_data = metadata.get("generated_spec") or {}

        # Extract changed file paths from decomposition steps
        changed_files: list[str] = []
        for step in steps:
            step_meta = step.get("metadata", {})
            path = step_meta.get("path", "")
            if path:
                changed_files.append(path)

        # Read revision_cycle from metadata (default 0)
        revision_cycle = int(metadata.get("revision_cycle", 0))

        # --- Step 1: Council review ---
        verdict = self._run_council_review(state.spec_id, changed_files, spec_data, revision_cycle)

        # Store council verdict in metadata
        if verdict is not None:
            self._update_metadata(
                state.spec_id,
                "council_verdict",
                {
                    "verdict": verdict.verdict,
                    "council_id": verdict.council_id,
                    "finding_count": verdict.finding_count,
                    "critical_count": verdict.critical_count,
                    "high_count": verdict.high_count,
                    "lint_passed": verdict.lint_passed,
                    "consensus_score": verdict.consensus_score,
                    "revision_directive": verdict.revision_directive,
                    "duration_seconds": verdict.duration_seconds,
                    "decided_at": verdict.decided_at,
                },
            )
        else:
            log.debug("metaloop_council_unavailable", spec_id=state.spec_id)

        verdict_str = verdict.verdict if verdict is not None else "accept"

        # --- Verdict: "accept" -> benchmark + learning ---
        if verdict_str == "accept":
            return self._run_post_accept(state, metadata)

        # --- Verdict: "revise" -> revision loop ---
        can_revise = revision_cycle < MAX_REVISION_CYCLES
        if verdict_str == "revise" and can_revise:
            return self._start_revision_cycle(state.spec_id, revision_cycle, verdict, metadata)

        # --- Verdict: "reject" or revision budget exhausted ---
        reason = "Council rejected"
        if verdict_str == "revise" and not can_revise:
            reason = f"Revision budget exhausted ({revision_cycle}/{MAX_REVISION_CYCLES})"
        elif verdict is not None:
            reason = (
                f"Council verdict: {verdict_str} "
                f"(critical={verdict.critical_count}, high={verdict.high_count})"
            )
        log.warning("metaloop_review_terminal", spec_id=state.spec_id, reason=reason)
        return self._backlog.advance_phase(
            state.spec_id,
            PipelinePhase.REJECTED,
            error=reason,
        )

    def _run_council_review(
        self,
        spec_id: str,
        changed_files: list[str],
        spec_data: dict,
        revision_cycle: int,
    ) -> Any | None:
        """Invoke ReviewCouncilService and return a CouncilVerdict, or None."""
        try:
            from hermit.plugins.builtin.hooks.quality.council_service import (
                ReviewCouncilService,
            )

            workspace = self._workspace_root
            if not workspace:
                import os

                workspace = os.environ.get("HERMIT_WORKSPACE_ROOT", "") or os.getcwd()

            provider_factory = self._get_provider
            council = ReviewCouncilService(provider_factory, workspace)
            return council.convene(
                spec_id,
                changed_files,
                spec_data,
                revision_cycle=revision_cycle,
                max_revision_cycles=MAX_REVISION_CYCLES,
            )
        except ImportError:
            log.debug("metaloop_council_import_failed", spec_id=spec_id)
            return None
        except Exception:
            log.exception("metaloop_council_review_error", spec_id=spec_id)
            return None

    def _run_post_accept(
        self,
        state: IterationState,
        metadata: dict,
    ) -> IterationState | None:
        """Run benchmark and learning after council accepts."""
        impl_info = metadata.get("implementation") or {}
        worktree_path = impl_info.get("worktree_path")

        # --- Benchmark ---
        benchmark_passed = True
        try:
            from hermit.plugins.builtin.hooks.benchmark.runner import BenchmarkRunner

            runner = BenchmarkRunner(self._store)
            result = _run_async(runner.run(state.spec_id, state.spec_id, worktree_path))

            benchmark_data: dict[str, Any] = {
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

            if self._benchmark_blocking:
                if not result.check_passed:
                    benchmark_passed = False
                    log.warning(
                        "metaloop_benchmark_check_failed",
                        spec_id=state.spec_id,
                        test_passed=result.test_passed,
                        test_total=result.test_total,
                    )
                elif result.regression_detected:
                    benchmark_passed = False
                    compared = result.compared_to_baseline
                    regression_details = (
                        "; ".join(f"{k}: {v}" for k, v in sorted(compared.items()))
                        if compared
                        else "details unavailable"
                    )
                    log.warning(
                        "metaloop_benchmark_regression",
                        spec_id=state.spec_id,
                        regressions=regression_details,
                    )
        except Exception:
            log.warning(
                "metaloop_benchmark_error",
                spec_id=state.spec_id,
                exc_info=True,
            )
            self._update_metadata(
                state.spec_id,
                "benchmark",
                {
                    "benchmark_skipped": True,
                    "reason": "BenchmarkRunner raised an exception",
                },
            )

        # --- Learning ---
        self._run_learning(state)

        # --- Final decision ---
        if not benchmark_passed:
            return self._backlog.mark_failed(
                state.spec_id,
                error="Benchmark failed or regression detected",
                max_retries=self._max_retries,
            )

        log.info("metaloop_accepted", spec_id=state.spec_id)
        return self._backlog.advance_phase(state.spec_id, PipelinePhase.ACCEPTED)

    def _run_learning(self, state: IterationState) -> None:
        """Run IterationLearner and create followup specs for mistake lessons."""
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        benchmark_data = metadata.get("benchmark") or {}
        current_depth = int(metadata.get("followup_depth", 0))

        if not benchmark_data:
            return

        try:
            from hermit.plugins.builtin.hooks.benchmark.learning import (
                IterationLearner,
            )
            from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult

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
                if followups_created >= MAX_FOLLOWUP_FANOUT:
                    log.info(
                        "metaloop_followup_fanout_capped",
                        spec_id=state.spec_id,
                        limit=MAX_FOLLOWUP_FANOUT,
                    )
                    break

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

                # Goal hash dedup
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

    def _start_revision_cycle(
        self,
        spec_id: str,
        revision_cycle: int,
        verdict: Any,
        metadata: dict,
    ) -> IterationState | None:
        """Store revision directive and go back to IMPLEMENTING for a revision."""
        new_cycle = revision_cycle + 1
        log.info(
            "metaloop_revision_cycle",
            spec_id=spec_id,
            cycle=new_cycle,
            max=MAX_REVISION_CYCLES,
        )

        # Store revision directive in metadata
        revision_info: dict[str, Any] = {
            "revision_cycle": new_cycle,
            "council_id": verdict.council_id if verdict else "",
            "directive": verdict.revision_directive if verdict else "",
            "initiated_at": time.time(),
        }
        self._update_metadata(spec_id, "revision_directive", revision_info)

        # Update revision_cycle counter in metadata
        entry = self._store.get_spec_entry(spec_id=spec_id)
        if entry is not None:
            data = entry if isinstance(entry, dict) else entry.__dict__
            meta = _parse_metadata(data.get("metadata"))
            meta["revision_cycle"] = new_cycle
            self._store.update_spec_status(
                spec_id=spec_id,
                status=data.get("status", "reviewing"),
                metadata=_serialize_metadata(meta),
            )

        # Transition REVIEWING -> IMPLEMENTING (revision loop)
        return self._backlog.advance_phase(spec_id, PipelinePhase.IMPLEMENTING)

    # ------------------------------------------------------------------
    # on_subtask_complete — DAG task finished -> advance to REVIEWING
    # ------------------------------------------------------------------

    def on_subtask_complete(
        self, task_id: str, *, success: bool = True, error: str | None = None
    ) -> IterationState | None:
        """Handle DAG subtask completion — advance IMPLEMENTING to REVIEWING.

        On success, stores the worktree branch info for later PR creation
        instead of merging directly to main.
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
        phase_str = data.get("status", "pending")

        try:
            phase = PipelinePhase(phase_str)
        except ValueError:
            return None

        if phase != PipelinePhase.IMPLEMENTING:
            return None

        metadata = _parse_metadata(data.get("metadata"))
        impl_info = metadata.get("implementation") or {}
        worktree_path = impl_info.get("worktree_path")

        if success:
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
            return self._backlog.advance_phase(spec_id, PipelinePhase.REVIEWING)

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


# ======================================================================
# SpecBacklogPoller
# ======================================================================


class SpecBacklogPoller:
    """Meta-loop poller that wakes for pending and active iterations.

    Uses adaptive polling: fast interval when work exists,
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
        """Return current poll interval based on idle streak."""
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
            from hermit.kernel.execution.self_modify.workspace import (
                SelfModifyWorkspace,
            )

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
        None if the task could not be checked.
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
            error=(f"IMPLEMENTING timed out after {int(elapsed)}s (dag_task_id={dag_task_id})"),
            max_retries=self._orchestrator._max_retries,
        )
        return True

    def _tick(self) -> None:
        """Claim pending AND advance active specs per tick — no early return."""
        did_work = False

        # 1. Check IMPLEMENTING specs for timeouts (runs regardless of pending)
        if hasattr(self._backlog._store, "list_spec_backlog"):
            try:
                implementing = self._backlog._store.list_spec_backlog(
                    status="implementing", limit=10
                )
                for impl_entry in implementing:
                    entry = impl_entry if isinstance(impl_entry, dict) else impl_entry.__dict__
                    if self._check_implementing_timeout(entry):
                        did_work = True
            except Exception:
                log.exception("metaloop_poller_implementing_check_error")

        # 2. Claim a pending spec (does NOT block active phase advancement)
        claimed = self._backlog.claim_next()
        if claimed is not None:
            log.info(
                "metaloop_poller_claimed",
                spec_id=claimed.spec_id,
                phase=claimed.phase.value,
            )
            self._orchestrator.advance(claimed.spec_id)
            did_work = True

        # 3. Advance active (non-terminal, non-pending) iterations.
        #    Uses the 3 active phases from the new pipeline.
        if hasattr(self._backlog._store, "list_spec_backlog"):
            try:
                for status in (
                    PipelinePhase.PLANNING.value,
                    PipelinePhase.IMPLEMENTING.value,
                    PipelinePhase.REVIEWING.value,
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
            except Exception:
                log.exception("metaloop_poller_advance_active_error")

        # Update idle counter for adaptive polling
        if did_work:
            self._consecutive_idle = 0
        else:
            self._consecutive_idle += 1


# ======================================================================
# SignalToSpecConsumer
# ======================================================================


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

        # Queue depth check before processing signals
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

            # Goal hash dedup
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
