#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/hermit-common.sh"
UV_BIN="$(resolve_uv_bin)"
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
    APP_PATH="${HOME}/Applications/Hermit.app"
    ;;
  dev)
    BASE_DIR="${HOME}/.hermit-dev"
    APP_PATH="${HOME}/Applications/Hermit Dev.app"
    ;;
  test)
    BASE_DIR="${HOME}/.hermit-test"
    APP_PATH="${HOME}/Applications/Hermit Test.app"
    ;;
  *)
    echo "Unknown environment: ${ENV_NAME}" >&2
    echo "Allowed values: prod, dev, test" >&2
    exit 1
    ;;
esac

WATCH_PID_FILE="${BASE_DIR}/watch-${ADAPTER}.pid"

read_pid_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    return
  fi
  tr -d '[:space:]' < "${path}"
}

pid_is_live() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" 2>/dev/null
}

format_pid_state() {
  local path="$1"
  local pid
  pid="$(read_pid_file "${path}")"
  if [[ -z "${pid}" ]]; then
    printf '\n'
    return
  fi
  if pid_is_live "${pid}"; then
    printf '%s (live)\n' "${pid}"
    return
  fi
  printf '%s (stale)\n' "${pid}"
}

matching_pids() {
  local marker="$1"
  ps eww -ax -o pid=,command= | awk -v base="${BASE_DIR}" -v marker="${marker}" '
    index($0, "HERMIT_BASE_DIR=" base) && index($0, marker) {print $1}
  '
}

# Fallback: find service processes by PID file or by command pattern when
# HERMIT_BASE_DIR is not in the environment (e.g. uv-tool-installed binary).
_fallback_service_pids() {
  local pid
  pid="$(read_pid_file "${BASE_DIR}/serve-${ADAPTER}.pid")"
  if pid_is_live "${pid}"; then
    printf '%s\n' "${pid}"
    return
  fi
  # Match installed binary: "hermit serve --adapter <adapter>"
  # Also match dev checkout: "-m hermit.surfaces.cli serve --adapter <adapter>"
  ps -ax -o pid=,command= | awk -v adapter="${ADAPTER}" '
    /hermit.*serve.*--adapter / && index($0, adapter) {print $1}
  '
}

service_pids() {
  local pids
  pids="$(matching_pids "-m hermit.surfaces.cli serve --adapter ${ADAPTER}")"
  if [[ -n "${pids}" ]]; then
    printf '%s\n' "${pids}"
    return
  fi
  _fallback_service_pids
}

menubar_pids() {
  matching_pids "-m hermit.apps.companion.menubar --adapter ${ADAPTER}"
}

