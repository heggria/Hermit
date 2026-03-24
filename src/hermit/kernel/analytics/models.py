from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ActionRiskEntry:
    """A single action with its risk classification and outcome."""

    action_type: str
    risk_level: str | None = None
    result_code: str = "succeeded"
    receipt_id: str | None = None
    rollback_supported: bool = False


@dataclass
class GovernanceMetrics:
    """Aggregated governance analytics for a time window."""

    task_throughput: int = 0
    approval_rate: float = 0.0
    avg_approval_latency: float = 0.0
    rollback_rate: float = 0.0
    evidence_sufficiency_avg: float = 0.0
    tool_usage_counts: dict[str, int] = field(default_factory=dict)
    action_class_distribution: dict[str, int] = field(default_factory=dict)
    risk_entries: list[ActionRiskEntry] = field(default_factory=list)
    window_start: float = 0.0
    window_end: float = 0.0
