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
import uuid
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.kernel.execution.self_modify._metadata_utils import parse_metadata as _parse_metadata
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
MAX_BENCHMARK_REPAIR_CYCLES = 2
PHASE_TIMEOUT_IMPLEMENT = 900  # 15 minutes
PHASE_TIMEOUT_PLANNING = 600  # 10 minutes
PHASE_TIMEOUT_REVIEWING = 600  # 10 minutes
POLL_INTERVAL_ACTIVE = 10.0  # seconds
POLL_INTERVAL_IDLE = 60.0  # seconds
_IDLE_TICKS_THRESHOLD = 6  # consecutive idle ticks before switching to idle interval
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_SONNET_MODEL = "claude-sonnet-4-6-20250514"
SpecDict = dict[str, Any]


async def _as_coroutine(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


def _run_async(awaitable: Awaitable[Any]) -> Any:
    """Run an async coroutine from a sync (daemon thread) context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _as_coroutine(awaitable)).result()
    return asyncio.run(_as_coroutine(awaitable))


def _serialize_metadata(meta: SpecDict) -> str:
    return json.dumps(meta, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def _coerce_dict(value: Any) -> SpecDict:
    if isinstance(value, dict):
        return cast(SpecDict, value)
    return cast(SpecDict, getattr(value, "__dict__", {}))


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return cast(list[Any], value)
    if isinstance(value, tuple):
        return list(cast(tuple[Any, ...], value))
    return []


class MetaLoopOrchestrator:
    """Advances self-improvement iterations through the 3-phase pipeline:
    PLANNING -> IMPLEMENTING -> REVIEWING -> ACCEPTED/REJECTED.
    """

    _backlog: Any
    _store: Any
    _max_retries: int
    _runner: Any | None
    _workspace_root: str
    _benchmark_blocking: bool

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

    @property
    def store(self) -> Any:
        return self._store

    @property
    def runner(self) -> Any | None:
        return self._runner

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    @property
    def task_controller(self) -> Any | None:
        if self._runner is None:
            return None
        return getattr(self._runner, "task_controller", None)

    def workspace_root_path(self) -> Path:
        workspace = self._workspace_root or ""
        if workspace:
            return Path(workspace)
        return Path.cwd()

    def set_runner(self, runner: Any) -> None:
        """Update the runner reference (for hot-reload)."""
        self._runner = runner

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_provider(self) -> Any:
        """Clone the LLM provider from the runner's agent. Returns None if unavailable."""
        try:
            if self._runner is None:
                return None
            agent = getattr(self._runner, "agent", None)
            if agent is None:
                return None
            provider = getattr(agent, "provider", None)
            if provider is None:
                return None
            return provider.clone()
        except Exception:
            return None

    def _get_model(self, attempt: int = 1) -> str:
        """Return the model to use for metaloop LLM calls.

        Escalates to a more capable model on retry:
          attempt 1 → default haiku (or settings override)
          attempt 2 → sonnet
          attempt 3+ → runner's primary model (provider.model)
        """
        if attempt <= 1:
            if self._runner is not None:
                settings = getattr(self._runner, "settings", None)
                if settings is not None:
                    model = getattr(settings, "metaloop_model", None)
                    if model:
                        return str(model)
            return _DEFAULT_MODEL
        if attempt == 2:
            return _SONNET_MODEL
        # attempt 3+: use the runner's primary model
        primary = self._get_primary_model()
        return primary if primary else _SONNET_MODEL

    def _get_primary_model(self) -> str | None:
        """Return the runner's configured primary model, or None."""
        try:
            if self._runner is None:
                return None
            agent = getattr(self._runner, "agent", None)
            if agent is None:
                return None
            provider = getattr(agent, "provider", None)
            if provider is None:
                return None
            model = getattr(provider, "model", None)
            return str(model) if model else None
        except Exception:
            return None

    def _query_relevant_lessons(self, goal: str, limit: int = 10) -> list[SpecDict]:
        """Query stored lessons relevant to the current iteration goal.

        Relevance scoring: count keyword overlap between lesson summary
        and goal text. Return top-N by relevance score (descending).
        """
        if not hasattr(self._store, "list_lessons"):
            return []
        try:
            all_lessons_raw = self._store.list_lessons(limit=50)
        except Exception:
            log.debug("metaloop_lessons_query_failed")
            return []
        all_lessons_raw_list: list[Any] = (
            cast(list[Any], all_lessons_raw) if isinstance(all_lessons_raw, list) else []
        )
        if not all_lessons_raw:
            return []
        all_lessons = [_coerce_dict(item) for item in all_lessons_raw_list]
        if not all_lessons:
            return []

        goal_keywords = {w for w in goal.lower().split() if len(w) >= 3}
        if not goal_keywords:
            return all_lessons[:limit]

        scored: list[tuple[float, SpecDict]] = []
        for lesson in all_lessons:
            summary = str(lesson.get("summary", ""))
            category = str(lesson.get("category", ""))
            summary_keywords = {w for w in str(summary).lower().split() if len(w) >= 3}
            overlap = len(goal_keywords & summary_keywords)
            category_boost = 1.5 if category in ("mistake", "rollback_pattern") else 1.0
            score = overlap * category_boost
            if score > 0:
                scored.append((score, lesson))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [lesson for _, lesson in scored[:limit]]

    def _check_kernel_approval_granted(self, spec_id: str) -> bool:
        """Return True if any kernel spec_approval for this spec was approved."""
        if self._runner is None:
            return False
        try:
            tc = self._runner.task_controller
            store = tc.store
            if not hasattr(store, "list_approvals"):
                return False
            task_id = f"metaloop-{spec_id}"
            approvals = store.list_approvals(task_id=task_id, status="approved")
            return bool(approvals)
        except Exception:
            log.debug("metaloop_kernel_approval_check_failed", spec_id=spec_id, exc_info=True)
            return False

    def _update_metadata(self, spec_id: str, key: str, value: Any) -> SpecDict:
        """Merge *value* under *key* in the spec's metadata and persist."""
        entry = self._store.get_spec_entry(spec_id=spec_id)
        data = _coerce_dict(entry)
        metadata = _parse_metadata(data.get("metadata"))
        metadata[key] = value
        self._store.update_spec_status(
            spec_id=spec_id,
            status=data.get("status", "pending"),
            metadata=_serialize_metadata(metadata),
        )
        return metadata

    def _capture_baseline_metrics(self) -> dict[str, Any]:
        """Capture current typecheck and lint error counts as a baseline.

        Runs quick shell commands (pyright error count and ruff violation count)
        so the benchmark can later do delta comparison — only failing if the
        iteration *introduced* new errors rather than requiring zero errors.
        """
        import subprocess

        workspace = self._workspace_root or ""
        if not workspace:
            import os as _os

            workspace = _os.environ.get("HERMIT_WORKSPACE_ROOT", "") or _os.getcwd()

        typecheck_errors = 0
        lint_violations = 0

        # Count typecheck errors (pyright)
        # Use .venv/bin/pyright directly since `uv run pyright` may not find
        # the dev dependency when running from the uv tool environment.
        try:
            from pathlib import Path as _Path

            pyright_bin = _Path(workspace) / ".venv" / "bin" / "pyright"
            pyright_cmd = str(pyright_bin) if pyright_bin.is_file() else "uv run pyright"
            proc = subprocess.run(
                f"{pyright_cmd} 2>&1 | tail -3",
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=180,
                shell=True,
            )
            import re as _re

            combined = proc.stdout + proc.stderr
            m = _re.search(r"(\d+)\s+error", combined)
            if m:
                typecheck_errors = int(m.group(1))
            log.info(
                "baseline_typecheck_raw",
                rc=proc.returncode,
                stdout_tail=proc.stdout[-200:] if proc.stdout else "",
                parsed=typecheck_errors,
            )
        except Exception:
            log.warning("baseline_typecheck_capture_failed", exc_info=True)

        # Count lint violations (ruff)
        try:
            proc = subprocess.run(
                ["uv", "run", "ruff", "check", "."],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            import re as _re

            m = _re.search(r"Found\s+(\d+)\s+error", proc.stdout + proc.stderr, _re.IGNORECASE)
            if m:
                lint_violations = int(m.group(1))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            log.debug("baseline_lint_capture_failed")

        baseline: dict[str, Any] = {
            "typecheck_errors": typecheck_errors,
            "lint_violations": lint_violations,
            "captured_at": time.time(),
        }
        log.info(
            "metaloop_baseline_captured",
            typecheck_errors=typecheck_errors,
            lint_violations=lint_violations,
        )
        return baseline

    def _measure_verification_baseline(self, plan: list[SpecDict]) -> list[SpecDict]:
        """Run each verification_plan entry's measurement_command to capture actual baselines.

        Runs before implementation so the benchmark runner can later compare
        real before/after deltas instead of relying on LLM-guessed ``before_expected``.
        """
        import os
        import subprocess

        results: list[SpecDict] = []
        workspace = self._workspace_root or ""
        if not workspace:
            workspace = os.environ.get("HERMIT_WORKSPACE_ROOT", "") or os.getcwd()
        for entry_raw in plan[:10]:  # cap at 10 measurements
            entry = _coerce_dict(entry_raw)
            cmd = str(entry.get("measurement_command", ""))
            if not cmd:
                continue
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                results.append(
                    {
                        "metric": entry.get("metric", "unknown"),
                        "command": cmd,
                        "before_value": proc.stdout.strip()[:500],
                        "exit_code": proc.returncode,
                        "measured_at": time.time(),
                    }
                )
            except Exception:
                results.append(
                    {
                        "metric": entry.get("metric", "unknown"),
                        "command": cmd,
                        "before_value": "(measurement failed)",
                        "exit_code": -1,
                        "measured_at": time.time(),
                    }
                )
        log.info(
            "metaloop_verification_baseline_captured",
            measurement_count=len(results),
        )
        return results

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
        data = _coerce_dict(entry)
        goal = str(data.get("goal", ""))
        metadata = _parse_metadata(data.get("metadata"))

        # Idempotency guard: if spec approval is already pending in the kernel,
        # skip the expensive research + spec-gen pipeline and check if granted.
        # Re-running research on every tick when approval is pending would block
        # the poller thread (network I/O) and starve IMPLEMENTING processing.
        approval_decision = cast(SpecDict, metadata.get("spec_approval_decision", {}))
        if approval_decision.get("method") == "pending_kernel_approval":
            # Check if any kernel approval for this spec was actually granted.
            approved = self._check_kernel_approval_granted(state.spec_id)
            if not approved:
                log.debug("metaloop_planning_approval_pending_wait", spec_id=state.spec_id)
                return state
            # Approval granted — clear pending decision and proceed to decompose.
            log.info("metaloop_planning_kernel_approval_granted", spec_id=state.spec_id)
            spec_data = cast(SpecDict, metadata.get("generated_spec", {}))
            research_data = cast(SpecDict, metadata.get("research", {}))
            if spec_data:
                self._run_decomposition(
                    state.spec_id, spec_data, research_data, attempt=state.attempt
                )
                baseline = self._capture_baseline_metrics()
                self._update_metadata(state.spec_id, "pre_implementation_baseline", baseline)
                verification_plan = cast(list[SpecDict], spec_data.get("verification_plan", []))
                if verification_plan:
                    vb = self._measure_verification_baseline(verification_plan)
                    self._update_metadata(state.spec_id, "verification_baseline", vb)
                return self._backlog.advance_phase(state.spec_id, PipelinePhase.IMPLEMENTING)
            # No spec data saved — fall through and re-run full pipeline
            self._update_metadata(state.spec_id, "spec_approval_decision", {})

        # Record planning start time for timeout detection
        if not metadata.get("planning_started_at"):
            self._update_metadata(state.spec_id, "planning_started_at", time.time())

        # --- Step 1: Research ---
        research_data = self._run_research(state.spec_id, goal, data, metadata)

        # --- Step 2: Spec generation (LLM with deterministic fallback) ---
        spec_data = self._run_spec_generation(
            state.spec_id, goal, research_data, attempt=state.attempt
        )
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
        self._run_decomposition(state.spec_id, spec_data, research_data, attempt=state.attempt)

        # --- Step 5: Capture pre-implementation baseline metrics ---
        baseline = self._capture_baseline_metrics()
        self._update_metadata(state.spec_id, "pre_implementation_baseline", baseline)

        # --- Step 6: Capture verification_plan baseline measurements ---
        verification_plan = spec_data.get("verification_plan", [])
        if verification_plan:
            verification_baseline = self._measure_verification_baseline(verification_plan)
            self._update_metadata(state.spec_id, "verification_baseline", verification_baseline)

        return self._backlog.advance_phase(state.spec_id, PipelinePhase.IMPLEMENTING)

    def _run_research(
        self,
        spec_id: str,
        goal: str,
        data: SpecDict,
        metadata: SpecDict,
    ) -> SpecDict:
        """Run the research pipeline and store results in metadata."""
        raw_hints = data.get("research_hints")
        hints: list[str] = []
        if raw_hints:
            try:
                parsed = (
                    _coerce_list(json.loads(raw_hints))
                    if isinstance(raw_hints, str)
                    else _coerce_list(raw_hints)
                )
                hints = [str(h) for h in parsed]
            except (json.JSONDecodeError, TypeError):
                pass

        # Query and inject relevance-filtered prior lessons as hints
        prior_lessons = self._query_relevant_lessons(goal)
        for lesson in prior_lessons:
            summary = str(lesson.get("summary", ""))
            category = str(lesson.get("category", ""))
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
        from hermit.plugins.builtin.hooks.research.tools import get_pipeline

        workspace = self._workspace_root or ""
        if not workspace:
            import os

            workspace = os.environ.get("HERMIT_WORKSPACE_ROOT", "") or os.getcwd()
        global_pipeline = get_pipeline()
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
        findings_dicts: list[SpecDict] = [
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
            "sources": sorted({str(f.source) for f in report.findings}),
            "prior_lessons": [
                {
                    "lesson_id": (ls.get("lesson_id", "")),
                    "category": (ls.get("category", "")),
                    "summary": (ls.get("summary", "")),
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
        research_data: SpecDict,
        *,
        attempt: int = 1,
    ) -> SpecDict | None:
        """Generate a spec via LLM, falling back to deterministic generator."""
        from hermit.plugins.builtin.hooks.research.models import (
            ResearchFinding,
            ResearchReport,
        )

        findings_raw = _coerce_list(research_data.get("findings", ()))
        findings = tuple(
            ResearchFinding(
                source=str(f.get("source", "")),
                title=str(f.get("title", "")),
                content=str(f.get("content", "")),
                relevance=float(f.get("relevance", 0.0)),
                url=str(f.get("url", "")),
                file_path=str(f.get("file_path", "")),
            )
            for f in cast(list[SpecDict], findings_raw)
        )
        report = ResearchReport(
            goal=str(research_data.get("goal", goal)),
            findings=findings,
            knowledge_gaps=tuple(str(g) for g in research_data.get("knowledge_gaps", ())),
            query_count=int(research_data.get("query_count", 0)),
            duration_seconds=float(research_data.get("duration_seconds", 0.0)),
        )

        # Build lesson-derived constraints
        lesson_constraints: list[str] = []
        prior_lessons = _coerce_list(research_data.get("prior_lessons", []))
        for ls in prior_lessons:
            lesson = _coerce_dict(ls)
            cat = str(lesson.get("category", ""))
            summary = str(lesson.get("summary", ""))
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
            model = self._get_model(attempt)
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
            "verification_plan": [dict(e) for e in spec.verification_plan],
            "research_ref": spec.research_ref,
            "trust_zone": spec.trust_zone,
        }
        self._update_metadata(spec_id, "generated_spec", spec_data)
        return spec_data

    def _check_spec_approval(
        self,
        spec_id: str,
        spec_data: SpecDict,
        data: SpecDict,
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
        data = _coerce_dict(entry)
        metadata = _parse_metadata(data.get("metadata"))

        # Determine risk level
        risk_budget = cast(SpecDict, metadata.get("risk_budget", {}))
        risk_band = str(risk_budget.get("band", ""))
        if not risk_band:
            trust_zone = str(spec_data.get("trust_zone", "normal"))
            risk_band = {"strict": "high", "normal": "medium", "relaxed": "low"}.get(
                trust_zone, "medium"
            )

        # Determine policy_profile
        policy_profile = str(metadata.get("policy_profile", ""))
        if not policy_profile:
            source = str(data.get("source", ""))
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
                            "file_count": len(cast(list[Any], spec_data.get("file_plan", []))),
                            "constraints": cast(list[Any], spec_data.get("constraints", [])),
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
        spec_data: SpecDict,
        research_data: SpecDict,
        *,
        attempt: int = 1,
    ) -> None:
        """Decompose spec into a task DAG via LLM, falling back to deterministic."""
        from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec

        # Collect lesson-referenced files for priority boosting
        lesson_files: set[str] = set()
        prior_lessons_raw = research_data.get("prior_lessons", [])
        prior_lessons = _coerce_list(prior_lessons_raw)
        for ls in prior_lessons:
            lesson = _coerce_dict(ls)
            lesson_id = str(lesson.get("lesson_id", ""))
            if lesson_id and hasattr(self._store, "get_lesson"):
                try:
                    full = self._store.get_lesson(lesson_id)
                    full_dict = _coerce_dict(full)
                    if full:
                        raw_files = full_dict.get("applicable_files")
                        if isinstance(raw_files, str):
                            try:
                                parsed_files = _coerce_list(json.loads(raw_files))
                                lesson_files.update(str(f) for f in parsed_files)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        elif isinstance(raw_files, list):
                            lesson_files.update(str(f) for f in _coerce_list(raw_files))
                except Exception:
                    pass

        file_plan_raw = _coerce_list(spec_data.get("file_plan", []))

        # Boost lesson-referenced files by prepending them if not present
        existing_paths = {str(e.get("path", "")) for e in file_plan_raw}
        boosted_entries: list[SpecDict] = []
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
            model = self._get_model(attempt)
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
        data = _coerce_dict(entry)
        metadata = _parse_metadata(data.get("metadata"))

        # If DAG already started (has timestamp), skip — let timeout checker handle it
        if metadata.get("implementing_started_at"):
            return state

        decomposition = _coerce_dict(metadata.get("decomposition_plan", {}))
        steps_raw = decomposition.get("steps", [])
        steps = cast(list[SpecDict], steps_raw) if isinstance(steps_raw, list) else []
        if not steps:
            return self._backlog.advance_phase(state.spec_id, PipelinePhase.REVIEWING)

        if self._runner is None:
            return self._backlog.advance_phase(state.spec_id, PipelinePhase.REVIEWING)

        from hermit.kernel.task.services.dag_builder import StepNode

        nodes = [
            StepNode(
                key=str(step.get("key", "")),
                kind=str(step.get("kind", "execute")),
                title=str(step.get("title", step.get("key", ""))),
                depends_on=cast(list[str], step.get("depends_on", ())),
                metadata=cast(dict[str, Any], step.get("metadata", {})),
            )
            for step in steps
        ]
        tc = self._runner.task_controller
        spec_data = _coerce_dict(metadata.get("generated_spec", {}))
        criteria = list(spec_data.get("acceptance_criteria", []))
        try:
            result = tc.start_dag_task(
                conversation_id=f"metaloop-{state.spec_id}",
                goal=f"Implement spec {state.spec_id}",
                source_channel="metaloop",
                nodes=nodes,
                policy_profile="autonomous",
                workspace_root=self._workspace_root,
                acceptance_criteria=criteria or None,
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
        # start_dag_task may return (task, ...) or a single object.
        if isinstance(result, tuple):
            result_tuple = cast(tuple[Any, ...], result)
            task = result_tuple[0] if result_tuple else None
        elif isinstance(result, list):
            result_list = cast(list[Any], result)
            task = result_list[0] if result_list else None
        else:
            task = result

        if task is None:
            return self._backlog.mark_failed(
                state.spec_id,
                error="start_dag_task returned empty result",
                max_retries=self._max_retries,
            )
        task_id_attr = getattr(task, "task_id", None)
        dag_task_id = str(task_id_attr) if task_id_attr is not None else None
        # Write dag_task_id and implementing_started_at directly — avoid
        # advance_phase self-transition which races with the poller's
        # _check_implementing_timeout reading the same spec in the same tick.
        if dag_task_id:
            self._store.update_spec_status(
                spec_id=state.spec_id,
                status="implementing",
                dag_task_id=dag_task_id,
            )
        self._update_metadata(state.spec_id, "implementing_started_at", time.time())
        # on_subtask_complete advances to REVIEWING when the DAG task finishes.
        return IterationState(
            spec_id=state.spec_id,
            phase=PipelinePhase.IMPLEMENTING,
            attempt=state.attempt,
            revision_cycle=state.revision_cycle,
            dag_task_id=dag_task_id,
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
        data = _coerce_dict(entry)
        metadata = _parse_metadata(data.get("metadata"))

        # Record reviewing start time for timeout detection
        if not metadata.get("reviewing_started_at"):
            self._update_metadata(state.spec_id, "reviewing_started_at", time.time())
            # Re-read metadata after update
            entry = self._store.get_spec_entry(spec_id=state.spec_id)
            data = _coerce_dict(entry)
            metadata = _parse_metadata(data.get("metadata"))

        decomposition = _coerce_dict(metadata.get("decomposition_plan", {}))
        _steps = cast(list[SpecDict], decomposition.get("steps", []))
        spec_data = cast(SpecDict, metadata.get("generated_spec", {}))

        # Extract changed file paths from spec's file_plan (authoritative source)
        file_plan = spec_data.get("file_plan", [])
        changed_files: list[str] = [
            fp["path"] for fp in file_plan if isinstance(fp, dict) and fp.get("path")
        ]

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
            self._store_exhaustion_retrospective(state.spec_id, revision_cycle)
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
        spec_data: SpecDict,
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
        metadata: SpecDict,
    ) -> IterationState | None:
        """Run benchmark and learning after council accepts."""
        impl_info = _coerce_dict(metadata.get("implementation", {}))
        worktree_path = impl_info.get("worktree_path")

        # Extract changed file paths from spec's file_plan (authoritative source)
        spec_data = metadata.get("generated_spec") or {}
        file_plan = spec_data.get("file_plan", [])
        changed_files: list[str] = [
            fp["path"] for fp in file_plan if isinstance(fp, dict) and fp.get("path")
        ]

        # --- Benchmark ---
        benchmark_passed = True
        benchmark_result = None
        try:
            from hermit.plugins.builtin.hooks.benchmark.runner import BenchmarkRunner

            # Extract verification_plan from spec metadata
            spec_data = _coerce_dict(metadata.get("generated_spec", {}))
            vp_raw = cast(list[SpecDict], spec_data.get("verification_plan", []))
            verification_plan = tuple(dict(e) for e in vp_raw) if vp_raw else None

            # Retrieve pre-implementation baseline for delta comparison
            pre_impl_baseline = metadata.get("pre_implementation_baseline")

            # Retrieve verification baseline (actual before measurements)
            verification_baseline = metadata.get("verification_baseline")

            runner = BenchmarkRunner(self._store)
            benchmark_result = _run_async(
                runner.run(
                    state.spec_id,
                    state.spec_id,
                    worktree_path,
                    verification_plan=verification_plan,
                    baseline_metrics=pre_impl_baseline,
                    changed_files=changed_files or None,
                    verification_baseline=verification_baseline,
                )
            )

            benchmark_data: dict[str, Any] = {
                "check_passed": benchmark_result.check_passed,
                "test_total": benchmark_result.test_total,
                "test_passed": benchmark_result.test_passed,
                "coverage": benchmark_result.coverage,
                "lint_violations": benchmark_result.lint_violations,
                "typecheck_errors": benchmark_result.typecheck_errors,
                "duration_seconds": benchmark_result.duration_seconds,
                "regression_detected": benchmark_result.regression_detected,
                "compared_to_baseline": dict(benchmark_result.compared_to_baseline),
                "delta_info": dict(benchmark_result.delta_info),
                "tier_reached": benchmark_result.tier_reached,
                "strategy_used": benchmark_result.strategy_used,
            }

            # Store verification results alongside benchmark data
            if benchmark_result.verification_results:
                benchmark_data["verification_results"] = [
                    dict(r) for r in benchmark_result.verification_results
                ]
                vr_passed = sum(1 for r in benchmark_result.verification_results if r.get("passed"))
                vr_total = len(benchmark_result.verification_results)
                benchmark_data["verification_summary"] = {
                    "total": vr_total,
                    "passed": vr_passed,
                    "failed": vr_total - vr_passed,
                    "all_passed": vr_passed == vr_total,
                }

            self._update_metadata(state.spec_id, "benchmark", benchmark_data)

            if self._benchmark_blocking:
                if not benchmark_result.check_passed:
                    benchmark_passed = False
                    log.warning(
                        "metaloop_benchmark_check_failed",
                        spec_id=state.spec_id,
                        test_passed=benchmark_result.test_passed,
                        test_total=benchmark_result.test_total,
                    )
                elif benchmark_result.regression_detected:
                    benchmark_passed = False
                    compared = benchmark_result.compared_to_baseline
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
            if self._benchmark_blocking:
                benchmark_passed = False
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
            # Attempt benchmark self-repair before giving up
            repair_cycle = int(metadata.get("benchmark_repair_cycle", 0))
            if repair_cycle < MAX_BENCHMARK_REPAIR_CYCLES and benchmark_result is not None:
                repair_goal = self._build_repair_goal(benchmark_result)
                if repair_goal:
                    log.info(
                        "metaloop_benchmark_repair_start",
                        spec_id=state.spec_id,
                        repair_cycle=repair_cycle + 1,
                        max_cycles=MAX_BENCHMARK_REPAIR_CYCLES,
                    )
                    return self._start_benchmark_repair(
                        state.spec_id,
                        repair_goal,
                        metadata,
                    )

            return self._backlog.mark_failed(
                state.spec_id,
                error="Benchmark failed or regression detected",
                max_retries=self._max_retries,
            )

        log.info("metaloop_accepted", spec_id=state.spec_id)
        return self._backlog.advance_phase(state.spec_id, PipelinePhase.ACCEPTED)

    # ------------------------------------------------------------------
    # Benchmark self-repair helpers
    # ------------------------------------------------------------------

    def _build_repair_goal(self, result: Any) -> str:
        """Build a targeted repair goal from benchmark error details.

        Reads BenchmarkResult.error_details to identify error categories
        (typecheck, test_failure, lint) and constructs a goal string with
        specific file paths and error summaries (truncated).

        Returns an empty string if no actionable errors can be identified.
        """
        parts: list[str] = []

        error_details = getattr(result, "error_details", ()) or ()
        for detail in error_details:
            category = getattr(detail, "category", "")
            count = getattr(detail, "count", 0)
            file_paths = getattr(detail, "file_paths", ())
            summary = getattr(detail, "summary", "")

            if category == "typecheck":
                files_str = ", ".join(file_paths[:10]) if file_paths else "see output"
                parts.append(f"Fix {count} typecheck error(s) in: {files_str}.")
                if summary:
                    parts.append(f"Errors:\n{summary[:300]}")

            elif category == "test_failure":
                test_names = ", ".join(file_paths[:10]) if file_paths else "see output"
                parts.append(f"Fix {count} failing test(s): {test_names}.")
                if summary:
                    parts.append(f"Failures:\n{summary[:300]}")

            elif category == "lint":
                files_str = ", ".join(file_paths[:10]) if file_paths else "see output"
                parts.append(f"Fix {count} lint violation(s) in: {files_str}.")
                if summary:
                    parts.append(f"Violations:\n{summary[:300]}")

        # Fallback: use raw_output if no structured errors were parsed
        if not parts:
            raw = getattr(result, "raw_output", "") or ""
            if raw:
                parts.append(f"Fix benchmark failures. Output:\n{raw[:500]}")

        if not parts:
            return ""

        goal = "Benchmark repair: " + " ".join(parts)
        goal += "\n\nRun `make check` to verify all fixes pass."
        return goal

    def _start_benchmark_repair(
        self,
        spec_id: str,
        repair_goal: str,
        metadata: SpecDict,
    ) -> IterationState | None:
        """Create a repair DAG and transition back to IMPLEMENTING.

        Builds a simple two-step DAG: a repair step that fixes the errors
        and a verify step that re-runs `make check`.
        """
        repair_cycle = int(metadata.get("benchmark_repair_cycle", 0)) + 1

        # Persist repair metadata and clear implementing_started_at for re-entry
        entry = self._store.get_spec_entry(spec_id=spec_id)
        if entry is None:
            return self._backlog.mark_failed(
                spec_id,
                error="Spec entry not found during benchmark repair",
                max_retries=self._max_retries,
            )
        data = _coerce_dict(entry)
        meta = _parse_metadata(data.get("metadata"))
        meta["benchmark_repair_cycle"] = repair_cycle
        meta["benchmark_repair_goal"] = repair_goal
        meta.pop("implementing_started_at", None)

        # Build a repair decomposition plan with two steps:
        # 1. repair — apply fixes for the benchmark errors
        # 2. verify — re-run make check to confirm
        meta["decomposition_plan"] = {
            "spec_id": spec_id,
            "steps": [
                {
                    "key": "repair",
                    "kind": "execute",
                    "title": f"Benchmark repair (cycle {repair_cycle})",
                    "depends_on": [],
                    "metadata": {
                        "goal": repair_goal,
                        "repair_cycle": repair_cycle,
                    },
                },
                {
                    "key": "verify",
                    "kind": "review",
                    "title": "Verify benchmark repair",
                    "depends_on": ["repair"],
                    "metadata": {
                        "goal": "Run make check and verify all checks pass.",
                        "repair_cycle": repair_cycle,
                    },
                },
            ],
            "dependency_graph": {"repair": [], "verify": ["repair"]},
            "estimated_duration_minutes": 10,
        }

        self._store.update_spec_status(
            spec_id=spec_id,
            status=data.get("status", "reviewing"),
            metadata=_serialize_metadata(meta),
        )

        log.info(
            "metaloop_benchmark_repair_scheduled",
            spec_id=spec_id,
            repair_cycle=repair_cycle,
            max_cycles=MAX_BENCHMARK_REPAIR_CYCLES,
        )

        # Transition REVIEWING -> IMPLEMENTING (benchmark repair loop)
        return self._backlog.advance_phase(spec_id, PipelinePhase.IMPLEMENTING)

    def _run_learning(self, state: IterationState) -> None:
        """Run IterationLearner and create followup specs for mistake lessons."""
        entry = self._store.get_spec_entry(spec_id=state.spec_id)
        if entry is None:
            return
        data = _coerce_dict(entry)
        metadata = _parse_metadata(data.get("metadata"))
        benchmark_data = cast(SpecDict, metadata.get("benchmark") or {})
        current_depth = int(metadata.get("followup_depth", 0))

        if not benchmark_data:
            return

        try:
            from hermit.plugins.builtin.hooks.benchmark.learning import (
                IterationLearner,
            )
            from hermit.plugins.builtin.hooks.benchmark.models import (
                BenchmarkErrorDetail,
                BenchmarkResult,
            )

            error_details: list[BenchmarkErrorDetail] = []
            for error_detail in _coerce_list(benchmark_data.get("error_details", ())):
                if isinstance(error_detail, BenchmarkErrorDetail):
                    error_details.append(error_detail)
                else:
                    error_detail_dict = _coerce_dict(error_detail)
                    error_details.append(
                        BenchmarkErrorDetail(
                            category=str(error_detail_dict.get("category", "")),
                            count=int(error_detail_dict.get("count", 0)),
                            summary=str(error_detail_dict.get("summary", "")),
                            file_paths=tuple(
                                str(path)
                                for path in _coerce_list(error_detail_dict.get("file_paths", ()))
                            ),
                        )
                    )

            br = BenchmarkResult(
                iteration_id=state.spec_id,
                spec_id=state.spec_id,
                check_passed=bool(benchmark_data.get("check_passed", False)),
                test_total=int(benchmark_data.get("test_total", 0)),
                test_passed=int(benchmark_data.get("test_passed", 0)),
                coverage=float(benchmark_data.get("coverage", 0.0)),
                lint_violations=int(benchmark_data.get("lint_violations", 0)),
                typecheck_errors=int(benchmark_data.get("typecheck_errors", 0)),
                duration_seconds=float(benchmark_data.get("duration_seconds", 0.0)),
                regression_detected=bool(benchmark_data.get("regression_detected", False)),
                compared_to_baseline=cast(
                    dict[str, Any], benchmark_data.get("compared_to_baseline", {})
                ),
                statistical_analysis=cast(
                    dict[str, Any] | None, benchmark_data.get("statistical_analysis")
                ),
                metadata=cast(dict[str, Any], benchmark_data.get("metadata", {})),
                error_details=tuple(error_details),
                raw_output=str(benchmark_data.get("raw_output", "")),
                verification_results=tuple(
                    _coerce_dict(item)
                    for item in _coerce_list(benchmark_data.get("verification_results", ()))
                ),
                delta_info=cast(dict[str, Any], benchmark_data.get("delta_info", {})),
                tier_reached=str(benchmark_data.get("tier_reached", "")),
                strategy_used=str(benchmark_data.get("strategy_used", "")),
            )
            learner = IterationLearner(self._store)
            lessons_raw = _run_async(learner.learn(state.spec_id, br))
            lessons = _coerce_list(lessons_raw)

            # Create followup specs for mistake lessons with safety limits
            mistake_lessons = [
                _coerce_dict(ls) for ls in lessons if _coerce_dict(ls).get("category") == "mistake"
            ]
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
                goal_text = f"Fix: {lesson.get('summary', '')}"
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
                            f"Lesson: {getattr(lesson, 'summary', '')}",
                        ],
                        metadata={
                            "followup_from": state.spec_id,
                            "lesson_id": getattr(lesson, "lesson_id", ""),
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
        metadata: SpecDict,
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
            data = _coerce_dict(entry)
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
    # Retrospective on revision exhaustion
    # ------------------------------------------------------------------

    def _store_exhaustion_retrospective(self, spec_id: str, revision_cycle: int) -> None:
        """Record a retrospective lesson when the revision budget is exhausted."""
        if not hasattr(self._store, "create_lesson"):
            log.debug("metaloop_retrospective_no_store", spec_id=spec_id)
            return

        goal = ""
        last_directive = ""
        try:
            entry = self._store.get_spec_entry(spec_id=spec_id)
            if entry is not None:
                data = _coerce_dict(entry)
                goal = data.get("goal", "") or ""
                meta = _parse_metadata(data.get("metadata"))
                cv = cast(SpecDict, meta.get("council_verdict", {}))
                last_directive = str(cv.get("revision_directive", ""))
        except Exception:
            pass

        goal_part = f" for goal: {goal!r}" if goal else ""
        directive_part = f" Last council directive: {last_directive!r}." if last_directive else ""
        summary = (
            f"Revision budget exhausted after {revision_cycle} cycles{goal_part}."
            f"{directive_part} "
            "The review council repeatedly rejected this implementation. "
            "Consider: (1) Is the spec too ambiguous? "
            "(2) Is the architectural approach fundamentally flawed? "
            "(3) Should the task be decomposed differently?"
        )

        try:
            self._store.create_lesson(
                lesson_id=f"retro-{uuid.uuid4().hex[:12]}",
                iteration_id=spec_id,
                category="mistake",
                summary=summary,
            )
            log.info("metaloop_retrospective_stored", spec_id=spec_id)
        except Exception:
            log.warning(
                "metaloop_retrospective_store_failed",
                spec_id=spec_id,
                exc_info=True,
            )

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

        data = _coerce_dict(entry)
        spec_id = data["spec_id"]
        phase_str = data.get("status", "pending")

        try:
            phase = PipelinePhase(phase_str)
        except ValueError:
            return None

        if phase != PipelinePhase.IMPLEMENTING:
            return None

        metadata = _parse_metadata(data.get("metadata"))
        impl_info = _coerce_dict(metadata.get("implementation", {}))
        worktree_path = str(impl_info.get("worktree_path", "")) or None

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

                ws = SelfModifyWorkspace(self.workspace_root_path())
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
        backlog: Any,
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
        workspace_root = self._orchestrator.workspace_root
        if not workspace_root:
            return
        try:
            from hermit.kernel.execution.self_modify.workspace import (
                SelfModifyWorkspace,
            )

            ws = SelfModifyWorkspace(self._orchestrator.workspace_root_path())
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
        if self._orchestrator.task_controller is None:
            return None
        try:
            tc = self._orchestrator.task_controller
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

    def _check_implementing_timeout(self, entry: SpecDict) -> bool:
        """Check if an IMPLEMENTING spec has timed out.

        Returns True if work was done (spec advanced or failed).
        """
        entry_dict = _coerce_dict(entry)
        spec_id = str(entry_dict.get("spec_id", ""))
        metadata = _parse_metadata(entry_dict.get("metadata"))
        started_at = metadata.get("implementing_started_at")
        dag_task_id_raw = entry_dict.get("dag_task_id")
        dag_task_id = str(dag_task_id_raw) if isinstance(dag_task_id_raw, str) else None

        if started_at is None:
            # DAG task not yet created by _handle_implementing — do nothing.
            # _handle_implementing will set implementing_started_at when it
            # creates the DAG task.  Writing the timestamp here would cause
            # _handle_implementing's guard to fire, preventing DAG creation.
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
                    tc = self._orchestrator.task_controller
                    if tc is not None:
                        tc.cancel_task(dag_task_id, reason="implementation timeout exceeded")
                    else:
                        log.warning(
                            "metaloop_implementing_cancel_task_controller_missing",
                            spec_id=spec_id,
                            dag_task_id=dag_task_id,
                        )
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
            max_retries=self._orchestrator.max_retries,
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
                    entry = _coerce_dict(impl_entry)
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
        #    PLANNING and REVIEWING: one spec per tick (synchronous, potentially slow).
        #    IMPLEMENTING: scan ALL unstarted specs so multiple parallel DAGs can be
        #    kicked off without waiting for round-robin turns (limit=1 starves them).
        if hasattr(self._backlog._store, "list_spec_backlog"):
            try:
                for status in (
                    PipelinePhase.PLANNING.value,
                    PipelinePhase.REVIEWING.value,
                ):
                    active = self._backlog._store.list_spec_backlog(status=status, limit=1)
                    if active:
                        entry = _coerce_dict(active[0])
                        spec_id = entry["spec_id"]

                        # Check phase timeout before advancing
                        metadata = _parse_metadata(entry.get("metadata"))
                        timed_out = False
                        if status == PipelinePhase.PLANNING.value:
                            started_at = metadata.get("planning_started_at")
                            if started_at is not None:
                                elapsed = time.time() - float(started_at)
                                if elapsed > PHASE_TIMEOUT_PLANNING:
                                    log.warning(
                                        "metaloop_planning_timeout",
                                        spec_id=spec_id,
                                        elapsed_seconds=int(elapsed),
                                    )
                                    self._backlog.mark_failed(
                                        spec_id,
                                        error=f"PLANNING timed out after {int(elapsed)}s",
                                        max_retries=self._orchestrator._max_retries,
                                    )
                                    timed_out = True
                                    did_work = True
                        elif status == PipelinePhase.REVIEWING.value:
                            started_at = metadata.get("reviewing_started_at")
                            if started_at is not None:
                                elapsed = time.time() - float(started_at)
                                if elapsed > PHASE_TIMEOUT_REVIEWING:
                                    log.warning(
                                        "metaloop_reviewing_timeout",
                                        spec_id=spec_id,
                                        elapsed_seconds=int(elapsed),
                                    )
                                    self._backlog.mark_failed(
                                        spec_id,
                                        error=f"REVIEWING timed out after {int(elapsed)}s",
                                        max_retries=self._orchestrator._max_retries,
                                    )
                                    timed_out = True
                                    did_work = True

                        if not timed_out:
                            log.info(
                                "metaloop_poller_advancing",
                                spec_id=spec_id,
                                phase=status,
                            )
                            self._orchestrator.advance(spec_id)
                            did_work = True
            except Exception:
                log.exception(
                    "metaloop_poller_advance_error",
                    phase="planning_or_reviewing",
                )

            # IMPLEMENTING: advance all unstarted specs (no implementing_started_at).
            # Specs that already have a DAG running are handled by
            # _check_implementing_timeout above — advance() on them is a cheap no-op.
            try:
                all_impl = self._backlog._store.list_spec_backlog(
                    status=PipelinePhase.IMPLEMENTING.value, limit=10
                )
                for impl_entry in all_impl:
                    entry = _coerce_dict(impl_entry)
                    impl_meta = _parse_metadata(entry.get("metadata", "{}"))
                    if not impl_meta.get("implementing_started_at"):
                        spec_id = entry["spec_id"]
                        log.info(
                            "metaloop_poller_advancing",
                            spec_id=spec_id,
                            phase=PipelinePhase.IMPLEMENTING.value,
                        )
                        self._orchestrator.advance(spec_id)
                        did_work = True
            except Exception:
                log.exception(
                    "metaloop_poller_advance_error",
                    phase=PipelinePhase.IMPLEMENTING.value,
                )

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
        self._store: Any
        self._poll_interval: float
        self._stop_event: threading.Event
        self._thread: threading.Thread | None
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
            signal_data = _coerce_dict(signal)
            signal_id = signal_data.get("signal_id")
            if not signal_id:
                continue
            source_kind = signal_data.get("source_kind")
            if source_kind not in self._ELIGIBLE_SOURCES:
                continue

            suggested_goal = signal_data.get("suggested_goal")
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
                risk_level = signal_data.get("risk_level", "normal")
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
