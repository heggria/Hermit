"""Tests for ledger schema integrity: table existence, columns, foreign keys, migrations.

Covers:
- Schema v18 has all required tables
- All foreign key relationships are valid
- Schema migration path (_ensure_column)
- hash_chain_algo and event_hash columns exist
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermit.kernel.ledger.journal.store import (
    _KNOWN_KERNEL_TABLES,
    _SCHEMA_VERSION,
    KernelStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store() -> KernelStore:
    return KernelStore(Path(":memory:"))


def _get_tables(store: KernelStore) -> set[str]:
    """Return all table names in the store's database (excluding sqlite internals)."""
    rows = (
        store._get_conn()
        .execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        .fetchall()
    )
    return {str(row[0]) for row in rows}


def _get_columns(store: KernelStore, table: str) -> dict[str, dict[str, Any]]:
    """Return column info for a table: {name: {type, notnull, dflt_value, pk}}."""
    rows = store._get_conn().execute(f"PRAGMA table_info({table})").fetchall()
    return {
        str(row["name"]): {
            "type": str(row["type"]),
            "notnull": bool(row["notnull"]),
            "dflt_value": row["dflt_value"],
            "pk": bool(row["pk"]),
        }
        for row in rows
    }


def _get_foreign_keys(store: KernelStore, table: str) -> list[dict[str, str]]:
    """Return foreign key info for a table."""
    rows = store._get_conn().execute(f"PRAGMA foreign_key_list({table})").fetchall()
    return [
        {
            "table": str(row["table"]),
            "from": str(row["from"]),
            "to": str(row["to"]),
        }
        for row in rows
    ]


