#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="${1:-}"
ACTION="${2:-status}"
ADAPTER="${HERMIT_ADAPTER:-feishu}"

if [[ -z "${ENV_NAME}" ]]; then
  echo "Usage: scripts/hermit-envctl.sh <prod|dev|test> <up|restart|down|status|logs>" >&2
  exit 1
fi

case "${ENV_NAME}" in
  prod)
    BASE_DIR="${HOME}/.hermit"
    APP_PATH="${HOME}/Applications/Hermit Menu.app"
    ;;
  dev)
    BASE_DIR="${HOME}/.hermit-dev"
    APP_PATH="${HOME}/Applications/Hermit Menu Dev.app"
    ;;
  test)
    BASE_DIR="${HOME}/.hermit-test"
    APP_PATH="${HOME}/Applications/Hermit Menu Test.app"
    ;;
  *)
    echo "Unknown environment: ${ENV_NAME}" >&2
    echo "Allowed values: prod, dev, test" >&2
    exit 1
    ;;
esac

service_pids() {
  ps eww -ax -o pid=,command= | awk -v base="${BASE_DIR}" -v adapter="${ADAPTER}" '
    index($0, "HERMIT_BASE_DIR=" base) && index($0, "-m hermit.main serve --adapter " adapter) {print $1}
  '
}

menubar_pids() {
  ps eww -ax -o pid=,command= | awk -v base="${BASE_DIR}" -v adapter="${ADAPTER}" '
    index($0, "HERMIT_BASE_DIR=" base) && index($0, "-m hermit.companion.menubar --adapter " adapter) {print $1}
  '
}

kill_pids() {
  local pids="$1"
  if [[ -z "${pids}" ]]; then
    return
  fi
  for pid in ${pids}; do
    kill "${pid}" 2>/dev/null || true
  done
  sleep 1
  for pid in ${pids}; do
    kill -9 "${pid}" 2>/dev/null || true
  done
}

ensure_macos_deps() {
  /opt/homebrew/bin/uv sync --extra dev --extra macos >/dev/null
}

ensure_menu_app() {
  if [[ -d "${APP_PATH}" ]]; then
    return
  fi
  /bin/zsh -lc "cd '${ROOT_DIR}' && scripts/hermit-menubar-install-env.sh ${ENV_NAME} --adapter ${ADAPTER}" >/dev/null
}

start_service() {
  mkdir -p "${BASE_DIR}/logs"
  nohup /bin/zsh -lc "cd '${ROOT_DIR}' && scripts/hermit-env.sh ${ENV_NAME} serve --adapter ${ADAPTER}" \
    > "${BASE_DIR}/logs/${ENV_NAME}-restart-service.out" 2>&1 &
}

start_menubar() {
  ensure_macos_deps
  ensure_menu_app
  open -na "${APP_PATH}"
}

print_status() {
  echo "ENV=${ENV_NAME}"
  echo "BASE_DIR=${BASE_DIR}"
  echo "PID_FILE=$(cat "${BASE_DIR}/serve-${ADAPTER}.pid" 2>/dev/null || true)"
  echo ""
  echo "[service]"
  ps eww -ax -o pid=,command= | awk -v base="${BASE_DIR}" -v adapter="${ADAPTER}" '
    index($0, "HERMIT_BASE_DIR=" base) && index($0, "-m hermit.main serve --adapter " adapter) {print}
  '
  echo ""
  echo "[menubar]"
  ps eww -ax -o pid=,command= | awk -v base="${BASE_DIR}" -v adapter="${ADAPTER}" '
    index($0, "HERMIT_BASE_DIR=" base) && index($0, "-m hermit.companion.menubar --adapter " adapter) {print}
  '
}

case "${ACTION}" in
  up)
    start_service
    sleep 3
    start_menubar
    sleep 2
    print_status
    ;;
  restart)
    kill_pids "$(service_pids)"
    kill_pids "$(menubar_pids)"
    start_service
    sleep 3
    start_menubar
    sleep 2
    print_status
    ;;
  down)
    kill_pids "$(service_pids)"
    kill_pids "$(menubar_pids)"
    print_status
    ;;
  status)
    print_status
    ;;
  logs)
    tail -n 50 "${BASE_DIR}/logs/${ENV_NAME}-restart-service.out" 2>/dev/null || true
    ;;
  *)
    echo "Usage: scripts/hermit-envctl.sh <prod|dev|test> <up|restart|down|status|logs>" >&2
    exit 1
    ;;
esac
