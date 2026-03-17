#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Hermit Approval Workflow Demo
#
# This script demonstrates the approval → approve/deny → receipt chain.
# Because approvals require interactive operator input, this script runs
# the task and then guides you through the remaining steps manually.
#
# Prerequisites:
#   - Hermit installed (bash install.sh or make install)
#   - Provider env vars configured
# =============================================================================

: "${HERMIT_PROVIDER:=claude}"
: "${HERMIT_MODEL:=claude-sonnet-4-20250514}"

echo "=== Hermit Approval Workflow Demo ==="
echo ""
echo "Provider : ${HERMIT_PROVIDER}"
echo "Model    : ${HERMIT_MODEL}"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Run a task that is likely to trigger an approval gate
#
# File-write operations typically require approval under default policy.
# ---------------------------------------------------------------------------
echo "--- Step 1: Running a task that may require approval ---"
echo ""
echo "Running: hermit run \"Create a file called /tmp/hermit-demo.txt with today's date\""
echo ""

hermit run "Create a file called /tmp/hermit-demo.txt containing today's date."

echo ""

# ---------------------------------------------------------------------------
# Step 2: Guide the operator through approval (if the task was suspended)
# ---------------------------------------------------------------------------
echo "--- Step 2: Check task status ---"
echo ""

hermit task list

echo ""
echo "If a task above shows status 'suspended' or 'pending_approval':"
echo ""
echo "  Approve:  hermit task approve <task-id>"
echo "  Deny:     hermit task approve <task-id> --deny"
echo ""

# ---------------------------------------------------------------------------
# Step 3: Inspect the receipt trail
# ---------------------------------------------------------------------------
echo "--- Step 3: Inspect receipts ---"
echo ""
echo "After approving or denying, run:"
echo ""
echo "  hermit task show <task-id>"
echo "  hermit task proof <task-id>"
echo ""

# ---------------------------------------------------------------------------
# Step 4: Optional rollback
# ---------------------------------------------------------------------------
echo "--- Step 4: Rollback (optional) ---"
echo ""
echo "To reverse the action (if supported by the receipt type):"
echo ""
echo "  hermit task rollback <task-id>"
echo ""
echo "=== Approval Workflow Demo complete ==="
