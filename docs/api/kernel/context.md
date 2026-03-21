# Context

Artifact-native context compilation, provider input injection, and memory governance.

## Package layout

```
hermit.kernel.context
├── compiler/          # ContextPack assembly
│   └── compiler       # ContextPack (v3), ContextCompiler
├── injection/         # LLM prompt assembly
│   └── provider_input # ProviderInputCompiler
├── models/            # Shared data models
│   └── context        # TaskExecutionContext
└── memory/            # Memory subsystems (20+ modules)
    ├── retrieval       # HybridRetrievalService, RetrievalResult, RetrievalReport
    ├── decay           # MemoryDecayService — TTL & freshness scoring
    ├── decay_models    # FreshnessState, FreshnessAssessment, DecaySweepTransition/Report
    ├── confidence      # ConfidenceDecayService, ConfidenceReport — half-life per retention class
    ├── consolidation   # ConsolidationService, ConsolidationReport
    ├── anti_pattern    # AntiPatternService, PitfallCandidate
    ├── embeddings      # EmbeddingService — vector encode/decode & schema bootstrap
    ├── episodic        # EpisodicMemoryService
    ├── episodic_models # EpisodeIndex, EpisodicResult, EpisodeKnowledge
    ├── governance      # ClaimSignals, MemoryClassification — extraction & classification
    ├── graph           # MemoryGraphService — entity triples & edges
    ├── graph_models    # EntityTriple, GraphEdge, GraphQueryResult
    ├── knowledge       # BeliefService, MemoryRecordService
    ├── lineage         # MemoryLineageService
    ├── lineage_models  # InfluenceLink, DecisionLineage, MemoryImpact, StaleMemory
    ├── memory_quality  # MemoryQualityService — weighted geometric mean scoring
    ├── procedural      # ProceduralMemoryService, ProceduralRecord
    ├── reflect         # ReflectionService, ReflectionInsight
    ├── reranker        # CrossEncoderReranker
    ├── taxonomy        # MemoryType enum, classify_memory_type()
    ├── text            # topic_tokens(), summary_prompt() — NLP helpers
    ├── token_index     # TokenIndex — inverted token index for fast candidate filtering
    └── working_memory  # WorkingMemoryManager, WorkingMemoryItem, WorkingMemoryPack
```

---

## Compiler

::: hermit.kernel.context.compiler.compiler
    options:
      members:
        - ContextPack
        - ContextCompiler

### ContextPack v3 fields

| Field | Type | Description |
|-------|------|-------------|
| `static_memory` | `list[dict]` | Long-lived memory records |
| `retrieval_memory` | `list[dict]` | Dynamically retrieved memories |
| `selected_beliefs` | `list[dict]` | High-confidence belief records |
| `working_state` | `dict` | Current working state (goals, loops, constraints) |
| `episodic_context` | `list[dict]` | Episode-based context entries |
| `procedural_context` | `list[dict]` | Procedural / how-to memory entries |
| `task_summary` | `dict` | Active task metadata |
| `step_summary` | `dict` | Current step metadata |
| `policy_summary` | `dict` | Policy engine state |
| `planning_state` | `dict` | Plan mode state |
| `carry_forward` | `dict \| None` | Cross-step carry-forward payload |
| `continuation_guidance` | `dict \| None` | Guidance for multi-turn continuations |
| `recent_notes` | `list[dict]` | Recent scratch-pad notes |
| `relevant_artifact_refs` | `list[str]` | Artifact URIs relevant to current step |
| `ingress_artifact_refs` | `list[str]` | Artifacts from the ingress message |
| `focus_summary` | `dict \| None` | Focus/attention summary |
| `bound_ingress_deltas` | `list[dict]` | Ingress delta bindings |
| `session_projection_ref` | `str \| None` | Artifact ref for session projection |
| `blackboard_entries` | `list[dict]` | Shared blackboard entries |
| `selection_reasons` | `dict[str, str]` | Why each memory was selected |
| `excluded_memory_ids` | `list[str]` | Memory IDs excluded from the pack |
| `excluded_reasons` | `dict[str, str]` | Why each memory was excluded |
| `pack_hash` | `str` | Content hash for deduplication |
| `artifact_uri` | `str \| None` | Stored artifact URI |
| `artifact_hash` | `str \| None` | Stored artifact hash |

---

## Injection

::: hermit.kernel.context.injection.provider_input
    options:
      members:
        - ProviderInputCompiler

---

## Models

::: hermit.kernel.context.models.context
    options:
      members:
        - TaskExecutionContext

---

## Memory subsystems

### Retrieval pipeline

::: hermit.kernel.context.memory.retrieval

::: hermit.kernel.context.memory.reranker

::: hermit.kernel.context.memory.token_index

::: hermit.kernel.context.memory.embeddings

### Governance & classification

::: hermit.kernel.context.memory.governance

::: hermit.kernel.context.memory.taxonomy

::: hermit.kernel.context.memory.knowledge

### Decay & confidence

::: hermit.kernel.context.memory.decay

::: hermit.kernel.context.memory.decay_models

::: hermit.kernel.context.memory.confidence

::: hermit.kernel.context.memory.memory_quality

### Consolidation & reflection

::: hermit.kernel.context.memory.consolidation

::: hermit.kernel.context.memory.reflect

::: hermit.kernel.context.memory.anti_pattern

### Episodic memory

::: hermit.kernel.context.memory.episodic

::: hermit.kernel.context.memory.episodic_models

### Procedural memory

::: hermit.kernel.context.memory.procedural

### Graph memory

::: hermit.kernel.context.memory.graph

::: hermit.kernel.context.memory.graph_models

### Lineage tracking

::: hermit.kernel.context.memory.lineage

::: hermit.kernel.context.memory.lineage_models

### Working memory

::: hermit.kernel.context.memory.working_memory

### Text utilities

::: hermit.kernel.context.memory.text
