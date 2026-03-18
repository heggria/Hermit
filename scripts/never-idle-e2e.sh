#!/usr/bin/env bash
# Never-Idle E2E driver — submits 12 tasks, monitors, selectively approves, verifies.
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
PORT="${1:-8321}"
APPROVE_DELAY="${2:-60}"
TIMEOUT="${3:-900}"
BASE_URL="http://127.0.0.1:${PORT}"
TASK_DIR="$(cd "$(dirname "$0")/../specs/never-idle-tasks" && pwd)"

# Tracking arrays
declare -a TASK_IDS=()
declare -a TASK_NAMES=()
MAX_CONCURRENT=0
COMPLETED_WHILE_BLOCKED=0
SIGNALS_EMITTED=0
START_TIME=$(date +%s)

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Helpers ───────────────────────────────────────────────────────────────────
elapsed() {
    local now=$(date +%s)
    local diff=$((now - START_TIME))
    printf "%02d:%02d" $((diff / 60)) $((diff % 60))
}

log_info()  { echo -e "${CYAN}[$(elapsed)]${RESET} $*"; }
log_ok()    { echo -e "${GREEN}[$(elapsed)] ✓${RESET} $*"; }
log_warn()  { echo -e "${YELLOW}[$(elapsed)] !${RESET} $*"; }
log_error() { echo -e "${RED}[$(elapsed)] ✗${RESET} $*"; }

api_get() {
    curl -s --max-time 10 "${BASE_URL}$1" 2>/dev/null || echo "{}"
}

api_post() {
    curl -s --max-time 10 -X POST -H "Content-Type: application/json" -d "$2" "${BASE_URL}$1" 2>/dev/null || echo "{}"
}

# ── Phase 1: Pre-flight ──────────────────────────────────────────────────────
preflight() {
    echo -e "\n${BOLD}══════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD} NEVER-IDLE E2E — Pre-flight Check${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════════════════════════${RESET}\n"

    local health
    health=$(api_get "/health")
    if echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
        log_ok "Webhook server is running at ${BASE_URL}"
    else
        log_error "Webhook server is NOT running at ${BASE_URL}"
        echo ""
        echo "  Start it with:"
        echo "    HERMIT_PATROL_ENABLED=true HERMIT_PATROL_INTERVAL_MINUTES=5 hermit serve --adapter webhook"
        echo ""
        exit 1
    fi

    local routes
    routes=$(api_get "/routes")
    if echo "$routes" | python3 -c "import sys,json; d=json.load(sys.stdin); assert any(r['name']=='fix' for r in d.get('routes',[]))" 2>/dev/null; then
        log_ok "Route 'fix' is registered"
    else
        log_warn "Route 'fix' not found — check ~/.hermit/webhooks.json"
        echo "$routes" | python3 -m json.tool 2>/dev/null || true
    fi
}

