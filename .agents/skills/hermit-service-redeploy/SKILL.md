---
name: hermit-service-redeploy
description: Redeploy the local Hermit service after code or config changes that must affect a running `hermit serve` process. Use when modifying runtime code, plugins, adapters, scheduler, webhook, Feishu integration, packaging, or any behavior served by the installed uv tool.
---

# Hermit Service Redeploy

Use this skill after changes that must take effect in the locally running Hermit service.

## When to use

- Modified code under `src/hermit/` that is exercised by `hermit serve`
- Changed builtin plugins, adapters, scheduler, webhook, or Feishu behavior
- Changed packaging or install behavior where the running process may still use the installed `uv tool` copy
- Updated config or resources that are loaded only at service startup or reload

## Why this exists

The running service may not import code from the current repo checkout.

In this project, the active process often runs from:

- `~/.local/share/uv/tools/hermit/bin/hermit`

If that process was started outside the repo root, a plain reload can keep using the installed package copy. In that case, source edits in the checkout do not take effect until the local tool is reinstalled.

## Required workflow

1. Confirm whether a local `hermit serve` process is running.
2. If the change should affect the installed CLI/runtime, reinstall the local tool:

```bash
bash install.sh
```

3. Reload the running adapter when possible:

```bash
~/.local/share/uv/tools/hermit/bin/hermit reload --adapter feishu
```

Replace `feishu` with the target adapter when different.

4. Verify the service is alive:

```bash
cat ~/.hermit/serve-<adapter>.pid
ps -p "$(cat ~/.hermit/serve-<adapter>.pid)" -o pid=,ppid=,etime=,command=
```

5. Verify recent logs show a fresh startup or reload:

```bash
tail -n 50 ~/.hermit/logs/<adapter>-stdout.log
tail -n 50 ~/.hermit/logs/<adapter>-stderr.log
```

## Decision rules

- If no service is running, start it instead of reloading.
- If `install.sh` changes the process identity or autostart wiring, re-read the PID file after reinstall.
- If reload succeeds but the installed package still lacks the new code, treat the deployment as incomplete and reinstall first.
- If logs show unrelated warnings, separate them from the deployment result unless they block startup.

## Minimum completion bar

Do not report success until all are true:

- local installation reflects the new code when reinstall is required
- the target service has been reloaded or restarted
- PID file resolves to a live process
- recent logs show the adapter is connected or started successfully
