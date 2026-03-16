#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/hermit-common.sh"
UV_BIN="$(resolve_uv_bin)"

exec "${UV_BIN}" run --project "${ROOT_DIR}" --group dev --group typecheck --python 3.13 \
  python "${ROOT_DIR}/scripts/hermit-watch.py" "$@"
