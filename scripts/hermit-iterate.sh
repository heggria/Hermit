#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/hermit-common.sh"
UV_BIN="$(resolve_uv_bin)"

SPEC_FILE="${1:?Usage: scripts/hermit-iterate.sh <spec-file>}"
[[ -f "${SPEC_FILE}" ]] || { echo "Spec not found: ${SPEC_FILE}" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Parse spec frontmatter
# ---------------------------------------------------------------------------
SPEC_CONTENT="$(cat "${SPEC_FILE}")"
SPEC_ID="$(sed -n 's/^id: *//p' "${SPEC_FILE}" | head -1)"
SPEC_TITLE="$(sed -n 's/^title: *["'"'"']\{0,1\}\(.*\)["'"'"']\{0,1\} *$/\1/p' "${SPEC_FILE}" | head -1)"
SPEC_ID="${SPEC_ID:-$(basename "${SPEC_FILE}" .md)}"
SPEC_TITLE="${SPEC_TITLE:-${SPEC_ID}}"

hermit_cmd() {
  "${UV_BIN}" run --project "${ROOT_DIR}" --python 3.13 hermit "$@"
}

export HERMIT_BASE_DIR="${HERMIT_BASE_DIR:-${HOME}/.hermit-dev}"
export PYTHONUNBUFFERED=1

# ---------------------------------------------------------------------------
# Step 1: Create work branch (if not already on one)
# ---------------------------------------------------------------------------
BRANCH="iterate/${SPEC_ID}"
CURRENT_BRANCH="$(git -C "${ROOT_DIR}" rev-parse --abbrev-ref HEAD)"
if [[ "${CURRENT_BRANCH}" != "${BRANCH}" ]]; then
  echo "==> Creating branch ${BRANCH}"
  git -C "${ROOT_DIR}" checkout -b "${BRANCH}" 2>/dev/null \
    || git -C "${ROOT_DIR}" checkout "${BRANCH}"
fi

# ---------------------------------------------------------------------------
# Step 2: Execute Hermit
# ---------------------------------------------------------------------------
PROMPT="Execute the following iteration spec. Follow all constraints. Implement the changes described using available tools. When done, summarize what you accomplished.

IMPORTANT TOOL USAGE RULES:
- When creating or modifying files, ALWAYS use the write_file tool instead of bash/shell commands (cat, echo, heredoc, tee, etc.).
- The write_file tool captures file prestate for rollback support. Bash file writes do NOT support rollback.
- Use bash only for non-file-write operations: running tests, git commands, listing files, etc.
- This is critical for governed execution: every file mutation must produce a rollback-capable receipt.

${SPEC_CONTENT}"

echo "==> Executing Hermit with spec: ${SPEC_FILE}"
hermit_cmd run --policy autonomous "${PROMPT}"

# ---------------------------------------------------------------------------
# Step 3: Find the iteration task (skip memory promotion tasks)
# ---------------------------------------------------------------------------
echo "==> Finding task ID..."
TASK_ID="$(hermit_cmd task list --limit 10 2>/dev/null \
  | grep -v "Promote durable memory" \
  | head -1 \
  | sed 's/\[//;s/\].*//')"

if [[ -z "${TASK_ID}" ]]; then
  echo "ERROR: Could not find iteration task ID" >&2
  exit 1
fi
echo "    Task: ${TASK_ID}"

# ---------------------------------------------------------------------------
# Step 4: Check task status
# ---------------------------------------------------------------------------
TASK_STATUS="$(hermit_cmd task show "${TASK_ID}" 2>/dev/null \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")"
echo "    Status: ${TASK_STATUS}"

if [[ "${TASK_STATUS}" != "completed" ]]; then
  echo "ERROR: Task did not complete (status=${TASK_STATUS}). Skipping PR." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Step 5: Export proof
