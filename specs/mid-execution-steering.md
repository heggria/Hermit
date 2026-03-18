# Mid-Execution Task Steering

## Status: Implemented

## Problem

Hermit's existing `append_note` mechanism allows operators to send messages to running tasks, but these are unstructured text appended as events. The agent may ignore them. There is no lifecycle tracking (whether the guidance was seen or acted upon), no receipt audit trail, and no typed semantics distinguishing "change direction" from "add context."

## Solution

A kernel-level `SteeringDirective` that upgrades operator guidance from unstructured notes to governed, lifecycle-tracked signals. Steerings reuse the existing `evidence_signals` table (via `source_kind` prefix `steering:*`), requiring zero schema migration.

## Data Model

### SteeringDirective

```
src/hermit/kernel/signals/models.py
```

| Field | Type | Description |
|-------|------|-------------|
| `directive_id` | `str` | Auto-generated `sig_steer_<hex12>` |
| `task_id` | `str` | Target task |
| `steering_type` | `str` | `scope` \| `constraint` \| `priority` \| `strategy` \| `policy` |
| `directive` | `str` | Human-readable instruction text |
| `evidence_refs` | `list[str]` | Why the operator is steering |
| `issued_by` | `str` | Principal identifier (`operator`, `cli:operator`, etc.) |
| `disposition` | `str` | Lifecycle state (see below) |
| `supersedes_id` | `str \| None` | Chain link to replaced directive |
| `metadata` | `dict` | Extensible key-value store |
| `created_at` | `float` | Unix timestamp |
| `applied_at` | `float \| None` | When the agent completed acting on it |

### Disposition Lifecycle

```
pending ──(context compiled)──> acknowledged ──(task finalized)──> applied
   │                                 │
   │                                 └──(protocol.reject)──> rejected
   │
   └──(protocol.supersede)──> superseded
```

- **pending** → directive issued, agent has not yet seen it
- **acknowledged** → agent's next context compilation included the directive
- **applied** → task step completed with the directive in effect
- **rejected** → operator or agent explicitly rejected
- **superseded** → replaced by a newer directive via `supersede()`

### Storage Mapping

`SteeringDirective` maps to `EvidenceSignal` via `to_signal()` / `from_signal()`:

| SteeringDirective | EvidenceSignal |
|---|---|
| `directive_id` | `signal_id` |
| `steering_type` | `source_kind` = `steering:<type>` |
| `task_id` | `task_id` |
| `directive` | `summary` + `suggested_goal` |
| `disposition` | `disposition` |
| `issued_by`, `supersedes_id`, `applied_at` | stored in `metadata_json` |

SQL filtering: `source_kind LIKE 'steering:%'` isolates steerings from other evidence signals.

## Architecture

### Entry Points

```
                    ┌──────────────┐
                    │   CLI        │  hermit task steer <task_id> "directive"
                    │              │  hermit task steerings <task_id>
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Steering    │  issue / acknowledge / apply / reject / supersede
                    │  Protocol    │  active_for_task
                    └──────┬───────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼───────┐ ┌─────▼──────┐  ┌──────▼───────┐
   │ Signal Store │ │  Event Log │  │  input_dirty  │
   │  (SQLite)    │ │ (append)   │  │  (step_attempt│
   └──────────────┘ └────────────┘  │   context)    │
                                    └───────────────┘
```

### Full Lifecycle Flow

1. **Issue** — Operator sends `/steer` via feishu or `hermit task steer` via CLI
2. **Persist** — `SteeringProtocol.issue()` → `store.create_steering()` → `evidence_signals` row
3. **Event** — `steering.issued` event appended to task event log
4. **Dirty signal** — `_mark_input_dirty()` flags the active step attempt
5. **Context compile** — Next `ProviderInputCompiler.compile()`:
   - Fetches active steerings via `_active_steerings(task_id)`
   - Auto-transitions `pending` → `acknowledged`
   - Injects into `ContextPack.active_steerings`
   - Renders as `<steering_directives>` block in agent context
6. **Agent execution** — Agent sees mandatory steering block with `MUST incorporate` instruction
7. **Finalize** — `TaskController.finalize_result()` auto-transitions `acknowledged` → `applied`

### Ingress Auto-Upgrade

When `TaskController.append_note()` receives text matching `/steer [--type <type>] <directive>`:

```
append_note("/steer --type constraint no DB migrations")
  └─> _try_upgrade_to_steering()
       └─> SteeringProtocol.issue(SteeringDirective{type="constraint", directive="no DB migrations"})
  └─> (normal note event also emitted — dual-write for backward compatibility)
```

The regex pattern: `^/steer\s+(?:--type\s+(\S+)\s+)?(.+)`

This means:
- `/steer focus on auth` → `type=scope`, `directive="focus on auth"`
- `/steer --type constraint no DB changes` → `type=constraint`, `directive="no DB changes"`
- `just a regular message` → no upgrade, normal append_note only

### Context Rendering

When active steerings exist, a dedicated block is rendered outside `<context_pack>`:

