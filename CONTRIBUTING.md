# Contributing to Hermit

Welcome, and thank you for considering a contribution to Hermit.

Hermit is the first operating system for AI tasks. Contributing to an OS is different from contributing to an app -- every change affects the trust guarantees that users depend on.

Hermit is a kernel-first governed agent runtime -- a project where every action flows through approvals, receipts, and proofs. We understand that contributing to a kernel-level project can feel intimidating. It does not need to be. Whether you are fixing a typo, writing a test, building a plugin, or proposing a change to the execution model, your contribution matters and we are glad you are here.

This guide will help you get oriented, set up your environment, and understand what we look for in contributions.

---

## Table of Contents

- [Development Setup](#development-setup)
- [Code Style](#code-style)
- [Testing](#testing)
- [Architecture Overview](#architecture-overview)
- [Types of Contributions](#types-of-contributions)
- [Pull Request Process](#pull-request-process)
- [Good First Issues](#good-first-issues)
- [Plugin Development](#plugin-development)
- [Communication](#communication)
- [License](#license)

---

## Development Setup

### Prerequisites

- **Python 3.13+** (required; Python 3.11/3.12 are not supported)
- **[uv](https://docs.astral.sh/uv/)** as the package manager (not pip or poetry)
- **Git**

### Getting Started

```bash
# Clone the repository
git clone https://github.com/heggria/Hermit.git
cd Hermit

# Install dependencies and initialize the project
make install

# Run the quick validation suite (lint + typecheck + tests)
make check
```

The `make install` target runs the project's `install.sh` script, which sets up your local environment with `uv` and installs all dependencies including dev tools.

### Useful Make Targets

| Command | Purpose |
|---------|---------|
| `make install` | Install with init |
| `make check` | Quick check: lint + typecheck + test |
| `make test` | Run the full test suite (parallel via pytest-xdist) |
| `make test-cov` | Run tests with coverage reporting |
| `make test-kernel` | Run kernel tests only |
| `make lint` | Run Ruff linting |
| `make format` | Run Ruff formatting |
| `make verify` | Full release verification pipeline (see Makefile for complete list of checks) |
| `make precommit-install` | Install git pre-commit hooks |

---

## Code Style

Hermit uses **[Ruff](https://docs.astral.sh/ruff/)** for both linting and formatting. Do not manually adjust style beyond what Ruff enforces.

### Key Settings

- **Line length:** 100 characters
- **Target version:** Python 3.13
- **Lint rules:** `ASYNC`, `B`, `E`, `F`, `I`, `RUF`, `SIM`, `UP` (with `E501` and other project-specific exclusions -- see `pyproject.toml` for the full ignore list)
- **Pre-commit hooks** run `ruff check` and `ruff format` automatically

### Format Before Committing

```bash
make format
make lint
```

Or install the pre-commit hooks so this happens automatically:

```bash
make precommit-install
```

### General Principles

- **Many small files over few large files.** Keep files focused: 200-400 lines typical, 800 max.
- **Functions should be small** -- under 50 lines.
- **Handle errors explicitly** at every level. Never silently swallow errors.
- **Validate inputs** at system boundaries. Fail fast with clear messages.
- **No hardcoded secrets.** Use environment variables or config files.

---

## Testing

Hermit uses **pytest** with `pytest-asyncio` (async mode: auto) and `pytest-xdist` for parallel execution. The minimum coverage threshold is **80%**.

### Running Tests

```bash
# Run the full suite
make test

# Run tests with coverage
make test-cov

# Run a specific test file
uv run pytest tests/unit/kernel/test_some_file.py -q

# Run a specific test function
uv run pytest tests/unit/kernel/test_some_file.py::test_function_name -q

# Run kernel tests only
make test-kernel
```

### Test Organization

Tests live under `tests/` and are organized by type:

```
tests/
├── unit/          # Unit tests (isolated, fast)
├── integration/   # Multi-component integration tests
├── scenario/      # Scenario-based tests
└── e2e/           # End-to-end tests
```

### Writing Tests

- Write tests alongside your code changes. If you fix a bug, include a regression test. If you add a feature, include unit tests covering the new behavior.
- Tests should be deterministic and not depend on external services (unless marked with the `@pytest.mark.network` marker).
- Use the existing test fixtures and patterns you find in the test suite -- consistency matters.

### Available Markers

| Marker | Purpose |
|--------|---------|
| `integration` | Multi-component flows or external-process interactions |
| `e2e` | End-to-end user or operator scenarios |
| `network` | Requires live network access |
| `slow` | Takes noticeably longer than the default suite |
| `benchmark` | Performance benchmark tests |

---

## Architecture Overview

Hermit is a **kernel-first governed agent runtime**. The high-level execution flow:

```
Surfaces (CLI)  +  Adapters (Feishu, Slack, Telegram)  +  Hooks (Scheduler, Webhook)
    -> AgentRunner (runtime/control/)
        -> PluginManager + Task Controller
            -> Policy Engine -> Approval -> WorkspaceLease -> CapabilityGrant -> Executor
                -> Artifacts, Receipts, Proofs, Rollback
                    -> Kernel Ledger (SQLite event journal + projections)
```

### Key Design Principles

- **Task-first:** All meaningful work flows through durable Task objects.
- **Governed execution:** Every mutation follows Approval -> WorkspaceLease -> CapabilityGrant -> Execution -> Receipt. No direct model-to-tool execution.
- **Event sourcing:** Durable state is derived from append-only event logs in a SQLite ledger.
- **Receipt-aware:** Every action produces receipts and hash-chained proof bundles.
- **Scoped authority:** CapabilityGrants and WorkspaceLeases enforce least-privilege execution.
- **Plugin architecture:** Adapters, hooks, tools, MCP servers, subagents, and bundles are all plugins loaded via PluginManager.

### Key Packages

| Package | Purpose |
|---------|---------|
| `src/hermit/kernel/` | Governed execution kernel (task, policy, execution, ledger, verification) |
| `src/hermit/runtime/` | Assembly, capability registry, control runner, provider host |
| `src/hermit/plugins/builtin/` | Built-in adapters, hooks, tools, MCP servers, bundles |
| `src/hermit/infra/` | Infrastructure: storage, locking, paths, i18n |
| `src/hermit/surfaces/cli/` | CLI dispatcher and TUI |

For a deeper dive, see [`docs/architecture.md`](docs/architecture.md).

### Current State vs. Target State

Hermit is converging from a local-first agent runtime toward a governed agent kernel. When reading or contributing to the codebase, keep in mind the distinction between:

- **Current implementation** -- what the code does today
- **Target architecture** -- what the kernel spec defines as the goal

When writing code or documentation, be explicit about which you are describing. Good phrasing: "Hermit currently ships...", "Hermit is converging toward...", "The v0.1 kernel spec defines...". Avoid presenting target-state behavior as if it is fully shipped.

---

## Types of Contributions

### Bug Fixes

Found something broken? We appreciate bug fix PRs. Please include:
- A clear description of the bug and how to reproduce it
- A regression test that fails before the fix and passes after
- The fix itself, scoped as narrowly as possible

### Kernel Improvements -- the core OS primitives (task lifecycle, policy engine, receipts, proofs)

Contributions that strengthen kernel semantics are especially valued:
- Task lifecycle, step, and step-attempt semantics
- Policy, approval, decision, and scoped authority flow
- Receipt and proof coverage
- Rollback reliability and recovery paths
- Event sourcing and ledger integrity
- Artifact handling and context compilation
- Belief and memory governance
- Operator visibility and inspectability

When working on kernel code, keep in mind that kernel methods are synchronous -- async only exists at surface boundaries.

### New Plugins -- extend the OS like kernel modules and device drivers

Hermit has a rich plugin system supporting adapters, hooks, tools, MCP servers, subagents, and bundles. Building a new plugin is one of the best ways to contribute without requiring deep kernel knowledge. See [Plugin Development](#plugin-development) below.

### Documentation

Good documentation lowers the barrier for everyone. Contributions include:
- Fixing inaccuracies in existing docs
- Adding examples and tutorials
- Improving API documentation under `docs/api/`
- Sharpening the boundary between current-state and target-state descriptions
- Translating content (Hermit supports en-US and zh-CN)

When writing docs: explain value before architecture on landing pages, explain architecture before internals in deep docs.

### Test Coverage

We maintain an 80% coverage floor. If you find an under-tested module, adding tests is a valuable contribution on its own. Check current coverage with:

```bash
make test-cov
```

---

## Pull Request Process

### Branch Naming

Use descriptive branch names with a type prefix:

```
feat/add-redis-adapter
fix/receipt-chain-validation
docs/improve-plugin-guide
test/increase-policy-coverage
refactor/simplify-dispatch-handler
```

### Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <description>

<optional body>
```

**Types:** `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`

**Examples:**

```
feat(kernel): add workspace lease renewal support
fix(policy): correct trust score calculation for nested delegation
test(ledger): add coverage for hash-chain verification edge cases
docs: update plugin development guide with MCP examples
```

### Before Submitting

1. Run the full check suite:
   ```bash
   make check
   ```
2. Ensure your changes include tests where appropriate.
3. Ensure Ruff formatting and linting pass with no new warnings.
4. Write a clear PR description explaining *what* changed and *why*.

### What a Good PR Looks Like

Good PRs usually include:

- A clear statement of what changed and why it matters in Hermit's kernel direction
- Notes on current-state vs. target-state impact, if relevant
- Tests for behavior changes
- Doc updates when terminology, operator behavior, or architectural understanding changes

If the change affects a governed execution path, task lifecycle, memory behavior, or proof/rollback semantics, include documentation updates in the same PR whenever practical.

### Review Expectations

- PRs are reviewed for correctness, test coverage, adherence to project conventions, and alignment with the kernel-first direction.
- Expect constructive feedback. Reviewers may ask questions or request changes -- this is a normal part of the process.
- Small, focused PRs are easier to review and merge than large ones. When in doubt, split your work into smaller PRs.

---

## Good First Issues

If you are new to Hermit and looking for a place to start, check the [issues labeled **"good first issue"**](https://github.com/heggria/Hermit/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) on GitHub. These are scoped tasks that provide a gentle introduction to the codebase without requiring deep kernel knowledge.

If no good first issues are currently open, consider:
- Adding test coverage to an under-tested module
- Fixing a documentation inaccuracy
- Improving error messages in the CLI surface
- Tightening a path where side effects should be more explicitly governed

---

## Plugin Development

Hermit's plugin system is one of the most accessible ways to extend the project. Plugins are discovered from two locations:

1. **Built-in:** `src/hermit/plugins/builtin/`
2. **User-installed:** `~/.hermit/plugins/`

### Plugin Structure

Every plugin needs a `plugin.toml` manifest:

```toml
[plugin]
name = "my-plugin"
version = "0.1.0"
description = "A brief description of what this plugin does"

[entry]
tools = "tools:register"
hooks = "hooks:register"
commands = "commands:register"
subagents = "subagents:register"
adapter = "adapter:register"
mcp = "mcp:register"
```

Only include the entry points your plugin actually implements. For example, a simple hook plugin only needs:

```toml
[plugin]
name = "my-hook"
version = "0.1.0"
description = "Custom post-run hook"

[entry]
hooks = "hooks:register"
```

### Plugin Categories

| Category | Purpose | Examples |
|----------|---------|---------|
| **Adapters** | Messaging platform integrations | Feishu, Slack, Telegram |
| **Hooks** | Lifecycle event handlers | Memory, scheduler, patrol, benchmark |
| **Tools** | Agent-callable tools | File tools, web tools, computer use |
| **MCP** | Model Context Protocol servers | GitHub integration, Hermit MCP server |
| **Bundles** | Slash command packages | `/compact`, `/plan`, `/usage` |
| **Subagents** | Delegated agent execution | Orchestrator |

### Available Hook Events

Plugins can register handlers for these lifecycle events:

| Event | When it fires |
|-------|---------------|
| `SYSTEM_PROMPT` | System prompt assembly |
| `REGISTER_TOOLS` | Tool registration phase |
| `SESSION_START` | A session begins |
| `SESSION_END` | A session ends |
| `PRE_RUN` | Before an agent run |
| `POST_RUN` | After an agent run |
| `SERVE_START` | Service starts |
| `SERVE_STOP` | Service stops |
| `DISPATCH_RESULT` | Dispatched task results arrive |
| `SUBTASK_SPAWN` | A subtask is spawned |
| `SUBTASK_COMPLETE` | A subtask completes |

For a complete guide to plugin development, study the existing built-in plugins under `src/hermit/plugins/builtin/`. They serve as living documentation of the plugin API and cover every entry point category.

---

## Communication

- **Bug reports and feature requests:** [GitHub Issues](https://github.com/heggria/Hermit/issues)
- **Questions and discussions:** [GitHub Discussions](https://github.com/heggria/Hermit/discussions)
- **Real-time chat:** [Discord](https://discord.gg/XCYqF3SN)
- **Security vulnerabilities:** Please report security issues privately via GitHub's [security advisory feature](https://github.com/heggria/Hermit/security/advisories) rather than opening a public issue.

When opening an issue, include as much context as possible: your Python version, OS, relevant logs, and steps to reproduce.

---

## License

Hermit is licensed under the [MIT License](LICENSE). By contributing, you agree that your contributions will be licensed under the same terms.

---

Thank you for helping make Hermit better. Every contribution -- whether it is a one-line fix or a new kernel subsystem -- helps move the project forward. Hermit does not need more vague features. It needs sharper semantics, clearer docs, and stronger governed execution. If that resonates with you, you are in the right place.
