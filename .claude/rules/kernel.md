---
paths:
  - "src/hermit/kernel/**"
---

# Kernel Code Conventions

- All mutations must go through the governed path: Approval → CapabilityGrant → Execution → Receipt
- No direct model-to-tool execution — the kernel authorizes all actions
- Use `@dataclass` with `from __future__ import annotations` for all record types
- Record naming: `TaskRecord`, `StepRecord`, `ApprovalRecord` etc.
- Service naming: `ApprovalService`, `PolicyEngine` etc.
- Use `structlog.get_logger()` for logging, not `logging.getLogger()`
- Define `_t()` i18n helper per module: `def _t(key: str, *, default: str | None = None, **kwargs) -> str`
- Use artifact store references (`*_ref` fields) instead of inline data for large payloads
- Timestamps as `float` (unix time): `created_at`, `updated_at`, `started_at`, `finished_at`
- Mutable dataclass defaults via `field(default_factory=list)` or `field(default_factory=dict)`
- Private constants prefixed with underscore: `_BLOCK_TYPES`, `_WITNESS_REQUIRED_ACTIONS`
- PEP 604 union syntax: `str | None` (not `Optional[str]`)
- Use `cast()` for type narrowing, not `assert isinstance()`
- Kernel layer is synchronous; async is handled at runner/dispatch level
