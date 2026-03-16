from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast


@dataclass
class ProgressSummary:
    summary: str
    detail: str | None = None
    phase: str | None = None
    progress_percent: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "detail": self.detail,
            "phase": self.phase,
            "progress_percent": self.progress_percent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProgressSummary":
        percent = data.get("progress_percent")
        try:
            progress_percent = int(percent) if percent is not None else None
        except (TypeError, ValueError):
            progress_percent = None
        return cls(
            summary=str(data.get("summary", "") or "").strip(),
            detail=str(data.get("detail", "") or "").strip() or None,
            phase=str(data.get("phase", "") or "").strip() or None,
            progress_percent=progress_percent,
        )

    def signature(self) -> tuple[str, str | None, str | None, int | None]:
        return (
            self.summary,
            self.detail,
            self.phase,
            self.progress_percent,
        )


def normalize_progress_summary(value: Any) -> ProgressSummary | None:
    if isinstance(value, ProgressSummary):
        return value
    if not isinstance(value, dict):
        return None
    d: dict[str, Any] = cast(dict[str, Any], value)
    summary = str(d.get("summary", "") or "").strip()
    if not summary:
        return None
    return ProgressSummary.from_dict(d)


class ProgressSummaryFormatter(Protocol):
    def summarize(self, *, facts: dict[str, Any]) -> ProgressSummary | None: ...
