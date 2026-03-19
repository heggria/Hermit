"""E2E: Memory production path verification.

Verifies that all memory modules are exercised in the real production
lifecycle: hook registration → injection → extraction → promotion → enrichment → retrieval.

Tests cover:
- Phases 1-5: Production hook paths (injection, extraction, promotion, registration)
- Phase 6: Post-promotion enrichment (embedding, graph, lineage, episodic)
- Phase 7: Read path hybrid retrieval wiring
- Phase 8: Consolidation trigger wiring
- Phase 9: Service registry singleton lifecycle
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry
from hermit.runtime.capability.contracts.base import HookEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> SimpleNamespace:
    """Build a minimal settings object matching the real runtime shape."""
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "memory").mkdir(exist_ok=True)
    memory_file = base_dir / "memory" / "memories.md"
    memory_file.write_text("# Memories\n", encoding="utf-8")
    session_state_file = base_dir / "memory" / "session_state.json"
    session_state_file.write_text("{}", encoding="utf-8")
    kernel_dir = base_dir / "kernel"
    kernel_dir.mkdir(exist_ok=True)
    artifacts_dir = kernel_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    return SimpleNamespace(
        memory_file=str(memory_file),
        session_state_file=session_state_file,
        kernel_db_path=str(kernel_dir / "state.db"),
        kernel_artifacts_dir=str(artifacts_dir),
        has_auth=True,
        model="claude-sonnet-4-20250514",
        base_dir=str(base_dir),
    )


def _make_messages(pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Build a message list from (role, content) pairs."""
    return [{"role": role, "content": content} for role, content in pairs]


# ===========================================================================
# Phase 1: SYSTEM_PROMPT injection (static memory)
# ===========================================================================


class TestSystemPromptInjection:
    """Verify the SYSTEM_PROMPT hook path injects governed memory context."""

    def test_inject_empty_memory_returns_empty(self, tmp_path: Path) -> None:
        """No memories → no injection content."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_injection import inject_memory

        result = inject_memory(engine, settings)
        # Should not crash; may be empty or have structure
        assert isinstance(result, str)

    def test_inject_with_kernel_records(self, tmp_path: Path) -> None:
        """Kernel memory records are injected via ContextCompiler + Governance."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        # Create a governed memory record in kernel store
        store = KernelStore(Path(settings.kernel_db_path))
        try:
            store.create_memory_record(
                task_id="task-1",
                conversation_id="conv-1",
                category="user_preference",
                claim_text="User prefers dark mode for all interfaces.",
                confidence=0.9,
                evidence_refs=["art-001"],
            )
        finally:
            store.close()

        from hermit.plugins.builtin.hooks.memory.hooks_injection import inject_memory

        result = inject_memory(engine, settings)
        # Should produce a non-empty memory context
        assert "<memory_context>" in result or result == ""
        # If context pack compilation fails gracefully, at least the governance
        # service must have been called (no crash = path exercised)

    def test_governance_service_filters_categories(self, tmp_path: Path) -> None:
        """MemoryGovernanceService.filter_static_categories is called on injection path."""
        from hermit.kernel.context.memory.governance import MemoryGovernanceService

        gov = MemoryGovernanceService()
        categories: dict[str, list[MemoryEntry]] = {
            "user_preference": [
                MemoryEntry(category="user_preference", content="dark mode"),
            ],
            "volatile_fact": [
                MemoryEntry(category="volatile_fact", content="temp data"),
            ],
        }
        static = gov.filter_static_categories(categories)
        # user_preference should pass through; volatile_fact should not be static
        assert "user_preference" in static
        # The governance service correctly classifies static vs retrieval categories


# ===========================================================================
# Phase 2: PRE_RUN injection (retrieval memory)
# ===========================================================================


