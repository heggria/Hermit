#!/usr/bin/env bash
# Hermit — fastest macOS install path
# Supports both local execution and remote usage:
#   bash install-macos.sh
#   curl -fsSL https://raw.githubusercontent.com/heggria/Hermit/main/install-macos.sh | bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Hermit's quick installer currently supports macOS only."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_DIR=""
ENV_FILE="$HOME/.hermit/.env"
WHEEL_DIR=""
CLEAN_SCRIPT=""

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf "$TEMP_DIR"
  fi
}
trap cleanup EXIT

if [[ -f "$SCRIPT_DIR/pyproject.toml" && -f "$SCRIPT_DIR/install.sh" ]]; then
  REPO_DIR="$SCRIPT_DIR"
  INSTALL_MODE="local"
else
  REPO_REF="${HERMIT_INSTALL_REF:-main}"
  TEMP_DIR="$(mktemp -d)"
  REPO_DIR="$TEMP_DIR/Hermit"
  INSTALL_MODE="snapshot"
  echo "-> Fetching Hermit (${REPO_REF})..."
  git clone --depth 1 --branch "$REPO_REF" https://github.com/heggria/Hermit.git "$REPO_DIR" >/dev/null
fi

CLEAN_SCRIPT="$REPO_DIR/scripts/clean_build_artifacts.py"

clean_local_build_artifacts() {
  if [[ -f "$CLEAN_SCRIPT" ]]; then
    python3 "$CLEAN_SCRIPT" "$REPO_DIR" >/dev/null
  fi
}

if ! command -v uv >/dev/null 2>&1; then
  echo "-> Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

echo "-> Installing Hermit..."
# Console scripts resolve Hermit from the tool environment, so local installs
# still need a real reinstall into site-packages instead of editable mode.
clean_local_build_artifacts
uv tool install --python 3.11 --force --reinstall --refresh --no-cache "$REPO_DIR[macos]" -q

UV_BIN="$(uv tool dir 2>/dev/null || echo "$HOME/.local/bin")"
export PATH="$UV_BIN:$PATH"
TOOL_PY="$UV_BIN/hermit/bin/python"

if [[ -x "$TOOL_PY" ]]; then
  # Older installs used the distribution name `hermit`, which leaves a stale
  # top-level package behind. Remove it before layering the current wheel.
  uv pip uninstall --python "$TOOL_PY" hermit >/dev/null 2>&1 || true

  if [[ -z "$TEMP_DIR" ]]; then
    TEMP_DIR="$(mktemp -d)"
  fi
  WHEEL_DIR="$TEMP_DIR/hermit-wheel"
  mkdir -p "$WHEEL_DIR"
  (
    cd "$REPO_DIR"
    clean_local_build_artifacts
    uv build --wheel --no-sources --out-dir "$WHEEL_DIR" >/dev/null
  )
  WHEEL_PATH="$(find "$WHEEL_DIR" -maxdepth 1 -name 'hermit_agent-*.whl' | head -n 1)"
  if [[ -n "$WHEEL_PATH" ]]; then
    uv pip install --python "$TOOL_PY" --reinstall "$WHEEL_PATH" >/dev/null
  fi
fi

EXISTING_AUTOSTART_ADAPTERS="$(
python3 - <<'PY'
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

hermit init -q 2>/dev/null || hermit init
hermit-menubar-install-app --adapter feishu >/dev/null 2>&1 || true

if [[ -n "$EXISTING_AUTOSTART_ADAPTERS" ]]; then
  echo "-> Refreshing existing auto-start services..."
  for adapter in $EXISTING_AUTOSTART_ADAPTERS; do
    hermit autostart enable --adapter "$adapter"
  done
fi

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"

SYNC_OUTPUT="$(
ENV_FILE="$ENV_FILE" python3 - <<'PY'
from __future__ import annotations

from pathlib import Path
import json
import os
import re

env_file = Path(os.environ["ENV_FILE"])
existing: dict[str, str] = {}
for raw_line in env_file.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    existing[key.strip()] = value.strip()

candidates: dict[str, tuple[str, str]] = {}
notes: list[str] = []