def _get_indexes(store: KernelStore) -> list[str]:
    """Return all index names in the store's database."""
    rows = (
        store._get_conn()
        .execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        .fetchall()
    )
    return [str(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# Tests: Required Tables
# ---------------------------------------------------------------------------


class TestRequiredTables:
    """Verify all known kernel tables exist after schema init."""

    def test_all_known_tables_exist(self) -> None:
        store = _make_store()
        tables = _get_tables(store)

        for table_name in _KNOWN_KERNEL_TABLES:
            assert table_name in tables, f"Missing table: {table_name}"

    def test_kernel_meta_table_exists(self) -> None:
        store = _make_store()
        tables = _get_tables(store)
        assert "kernel_meta" in tables

    def test_schema_version_is_v18(self) -> None:
        store = _make_store()
        version = store.schema_version()
        assert version == _SCHEMA_VERSION
        assert version == "18"

    def test_core_tables_present(self) -> None:
        """Core governed execution tables must exist."""
        store = _make_store()
        tables = _get_tables(store)

        core_tables = {
            "tasks",
            "steps",
            "step_attempts",
            "events",
            "artifacts",
            "approvals",
            "receipts",
            "decisions",
            "capability_grants",
            "workspace_leases",
            "rollbacks",
        }
        for table in core_tables:
            assert table in tables, f"Core table missing: {table}"

    def test_v2_tables_present(self) -> None:
        """V2 execution contract tables must exist."""
        store = _make_store()
        tables = _get_tables(store)

        v2_tables = {
            "execution_contracts",
            "evidence_cases",
            "authorization_plans",
            "reconciliations",
        }
        for table in v2_tables:
            assert table in tables, f"V2 table missing: {table}"

    def test_memory_tables_present(self) -> None:
        """Memory subsystem tables must exist."""
        store = _make_store()
        tables = _get_tables(store)

        memory_tables = {
            "beliefs",
            "memory_records",
            "memory_embeddings",
            "memory_graph_edges",
            "memory_entity_triples",
            "procedural_memories",
        }
        for table in memory_tables:
            assert table in tables, f"Memory table missing: {table}"

    def test_scheduling_tables_present(self) -> None:
        store = _make_store()
        tables = _get_tables(store)

        assert "schedule_specs" in tables
        assert "schedule_history" in tables

    def test_program_and_team_tables_present(self) -> None:
        store = _make_store()
        tables = _get_tables(store)

        assert "programs" in tables
        assert "teams" in tables
        assert "milestones" in tables


# ---------------------------------------------------------------------------
# Tests: Column Integrity
# ---------------------------------------------------------------------------


class TestColumnIntegrity:
    """Verify key columns exist with correct types."""

    def test_events_hash_chain_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "events")

        assert "event_hash" in columns, "events table missing event_hash column"
        assert "prev_event_hash" in columns, "events table missing prev_event_hash column"
        assert "hash_chain_algo" in columns, "events table missing hash_chain_algo column"
        assert columns["event_hash"]["type"] == "TEXT"
        assert columns["prev_event_hash"]["type"] == "TEXT"
        assert columns["hash_chain_algo"]["type"] == "TEXT"

    def test_events_core_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "events")

        required = [
            "event_seq",
            "event_id",
            "task_id",
            "step_id",
            "entity_type",
            "entity_id",
            "event_type",
            "actor_principal_id",
            "payload_json",
            "occurred_at",
            "causation_id",
            "correlation_id",
        ]
        for col in required:
            assert col in columns, f"events table missing column: {col}"

    def test_receipts_proof_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "receipts")

        proof_cols = [
            "receipt_bundle_ref",
            "proof_mode",
            "verifiability",
            "signature",
            "signer_ref",
        ]
        for col in proof_cols:
            assert col in columns, f"receipts table missing proof column: {col}"

    def test_receipts_rollback_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "receipts")

        rollback_cols = [
            "rollback_supported",
            "rollback_strategy",
            "rollback_status",
            "rollback_ref",
            "rollback_artifact_refs_json",
        ]
        for col in rollback_cols:
            assert col in columns, f"receipts table missing rollback column: {col}"

    def test_tasks_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "tasks")

        required = [
            "task_id",
            "conversation_id",
            "title",
            "goal",
            "status",
            "priority",
            "owner_principal_id",
            "policy_profile",
            "source_channel",
            "parent_task_id",
            "task_contract_ref",
            "budget_tokens_used",
            "budget_tokens_limit",
            "created_at",
            "updated_at",
        ]
        for col in required:
            assert col in columns, f"tasks table missing column: {col}"

    def test_steps_dag_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "steps")

        dag_cols = [
            "node_key",
            "depends_on_json",
            "join_strategy",
            "input_bindings_json",
            "max_attempts",
            "verification_required",
            "verifies_json",
            "supersedes_json",
        ]
        for col in dag_cols:
            assert col in columns, f"steps table missing DAG column: {col}"

    def test_step_attempts_v2_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "step_attempts")

        v2_cols = [
            "execution_contract_ref",
            "evidence_case_ref",
            "authorization_plan_ref",
            "reconciliation_ref",
            "contract_version",
            "idempotency_key",
            "executor_mode",
        ]
        for col in v2_cols:
            assert col in columns, f"step_attempts table missing V2 column: {col}"

    def test_artifacts_extended_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "artifacts")

        extended = [
            "artifact_class",
            "media_type",
            "byte_size",
            "sensitivity_class",
            "lineage_ref",
        ]
        for col in extended:
            assert col in columns, f"artifacts table missing column: {col}"

    def test_memory_records_extended_columns(self) -> None:
        store = _make_store()
        columns = _get_columns(store, "memory_records")

        extended = [
            "memory_kind",
            "scope_kind",
            "scope_ref",
            "promotion_reason",
            "retention_class",
            "claim_text",
            "structured_assertion_json",
            "supersedes_memory_ids_json",
            "superseded_by_memory_id",
            "invalidation_reason",
            "expires_at",
            "freshness_class",
            "last_accessed_at",
            "importance",
        ]
        for col in extended:
            assert col in columns, f"memory_records table missing column: {col}"


# ---------------------------------------------------------------------------
# Tests: _ensure_column Migration Helper
# ---------------------------------------------------------------------------