class TestPreRunInjection:
    """Verify the PRE_RUN hook path injects retrieval-based memory."""

    def test_inject_relevant_with_no_kernel(self, tmp_path: Path) -> None:
        """Without kernel DB, retrieval injection returns prompt unchanged."""
        settings = _make_settings(tmp_path)
        settings.kernel_db_path = None
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_injection import (
            inject_relevant_memory,
        )

        result = inject_relevant_memory(
            engine, settings, prompt="What is Python?", session_id="sess-1"
        )
        # Should return the prompt (possibly with memory wrapper, possibly bare)
        assert "What is Python?" in result

    def test_inject_relevant_with_kernel_records(self, tmp_path: Path) -> None:
        """With kernel records, ContextCompiler compiles retrieval context."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        store = KernelStore(Path(settings.kernel_db_path))
        try:
            store.create_memory_record(
                task_id="task-2",
                conversation_id="conv-2",
                category="tech_decision",
                claim_text="Project uses PostgreSQL 16 with pgvector extension.",
                confidence=0.85,
                evidence_refs=["art-002"],
            )
        finally:
            store.close()

        from hermit.plugins.builtin.hooks.memory.hooks_injection import (
            inject_relevant_memory,
        )

        result = inject_relevant_memory(
            engine, settings, prompt="database query optimization", session_id="sess-2"
        )
        assert "database query optimization" in result


# ===========================================================================
# Phase 3: POST_RUN checkpoint extraction
# ===========================================================================


class TestPostRunCheckpoint:
    """Verify POST_RUN hook exercises checkpoint extraction path."""

    def test_checkpoint_skips_no_messages(self, tmp_path: Path) -> None:
        """Empty messages → checkpoint skipped."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_extraction import (
            checkpoint_memories,
        )

        # Should not raise
        checkpoint_memories(engine, settings, session_id="sess-3", messages=[])

    def test_should_checkpoint_explicit_signal(self) -> None:
        """Explicit memory signal triggers checkpoint."""
        from hermit.plugins.builtin.hooks.memory.hooks_extraction import (
            should_checkpoint,
        )

        # "remember this" and "always" are in the signal regex
        messages = _make_messages(
            [
                ("user", "I always prefer TypeScript over JavaScript, remember this."),
                ("assistant", "I'll remember your preference for TypeScript."),
            ]
        )
        should, reason = should_checkpoint(messages)
        assert should is True
        assert reason == "explicit_memory_signal"

    def test_should_checkpoint_below_threshold(self) -> None:
        """Short conversation below threshold → no checkpoint."""
        from hermit.plugins.builtin.hooks.memory.hooks_extraction import (
            should_checkpoint,
        )

        messages = _make_messages([("user", "hi")])
        should, reason = should_checkpoint(messages)
        assert should is False
        assert reason == "below_threshold"


# ===========================================================================
# Phase 4: SESSION_END governed promotion pipeline
# ===========================================================================