# ── Phase 2: Submit tasks ────────────────────────────────────────────────────
submit_tasks() {
    echo -e "\n${BOLD}══════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD} Submitting 12 tasks${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════════════════════════${RESET}\n"

    for f in "$TASK_DIR"/*.json; do
        local name
        name=$(basename "$f" .json)
        log_info "Submitting ${name}..."

        local resp
        resp=$(curl -s --max-time 10 -X POST \
            -H "Content-Type: application/json" \
            -d @"$f" \
            "${BASE_URL}/webhook/fix" 2>/dev/null || echo "ERROR")

        TASK_NAMES+=("$name")
        log_ok "Submitted ${name}"

        sleep 2  # avoid rate-limit
    done

    log_ok "All 12 tasks submitted"
}

# ── Phase 3: Monitor loop ────────────────────────────────────────────────────
print_status_table() {
    local tasks_json="$1"
    local running blocked completed failed queued
    running=$(echo "$tasks_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for t in d.get('tasks',[]) if t.get('status')=='running'))
" 2>/dev/null || echo 0)
    blocked=$(echo "$tasks_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for t in d.get('tasks',[]) if t.get('status') in ('suspended','blocked')))
" 2>/dev/null || echo 0)
    completed=$(echo "$tasks_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for t in d.get('tasks',[]) if t.get('status')=='completed'))
" 2>/dev/null || echo 0)
    failed=$(echo "$tasks_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for t in d.get('tasks',[]) if t.get('status')=='failed'))
" 2>/dev/null || echo 0)
    queued=$(echo "$tasks_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for t in d.get('tasks',[]) if t.get('status') in ('queued','pending')))
" 2>/dev/null || echo 0)

    # Track max concurrent
    if [ "$running" -gt "$MAX_CONCURRENT" ]; then
        MAX_CONCURRENT=$running
    fi
    # Track completed while blocked
    if [ "$blocked" -gt 0 ] && [ "$completed" -gt 0 ]; then
        COMPLETED_WHILE_BLOCKED=$completed
    fi

    echo -e "\n${BOLD}══════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD} NEVER-IDLE E2E — $(elapsed) elapsed${RESET}"
    echo -e "${BOLD} Workers: ${running}/4 active │ Blocked: ${blocked} │ Done: ${completed}/12 │ Failed: ${failed} │ Queued: ${queued}${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════════════════════════${RESET}"

    # Per-task status
    echo "$tasks_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
icons = {'running':'██','suspended':'░░','blocked':'░░','completed':'✓ ','failed':'✗ ','queued':'··','pending':'··'}
for t in d.get('tasks',[]):
    s = t.get('status','?')
    icon = icons.get(s, '? ')
    tid = t.get('task_id','?')[:10]
    title = t.get('title','')[:40]
    print(f'  [{tid}] {icon} {s:<12} {title}')
" 2>/dev/null || true
}

# ── Phase 4: Selective approval ──────────────────────────────────────────────
approve_one() {
    local approvals_json
    approvals_json=$(api_get "/approvals/pending")
    local approval_id
    approval_id=$(echo "$approvals_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
pending = d.get('approvals', [])
if pending:
    print(pending[0].get('approval_id',''))
else:
    print('')
" 2>/dev/null || echo "")

    if [ -n "$approval_id" ] && [ "$approval_id" != "" ]; then
        log_info "Approving: ${approval_id}"
        api_post "/approvals/${approval_id}/approve" '{}' > /dev/null
        log_ok "Approved: ${approval_id}"
        return 0
    fi
    return 1
}

# ── Main loop ─────────────────────────────────────────────────────────────────
monitor_and_approve() {
    echo -e "\n${BOLD}══════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD} Monitoring (timeout: ${TIMEOUT}s, approve after: ${APPROVE_DELAY}s)${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════════════════════════${RESET}\n"

    local approve_started=false
    local approve_last=0
    local iteration=0

    while true; do
        local now=$(date +%s)
        local elapsed_s=$((now - START_TIME))

        # Timeout check
        if [ "$elapsed_s" -ge "$TIMEOUT" ]; then
            log_warn "Timeout reached (${TIMEOUT}s)"
            break
        fi

        # Fetch task status
        local tasks_json
        tasks_json=$(api_get "/tasks?limit=50")
        print_status_table "$tasks_json"

        # Check if all done
        local total_terminal
        total_terminal=$(echo "$tasks_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
tasks = d.get('tasks', [])
terminal = sum(1 for t in tasks if t.get('status') in ('completed','failed','cancelled'))
# Only count webhook tasks (skip startup tasks)
webhook_tasks = [t for t in tasks if 'Webhook' in t.get('title','')]
webhook_terminal = sum(1 for t in webhook_tasks if t.get('status') in ('completed','failed','cancelled'))
total_webhook = len(webhook_tasks)
if total_webhook >= 12 and webhook_terminal >= total_webhook:
    print('DONE')
else:
    print(f'{webhook_terminal}/{total_webhook}')
" 2>/dev/null || echo "0/0")

        if [ "$total_terminal" = "DONE" ]; then
            log_ok "All tasks reached terminal state"
            break
        fi

        # Selective approval (after delay)
        if [ "$elapsed_s" -ge "$APPROVE_DELAY" ]; then
            if [ "$approve_started" != "true" ]; then
                log_info "Approval window opened (after ${APPROVE_DELAY}s delay)"
                approve_started=true
            fi
            # Approve one every 30s
            local approve_elapsed=$((now - approve_last))
            if [ "$approve_elapsed" -ge 30 ]; then
                if approve_one; then
                    approve_last=$now
                fi
            fi
        fi

        sleep 10
        iteration=$((iteration + 1))
    done
}

# ── Phase 5: Collect signals ─────────────────────────────────────────────────
collect_signals() {
    echo -e "\n${BOLD}══════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD} Collecting signals & overnight report${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════════════════════════${RESET}\n"

    # Try overnight endpoint
    local overnight
    overnight=$(api_get "/overnight/report" 2>/dev/null || echo "{}")
    echo "$overnight" | python3 -m json.tool 2>/dev/null || log_warn "No overnight endpoint available"

    # Try signals endpoint
    local signals
    signals=$(api_get "/overnight/signals" 2>/dev/null || echo "{}")
    SIGNALS_EMITTED=$(echo "$signals" | python3 -c "
import sys, json
d = json.load(sys.stdin)
sigs = d.get('signals', [])
print(len(sigs))
" 2>/dev/null || echo 0)
    log_info "Signals emitted: ${SIGNALS_EMITTED}"
}

# ── Phase 6: Verification report ─────────────────────────────────────────────
verification_report() {
    echo -e "\n${BOLD}══════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD} VERIFICATION REPORT${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════════════════════════${RESET}\n"

    local pass_count=0
    local fail_count=0

    # V1: Dispatch parallelism
    if [ "$MAX_CONCURRENT" -ge 3 ]; then
        echo -e "  ${GREEN}✓ V1${RESET} Dispatch parallelism: peak concurrent = ${MAX_CONCURRENT} (>= 3)"
        pass_count=$((pass_count + 1))
    else
        echo -e "  ${RED}✗ V1${RESET} Dispatch parallelism: peak concurrent = ${MAX_CONCURRENT} (< 3)"
        fail_count=$((fail_count + 1))
    fi

    # V2: Approval doesn't block
    if [ "$COMPLETED_WHILE_BLOCKED" -gt 0 ]; then
        echo -e "  ${GREEN}✓ V2${RESET} Approval non-blocking: ${COMPLETED_WHILE_BLOCKED} completed while others blocked"
        pass_count=$((pass_count + 1))
    else
        echo -e "  ${RED}✗ V2${RESET} Approval non-blocking: no tasks completed while others were blocked"
        fail_count=$((fail_count + 1))
    fi

    # V3: Trigger/patrol signals
    if [ "$SIGNALS_EMITTED" -gt 0 ]; then
        echo -e "  ${GREEN}✓ V3${RESET} Signals emitted: ${SIGNALS_EMITTED}"
        pass_count=$((pass_count + 1))
    else
        echo -e "  ${YELLOW}~ V3${RESET} Signals emitted: ${SIGNALS_EMITTED} (may need patrol enabled)"
        # Don't count as fail, it's optional
    fi

    # V5: Zero worker idle (heuristic: max concurrent > 0)
    if [ "$MAX_CONCURRENT" -gt 0 ]; then
        echo -e "  ${GREEN}✓ V5${RESET} Workers were active (max concurrent: ${MAX_CONCURRENT})"
        pass_count=$((pass_count + 1))
    else
        echo -e "  ${RED}✗ V5${RESET} Workers were never active"
        fail_count=$((fail_count + 1))
    fi

    echo ""
    echo -e "${BOLD}  Results: ${pass_count} passed, ${fail_count} failed${RESET}"
    echo -e "${BOLD}  Total elapsed: $(elapsed)${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════════════════════════${RESET}\n"

    if [ "$fail_count" -gt 0 ]; then
        exit 1
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    # Parse named args
    while [[ $# -gt 0 ]]; do
        case $1 in
            --port)     PORT="$2"; BASE_URL="http://127.0.0.1:${PORT}"; shift 2 ;;
            --approve-delay) APPROVE_DELAY="$2"; shift 2 ;;
            --timeout)  TIMEOUT="$2"; shift 2 ;;
            *)          shift ;;
        esac
    done

    preflight
    submit_tasks
    monitor_and_approve
    collect_signals
    verification_report
}

main "$@"