def remember(key: str, value: object | None, source: str) -> None:
    if key in candidates:
        return
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    candidates[key] = (text, source)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_codex_model(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    match = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"', text)
    return match.group(1).strip() if match else None


shell_aliases = {
    "ANTHROPIC_API_KEY": ["HERMIT_CLAUDE_API_KEY", "ANTHROPIC_API_KEY"],
    "HERMIT_AUTH_TOKEN": ["HERMIT_CLAUDE_AUTH_TOKEN", "HERMIT_AUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"],
    "HERMIT_BASE_URL": ["HERMIT_CLAUDE_BASE_URL", "HERMIT_BASE_URL", "ANTHROPIC_BASE_URL"],
    "HERMIT_CUSTOM_HEADERS": ["HERMIT_CLAUDE_HEADERS", "HERMIT_CUSTOM_HEADERS", "ANTHROPIC_CUSTOM_HEADERS"],
    "OPENAI_API_KEY": ["HERMIT_OPENAI_API_KEY", "OPENAI_API_KEY"],
    "HERMIT_PROVIDER": ["HERMIT_PROVIDER"],
    "HERMIT_MODEL": ["HERMIT_MODEL", "ANTHROPIC_MODEL"],
    "HERMIT_FEISHU_APP_ID": ["HERMIT_FEISHU_APP_ID", "FEISHU_APP_ID"],
    "HERMIT_FEISHU_APP_SECRET": ["HERMIT_FEISHU_APP_SECRET", "FEISHU_APP_SECRET"],
    "HERMIT_SCHEDULER_FEISHU_CHAT_ID": ["HERMIT_SCHEDULER_FEISHU_CHAT_ID"],
}

for target_key, aliases in shell_aliases.items():
    for alias in aliases:
        value = os.environ.get(alias)
        if value and value.strip():
            remember(target_key, value, f"shell:{alias}")
            break

codex_auth_path = Path.home() / ".codex" / "auth.json"
if codex_auth_path.exists():
    remember("HERMIT_PROVIDER", "codex-oauth", "codex:~/.codex/auth.json")
    notes.append("Hermit can reuse Codex OAuth state from ~/.codex/auth.json.")

codex_model = read_codex_model(Path.home() / ".codex" / "config.toml")
if codex_model:
    remember("HERMIT_MODEL", codex_model, "codex:~/.codex/config.toml")

claude_settings = read_json(Path.home() / ".claude" / "settings.json")
claude_env = claude_settings.get("env")
if isinstance(claude_env, dict):
    remember("ANTHROPIC_API_KEY", claude_env.get("ANTHROPIC_API_KEY"), "claude-code:settings.json env")
    remember("HERMIT_AUTH_TOKEN", claude_env.get("ANTHROPIC_AUTH_TOKEN"), "claude-code:settings.json env")
    remember("HERMIT_BASE_URL", claude_env.get("ANTHROPIC_BASE_URL"), "claude-code:settings.json env")
    remember("HERMIT_CUSTOM_HEADERS", claude_env.get("ANTHROPIC_CUSTOM_HEADERS"), "claude-code:settings.json env")
    remember("HERMIT_MODEL", claude_env.get("ANTHROPIC_MODEL"), "claude-code:settings.json env")
    if any(
        isinstance(claude_env.get(name), str) and claude_env.get(name, "").strip()
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
    ):
        notes.append("Claude Code env values can be imported from ~/.claude/settings.json when present.")

openclaw_path = Path.home() / ".openclaw" / "openclaw.json"
openclaw = read_json(openclaw_path)
if openclaw:
    model_primary = (
        openclaw.get("agents", {})
        .get("defaults", {})
        .get("model", {})
        .get("primary")
    )
    if isinstance(model_primary, str) and model_primary.strip():
        normalized_model = model_primary.split("/")[-1].strip()
        remember("HERMIT_MODEL", normalized_model, "openclaw:~/.openclaw/openclaw.json")

    feishu_accounts = (
        openclaw.get("channels", {})
        .get("feishu", {})
        .get("accounts", {})
    )
    if isinstance(feishu_accounts, dict):
        account = None
        for preferred in ("main", "default"):
            maybe = feishu_accounts.get(preferred)
            if isinstance(maybe, dict):
                account = maybe
                break
        if account is None:
            for value in feishu_accounts.values():
                if isinstance(value, dict):
                    account = value
                    break
        if isinstance(account, dict):
            remember("HERMIT_FEISHU_APP_ID", account.get("appId"), "openclaw:~/.openclaw/openclaw.json")
            remember("HERMIT_FEISHU_APP_SECRET", account.get("appSecret"), "openclaw:~/.openclaw/openclaw.json")
            if account.get("appId") or account.get("appSecret"):
                notes.append("OpenClaw Feishu settings can seed Hermit's Feishu adapter credentials.")

openclaw_oauth_path = Path.home() / ".openclaw" / "credentials" / "oauth.json"
if openclaw_oauth_path.exists() and not codex_auth_path.exists():
    notes.append(
        "Detected OpenClaw Codex OAuth tokens, but Hermit does not copy them into ~/.codex/auth.json automatically. "
        "Run `codex login` or set OPENAI_API_KEY if you want Hermit Codex auth right away."
    )

if "HERMIT_PROVIDER" not in candidates:
    if "OPENAI_API_KEY" in candidates:
        remember("HERMIT_PROVIDER", "codex", "inferred:openai-api-key")
    elif "ANTHROPIC_API_KEY" in candidates or "HERMIT_AUTH_TOKEN" in candidates:
        remember("HERMIT_PROVIDER", "claude", "inferred:claude-auth")

appended = 0
for key, (value, source) in candidates.items():
    if key in existing:
        continue
    with env_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")
    appended += 1
    print(f"ADDED\t{key}\t{source}")

if appended == 0:
    print("SUMMARY\t0")
else:
    print(f"SUMMARY\t{appended}")

for note in dict.fromkeys(notes):
    print(f"NOTE\t{note}")
PY
)"