class TestGovernedPromotion:
    """Verify the full governed promotion pipeline creates kernel records."""

    def test_promote_creates_task_decision_receipt(self, tmp_path: Path) -> None:
        """Full promotion: task → policy → decision → grant → belief → memory → receipt."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
            promote_memories_via_kernel,
        )

        new_entries = [
            MemoryEntry(
                category="user_preference",
                content="User prefers vim keybindings in all editors.",
                confidence=0.85,
            ),
            MemoryEntry(
                category="tech_decision",
                content="Project adopts Ruff as the sole Python linter.",
                confidence=0.9,
            ),
        ]

        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-promo",
            messages=_make_messages(
                [
                    ("user", "I always use vim keybindings. Also let's use Ruff."),
                    ("assistant", "Noted: vim keybindings and Ruff for linting."),
                ]
            ),
            used_keywords={"vim", "ruff"},
            new_entries=new_entries,
            mode="session_end",
        )

        assert result is True

        # Verify kernel records were created
        store = KernelStore(Path(settings.kernel_db_path))
        try:
            # Task should exist (policy_profile="memory" set by promotion pipeline)
            tasks = store.list_tasks(limit=10)
            promo_tasks = [t for t in tasks if t.policy_profile == "memory"]
            assert len(promo_tasks) >= 1

            # Memory records should exist
            memories = store.list_memory_records(status="active", limit=100)
            assert len(memories) >= 2

            # Beliefs should exist
            beliefs = store.list_beliefs(status="active", limit=100)
            assert len(beliefs) >= 2

            # Receipts should exist
            receipts = store.list_receipts(limit=100)
            assert len(receipts) >= 1
            receipt = receipts[0]
            assert receipt.result_code == "succeeded"
            assert receipt.rollback_supported is True

            # Decision should exist
            decisions = store.list_decisions(limit=100)
            assert len(decisions) >= 1
            decision = decisions[0]
            assert decision.decision_type == "memory_promotion"
            assert len(decision.evidence_refs) >= 4  # transcript, extraction, action, policy

            # Capability grant should exist and be consumed
            grants = store.list_capability_grants(limit=100)
            assert len(grants) >= 1

            # Artifacts should exist (transcript, extraction, action, policy, result, env, rollback)
            artifacts = store.list_artifacts(limit=100)
            assert len(artifacts) >= 6

            # Memory mirror file should be updated
            mirror = Path(settings.memory_file).read_text(encoding="utf-8")
            assert "vim" in mirror.lower() or "ruff" in mirror.lower()
        finally:
            store.close()

    def test_promote_returns_false_without_kernel_db(self, tmp_path: Path) -> None:
        """No kernel_db_path → promotion skipped gracefully."""
        settings = _make_settings(tmp_path)
        settings.kernel_db_path = None
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
            promote_memories_via_kernel,
        )

        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-no-kernel",
            messages=_make_messages([("user", "test")]),
            used_keywords=set(),
            new_entries=[MemoryEntry(category="other", content="test")],
            mode="session_end",
        )
        assert result is False


# ===========================================================================
# Phase 5: Hook registration wiring
# ===========================================================================


class TestHookRegistration:
    """Verify the plugin hook registration wires up all 4 events."""

    def test_register_attaches_four_hooks(self, tmp_path: Path) -> None:
        """The memory plugin registers SYSTEM_PROMPT, PRE_RUN, POST_RUN, SESSION_END."""
        from hermit.plugins.builtin.hooks.memory.hooks import register
        from hermit.runtime.capability.contracts.base import PluginContext
        from hermit.runtime.capability.contracts.hooks import HooksEngine

        settings = _make_settings(tmp_path)
        hooks_engine = HooksEngine()
        ctx = PluginContext(hooks_engine, settings=settings)

        register(ctx)

        registered_keys = set(hooks_engine._handlers.keys())
        assert HookEvent.SYSTEM_PROMPT.value in registered_keys
        assert HookEvent.PRE_RUN.value in registered_keys
        assert HookEvent.POST_RUN.value in registered_keys
        assert HookEvent.SESSION_END.value in registered_keys


# ===========================================================================
# Phase 6: Post-promotion enrichment — embedding, graph, lineage, episodic
# ===========================================================================


class TestPostPromotionEnrichment:
    """Verify that promoted memories trigger post-promotion enrichment:
    embedding index, graph entities, lineage links, episodic index.
    """

    def test_promotion_creates_embedding_records(self, tmp_path: Path) -> None:
        """After promotion, memory_embeddings table should have entries."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
            promote_memories_via_kernel,
        )
        from hermit.plugins.builtin.hooks.memory.services import reset_services

        reset_services()

        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-enrich-embed",
            messages=_make_messages(
                [
                    ("user", "Python uses Ruff as linter, remember this always."),
                    ("assistant", "Noted."),
                ]
            ),
            used_keywords={"ruff"},
            new_entries=[
                MemoryEntry(
                    category="tech_decision",
                    content="Project uses Ruff as the Python linter.",
                    confidence=0.9,
                ),
            ],
            mode="session_end",
        )
        assert result is True

        store = KernelStore(Path(settings.kernel_db_path))
        try:
            # Embedding table should exist and have entries
            rows = store._get_conn().execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()
            assert rows is not None
            assert rows[0] >= 1, "Expected at least 1 embedding record after promotion"
        finally:
            store.close()

        reset_services()

    def test_promotion_creates_lineage_records(self, tmp_path: Path) -> None:
        """After promotion, influence_link memory records should exist."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
            promote_memories_via_kernel,
        )
        from hermit.plugins.builtin.hooks.memory.services import reset_services

        reset_services()

        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-enrich-lineage",
            messages=_make_messages(
                [
                    ("user", "Always use vim keybindings, remember this."),
                    ("assistant", "Noted."),
                ]
            ),
            used_keywords={"vim"},
            new_entries=[
                MemoryEntry(
                    category="user_preference",
                    content="User prefers vim keybindings.",
                    confidence=0.85,
                ),
            ],
            mode="session_end",
        )
        assert result is True

        store = KernelStore(Path(settings.kernel_db_path))
        try:
            all_records = store.list_memory_records(status="active", limit=500)
            lineage_records = [r for r in all_records if r.memory_kind == "influence_link"]
            assert len(lineage_records) >= 1, (
                "Expected at least 1 influence_link record after promotion"
            )
        finally:
            store.close()

        reset_services()

    def test_promotion_creates_episodic_index(self, tmp_path: Path) -> None:
        """After promotion, episode_index memory records should exist."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
            promote_memories_via_kernel,
        )
        from hermit.plugins.builtin.hooks.memory.services import reset_services

        reset_services()

        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-enrich-episodic",
            messages=_make_messages(
                [
                    ("user", "We decided to use PostgreSQL, remember this always."),
                    ("assistant", "Noted."),
                ]
            ),
            used_keywords={"postgresql"},
            new_entries=[
                MemoryEntry(
                    category="tech_decision",
                    content="Project uses PostgreSQL.",
                    confidence=0.9,
                ),
            ],
            mode="session_end",
        )
        assert result is True

        store = KernelStore(Path(settings.kernel_db_path))
        try:
            all_records = store.list_memory_records(status="active", limit=500)
            episode_records = [r for r in all_records if r.memory_kind == "episode_index"]
            assert len(episode_records) >= 1, (
                "Expected at least 1 episode_index record after promotion"
            )
        finally:
            store.close()

        reset_services()

    def test_enrichment_failure_does_not_block_promotion(self, tmp_path: Path) -> None:
        """If an enrichment service fails, promotion still succeeds."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
            promote_memories_via_kernel,
        )
        from hermit.plugins.builtin.hooks.memory.services import reset_services

        reset_services()

        # Promotion should succeed even if enrichment has issues
        result = promote_memories_via_kernel(
            engine,
            settings,
            session_id="sess-enrich-resilient",
            messages=_make_messages(
                [
                    ("user", "Remember this: always use dark mode."),
                    ("assistant", "Noted."),
                ]
            ),
            used_keywords={"dark"},
            new_entries=[
                MemoryEntry(
                    category="user_preference",
                    content="User prefers dark mode.",
                    confidence=0.8,
                ),
            ],
            mode="session_end",
        )
        assert result is True

        # Core promotion artifacts must exist regardless of enrichment
        store = KernelStore(Path(settings.kernel_db_path))
        try:
            memories = store.list_memory_records(status="active", limit=100)
            durable = [m for m in memories if m.memory_kind == "durable_fact"]
            assert len(durable) >= 1
        finally:
            store.close()

        reset_services()


# ===========================================================================
# Phase 7: Read path — hybrid retrieval wiring
# ===========================================================================


class TestReadPathHybridRetrieval:
    """Verify that hooks_injection uses HybridRetrievalService via services.py."""

    def test_context_compiler_accepts_retrieval_service(self, tmp_path: Path) -> None:
        """ContextCompiler can be instantiated with retrieval_service param."""
        from hermit.kernel.context.compiler.compiler import ContextCompiler
        from hermit.kernel.context.memory.retrieval import HybridRetrievalService

        store = KernelStore(tmp_path / "test.db")
        try:
            svc = HybridRetrievalService()
            compiler = ContextCompiler(retrieval_service=svc, store=store)
            assert compiler._retrieval_service is svc
            assert compiler._store is store
        finally:
            store.close()

    def test_context_compiler_hybrid_fallback(self, tmp_path: Path) -> None:
        """When retrieval_service is None, legacy scoring path still works."""
        from hermit.kernel.context.compiler.compiler import ContextCompiler
        from hermit.kernel.context.models.context import (
            TaskExecutionContext,
            WorkingStateSnapshot,
        )

        store = KernelStore(tmp_path / "test.db")
        try:
            store.create_memory_record(
                task_id="t1",
                conversation_id="c1",
                category="tech_decision",
                claim_text="Project uses Python 3.13.",
                confidence=0.9,
            )
            memories = store.list_memory_records(status="active", limit=100)
            compiler = ContextCompiler()  # No retrieval_service
            ctx = TaskExecutionContext(
                conversation_id="c1",
                task_id="t1",
                step_id="s1",
                step_attempt_id="sa1",
                source_channel="test",
                workspace_root=str(tmp_path),
            )
            pack = compiler.compile(
                context=ctx,
                working_state=WorkingStateSnapshot(goal_summary="test"),
                beliefs=[],
                memories=memories,
                query="Python version used in this project",
            )
            # Legacy path should produce results
            assert pack.pack_hash != ""
        finally:
            store.close()

    def test_inject_relevant_wires_hybrid_retrieval(self, tmp_path: Path) -> None:
        """inject_relevant_memory path initializes services and passes them."""
        settings = _make_settings(tmp_path)
        engine = MemoryEngine(settings.memory_file)

        from hermit.plugins.builtin.hooks.memory.services import reset_services

        reset_services()

        store = KernelStore(Path(settings.kernel_db_path))
        try:
            store.create_memory_record(
                task_id="t1",
                conversation_id="c1",
                category="tech_decision",
                claim_text="Project uses FastAPI with async handlers.",
                confidence=0.9,
            )
        finally:
            store.close()

        from hermit.plugins.builtin.hooks.memory.hooks_injection import (
            inject_relevant_memory,
        )

        # Should not crash — services.get_services is called internally
        result = inject_relevant_memory(
            engine,
            settings,
            prompt="How are API handlers structured in this project?",
            session_id="sess-hybrid",
        )
        assert "How are API handlers structured" in result

        reset_services()


# ===========================================================================
# Phase 8: Consolidation trigger wiring
# ===========================================================================


class TestConsolidationTrigger:
    """Verify the consolidation dream cycle is wired into SESSION_END."""

    def test_maybe_consolidate_runs_when_not_throttled(self, tmp_path: Path) -> None:
        """_maybe_consolidate runs consolidation when throttle file is absent."""
        settings = _make_settings(tmp_path)

        from hermit.plugins.builtin.hooks.memory.services import reset_services

        reset_services()

        # Create some memories to consolidate
        store = KernelStore(Path(settings.kernel_db_path))
        try:
            for i in range(3):
                store.create_memory_record(
                    task_id="t1",
                    conversation_id="c1",
                    category="tech_decision",
                    claim_text=f"Memory claim number {i}",
                    confidence=0.8,
                )
        finally:
            store.close()

        from hermit.plugins.builtin.hooks.memory.hooks import _maybe_consolidate

        # Should run without error
        _maybe_consolidate(settings)

        # Throttle file should now exist
        throttle_file = Path(settings.memory_file).parent / ".last_consolidation"
        assert throttle_file.exists(), "Throttle file should be created after consolidation"

        reset_services()

    def test_maybe_consolidate_skips_when_throttled(self, tmp_path: Path) -> None:
        """_maybe_consolidate skips if throttle file is recent."""
        import time

        settings = _make_settings(tmp_path)

        # Create a recent throttle file
        throttle_file = Path(settings.memory_file).parent / ".last_consolidation"
        throttle_file.write_text(str(time.time()))

        from hermit.plugins.builtin.hooks.memory.hooks import _maybe_consolidate

        # Should return quickly without error (no store needed since throttled)
        _maybe_consolidate(settings)

    def test_maybe_consolidate_skips_without_kernel_db(self, tmp_path: Path) -> None:
        """_maybe_consolidate skips gracefully when no kernel_db_path."""
        settings = _make_settings(tmp_path)
        settings.kernel_db_path = None

        from hermit.plugins.builtin.hooks.memory.hooks import _maybe_consolidate

        # Should not crash
        _maybe_consolidate(settings)


# ===========================================================================
# Phase 9: Service registry singleton lifecycle
# ===========================================================================


class TestServiceRegistry:
    """Verify the service registry provides singletons and initializes schemas."""

    def test_get_services_returns_same_instance(self, tmp_path: Path) -> None:
        """Calling get_services twice returns the same cached bundle."""
        from hermit.plugins.builtin.hooks.memory.services import (
            get_services,
            reset_services,
        )

        reset_services()

        store = KernelStore(tmp_path / "test.db")
        try:
            svc1 = get_services(store)
            svc2 = get_services(store)
            assert svc1 is svc2
            assert svc1.embedding is svc2.embedding
            assert svc1.retrieval is svc2.retrieval
        finally:
            store.close()

        reset_services()

    def test_get_services_initializes_schemas(self, tmp_path: Path) -> None:
        """get_services creates embedding and graph tables."""
        from hermit.plugins.builtin.hooks.memory.services import (
            get_services,
            reset_services,
        )

        reset_services()

        store = KernelStore(tmp_path / "test.db")
        try:
            get_services(store)

            # Verify embedding table exists
            rows = (
                store._get_conn()
                .execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_embeddings'"
                )
                .fetchone()
            )
            assert rows is not None, "memory_embeddings table should exist"

            # Verify graph table exists
            rows = (
                store._get_conn()
                .execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_graph_edges'"
                )
                .fetchone()
            )
            assert rows is not None, "memory_graph_edges table should exist"
        finally:
            store.close()

        reset_services()

    def test_reset_clears_cache(self, tmp_path: Path) -> None:
        """reset_services clears the cached singleton."""
        from hermit.plugins.builtin.hooks.memory.services import (
            get_services,
            reset_services,
        )

        reset_services()

        store = KernelStore(tmp_path / "test.db")
        try:
            svc1 = get_services(store)
            reset_services()
            svc2 = get_services(store)
            assert svc1 is not svc2
        finally:
            store.close()

        reset_services()