# ---------------------------------------------------------------------------
PROOF_DIR="${ROOT_DIR}/.hermit-proof"
mkdir -p "${PROOF_DIR}"
PROOF_FILE="${PROOF_DIR}/${SPEC_ID}.json"
echo "==> Exporting proof to ${PROOF_FILE}"
hermit_cmd task proof-export "${TASK_ID}" --output "${PROOF_FILE}"

# Extract proof summary
PROOF_SUMMARY="$(hermit_cmd task proof "${TASK_ID}" 2>/dev/null \
  | python3 -c "
import json, sys
p = json.load(sys.stdin)
proj = p.get('projection', {})
chain = p.get('chain_verification', {})
print(f\"Task: {p['task']['task_id']}\")
print(f\"Status: {p['task']['status']}\")
print(f\"Policy: {p['task'].get('policy_profile', 'default')}\")
print(f\"Proof mode: {p.get('proof_mode', 'unknown')}\")
print(f\"Events: {proj.get('events_processed', 0)}\")
print(f\"Steps: {proj.get('step_count', 0)}\")
print(f\"Receipts: {proj.get('receipt_count', 0)}\")
print(f\"Decisions: {proj.get('decision_count', 0)}\")
print(f\"Grants: {proj.get('capability_grant_count', 0)}\")
print(f\"Chain valid: {chain.get('valid', False)}\")
print(f\"Head hash: {chain.get('head_hash', 'none')[:16]}...\")
")"
echo "${PROOF_SUMMARY}"

# Extract receipt details for PR body
RECEIPT_TABLE="$(hermit_cmd task receipts --task-id "${TASK_ID}" 2>/dev/null \
  | python3 -c "
import json, sys
receipts = json.load(sys.stdin)
for r in receipts:
    rb = '✅' if r['rollback_supported'] else '—'
    status = r.get('rollback_status', 'n/a')
    print(f\"| \`{r['receipt_id'][:20]}\` | {r['action_type']} | {r['result_code']} | {rb} | {status} |\")
" 2>/dev/null || echo "| (unable to extract receipts) | | | | |")"

# ---------------------------------------------------------------------------
# Step 6: Commit and push
# ---------------------------------------------------------------------------
echo "==> Committing changes..."
cd "${ROOT_DIR}"
git add -A
git commit -m "iterate(${SPEC_ID}): ${SPEC_TITLE}

Task: ${TASK_ID}
Proof: .hermit-proof/${SPEC_ID}.json" || echo "(nothing to commit)"

echo "==> Pushing to origin..."
git push -u origin "${BRANCH}"

# ---------------------------------------------------------------------------
# Step 7: Create PR
# ---------------------------------------------------------------------------
echo "==> Creating PR..."
PR_URL="$(gh pr create \
  --title "iterate(${SPEC_ID}): ${SPEC_TITLE}" \
  --body "$(cat <<PREOF
## Summary

Automated iteration driven by \`${SPEC_FILE}\`.

Hermit read the spec, autonomously implemented changes under governed execution (policy → decision → grant → receipt), and exported a verifiable proof chain.

## Proof Summary

\`\`\`
${PROOF_SUMMARY}
\`\`\`

## Receipts

| Receipt | Action | Result | Rollback | Status |
|---------|--------|--------|----------|--------|
${RECEIPT_TABLE}

## Acceptance Criteria

> Run \`make check\` to validate.

## Proof Bundle

Full proof bundle: [\`.hermit-proof/${SPEC_ID}.json\`](.hermit-proof/${SPEC_ID}.json)

---
🤖 Generated by [hermit-iterate](scripts/hermit-iterate.sh) with governed autonomous execution.
PREOF
)" 2>&1)" || PR_URL="(PR creation failed — may already exist)"

echo ""
echo "==========================================="
echo "  Iteration complete"
echo "==========================================="
echo "  Spec:   ${SPEC_FILE}"
echo "  Branch: ${BRANCH}"
echo "  Task:   ${TASK_ID}"
echo "  Status: ${TASK_STATUS}"
echo "  Proof:  ${PROOF_FILE}"
echo "  PR:     ${PR_URL}"
echo "==========================================="