SAVED=0
SYNC_NOTES=()
while IFS=$'\t' read -r kind first second; do
  [[ -z "${kind:-}" ]] && continue
  case "$kind" in
    ADDED)
      SAVED=$((SAVED + 1))
      echo "  saved ${first} (${second})"
      ;;
    NOTE)
      SYNC_NOTES+=("$first")
      ;;
  esac
done <<< "$SYNC_OUTPUT"

if [[ "$SAVED" -gt 0 ]]; then
  echo "-> Saved $SAVED compatible setting(s) into ~/.hermit/.env."
fi

if [[ "${#SYNC_NOTES[@]}" -gt 0 ]]; then
  echo "-> Compatibility notes:"
  for note in "${SYNC_NOTES[@]}"; do
    echo "   - $note"
  done
fi

PROFILE_HINT=""
if ! grep -q "$UV_BIN" "$HOME/.zshrc" 2>/dev/null && ! grep -q "$UV_BIN" "$HOME/.bash_profile" 2>/dev/null; then
  PROFILE_HINT="echo 'export PATH=\"$UV_BIN:\$PATH\"' >> ~/.zshrc && source ~/.zshrc"
fi

HAS_AUTH="$(grep -cE '^(ANTHROPIC_API_KEY|OPENAI_API_KEY|HERMIT_AUTH_TOKEN)=.+' "$ENV_FILE" 2>/dev/null || true)"

echo ""
echo "Hermit is installed."
echo ""
echo "Fastest next steps:"
echo "  hermit auth status"
echo "  hermit run \"Summarize the current repository\""
echo "  hermit task list"
echo ""
echo "Optional macOS companion:"
echo "  hermit-menubar --adapter feishu"
echo "  open ~/Applications/Hermit\\ Menu.app"

if [[ "$HAS_AUTH" -eq 0 && -z "${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}${HERMIT_AUTH_TOKEN:-}" ]]; then
  echo ""
  echo "One more step: add credentials to ~/.hermit/.env, for example:"
  echo "  echo 'OPENAI_API_KEY=sk-...' >> ~/.hermit/.env"
  echo "  echo 'HERMIT_PROVIDER=codex' >> ~/.hermit/.env"
fi

if [[ -n "$PROFILE_HINT" ]]; then
  echo ""
  echo "To use hermit in new terminals:"
  echo "  $PROFILE_HINT"
fi

echo ""
