---
description: "Hermit's evidence-bound memory model: scoped governance, confidence decay, consolidation, and memory subsystems."
---

# Memory Model

Hermit's memory model is built around a simple idea:

**memory should not be an ungoverned place where assertions quietly become authority**

That is why Hermit separates working state, beliefs, and durable memory records.

## The Three Layers

### Working State

Working state is bounded execution state for active work.

It should be:

- task-scoped
- size-bounded
- shaped for continuation and planning
- easy to supersede

Working state is not the same thing as durable memory.

### Beliefs

Beliefs are revisable working truths.

A belief represents what the system currently treats as true enough to reason with inside or near a task boundary. Beliefs can cite evidence, carry confidence, and later be invalidated or superseded.

In practical terms, beliefs help Hermit avoid two bad choices:

- pretending every claim is durable memory
- pretending every claim should disappear with the current turn

### Memory Records

Memory records are durable cross-task knowledge.

They are for facts and conventions that should survive beyond the current task, but only under governance rules such as:

- evidence references
- scope
- retention class
- expiration
- invalidation or supersession

## Why Evidence Matters

Memory without provenance becomes hidden system prompt.

Hermit's direction is that durable memory promotion should cite evidence and remain inspectable later. This makes memory less like a bag of sticky notes and more like a governed knowledge layer.

## Scope And Retention

The current codebase already contains memory governance logic for:

- scope matching
- retention classes
- expiration
- static injection eligibility
- retrieval eligibility
- supersession logic

### Scope Kinds

- **global** — always matches any context
- **conversation** — matches when scope_ref equals the active conversation ID
- **workspace** — matches the resolved workspace path or `workspace:default`
- **entity** — matches when scope_ref equals the active task ID, step ID, or step attempt ID

### Retention Classes

The codebase uses 9 retention classes:

- **user_preference** — stable user preferences, globally scoped, statically injected (half-life: 180 days)
- **project_convention** — workspace conventions, statically injected (half-life: 90 days)
- **tooling_environment** — workspace tooling facts, statically injected (half-life: 60 days)
- **pitfall_warning** — inverted anti-pattern warnings, statically injected, workspace-scoped
- **procedural** — extracted how-to procedures, workspace-scoped, retrieval only
- **volatile_fact** — short-lived conversational facts (TTL: 24 hours, half-life: 14 days)
- **task_state** — task-scoped state (TTL: 7 days, half-life: 7 days)
- **sensitive_fact** — sensitive data, scope-restricted retrieval only
- **audit** — internal bookkeeping records, excluded from decay sweeps

This matters because not every memory should follow the system everywhere.

## Promotion And Retrieval

The practical lifecycle is:

1. working activity produces claims
2. some claims become beliefs
3. some beliefs become durable memory records
4. retrieval logic decides what can re-enter context
5. invalidation and supersession keep memory from silently rotting

The key design choice is that promotion is not just "the model thought this sounded useful."

## Confidence Decay

Memory confidence is not static. Hermit applies a half-life based confidence decay model (`ConfidenceDecayService`):

```
effective_confidence = base_confidence * (0.5 ^ (age / half_life))
```

where age resets each time a memory is referenced (`last_accessed_at` update). Half-life values per retention class:

- user_preference: 180 days
- project_convention: 90 days
- tooling_environment: 60 days
- volatile_fact: 14 days
- task_state: 7 days
- default: 30 days

Batch recomputation runs periodically, storing effective confidence in `structured_assertion` for retrieval scoring.

## Freshness States

In addition to the binary active/expired model, Hermit evaluates memory freshness on a four-state continuous spectrum (`MemoryDecayService`):

- **fresh** — less than 50% of TTL consumed
- **aging** — 50-75% of TTL consumed
- **stale** — 75-90% of TTL consumed
- **expired** — more than 90% of TTL consumed

Expired memories become quarantine candidates. Quarantined memories are soft-deleted but can be **revived** with new evidence, resetting their decay clock.

The `MemoryQualityService` produces a unified 0.0-1.0 quality score per memory record by combining the freshness-based decay score and the half-life confidence score via a weighted geometric mean (decay weight 0.4, confidence weight 0.6).

## Consolidation: The Dream Cycle

Consolidation runs as a background sweep — analogous to sleep-cycle memory consolidation in neuroscience.

The `ConsolidationService` runs periodic consolidation of the memory store. It executes five passes in order:

1. **Dedup** — merge semantically identical memories (similarity >= 0.9), keeping the higher-confidence record and invalidating the lower
2. **Strengthen** — boost confidence for frequently referenced memories (reference_count >= 3, increment +0.1, capped at 0.95)
3. **Decay** — run a full decay sweep, updating freshness states and collecting quarantine candidates
4. **Reflect** — synthesize higher-order insights from clusters of 3+ related memories (`ReflectionService`), promoting generalizations, patterns, and contradiction resolutions as new beliefs
5. **Anti-pattern** — detect memories with high failure rates across decisions they influenced (`AntiPatternService`), then invert them into PITFALL warning memories with boosted confidence

