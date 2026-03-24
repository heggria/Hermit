"""Backward-compatibility shim — delegates to execution_helpers.

All canonical implementations now live in ``execution_helpers``.  This module
re-exports them under the original un-prefixed names so existing callers that
import ``attempt_helpers.set_attempt_phase`` / ``.contract_refs`` /
``.load_witness_payload`` continue to work without modification.

New code should import directly from ``execution_helpers`` instead.

.. deprecated::
    Importing from ``attempt_helpers`` is deprecated and will be removed in a
    future release.  Update your imports to use
    ``hermit.kernel.execution.executor.execution_helpers`` directly.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "hermit.kernel.execution.executor.attempt_helpers is deprecated. "
    "Import from hermit.kernel.execution.executor.execution_helpers instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hermit.kernel.execution.executor.execution_helpers import (  # noqa: E402
    contract_refs,
    load_witness_payload,
    set_attempt_phase,
)

__all__ = [
    "contract_refs",
    "load_witness_payload",
    "set_attempt_phase",
]
