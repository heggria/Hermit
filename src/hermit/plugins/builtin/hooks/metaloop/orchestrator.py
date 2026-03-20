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
    ) -> None:
        self._backlog = SpecBacklog(store)
        self._store = store
        self._max_retries = max_retries
        self._runner = runner
        self._workspace_root = workspace_root

    def set_runner(self, runner: Any) -> None:
        """Update the runner reference (for hot-reload)."""
        self._runner = runner

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
        """Handle DAG subtask completion — advance IMPLEMENTING to REVIEWING."""
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
            # Merge worktree if present
            if worktree_path and self._workspace_root:
                try:
                    from hermit.kernel.execution.self_modify.workspace import (
                        SelfModifyWorkspace,
                    )

                    ws = SelfModifyWorkspace(self._workspace_root)
                    ws.merge_to_main(spec_id)
                    ws.remove(spec_id)
                except Exception:
                    log.exception("metaloop_worktree_merge_failed", spec_id=spec_id)

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

        # Inject prior lessons as hints
        if hasattr(self._store, "list_lessons"):
            try:
                for lesson in self._store.list_lessons():
                    summary = (
                        lesson.get("summary", "")
                        if isinstance(lesson, dict)
                        else getattr(lesson, "summary", "")
                    )
                    if summary:
                        hints.append(f"Prior lesson: {summary}")
            except Exception:
                log.debug("metaloop_lessons_inject_failed", spec_id=state.spec_id)

        # Run research pipeline
        from hermit.plugins.builtin.hooks.research.pipeline import ResearchPipeline
        from hermit.plugins.builtin.hooks.research.strategies import (
            CodebaseStrategy,
            GitHistoryStrategy,
        )

        workspace = self._workspace_root or ""
        pipeline = ResearchPipeline(
            [
                CodebaseStrategy(workspace),
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

        # Generate spec
        from hermit.plugins.builtin.hooks.decompose.spec_generator import SpecGenerator

        spec = SpecGenerator().generate(goal, research_report=report)

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

        return self._backlog.advance_phase(state.spec_id, IterationPhase.DECOMPOSING)

    def _handle_decomposing(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return state
        data = entry if isinstance(entry, dict) else entry.__dict__
        metadata = _parse_metadata(data.get("metadata"))
        spec_data = metadata.get("generated_spec") or {}

        # Reconstruct GeneratedSpec from metadata
        from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec
        from hermit.plugins.builtin.hooks.decompose.task_decomposer import TaskDecomposer

        file_plan_raw = spec_data.get("file_plan", [])
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
        result = tc.start_dag_task(
            conversation_id=f"metaloop-{state.spec_id}",
            goal=f"Implement spec {state.spec_id}",
            source_channel="metaloop",
            nodes=nodes,
            policy_profile="autonomous",
            workspace_root=self._workspace_root,
        )
        # start_dag_task may return a tuple (task, ...) or a single object
        task = result[0] if isinstance(result, tuple) else result
        dag_task_id = task.task_id if hasattr(task, "task_id") else ""
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
                return self._backlog.mark_failed(
                    state.spec_id,
                    error="Review found BLOCKING findings",
                    max_retries=self._max_retries,
                )
        except ImportError:
            log.debug(
                "metaloop_reviewer_import_failed",
                spec_id=state.spec_id,
            )
        except Exception:
            log.exception("metaloop_review_error", spec_id=state.spec_id)

        return self._backlog.advance_phase(state.spec_id, IterationPhase.BENCHMARKING)

    def _handle_benchmarking(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
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

            self._update_metadata(
                state.spec_id,
                "benchmark",
                {
                    "check_passed": result.check_passed,
                    "test_total": result.test_total,
                    "test_passed": result.test_passed,
                    "coverage": result.coverage,
                    "lint_violations": result.lint_violations,
                    "duration_seconds": result.duration_seconds,
                    "regression_detected": result.regression_detected,
                    "compared_to_baseline": dict(result.compared_to_baseline),
                },
            )
        except Exception:
            log.exception("metaloop_benchmark_error", spec_id=state.spec_id)

        return self._backlog.advance_phase(state.spec_id, IterationPhase.LEARNING)

    def _handle_learning(self, state: IterationState) -> IterationState | None:
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return self._backlog.advance_phase(state.spec_id, IterationPhase.COMPLETED)
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

        return self._backlog.advance_phase(state.spec_id, IterationPhase.COMPLETED)


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

    def _check_implementing_timeout(self, entry: dict) -> bool:
        """Check if an IMPLEMENTING spec has timed out (Fix 1).

        Returns True if work was done (spec advanced or failed).
        """
        spec_id = entry["spec_id"]
        metadata = _parse_metadata(entry.get("metadata"))
        started_at = metadata.get("implementing_started_at")
        dag_task_id = entry.get("dag_task_id")

        if started_at is None:
            # No timestamp — write one now for future checks
            self._orchestrator._update_metadata(spec_id, "implementing_started_at", time.time())
            return False

        elapsed = time.time() - float(started_at)
        if elapsed < PHASE_TIMEOUT_IMPLEMENT:
            return False  # not timed out yet

        # Check if the DAG task has completed/failed
        if dag_task_id and self._orchestrator._runner is not None:
            try:
                tc = self._orchestrator._runner.task_controller
                task = tc.store.get_task(dag_task_id)
                if task is not None:
                    if task.status == "completed":
                        log.info(
                            "metaloop_implementing_task_completed",
                            spec_id=spec_id,
                            dag_task_id=dag_task_id,
                        )
                        self._orchestrator.on_subtask_complete(dag_task_id, success=True)
                        return True
                    elif task.status in ("failed", "cancelled"):
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
                    else:
                        # Task still running — extend timeout
                        self._orchestrator._update_metadata(
                            spec_id,
                            "implementing_started_at",
                            time.time(),
                        )
                        log.debug(
                            "metaloop_implementing_extended",
                            spec_id=spec_id,
                            dag_task_id=dag_task_id,
                            task_status=task.status,
                        )
                        return False
            except Exception:
                log.exception(
                    "metaloop_implementing_check_error",
                    spec_id=spec_id,
                )

        # No task controller or task not found — mark failed
        log.warning(
            "metaloop_implementing_timeout",
            spec_id=spec_id,
            elapsed_seconds=elapsed,
        )
        self._backlog.mark_failed(
            spec_id,
            error=f"IMPLEMENTING timed out after {int(elapsed)}s (dag_task_id={dag_task_id})",
            max_retries=self._orchestrator._max_retries,
        )
        return True

    def _tick(self) -> None:
        """Claim and advance the next pending spec, or continue active ones."""
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

        claimed = self._backlog.claim_next()
        if claimed is not None:
            log.info(
                "metaloop_poller_claimed",
                spec_id=claimed.spec_id,
                phase=claimed.phase.value,
            )
            self._orchestrator.advance(claimed.spec_id)
            self._consecutive_idle = 0
            return

        # No pending specs — advance any active (non-terminal, non-pending)
        # iterations. IMPLEMENTING is handled above.
        if not hasattr(self._backlog._store, "list_spec_backlog"):
            if not did_work:
                self._consecutive_idle += 1
            else:
                self._consecutive_idle = 0
            return
        try:
            for status in (
                "researching",
                "generating_spec",
                "spec_approval",
                "decomposing",
                "reviewing",
                "benchmarking",
                "learning",
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
                    self._consecutive_idle = 0
                    return
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