## Memory Subsystems

Beyond the core working state, beliefs, and durable memory records, the codebase implements several specialized memory subsystems.

### Memory Taxonomy

The `MemoryType` taxonomy (`src/hermit/kernel/context/memory/taxonomy.py`) classifies memory records into cognitive types following the CoALA architecture:

- **episodic** — task execution episodes (`episode_index` kind)
- **semantic** — durable facts, influence links, pitfall warnings, and most knowledge
- **procedural** — how-to procedures with trigger patterns and steps
- **working** — volatile conversation-scoped facts with short TTL

### Episodic Memory

`EpisodicMemoryService` links memories to task execution episodes for temporal and contextual retrieval. When a task completes, an episode index is created that records the task's memories, produced artifacts, and tools used. Queries support lookup by task ID, artifact pattern, or tool name. Stale episodes are decayed after a configurable age threshold (default 30 days).

### Procedural Memory

`ProceduralMemoryService` extracts reusable how-to procedures from memory text by detecting patterns like "Step 1/2/3" sequences, "first X, then Y" constructions, and numbered lists. Each procedure has a trigger pattern for matching against future queries, and success/failure reinforcement tracking with automatic flagging for review when the failure rate exceeds 70%.

### Memory Graph

`MemoryGraphService` maintains a relationship graph with entity triples and multi-hop retrieval:

- **Entity extraction** — extracts (subject, predicate, object) triples from memory text using regex patterns
- **Edge building** — creates graph edges between memories via same-entity, related-topic (semantic similarity), and temporal-sequence (same task, within 1 hour) relations
- **Multi-hop retrieval** — BFS traversal from seed memories, expanding along edges up to configurable hop depth
- **Zettelkasten-style auto-linking** — automatically finds top-N similar memories for new records and creates edges

Graph edges and entity triples are stored in dedicated SQLite tables (`memory_graph_edges`, `memory_entity_triples`) with indexed lookups.

### Memory Lineage

`MemoryLineageService` tracks causal chains from memories to decisions via influence links. It supports:

- **Forward tracing**: given a decision, find all memories that influenced it
- **Backward tracing**: given a memory, find all decisions it influenced and their outcomes
- **Stale influencer detection**: find memories with high failure rates across their influenced decisions (used by the anti-pattern pass)

Influence links are stored as memory records with `memory_kind='influence_link'`.

### Embeddings

`EmbeddingService` provides embedding-based similarity search with lazy loading and graceful degradation:

- Uses `sentence-transformers` (`all-MiniLM-L6-v2`) when available
- Falls back to hash-based pseudo-embeddings (64-dim, deterministic from text tokens) when the library is not installed
- Embeddings are stored as binary blobs in the `memory_embeddings` SQLite table

### Working Memory Manager

`WorkingMemoryManager` implements bounded working memory context injection with a fixed token budget (default 4000 tokens). Memories are selected in priority order:

1. PITFALL warnings (highest priority)
2. Procedural matches
3. Static memories (by freshness)
4. Retrieved memories (by relevance)
5. Overflow items are excluded with a footer indicating how many were truncated

## What Exists Today

Safe claims:

- the repository already defines `BeliefRecord` and `MemoryRecord`
- the repository already has memory governance services with 4 scope kinds and 9 retention classes
- current logic already covers classification, scope, retention, expiry, and supersession paths
- half-life confidence decay and four-state freshness model are implemented
- unified memory quality scoring (weighted geometric mean of decay and confidence) is implemented
- the dream cycle consolidation pipeline (dedup, strengthen, decay, reflect, anti-pattern) is implemented
- episodic, procedural, semantic, and working memory types are classified via the CoALA-inspired taxonomy
- a memory graph with entity triples, multi-hop BFS retrieval, and Zettelkasten auto-linking is implemented
- memory lineage tracks causal chains from memories to decisions via influence links
- embedding service with `sentence-transformers` support and fallback pseudo-embeddings is implemented
- bounded working memory manager with token budget and priority-based selection is implemented

Careful claims:

- the memory model is materially real, but still evolving
- evidence-bound memory is a shipped direction with partial implementation, not a fully settled final system

## Why This Matters

Many agent systems say they have memory. Fewer say what kind of memory, under what scope, with what evidence, and with what invalidation rule.

Hermit's memory model matters because durable work needs:

- memory that can be trusted enough to use
- memory that can still be challenged
- memory that does not silently outrank evidence
