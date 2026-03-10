#!/usr/bin/env bash
# Hermit — one-command installer for macOS
# Usage: bash install.sh
#   OR:  make          (if Makefile present)
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HOME/.hermit/.env"

# ── 1. uv ────────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  echo "→ Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# ── 2. hermit ─────────────────────────────────────────────────────────────
echo "→ Installing hermit..."
uv tool install --python 3.11 --reinstall "$REPO_DIR" -q

# Ensure uv tool bin is on PATH for this session
export PATH="$(uv tool dir 2>/dev/null || echo "$HOME/.local/bin"):$PATH"

# ── 3. workspace ─────────────────────────────────────────────────────────────
hermit init -q 2>/dev/null || hermit init

# ── 4. auto-save credentials already in env ──────────────────────────────────
mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"

_save() {
  local key="$1" val="${!1}"   # indirect variable expansion
  [[ -z "$val" ]] && return
  grep -q "^${key}=" "$ENV_FILE" 2>/dev/null && return
  echo "${key}=${val}" >> "$ENV_FILE"
  echo "  ✓ ${key}"
}

SAVED=0
for var in \
  ANTHROPIC_API_KEY \
  HERMIT_AUTH_TOKEN \
  HERMIT_BASE_URL \
  HERMIT_CUSTOM_HEADERS \
  HERMIT_MODEL \
  HERMIT_FEISHU_APP_ID \
  HERMIT_FEISHU_APP_SECRET \
  HERMIT_SCHEDULER_FEISHU_CHAT_ID; do
  before=$(wc -l < "$ENV_FILE")
  _save "$var"
  after=$(wc -l < "$ENV_FILE")
  [[ "$after" -gt "$before" ]] && SAVED=$((SAVED + 1))
done

[[ "$SAVED" -gt 0 ]] && echo "  → saved $SAVED credential(s) from current shell"

# ── 5. PATH hint (add to shell profile if needed) ────────────────────────────
UV_BIN="$(uv tool dir 2>/dev/null || echo "$HOME/.local/bin")"
PROFILE_HINT=""
if ! grep -q "$UV_BIN" "$HOME/.zshrc" 2>/dev/null && \
   ! grep -q "$UV_BIN" "$HOME/.bash_profile" 2>/dev/null; then
  PROFILE_HINT="  echo 'export PATH=\"$UV_BIN:\$PATH\"' >> ~/.zshrc && source ~/.zshrc"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "Done!  hermit is ready."
echo ""

HAS_AUTH=$(grep -cE "^(ANTHROPIC_API_KEY|HERMIT_AUTH_TOKEN)=.+" "$ENV_FILE" 2>/dev/null || true)
if [[ "$HAS_AUTH" -gt 0 ]] || [[ -n "${ANTHROPIC_API_KEY}${HERMIT_AUTH_TOKEN}" ]]; then
  echo "  hermit chat"
  [[ -n "$HERMIT_FEISHU_APP_ID" || $(grep -c "FEISHU_APP_ID" "$ENV_FILE" 2>/dev/null) -gt 0 ]] && \
    echo "  hermit serve feishu"
else
  echo "  One more step — add your API key to ~/.hermit/.env:"
  echo "    echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.hermit/.env"
  echo "  Then:"
  echo "    hermit chat"
fi

[[ -n "$PROFILE_HINT" ]] && echo "" && echo "  To use hermit in new terminals:" && echo "    $PROFILE_HINT"
echo ""
