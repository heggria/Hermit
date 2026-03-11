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

EXISTING_AUTOSTART_ADAPTERS="$(python3 - <<'PY'
from pathlib import Path
import plistlib

launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
prefixes = ("com.hermit.serve", "com.moltforge.serve")
adapters = set()

if launch_agents_dir.exists():
    for prefix in prefixes:
        for plist in launch_agents_dir.glob(f"{prefix}*.plist"):
            try:
                data = plistlib.loads(plist.read_bytes())
            except Exception:
                continue
            args = data.get("ProgramArguments")
            if not isinstance(args, list):
                continue
            args = [str(arg) for arg in args]
            adapter = None
            if "--adapter" in args:
                idx = args.index("--adapter")
                if idx + 1 < len(args):
                    adapter = args[idx + 1]
            elif len(args) >= 3 and args[1] == "serve":
                adapter = args[2]
            if adapter:
                adapters.add(adapter)

print(" ".join(sorted(adapters)))
PY
)"

# ── 3. workspace ─────────────────────────────────────────────────────────────
hermit init -q 2>/dev/null || hermit init

if [[ -n "$EXISTING_AUTOSTART_ADAPTERS" ]]; then
  echo "→ Refreshing existing auto-start services..."
  for adapter in $EXISTING_AUTOSTART_ADAPTERS; do
    hermit autostart enable --adapter "$adapter"
  done
fi

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
