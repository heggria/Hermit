#!/usr/bin/env bash
# Record a terminal demo of Hermit's core task flow.
# Produces docs/assets/demo.cast (asciinema) and docs/assets/demo.gif (agg).
#
# Must be run from the repo root.
set -euo pipefail

CAST_FILE="docs/assets/demo.cast"
GIF_FILE="docs/assets/demo.gif"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Create the demo script inline
DEMO_SCRIPT=$(mktemp /tmp/hermit-demo.XXXXXX.sh)
cat > "$DEMO_SCRIPT" << 'INNERSCRIPT'
#!/usr/bin/env bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:$PATH"

# Load hermit env
set -a
source ~/.hermit/.env 2>/dev/null || true
set +a

# Type a command slowly for visual effect, showing "hermit" but running "uv run hermit"
fake_type() {
  local display="$1"
  shift
  echo ""
  printf "\033[1;32m❯\033[0m "
  for (( i=0; i<${#display}; i++ )); do
    printf '%s' "${display:$i:1}"
    sleep 0.04
  done
  echo ""
  sleep 0.3
  "$@"
  sleep 1.5
}

echo ""
echo "  Hermit — Local-first governed agent kernel"
echo "  Task -> Approval -> Receipt -> Proof -> Rollback"
echo ""
sleep 2

# Step 1: Run a task
fake_type 'hermit run "Summarize this repository and leave a durable task record"' \
  uv run hermit run "Summarize this repository and leave a durable task record"
sleep 1

# Step 2: List tasks
fake_type 'hermit task list' \
  uv run hermit task list
sleep 1

# Step 3: Show the latest task
TASK_ID=$(uv run hermit task list 2>/dev/null | grep -oE 'task_[a-f0-9]+' | head -n 1 || true)
if [ -n "$TASK_ID" ]; then
  fake_type "hermit task show $TASK_ID" \
    uv run hermit task show "$TASK_ID"
  sleep 1
  fake_type "hermit task proof $TASK_ID" \
    uv run hermit task proof "$TASK_ID"
else
  echo ""
  echo "  (No task found — try running 'hermit run' first)"
fi

sleep 2
echo ""
echo "  Done. Task executed, inspected, and proven — all locally."
echo ""
sleep 3
INNERSCRIPT

chmod +x "$DEMO_SCRIPT"

echo "Recording demo to $CAST_FILE ..."
asciinema rec \
  --overwrite \
  --cols 100 \
  --rows 30 \
  --command "bash $DEMO_SCRIPT" \
  "$CAST_FILE"

rm -f "$DEMO_SCRIPT"

echo "Converting to GIF ..."
agg \
  --cols 100 \
  --rows 30 \
  --font-size 14 \
  --theme monokai \
  "$CAST_FILE" "$GIF_FILE"

echo ""
echo "Done!"
echo "  Cast: $CAST_FILE"
echo "  GIF:  $GIF_FILE"
