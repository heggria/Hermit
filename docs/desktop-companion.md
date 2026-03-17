# Desktop Companion

The Hermit menu bar companion is a macOS control process that is separate from the runtime and not part of the plugin system.

Related entrypoints:

- `hermit-menubar`
- `hermit-menubar-install-app`

Source files:

- [`src/hermit/apps/companion/menubar.py`](https://github.com/heggria/Hermit/blob/main/src/hermit/apps/companion/menubar.py)
- [`src/hermit/apps/companion/control.py`](https://github.com/heggria/Hermit/blob/main/src/hermit/apps/companion/control.py)
- [`src/hermit/apps/companion/appbundle.py`](https://github.com/heggria/Hermit/blob/main/src/hermit/apps/companion/appbundle.py)

## Design Boundary

The menu bar companion is a control layer, not the place where the agent runtime lives.

It is responsible for:

- viewing service status
- starting / stopping / reloading `hermit serve`
- managing `launchd` autostart
- managing the menu bar app’s own Login Item
- opening settings, README, Wiki, the logs directory, and the Hermit home directory
- showing an About panel so the current version and runtime context are easy to verify

It is not responsible for:

- executing plugin logic directly
- replacing `serve`
- owning the main session / memory / scheduler logic

## Installation

Requires macOS and the menu bar dependencies:

```bash
uv sync --group dev --group typecheck --group docs --group security --group release --extra macos
```

If you use the repository’s one-step installer:

```bash
bash install.sh
```

The install script also installs `hermit-menubar` and tries to install the local app bundle.

## Startup Modes

Manage the `feishu` adapter by default:

```bash
hermit-menubar
```

Explicitly select an adapter:

```bash
hermit-menubar --adapter feishu
```

Specify a profile:

```bash
hermit-menubar --adapter feishu --profile codex-local
```

Specify a base dir:

```bash
hermit-menubar --base-dir ~/.hermit --adapter feishu
```

## Current Menu Items

The menu bar refreshes status every 5 seconds and shows:

- runtime status and PID
- current profile
- current provider
- current model

Available actions:

- Start Service
- Stop Service
- Reload Service
- Enable Auto-start
- Disable Auto-start
- Enable / Disable Menu Login Item
- Install / Open Menu App
- Open Settings
- Open README
- Open Wiki
- Open Logs
- Open Hermit Home
- About Hermit

## Service Control Implementation

The menu bar does not embed the runtime directly. It controls it through CLI commands:

- `hermit serve --adapter <adapter>`
- `hermit reload --adapter <adapter>`
- `hermit autostart enable --adapter <adapter>`
- `hermit autostart disable --adapter <adapter>`

Logs are written by default to:

```text
~/.hermit/logs/
```

For example:

- `feishu-menubar-stdout.log`
- `feishu-menubar-stderr.log`

## App Bundle and Login Item

`hermit-menubar-install-app` generates a double-clickable local app bundle.

Current design:

- the default prod app lives at `~/Applications/Hermit.app`
- dev/test automatically get environment suffixes, for example `~/Applications/Hermit Dev.app`
- the launcher passes adapter / profile / base-dir through environment variables and command arguments
- the Login Item belongs to the menu bar app itself, not `hermit serve`

That means:

- the menu bar app can start at login
- the app can then control the background `serve`
- the GUI layer and runtime lifecycle remain decoupled

## Multi-Environment Guidance

If you maintain prod / dev / test on the same machine, do not hand-type `HERMIT_BASE_DIR`. Use the wrapper scripts consistently:

```bash
scripts/hermit-menubar-env.sh prod --adapter feishu
scripts/hermit-menubar-env.sh dev --adapter feishu
scripts/hermit-menubar-install-env.sh dev --open
scripts/hermit-autostart-env.sh test enable --adapter feishu
```

Naming rules:

- prod app: `Hermit.app`
- dev app: `Hermit Dev.app`
- test app: `Hermit Test.app`

The matching Login Item names follow the app names, so they do not overwrite one another.

## Config File Behavior

When you click `Open Settings` from the menu bar:

- if `~/.hermit/config.toml` does not exist
- the companion first generates a default template

The default template includes:

```toml
default_profile = "default"

[profiles.default]
provider = "claude"
model = "claude-3-7-sonnet-latest"
```

## Limits and Notes

- macOS only
- will not start without `rumps`
- not a replacement for a real process manager; for long-running hosting, `launchd` is still the better choice
- it is only a control layer, so business logic should not keep accumulating under `src/hermit/apps/companion/`

## What This Documentation Update Corrects

- the companion is now documented as an independent module, not a “helper script”
- the previous docs did not fully describe config, logs, and Login Item behavior
- all service control examples now consistently use `--adapter`
