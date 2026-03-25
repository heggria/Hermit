#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/hermit-common.sh"
UV_BIN="$(resolve_uv_bin)"
ENV_NAME="${1:-}"

if [[ -z "${ENV_NAME}" ]]; then
  echo "Usage: scripts/hermit-env.sh <prod|dev|test> <hermit args...>" >&2
  exit 1
fi

shift

# Clear runtime overrides from the current shell so environments do not bleed
# into each other. Hermit will read the selected base dir's `.env` itself.
for key in \
  HERMIT_PROFILE \
  HERMIT_PROVIDER \
  HERMIT_MODEL \
  HERMIT_AUTH_TOKEN \
  HERMIT_BASE_URL \
  HERMIT_CUSTOM_HEADERS \
  HERMIT_CLAUDE_API_KEY \
  HERMIT_CLAUDE_AUTH_TOKEN \
  HERMIT_CLAUDE_BASE_URL \
  HERMIT_CLAUDE_HEADERS \
  HERMIT_OPENAI_API_KEY \
  HERMIT_OPENAI_BASE_URL \
  HERMIT_OPENAI_HEADERS \
  HERMIT_FEISHU_APP_ID \
  HERMIT_FEISHU_APP_SECRET \
  HERMIT_SCHEDULER_FEISHU_CHAT_ID \
  ANTHROPIC_API_KEY \
  OPENAI_API_KEY \
  FEISHU_APP_ID \
  FEISHU_APP_SECRET; do
  unset "${key}" || true
done

case "${ENV_NAME}" in
  prod)
    export HERMIT_BASE_DIR="${HOME}/.hermit"
    ;;
  dev)
    export HERMIT_BASE_DIR="${HOME}/.hermit-dev"
    ;;
  test)
    export HERMIT_BASE_DIR="${HOME}/.hermit-test"
    ;;
  *)
    echo "Unknown environment: ${ENV_NAME}" >&2
    echo "Allowed values: prod, dev, test" >&2
    exit 1
    ;;
esac

# Sync the venv once to ensure all dependencies are installed, then run with
# --no-sync so that the exec'd process does not rebuild or modify the venv
# (which would race with other uv processes sharing the same .venv).
"${UV_BIN}" sync --project "${ROOT_DIR}" --python 3.13 --group dev --extra macos >/dev/null 2>&1 || true

export PYTHONUNBUFFERED=1

# Export env-specific .env variables so that values read via os.environ.get
# (e.g. HERMIT_POOL_SCALE, HERMIT_LLM_CONCURRENCY) are available to the process.
if [[ -f "${HERMIT_BASE_DIR}/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    export "$key=$value"
  done < "${HERMIT_BASE_DIR}/.env"
fi

exec "${UV_BIN}" run --project "${ROOT_DIR}" --python 3.13 --no-sync python -m hermit.surfaces.cli "$@"
