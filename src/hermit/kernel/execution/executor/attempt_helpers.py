"""Backward-compatibility shim — delegates to execution_helpers.

All canonical implementations now live in ``execution_helpers``.  This module
re-exports them under the original un-prefixed names so existing callers that
import ``attempt_helpers.set_attempt_phase`` / ``.contract_refs`` /
``.load_witness_payload`` continue to work without modification.

New code should import directly from ``execution_helpers`` instead.
"""

from __future__ import annotations

from hermit.kernel.execution.executor.execution_helpers import (
    _contract_refs as contract_refs,
)
from hermit.kernel.execution.executor.execution_helpers import (
    _load_witness_payload as load_witness_payload,
)
from hermit.kernel.execution.executor.execution_helpers import (
    _set_attempt_phase as set_attempt_phase,
)

__all__ = [
    "contract_refs",
    "load_witness_payload",
    "set_attempt_phase",
]
