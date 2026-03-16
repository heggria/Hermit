# ruff: noqa: F401
"""Tests for the Feishu adapter plugin normalize + AgentRunner integration."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyEngine
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.plugins.builtin.adapters.feishu.normalize import FeishuMessage, normalize_event
from hermit.plugins.builtin.adapters.feishu.reply import (
    build_approval_card,
    build_task_topic_card,
    make_tool_step,
)
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec
from hermit.runtime.control.lifecycle.session import SessionManager
from hermit.runtime.control.runner.runner import AgentRunner
from hermit.runtime.provider_host.execution.runtime import AgentRuntime
from hermit.runtime.provider_host.llm.claude import ClaudeProvider


@pytest.fixture(autouse=True)
def _force_feishu_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")


@dataclass
class FakeResponse:
    content: list
    stop_reason: str = "end_turn"


class FakeMessagesAPI:
    def __init__(self, answer: str = "ok") -> None:
        self.answer = answer
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(copy.deepcopy(kwargs))
        return FakeResponse(content=[{"type": "text", "text": self.answer}])


class FakeClient:
    def __init__(self, answer: str = "ok") -> None:
        self.messages = FakeMessagesAPI(answer)


def _make_event(chat_id: str, text: str, chat_type: str = "p2p") -> dict:
    return {
        "message": {
            "chat_id": chat_id,
            "message_id": f"om_{chat_id}",
            "content": json.dumps({"text": text}),
            "message_type": "text",
            "chat_type": chat_type,
        },
        "sender": {"sender_id": {"open_id": "user-1"}},
    }


def _make_runner(tmp_path, answer: str = "reply") -> tuple[AgentRunner, FakeClient]:
    client = FakeClient(answer=answer)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    agent = AgentRuntime(
        provider=ClaudeProvider(client, model="fake"),
        registry=ToolRegistry(),
        model="fake",
    )
    manager = SessionManager(tmp_path / "sessions", store=store)
    pm = PluginManager()
    runner = AgentRunner(agent, manager, pm, task_controller=TaskController(store))
    return runner, client


__all__ = [name for name in globals() if not name.startswith("__")]
