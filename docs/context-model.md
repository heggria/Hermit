---
description: "Hermit's artifact-native context model: context packs, hybrid retrieval, continuation guidance, and provider input compilation."
---

# Context Model

Hermit treats context as more than message history.

Message history still matters, but it is not the primary substrate for durable work. Hermit is moving toward an artifact-native context model where the kernel compiles a task-scoped context pack from structured state.

This matters because durable work should be grounded in what the system actually knows, what it has observed, and what it has produced, not just what happened to be said recently.

## Core Principle

Most agents treat transcript as default context. Hermit treats artifacts and structured state as default context units.

In practice, Hermit wants context assembly to answer:

- what task is in focus
- what state is currently bounded and active
- what beliefs are currently usable
- what durable memories are in scope
- what artifacts are relevant evidence
- what recent deltas matter for continuation

## Context Pack

The current codebase already contains a context compiler and a context pack model (`ContextPack`, versioned as `context.pack/v3`).

Current pack fields include:

- static memory
- retrieval memory
- selected beliefs
- working state
- task summary
- step summary
- policy summary
- planning state
- episodic context (task-episode-linked memories)
- procedural context (matched how-to procedures)
- carry-forward information
- continuation guidance (mode-aware anchor interpretation)
- recent notes
- relevant artifact references
- ingress artifact references
- focus summary
- bound ingress deltas (new ingress events since last compilation)
- session projection ref
- blackboard entries (claims, evidence, patches, risks, conflicts, todos, decisions)
- selection reasons (per-memory explanations for inclusion)
- excluded memory IDs and excluded reasons (per-memory explanations for exclusion)
- pack hash (SHA-256 integrity hash of the pack payload)
- artifact URI and artifact hash (when the pack is persisted to the artifact store)

This is one of the strongest implementation signals that Hermit is no longer just transcript-driven.

## Artifact-Native Context

Artifacts matter because they can be:

- cited
- hashed
- referred to by receipts
- reused across task boundaries
- treated as evidence instead of just raw text

Examples include:

- input payloads
- output payloads
- receipt bundles
- proof bundles
- context manifests
- state witnesses

The point is not to eliminate text. The point is to make text only one context source among several.

## Working State, Beliefs, And Memory

Hermit distinguishes among:

- **working state**: bounded execution state for active work
- **beliefs**: revisable working truth with evidence references
- **memory records**: durable cross-task knowledge promoted under governance rules

This separation helps prevent two failure modes:

- transcript-only drift
- memory turning into hidden authority

## Continuation And Carry-Forward

A durable task often needs more than "continue the conversation."

Hermit's direction is to carry forward:

- the right task anchor
- the right recent notes
- the right artifacts
- the right bounded state

without flattening everything into unbounded transcript replay.

## ProviderInputCompiler

The `ContextCompiler` produces a `ContextPack`, but the full provider-facing pipeline is handled by `ProviderInputCompiler` (`src/hermit/kernel/context/injection/provider_input.py`). It sits between the context compiler and the LLM provider and is responsible for:

- **Ingress normalization**: stripping runtime markup, extracting code blocks and long prose payloads into artifacts, producing a compact inline excerpt
- **Full compilation**: assembling a `CompiledProviderInput` from the context pack, conversation projection, planning state, continuation guidance, and steering directives
- **Artifact materialization**: persisting the context pack and working state snapshot as auditable artifacts with lineage references
- **Message rendering**: converting the compiled context into a structured provider message with XML-tagged sections (`<context_pack>`, `<continuation_guidance>`, `<current_request>`, `<steering_directives>`)

The flow is: raw ingress text -> `ProviderInputCompiler.compile()` -> `ContextCompiler.compile()` -> `ContextPack` -> rendered message -> LLM provider.

## Hybrid Retrieval

Memory retrieval uses a 7-way hybrid retrieval pipeline (`HybridRetrievalService`) with dual-path mode selection:

- **Fast path** (short queries <50 chars): token overlap only, targeting <5ms
- **Deep path** (longer queries or explicit signal): all seven retrieval paths

The seven retrieval paths are:

1. **Token overlap** — Jaccard similarity with inverted index, cached tokens, and precomputed query-side values for fast candidate filtering
2. **Semantic embedding** — cosine similarity using `sentence-transformers` (model: `all-MiniLM-L6-v2`), with hash-based pseudo-embedding fallback when the library is unavailable
3. **Graph traversal** — BFS expansion along memory graph edges (same-entity, related-topic, temporal-sequence relations) using seed memories from token overlap
4. **Temporal freshness** — recency-weighted scoring combined with half-life confidence decay
5. **Importance score** — LLM-extracted importance rating (1-10 scale)
6. **Procedural memory matching** — trigger pattern matching against extracted how-to procedures
7. **Entity knowledge graph** — entity co-occurrence lookup via the `entity_links` table

Results from all active paths are fused using **Reciprocal Rank Fusion (RRF)** with constant k=60.

### Cross-Encoder Reranking

After RRF fusion, an optional second-stage **cross-encoder reranker** (`CrossEncoderReranker`, model: `cross-encoder/ms-marco-MiniLM-L-6-v2`) scores each (query, candidate_text) pair jointly for deeper semantic relevance. This runs only on the deep path when the model is available, and falls back to passthrough when it is not.

## Continuation Guidance

When a new task carries forward context from a completed anchor task, the `ProviderInputCompiler` generates continuation guidance with mode-aware interpretation. The supported modes are:

- **explicit_topic_shift** — the current request explicitly starts a new topic; ignore the anchor when deciding intent
- **strong_topic_shift** — the current request carries strong new semantics that do not match the anchor topic; treat as a new topic unless the user clearly refers back
- **anchor_correction** — the current request is short or ambiguous; prefer interpreting it as a clarification or correction of the anchor task
- **plain_new_task** (default) — use the anchor as background context, but treat the current request as a normal new task

Interpretation priority: explicit topic shift > strong new-topic semantics > anchor clarification/correction > ordinary new task.

## What Exists Today

Safe claims:

- the repository already has a context compiler and a `ProviderInputCompiler` that bridges it to LLM providers
- the repository already has a structured context pack with 21+ fields (`context.pack/v3`)
- artifacts, beliefs, and memories already participate in context assembly
- hybrid retrieval with 7-way fusion and optional cross-encoder reranking is implemented
- continuation guidance with mode-aware anchor interpretation is implemented
- context packs are persisted as auditable artifacts with SHA-256 integrity hashes

Careful claims:

- Hermit is still converging on artifact-native context everywhere
- transcript still exists as part of the broader runtime and provider flow

## Why This Matters

Artifact-native context is one of the main reasons Hermit does not collapse back into a generic agent wrapper.

It gives the kernel a better answer to:

- what was in scope
- what evidence informed the action
- what state should carry forward
- what should be durable versus revisable
