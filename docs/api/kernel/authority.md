# Authority

Identity, workspace leases, and capability grants.

The authority module implements Hermit's security control plane. It contains
three sub-modules that together answer three questions for every side-effecting
action the kernel dispatches:

1. **Identity** — *Who* is making the request? (`PrincipalService`)
2. **Workspaces** — *Where* may they operate? (`WorkspaceLeaseService`)
3. **Grants** — *What* are they allowed to do? (`CapabilityGrantService`)

Every tool invocation in the dispatch pipeline must hold a valid
`CapabilityGrantRecord` that has been issued against a policy decision,
optionally scoped to an active workspace lease. The grant is **enforced** at
dispatch time (action class, resource scope, constraints, lease validity, and
TTL are all re-checked) and then **consumed** so it cannot be replayed.

## Identity

Resolves human users, adapters, and system actors into stable
`PrincipalRecord` entries that are referenced throughout the ledger.

### Service

::: hermit.kernel.authority.identity.service

### Models

::: hermit.kernel.authority.identity.models

## Capability Grants

Issue, enforce, consume, and revoke fine-grained capability grants that
authorise individual side-effecting actions.

A grant ties together a policy decision (`decision_ref`), an optional human
approval (`approval_ref`), an action class (e.g. `execute_command`), a
resource scope, and runtime constraints such as `target_paths`,
`network_hosts`, or `command_preview`. Grants are enforced at dispatch time
via `CapabilityGrantService.enforce()`, which validates:

- status is `issued` (not consumed, revoked, or invalid)
- TTL has not expired
- action class matches
- resource scope is a subset of the granted scope
- runtime constraints match (paths, hosts, command preview, lease root)
- any referenced workspace lease is still active

### Service

::: hermit.kernel.authority.grants.service

### Models

::: hermit.kernel.authority.grants.models

## Workspace Leases

Manage exclusive or shared access to workspace directories so that
concurrent tasks do not clobber each other's file-system state.

A workspace lease binds a `(workspace_id, root_path)` to a principal for
a limited TTL. The service supports two modes:

| Mode | Semantics |
|------|-----------|
| `mutable` | Exclusive write access. Only one mutable lease may be active per workspace at a time. Conflicting requests are queued (`WorkspaceLeaseQueued`). |
| `readonly` | Shared read access. Multiple readonly leases may coexist. |

When a mutable lease is released or expires, the service automatically
processes the queue and promotes the next pending entry.

### Service

::: hermit.kernel.authority.workspaces.service

### Models

::: hermit.kernel.authority.workspaces.models

## Public Exports

Each sub-module's `__init__.py` uses lazy `__getattr__` imports to keep
import time low. The public surface is:

### `hermit.kernel.authority.grants`

| Export | Kind | Description |
|--------|------|-------------|
| `CapabilityGrantRecord` | dataclass | Immutable record of an issued grant |
| `CapabilityGrantService` | class | Issue / enforce / consume / revoke grants |
| `CapabilityGrantError` | exception | Raised on enforcement failures (carries `.code`) |

### `hermit.kernel.authority.identity`

| Export | Kind | Description |
|--------|------|-------------|
| `PrincipalRecord` | dataclass | Identity record for a user or system actor |
| `PrincipalService` | class | Resolve or create principal records |

### `hermit.kernel.authority.workspaces`

| Export | Kind | Description |
|--------|------|-------------|
| `WorkspaceLeaseRecord` | dataclass | Active or historical workspace lease |
| `WorkspaceLeaseQueueEntry` | dataclass | Queued mutable-lease request |
| `WorkspaceLeaseService` | class | Acquire / release / extend / expire leases |
| `WorkspaceLeaseConflict` | exception | Base error for lease conflicts |
| `WorkspaceLeaseQueued` | exception | Raised when a request is queued (carries `.queue_entry_id`, `.position`) |
| `capture_execution_environment` | function | Snapshot OS / Python / cwd metadata for a lease |
