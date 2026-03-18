"""Patrol Engine — daemon thread for periodic code health checks."""

from __future__ import annotations

import threading
import time
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.patrol.checks import BUILTIN_CHECKS
from hermit.plugins.builtin.hooks.patrol.models import PatrolCheckResult, PatrolReport

log = structlog.get_logger()

_SOURCE_KIND_MAP: dict[str, str] = {
    "lint": "lint_violation",
    "test": "test_failure",
    "todo_scan": "todo_scan",
    "coverage": "coverage_drop",
    "security": "security_vuln",
}


class PatrolEngine:
    """Background engine that runs code health checks at a configurable interval.

    Follows the same daemon-thread pattern as SchedulerEngine.
    """

    def __init__(
        self,
        *,
        interval_minutes: int = 60,
        enabled_checks: str = "lint,test,todo_scan",
        workspace_root: str = "",
    ) -> None:
        self._interval = max(1, interval_minutes) * 60
        self._check_names = [c.strip() for c in enabled_checks.split(",") if c.strip()]
        self._workspace_root = workspace_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._runner: Any = None
        self._last_report: PatrolReport | None = None

    def set_runner(self, runner: Any) -> None:
        """Store a reference to the serve-context AgentRunner."""
        self._runner = runner

    def start(self) -> None:
        """Start the patrol daemon thread."""
        self._thread = threading.Thread(target=self._loop, daemon=True, name="patrol-engine")
        self._thread.start()
        log.info("patrol_started", interval=self._interval, checks=self._check_names)

    def stop(self) -> None:
        """Stop the patrol daemon thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        log.info("patrol_stopped")

    @property
    def last_report(self) -> PatrolReport | None:
        """Return the most recent patrol report, if any."""
        return self._last_report

    def run_patrol(self) -> PatrolReport:
        """Execute all enabled checks and return a PatrolReport."""
        report = PatrolReport(started_at=time.time(), workspace_root=self._workspace_root)
        for name in self._check_names:
            check_cls = BUILTIN_CHECKS.get(name)
            if check_cls is None:
                continue
            try:
                check = check_cls()
                result = check.run(self._workspace_root)
                report.checks.append(result)
                report.total_issues += result.issue_count
            except Exception:
                log.exception("patrol_check_failed", check=name)
        report.finished_at = time.time()
        self._last_report = report
        self._emit_signals(report)
        log.info(
            "patrol_complete",
            total_issues=report.total_issues,
            checks=len(report.checks),
        )
        return report

    def _emit_signals(self, report: PatrolReport) -> None:
        """Emit EvidenceSignals for checks that found issues."""
        if self._runner is None:
            return
        tc = getattr(self._runner, "task_controller", None)
        if tc is None:
            return
        store = getattr(tc, "store", None)
        if store is None or not hasattr(store, "create_signal"):
            return

        for check in report.checks:
            if check.status != "issues_found" or check.issue_count == 0:
                continue
            source_kind = _SOURCE_KIND_MAP.get(check.check_name, check.check_name)
            cooldown_key = f"patrol:{check.check_name}"
            if store.check_cooldown(cooldown_key, 3600):
                continue
            self._emit_single(store, check, source_kind, cooldown_key)

    def _emit_single(
        self,
        store: Any,
        check: PatrolCheckResult,
        source_kind: str,
        cooldown_key: str,
    ) -> None:
        from hermit.kernel.signals.models import EvidenceSignal

        risk = "low"
        if source_kind == "security_vuln":
            risk = "critical"
        elif source_kind == "test_failure":
            risk = "medium"

        signal = EvidenceSignal(
            source_kind=source_kind,
            source_ref=f"patrol://{check.check_name}",
            summary=check.summary,
            confidence=0.8,
            evidence_refs=[f"patrol://{check.check_name}/report"],
            suggested_goal=f"Fix {check.check_name} issues: {check.summary}",
            suggested_policy_profile="autonomous" if risk == "low" else "default",
            risk_level=risk,
            cooldown_key=cooldown_key,
            cooldown_seconds=3600,
        )
        store.create_signal(signal)
        log.info(
            "patrol_signal_emitted",
            check=check.check_name,
            source_kind=source_kind,
            issues=check.issue_count,
        )

    def _loop(self) -> None:
        """Main loop — run patrol, then wait for the configured interval."""
        while not self._stop.is_set():
            try:
                self.run_patrol()
            except Exception:
                log.exception("patrol_loop_error")
            self._stop.wait(self._interval)
