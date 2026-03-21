from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from hermit.kernel.context.memory.knowledge import MemoryRecordService
from hermit.kernel.ledger.journal.store import KernelSchemaError, KernelStore


def test_memory_record_roundtrips_new_schema_fields(kernel_store: KernelStore) -> None:
    record = kernel_store.create_memory_record(
        task_id="task-1",
        conversation_id="chat-1",
        category="project_convention",
        claim_text="默认在 /repo 执行命令",
        structured_assertion={"workspace_root": "/repo"},
        scope_kind="workspace",
        scope_ref="/repo",
        promotion_reason="belief_promotion",
        retention_class="project_convention",
        evidence_refs=["artifact-1"],
        supersedes=["旧约定"],
        supersedes_memory_ids=["memory-old"],
        source_belief_ref="belief-1",
        expires_at=time.time() + 60,
    )

    loaded = kernel_store.get_memory_record(record.memory_id)

    assert loaded is not None
    assert loaded.claim_text == "默认在 /repo 执行命令"
    assert loaded.structured_assertion == {"workspace_root": "/repo"}
    assert loaded.scope_kind == "workspace"
    assert loaded.scope_ref == "/repo"
    assert loaded.promotion_reason == "belief_promotion"
    assert loaded.retention_class == "project_convention"
    assert loaded.supersedes_memory_ids == ["memory-old"]


def test_active_memory_query_excludes_expired_invalidated_and_revoked(
    kernel_store: KernelStore,
) -> None:
    active = kernel_store.create_memory_record(
        task_id="task-1",
        conversation_id="chat-1",
        category="user_preference",
        content="统一使用简体中文",
    )
    kernel_store.create_memory_record(
        task_id="task-2",
        conversation_id="chat-1",
        category="tech_decision",
        content="这是过期事实",
        expires_at=time.time() - 1,
    )
    kernel_store.create_memory_record(
        task_id="task-3",
        conversation_id="chat-1",
        category="tech_decision",
        content="这是失效事实",
        status="invalidated",
        invalidated_at=time.time(),
    )
    kernel_store.create_memory_record(
        task_id="task-4",
        conversation_id="chat-1",
        category="other",
        content="这是撤销事实",
        status="revoked",
    )

    active_records = kernel_store.list_memory_records(status="active", limit=50)

    assert [record.memory_id for record in active_records] == [active.memory_id]


def test_schema_v3_database_requires_hard_cut_archive_or_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE kernel_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO kernel_meta(key, value) VALUES ('schema_version', '3');
            CREATE TABLE beliefs (
                belief_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                conversation_id TEXT,
                scope_kind TEXT NOT NULL,
                scope_ref TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL NOT NULL,
                trust_tier TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                supersedes_json TEXT NOT NULL,
                contradicts_json TEXT NOT NULL,
                memory_ref TEXT,
                invalidated_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE memory_records (
                memory_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                conversation_id TEXT,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL NOT NULL,
                trust_tier TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                supersedes_json TEXT NOT NULL,
                source_belief_ref TEXT,
                invalidated_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO memory_records (
                memory_id, task_id, conversation_id, category, content, status, confidence,
                trust_tier, evidence_refs_json, supersedes_json, source_belief_ref, invalidated_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "memory-legacy",
                "task-1",
                "chat-1",
                "active_task",
                "旧 content 字段",
                "superseded",
                0.7,
                "durable",
                "[]",
                "[]",
                None,
                None,
                time.time(),
                time.time(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        KernelStore(db_path)
    except KernelSchemaError as exc:
        message = str(exc)
        assert "schema_version=3" in message
        assert "requires schema_version=" in message
        assert "Archive or delete" in message
    else:
        raise AssertionError("KernelStore should hard-fail for schema_version=3")


def test_memory_mirror_exports_scope_and_retention_metadata(
    kernel_store: KernelStore, tmp_path: Path
) -> None:
    mirror = tmp_path / "memories.md"
    kernel_store.create_memory_record(
        task_id="task-1",
        conversation_id="chat-1",
        category="project_convention",
        claim_text="默认在 /repo 执行命令",
        scope_kind="workspace",
        scope_ref="/repo",
        retention_class="project_convention",
        supersedes=["旧约定"],
    )

    exported = MemoryRecordService(kernel_store, mirror_path=mirror).export_mirror()

    raw = mirror.read_text(encoding="utf-8")
    assert exported == mirror
    assert '"scope_kind":"workspace"' in raw
    assert '"scope_ref":"/repo"' in raw
    assert '"retention_class":"project_convention"' in raw
    assert '"supersedes":["旧约定"]' in raw
