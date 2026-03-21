# Ledger

SQLite-backed persistent ledger with hash-chained event sourcing, mixin-based store architecture, and projection caching.

## Architecture

`KernelStore` is composed of **12 mixins**, each responsible for a specific domain of persistence. The current database schema is at **version 18**.

| Mixin | Module | Domain |
|---|---|---|
| `KernelTaskStoreMixin` | `ledger.journal.store_tasks` | Task lifecycle |
| `KernelLedgerStoreMixin` | `ledger.events.store_ledger` | Event journal / ledger |
| `KernelProjectionStoreMixin` | `ledger.projections.store_projection` | Projection caching |
| `KernelSchedulerStoreMixin` | `ledger.journal.store_scheduler` | Scheduled jobs |
| `KernelStoreRecordMixin` | `ledger.journal.store_records` | Generic record storage |
| `KernelV2StoreMixin` | `ledger.journal.store_v2` | V2 schema extensions |
| `SignalStoreMixin` | `signals.store` | Signal handling |
| `CompetitionStoreMixin` | `execution.competition.store` | Competition / concurrency |
| `DelegationStoreMixin` | `task.services.delegation_store` | Delegation tracking |
| `SelfIterateStoreMixin` | `ledger.journal.store_self_iterate` | Self-iteration state |
| `ProgramStoreMixin` | `ledger.journal.store_programs` | Program / workflow storage |
| `KernelTeamStoreMixin` | `ledger.journal.store_teams` | Team coordination |

All mixins inherit from `KernelStoreTypingBase` (defined in `ledger.journal.store_types`), which provides typing stubs for shared `self.conn` / `self.cursor` access.

## Journal – Core Store

::: hermit.kernel.ledger.journal.store

## Journal – Types

::: hermit.kernel.ledger.journal.store_types

## Journal – Support Utilities

::: hermit.kernel.ledger.journal.store_support

## Journal – Records

::: hermit.kernel.ledger.journal.store_records

## Journal – Tasks

::: hermit.kernel.ledger.journal.store_tasks

## Journal – V2 Extensions

::: hermit.kernel.ledger.journal.store_v2

## Journal – Scheduler

::: hermit.kernel.ledger.journal.store_scheduler

## Journal – Self-Iteration

::: hermit.kernel.ledger.journal.store_self_iterate

## Journal – Programs

::: hermit.kernel.ledger.journal.store_programs

## Journal – Teams

::: hermit.kernel.ledger.journal.store_teams

## Events

::: hermit.kernel.ledger.events.store_ledger

## Projections

::: hermit.kernel.ledger.projections.store_projection

## External Mixins

The following mixins are defined outside the `ledger/` package but are composed into `KernelStore`:

### Signals

::: hermit.kernel.signals.store

### Competition

::: hermit.kernel.execution.competition.store

### Delegation

::: hermit.kernel.task.services.delegation_store
