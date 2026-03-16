# Kernel Code Guidance

All mutations must go through the governed path: Approval → CapabilityGrant → Execution → Receipt. No direct model-to-tool execution.

## Conventions

- `@dataclass` with `from __future__ import annotations` for all record types
- Record naming: `TaskRecord`, `StepRecord`, `ApprovalRecord`; Service naming: `ApprovalService`, `PolicyEngine`
- `structlog.get_logger()` for logging; `_t()` per-module i18n helper
- Artifact store references (`*_ref` fields) for large payloads, not inline data
- Timestamps as `float` (unix time); PEP 604 union syntax (`str | None`)
- Kernel layer is synchronous; async is handled at runner/dispatch level
- Use `cast()` for type narrowing; `field(default_factory=...)` for mutable defaults
