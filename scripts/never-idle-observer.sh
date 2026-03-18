#!/usr/bin/env bash
# Never-Idle Observer — real-time structured log monitor for Hermit webhook events.
set -euo pipefail

ADAPTER="${1:-webhook}"
LOG_DIR="${HOME}/.hermit/logs"
LOG_FILE="${LOG_DIR}/${ADAPTER}-stdout.log"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

echo -e "\n${BOLD}══════════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD} NEVER-IDLE OBSERVER — watching ${ADAPTER} logs${RESET}"
echo -e "${BOLD} Log file: ${LOG_FILE}${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════════════════════${RESET}\n"

if [ ! -f "$LOG_FILE" ]; then
    echo -e "${YELLOW}Log file not found: ${LOG_FILE}${RESET}"
    echo "Waiting for log file to appear..."
    while [ ! -f "$LOG_FILE" ]; do
        sleep 2
    done
    echo -e "${GREEN}Log file appeared, starting tail...${RESET}\n"
fi

# Event patterns and their display colours
# We use grep --line-buffered to ensure real-time output
tail -f "$LOG_FILE" 2>/dev/null | while IFS= read -r line; do
    # Auto-park focus switch
    if echo "$line" | grep -q "auto_park_focus_switched"; then
        echo -e "${YELLOW}🟡 PARK${RESET}    $(echo "$line" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(f\"from={d.get('from_task','')} → to={d.get('to_task','')}\")
except: print(sys.stdin.read() if hasattr(sys.stdin,'read') else '')
" 2>/dev/null || echo "$line")"
        continue
    fi

    if echo "$line" | grep -q "auto_unpark_focus_switched"; then
        echo -e "${YELLOW}🟡 UNPARK${RESET}  $(echo "$line" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(f\"resumed={d.get('to_task','')}\")
except: print('')
" 2>/dev/null || echo "$line")"
        continue
    fi

    # Task suspended
    if echo "$line" | grep -q "mark_suspended"; then
        echo -e "${RED}🔴 SUSPEND${RESET} $(echo "$line" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(f\"task={d.get('task_id','')} kind={d.get('waiting_kind','')}\")
except: print('')
" 2>/dev/null || echo "$line")"
        continue
    fi

    # Webhook dispatch
    if echo "$line" | grep -q "webhook_dispatch"; then
        echo -e "${GREEN}🟢 DISPATCH${RESET} $(echo "$line" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(f\"route={d.get('route','')} session={d.get('session_id','')}\")
except: print('')
" 2>/dev/null || echo "$line")"
        continue
    fi

    # Patrol signals
    if echo "$line" | grep -q "patrol_signal_emitted\|patrol_complete"; then
        echo -e "${BLUE}🔵 PATROL${RESET}  $(echo "$line" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    event = d.get('event','')
    count = d.get('signal_count', d.get('issues_found', ''))
    print(f\"event={event} count={count}\")
except: print('')
" 2>/dev/null || echo "$line")"
        continue
    fi

    # Trigger engine
    if echo "$line" | grep -q "trigger_followup_created"; then
        echo -e "${MAGENTA}🟣 TRIGGER${RESET} $(echo "$line" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(f\"source={d.get('source_task','')} followup={d.get('followup_task','')}\")
except: print('')
" 2>/dev/null || echo "$line")"
        continue
    fi

    # Task completed/failed
    if echo "$line" | grep -q "task\.completed\|task\.failed\|finalize_result"; then
        echo -e "${CYAN}📋 TASK${RESET}    $(echo "$line" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    status = d.get('status', d.get('event',''))
    task = d.get('task_id','')
    print(f\"task={task} status={status}\")
except: print('')
" 2>/dev/null || echo "$line")"
        continue
    fi

    # Approval events
    if echo "$line" | grep -q "approval"; then
        echo -e "${CYAN}📋 APPROVAL${RESET} $line"
        continue
    fi
done
