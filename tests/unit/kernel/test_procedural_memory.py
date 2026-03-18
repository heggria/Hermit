from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.procedural import ProceduralMemoryService, ProceduralRecord
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memory(store: KernelStore, *, task_id: str = "task-1", **kwargs):
    """Helper to create a memory record with sensible defaults."""
    defaults = dict(
        task_id=task_id,
        conversation_id="conv-1",
        category="project_convention",
        claim_text="default claim",
        scope_kind="workspace",
        scope_ref="workspace:default",
        retention_class="project_convention",
        memory_kind="durable_fact",
        confidence=0.8,
        trust_tier="durable",
    )
    defaults.update(kwargs)
    return store.create_memory_record(**defaults)


def test_extract_procedure_step_pattern(tmp_path: Path) -> None:
    """Text with 'Step 1: X, Step 2: Y' produces a ProceduralRecord."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ProceduralMemoryService()
        record = _create_memory(
            store,
            claim_text="To deploy the app, Step 1: build the image. Step 2: push to registry.",
        )

        proc = svc.extract_procedure(record)

        assert proc is not None
        assert isinstance(proc, ProceduralRecord)
        assert len(proc.steps) >= 2
        assert proc.source_memory_ids == [record.memory_id]
    finally:
        store.close()


def test_extract_procedure_first_then_pattern(tmp_path: Path) -> None:
    """Text with 'First X, then Y' produces a procedure."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ProceduralMemoryService()
        record = _create_memory(
            store,
            claim_text="To run tests, first install dependencies, then run pytest.",
        )

        proc = svc.extract_procedure(record)

        assert proc is not None
        assert len(proc.steps) >= 2
    finally:
        store.close()


def test_extract_procedure_no_steps(tmp_path: Path) -> None:
    """Plain fact without step patterns returns None."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ProceduralMemoryService()
        record = _create_memory(store, claim_text="Python is a programming language")

        proc = svc.extract_procedure(record)

        assert proc is None
    finally:
        store.close()


def test_match_procedures_by_trigger(tmp_path: Path) -> None:
    """A saved procedure's trigger pattern matches a relevant query."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ProceduralMemoryService()
        proc = ProceduralRecord(
            procedure_id="proc-test-match",
            trigger_pattern="deploy the application",
            steps=["build image", "push to registry", "restart service"],
            confidence=0.8,
            source_memory_ids=["mem-1"],
            created_at=1000.0,
            updated_at=1000.0,
        )
        svc.save_procedure(proc, store)

        matches = svc.match_procedures("how to deploy the application", store)

        assert len(matches) >= 1
        assert matches[0].procedure_id == "proc-test-match"
    finally:
        store.close()


def test_reinforce_success(tmp_path: Path) -> None:
    """Reinforcing with success=True increments success_count."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ProceduralMemoryService()
        proc = ProceduralRecord(
            procedure_id="proc-reinforce-ok",
            trigger_pattern="run tests",
            steps=["pytest", "check output"],
            confidence=0.8,
            created_at=1000.0,
            updated_at=1000.0,
        )
        svc.save_procedure(proc, store)

        svc.reinforce("proc-reinforce-ok", success=True, store=store)

        loaded = svc._load_procedure("proc-reinforce-ok", store)
        assert loaded is not None
        assert loaded.success_count == 1
    finally:
        store.close()


def test_reinforce_failure_flags_review(tmp_path: Path) -> None:
    """High failure rate (>70% with 3+ attempts) flags procedure for review."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ProceduralMemoryService()
        proc = ProceduralRecord(
            procedure_id="proc-fail",
            trigger_pattern="problematic step",
            steps=["step a", "step b"],
            confidence=0.6,
            success_count=0,
            failure_count=2,
            created_at=1000.0,
            updated_at=1000.0,
        )
        svc.save_procedure(proc, store)

        # Third failure brings total to 3, failure_rate = 3/3 = 100% > 70%
        svc.reinforce("proc-fail", success=False, store=store)

        loaded = svc._load_procedure("proc-fail", store)
        assert loaded is not None
        assert loaded.status == "review"
        assert loaded.failure_count == 3
    finally:
        store.close()


def test_save_and_load_procedure(tmp_path: Path) -> None:
    """Roundtrip: save a procedure then load it back with all fields intact."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ProceduralMemoryService()
        proc = ProceduralRecord(
            procedure_id="proc-roundtrip",
            trigger_pattern="deploy service",
            steps=["build", "test", "deploy"],
            confidence=0.75,
            source_memory_ids=["mem-a", "mem-b"],
            success_count=5,
            failure_count=1,
            status="active",
            created_at=1000.0,
            updated_at=2000.0,
        )
        svc.save_procedure(proc, store)

        loaded = svc._load_procedure("proc-roundtrip", store)
        assert loaded is not None
        assert loaded.procedure_id == "proc-roundtrip"
        assert loaded.trigger_pattern == "deploy service"
        assert loaded.steps == ["build", "test", "deploy"]
        assert loaded.confidence == 0.75
        assert loaded.source_memory_ids == ["mem-a", "mem-b"]
        assert loaded.success_count == 5
        assert loaded.failure_count == 1
        assert loaded.status == "active"
    finally:
        store.close()