```xml
<steering_directives>
The operator has issued the following mid-execution steering directives.
You MUST incorporate these into your current execution.
Each directive constrains or redirects your approach.
- [sig_steer_abc123] type=scope disposition=acknowledged issued_by=operator:
  Focus on the API layer only
- [sig_steer_def456] type=constraint disposition=acknowledged issued_by=cli:operator:
  No database migrations allowed
</steering_directives>
```

When no steerings are active, the block is omitted entirely.

### Relationship to append_note

- **append_note is not replaced.** Plain text follow-up messages still flow through the existing `task.note.appended` event path.
- **Steering is a structured upgrade.** `/steer` prefix triggers dual-write: both a note event (backward compat) and a `SteeringDirective` (governed lifecycle).
- **Shared dirty signal.** Both paths set `input_dirty=True` on the active step attempt, ensuring the agent recompiles context at the next boundary.

### Relationship to Evidence Signals

- Steerings live in the same `evidence_signals` table, filtered by `source_kind LIKE 'steering:%'`
- Existing `SignalProtocol` (emit/consume/suppress) is untouched
- `SteeringProtocol` is a parallel coordination interface with its own lifecycle semantics
- Zero schema changes — no new tables, no new columns, no migration

## Files

| File | Change |
|------|--------|
| `src/hermit/kernel/signals/models.py` | `SteeringDirective` dataclass with `to_signal()`/`from_signal()` |
| `src/hermit/kernel/signals/store.py` | 4 convenience methods on `SignalStoreMixin` |
| `src/hermit/kernel/signals/steering.py` | `SteeringProtocol` (new file) |
| `src/hermit/kernel/signals/__init__.py` | Re-exports |
| `src/hermit/kernel/context/compiler/compiler.py` | `ContextPack.active_steerings` field |
| `src/hermit/kernel/context/injection/provider_input.py` | `_active_steerings()` + auto-acknowledge + structured rendering |
| `src/hermit/kernel/task/services/controller.py` | `_try_upgrade_to_steering()` in `append_note()` + `_apply_acknowledged_steerings()` in `finalize_result()` |
| `src/hermit/surfaces/cli/_commands_task.py` | `task steer` + `task steerings` commands |
| `tests/unit/kernel/test_steering.py` | 27 tests across 8 test classes |

## Store Methods

Added to `SignalStoreMixin`:

| Method | Purpose |
|--------|---------|
| `create_steering(directive)` | Persist via `create_signal(directive.to_signal())` |
| `list_steerings_for_task(task_id, disposition?, limit?)` | SQL filter `source_kind LIKE 'steering:%' AND task_id = ?` |
| `active_steerings_for_task(task_id)` | Disposition in (`pending`, `acknowledged`, `applied`) |
| `update_steering_disposition(directive_id, disposition, applied_at?)` | Update disposition, optionally store `applied_at` in metadata |

## SteeringProtocol

```python
class SteeringProtocol:
    def issue(directive) -> SteeringDirective      # persist + event + input_dirty
    def acknowledge(directive_id) -> None           # pending → acknowledged + event
    def apply(directive_id) -> None                 # → applied + applied_at + event
    def reject(directive_id, reason?) -> None       # → rejected + event
    def supersede(old_id, new) -> SteeringDirective # old → superseded, new → issue()
    def active_for_task(task_id) -> list             # active directives
```

## CLI

```bash
# Issue a steering directive
hermit task steer <task_id> "focus on auth" --type scope
hermit task steer <task_id> "no database migrations" --type constraint

# List active steerings
hermit task steerings <task_id>
```

## Events

All steering lifecycle transitions emit auditable events:

| Event Type | Payload |
|---|---|
| `steering.issued` | `directive_id`, `steering_type`, `directive`, `supersedes_id` |
| `steering.acknowledged` | `directive_id` |
| `steering.applied` | `directive_id`, `applied_at` |
| `steering.rejected` | `directive_id`, `reason` |
| `steering.superseded` | `old_directive_id`, `new_directive_id` |

Plus the existing `step_attempt.input_dirty` event fires when a steering marks the attempt dirty.

## Tests

27 tests in `tests/unit/kernel/test_steering.py`:

| Class | Tests | Coverage |
|-------|-------|----------|
| `TestSteeringDirectiveModel` | 5 | auto-ID, explicit ID, to_signal roundtrip, from_signal roundtrip, source_kind extraction |
| `TestSteeringStore` | 5 | create/get, list by task, filter by disposition, active filtering, disposition update with applied_at |
| `TestSteeringProtocol` | 7 | issue, acknowledge, apply, reject, supersede, active_for_task |
| `TestSteeringContextIntegration` | 1 | active steerings retrievable for context pack |
| `TestIngressAutoUpgrade` | 4 | /steer prefix, /steer --type, regular note no upgrade, dual-write (note + steering) |
| `TestAutoAcknowledge` | 2 | pending → acknowledged on compile, already acknowledged unchanged |
| `TestAutoApplyOnFinalize` | 2 | acknowledged → applied on finalize, pending stays pending |
| `TestStructuredRendering` | 2 | steering_directives block rendered, no block when empty |

## Verification

```bash
uv run pytest tests/unit/kernel/test_steering.py -q          # 27 passed
uv run pytest tests/unit/kernel/test_evidence_signals.py -q   # 19 passed (no regression)
uv run ruff check <all changed files>                         # All checks passed
```
