#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Hermit Quick Start Demo
#
# This script walks through the minimal task lifecycle:
#   run → list → show → proof
#
# Prerequisites:
#   - Hermit installed (bash install.sh or make install)
#   - Provider env vars configured (see below)
# =============================================================================

# ---------------------------------------------------------------------------
# Provider configuration — set these before running, or export them in your
# shell / ~/.hermit/.env
# ---------------------------------------------------------------------------
: "${HERMIT_PROVIDER:=claude}"
: "${HERMIT_MODEL:=claude-sonnet-4-20250514}"
# Make sure the matching API key is set:
#   ANTHROPIC_API_KEY  (for claude provider)
#   OPENAI_API_KEY     (for codex provider)

echo "=== Hermit Quick Start ==="
echo ""
echo "Provider : ${HERMIT_PROVIDER}"
echo "Model    : ${HERMIT_MODEL}"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Run a task
# ---------------------------------------------------------------------------
echo "--- Step 1: Running a task ---"
echo ""

hermit run "List the files in the current directory and summarize what you see."

echo ""

# ---------------------------------------------------------------------------
# Step 2: List tasks to find the one we just created
# ---------------------------------------------------------------------------
echo "--- Step 2: Listing tasks ---"
echo ""

hermit task list

echo ""

# ---------------------------------------------------------------------------
# Step 3: Show the most recent task
#
# We grab the first task ID from the list output. Adjust the parsing if the
# CLI output format changes.
# ---------------------------------------------------------------------------
echo "--- Step 3: Showing task details ---"
echo ""

TASK_ID=$(hermit task list --json 2>/dev/null | python3 -c "
import sys, json
tasks = json.load(sys.stdin)
if tasks:
    print(tasks[0]['id'])
" 2>/dev/null || true)

if [ -n "${TASK_ID:-}" ]; then
    hermit task show "$TASK_ID"
    echo ""

    # -----------------------------------------------------------------------
    # Step 4: Verify the proof chain
    # -----------------------------------------------------------------------
    echo "--- Step 4: Verifying proof chain ---"
    echo ""

    hermit task proof "$TASK_ID"
else
    echo "(Could not extract task ID automatically."
    echo " Run 'hermit task list' and then 'hermit task show <id>' manually.)"
fi

echo ""
echo "=== Quick Start complete ==="
