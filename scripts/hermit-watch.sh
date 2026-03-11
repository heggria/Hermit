#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec /opt/homebrew/bin/uv run --project "${ROOT_DIR}" --extra dev --python 3.11 \
  python "${ROOT_DIR}/scripts/hermit-watch.py" "$@"