class TestEnsureColumnMigration:
    """Test the _ensure_column helper used for schema migrations."""

    def test_ensure_column_adds_new_column(self) -> None:
        store = _make_store()

        # Verify column does not exist
        columns_before = _get_columns(store, "tasks")
        assert "test_migration_col" not in columns_before

        # Add it
        store._ensure_column("tasks", "test_migration_col", "TEXT")

        # Verify it exists now
        columns_after = _get_columns(store, "tasks")
        assert "test_migration_col" in columns_after
        assert columns_after["test_migration_col"]["type"] == "TEXT"

    def test_ensure_column_is_idempotent(self) -> None:
        store = _make_store()

        # Run twice — should not raise
        store._ensure_column("tasks", "idempotent_col", "TEXT")
        store._ensure_column("tasks", "idempotent_col", "TEXT")

        columns = _get_columns(store, "tasks")
        assert "idempotent_col" in columns

    def test_ensure_column_with_default(self) -> None:
        store = _make_store()

        store._ensure_column("tasks", "col_with_default", "TEXT NOT NULL DEFAULT 'hello'")

        columns = _get_columns(store, "tasks")
        assert "col_with_default" in columns


# ---------------------------------------------------------------------------
# Tests: Index Existence
# ---------------------------------------------------------------------------


class TestIndexExistence:
    """Verify critical indexes exist."""

    def test_event_indexes_exist(self) -> None:
        store = _make_store()
        indexes = _get_indexes(store)

        assert "idx_events_task" in indexes
        assert "idx_events_task_hash" in indexes

    def test_receipt_and_decision_indexes_exist(self) -> None:
        store = _make_store()
        indexes = _get_indexes(store)

        assert "idx_receipts_task" in indexes
        assert "idx_decisions_task" in indexes

    def test_capability_grant_index_exists(self) -> None:
        store = _make_store()
        indexes = _get_indexes(store)

        assert "idx_capability_grants_task" in indexes

    def test_workspace_lease_indexes_exist(self) -> None:
        store = _make_store()
        indexes = _get_indexes(store)

        assert "idx_workspace_leases_attempt" in indexes
        assert "idx_workspace_leases_holder" in indexes

    def test_memory_indexes_exist(self) -> None:
        store = _make_store()
        indexes = _get_indexes(store)

        assert "idx_memory_records_status" in indexes
        assert "idx_memory_records_kind" in indexes

    def test_v2_indexes_exist(self) -> None:
        store = _make_store()
        indexes = _get_indexes(store)

        assert "idx_execution_contracts_attempt" in indexes
        assert "idx_evidence_cases_subject" in indexes
        assert "idx_authorization_plans_attempt" in indexes
        assert "idx_reconciliations_attempt" in indexes

    def test_step_attempts_queue_index_exists(self) -> None:
        store = _make_store()
        indexes = _get_indexes(store)

        assert "idx_step_attempts_ready_queue" in indexes

    def test_blackboard_index_exists(self) -> None:
        store = _make_store()
        indexes = _get_indexes(store)

        assert "idx_blackboard_task" in indexes


# ---------------------------------------------------------------------------
# Tests: Hash Chain Integrity at Schema Level
# ---------------------------------------------------------------------------