watch_pid() {
  local pid
  pid="$(read_pid_file "${WATCH_PID_FILE}")"
  if pid_is_live "${pid}"; then
    printf '%s\n' "${pid}"
  fi
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

kill_watch() {
  local pid
  pid="$(watch_pid)"
  if [[ -z "${pid}" ]]; then
    return
  fi
  kill "${pid}" 2>/dev/null || true
  sleep 1
  if pid_is_live "${pid}"; then
    kill -9 "${pid}" 2>/dev/null || true
  fi
}

clear_runtime_pid_files() {
  rm -f "${BASE_DIR}/serve-${ADAPTER}.pid" "${WATCH_PID_FILE}"
}

ensure_macos_deps() {
  "${UV_BIN}" sync --project "${ROOT_DIR}" --python 3.13 --group dev --extra macos >/dev/null
}

ensure_menu_app() {
  if [[ -d "${APP_PATH}" ]]; then
    return
  fi
  /bin/zsh -lc "cd '${ROOT_DIR}' && scripts/hermit-menubar-install-env.sh ${ENV_NAME} --adapter ${ADAPTER}" >/dev/null
}

start_service() {
  if [[ -n "$(service_pids)" ]]; then
    return
  fi
  mkdir -p "${BASE_DIR}/logs"
  if [[ "${ENV_NAME}" == "prod" ]]; then
    # prod uses the uv-tool-installed hermit binary
    local hermit_bin
    hermit_bin="$(command -v hermit 2>/dev/null || echo "${HOME}/.local/bin/hermit")"
    nohup env HERMIT_BASE_DIR="${BASE_DIR}" "${hermit_bin}" serve --adapter "${ADAPTER}" \
      > "${BASE_DIR}/logs/${ENV_NAME}-restart-service.out" 2>&1 &
  else
    # dev/test use the repo checkout via hermit-env.sh
    nohup /bin/zsh -lc "cd '${ROOT_DIR}' && scripts/hermit-env.sh ${ENV_NAME} serve --adapter ${ADAPTER}" \
      > "${BASE_DIR}/logs/${ENV_NAME}-restart-service.out" 2>&1 &
  fi
}

start_menubar() {
  if [[ -n "$(menubar_pids)" ]]; then
    return
  fi
  # ensure_macos_deps is called once before start_service; skip here to avoid
  # concurrent uv sync races with the background service process.
  ensure_menu_app
  open -na "${APP_PATH}"
}

print_status() {
  local service_pid_state
  local service_discovered
  local watch_pid_value
  local watch_pid_state
  watch_pid_value="$(watch_pid)"
  service_pid_state="$(format_pid_state "${BASE_DIR}/serve-${ADAPTER}.pid")"
  if [[ -z "${service_pid_state}" ]]; then
    service_discovered="$(service_pids | paste -sd ',' -)"
    if [[ -n "${service_discovered}" ]]; then
      service_pid_state="missing (discovered live process: ${service_discovered})"
    fi
  fi
  watch_pid_state="$(format_pid_state "${WATCH_PID_FILE}")"
  if [[ -z "${watch_pid_state}" && -n "${watch_pid_value}" ]]; then
    watch_pid_state="missing (discovered live process: ${watch_pid_value})"
  fi
  echo "ENV=${ENV_NAME}"
  echo "BASE_DIR=${BASE_DIR}"
  echo "PID_FILE=${service_pid_state}"
  echo "WATCH_PID_FILE=${watch_pid_state}"
  echo ""
  echo "[service]"
  local svc_pids
  svc_pids="$(service_pids)"
  if [[ -n "${svc_pids}" ]]; then
    for p in ${svc_pids}; do
      ps -p "${p}" -o pid=,start=,stat=,command= 2>/dev/null
    done
  fi
  echo ""
  echo "[menubar]"
  ps eww -ax -o pid=,command= | awk -v base="${BASE_DIR}" -v adapter="${ADAPTER}" '
    index($0, "HERMIT_BASE_DIR=" base " ") && index($0, "-m hermit.apps.companion.menubar --adapter " adapter) {print}
  '
  echo ""
  echo "[watch]"
  if [[ -n "${watch_pid_value}" ]]; then
    ps -p "${watch_pid_value}" -o pid=,ppid=,etime=,command=
  fi
}

case "${ACTION}" in
  up)
    if [[ -n "$(watch_pid)" ]]; then
      echo "watch mode is already active for ${ENV_NAME}/${ADAPTER}; leaving it in control."
      print_status
      exit 0
    fi
    # Sync deps (including macos extras) BEFORE starting the service so that
    # ``uv run`` inside hermit-env.sh does not recreate / corrupt the venv
    # while ensure_macos_deps is running concurrently.
    ensure_macos_deps
    start_service
    sleep 3
    start_menubar
    sleep 2
    print_status
    ;;
  restart)
    kill_watch
    kill_pids "$(service_pids)"
    kill_pids "$(menubar_pids)"
    clear_runtime_pid_files
    ensure_macos_deps
    start_service
    sleep 3
    start_menubar
    sleep 2
    print_status
    ;;
  down)
    kill_watch
    kill_pids "$(service_pids)"
    kill_pids "$(menubar_pids)"
    clear_runtime_pid_files
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