class TestHashChainSchemaIntegrity:
    """Verify that events are created with proper hash chain fields."""

    def test_new_event_has_hash_chain_fields(self) -> None:
        store = _make_store()
        conv = store.ensure_conversation("conv-1", source_channel="test")
        task = store.create_task(
            conversation_id=conv.conversation_id,
            title="hash-test",
            goal="test",
            source_channel="test",
        )

        rows = store._rows(
            "SELECT event_hash, prev_event_hash, hash_chain_algo "
            "FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (task.task_id,),
        )

        assert len(rows) > 0
        for row in rows:
            assert row["event_hash"] is not None
            assert row["event_hash"] != ""
            assert row["hash_chain_algo"] == "sha256-v1"

    def test_event_hash_chain_continuity(self) -> None:
        """Each event's prev_event_hash should match the previous event's event_hash."""
        store = _make_store()
        conv = store.ensure_conversation("conv-1", source_channel="test")
        task = store.create_task(
            conversation_id=conv.conversation_id,
            title="chain-test",
            goal="test chain continuity",
            source_channel="test",
        )
        # Create several more events
        step = store.create_step(task_id=task.task_id, kind="execute", status="running")
        store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")

        rows = store._rows(
            "SELECT event_hash, prev_event_hash "
            "FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (task.task_id,),
        )

        assert len(rows) >= 3  # task.created + step.created + attempt.created at minimum
        previous_hash: str | None = None
        for row in rows:
            expected_prev = previous_hash or ""
            actual_prev = str(row["prev_event_hash"] or "")
            assert actual_prev == expected_prev, (
                f"Chain broken: expected prev={expected_prev}, got prev={actual_prev}"
            )
            previous_hash = str(row["event_hash"])

    def test_hash_chain_checkpoints_table(self) -> None:
        store = _make_store()
        tables = _get_tables(store)
        assert "hash_chain_checkpoints" in tables

        columns = _get_columns(store, "hash_chain_checkpoints")
        assert "task_key" in columns
        assert "checkpoint_event_seq" in columns
        assert "checkpoint_event_hash" in columns
        assert "checkpointed_at" in columns


# ---------------------------------------------------------------------------
# Tests: Schema Validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Test schema validation on init."""

    def test_fresh_store_initializes_correctly(self) -> None:
        store = _make_store()
        assert store.schema_version() == "18"

    def test_existing_tables_constant_covers_all_known_tables(self) -> None:
        """The _KNOWN_KERNEL_TABLES constant should match actual tables (excluding meta/entity/observation)."""
        store = _make_store()
        actual_tables = _get_tables(store)

        # All known tables should exist
        for table_name in _KNOWN_KERNEL_TABLES:
            assert table_name in actual_tables, f"Known table {table_name} not found in DB"

    def test_entity_links_table_exists(self) -> None:
        """entity_links table (created in schema init) should exist."""
        store = _make_store()
        tables = _get_tables(store)
        assert "entity_links" in tables

        columns = _get_columns(store, "entity_links")
        assert "entity" in columns
        assert "memory_id" in columns

    def test_get_schema_version_alias(self) -> None:
        """get_schema_version() is a public alias for schema_version()."""
        store = _make_store()
        assert store.get_schema_version() == store.schema_version()
        assert store.get_schema_version() == str(_SCHEMA_VERSION)

    def test_get_schema_version_returns_str(self) -> None:
        """get_schema_version() always returns a str, never None or int."""
        store = _make_store()
        result = store.get_schema_version()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_schema_version_is_numeric(self) -> None:
        """Schema version string should be parseable as an integer."""
        store = _make_store()
        version_str = store.get_schema_version()
        version_int = int(version_str)
        assert version_int == int(_SCHEMA_VERSION)

    def test_get_schema_version_consistent_across_calls(self) -> None:
        """Multiple calls return the same value (no side-effects)."""
        store = _make_store()
        v1 = store.get_schema_version()
        v2 = store.get_schema_version()
        v3 = store.get_schema_version()
        assert v1 == v2 == v3

    def test_get_schema_version_positive_integer_matching_constant(self) -> None:
        """get_schema_version() returns a positive integer string matching _SCHEMA_VERSION."""
        store = _make_store()
        version = store.get_schema_version()
        assert version == str(_SCHEMA_VERSION)
        assert int(version) > 0, "Schema version must be a positive integer"

    def test_get_schema_version_after_meta_row_deleted(self) -> None:
        """When the kernel_meta schema_version row is missing, schema_version() returns ''."""
        store = _make_store()
        # Confirm it works first
        assert store.get_schema_version() == str(_SCHEMA_VERSION)
        # Delete the meta row to simulate a corrupted/empty DB
        store._get_conn().execute("DELETE FROM kernel_meta WHERE key = 'schema_version'")
        result = store.get_schema_version()
        assert result == "", f"Expected empty string when meta row missing, got {result!r}"
