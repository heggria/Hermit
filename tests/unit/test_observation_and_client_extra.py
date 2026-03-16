from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import hermit.plugins.builtin.adapters.feishu._client as feishu_client
import hermit.runtime.assembly.config as config_mod
from hermit.kernel.execution.coordination.observation import (
    ObservationPollResult,
    ObservationProgress,
    ObservationService,
    ObservationTicket,
    normalize_observation_progress,
    normalize_observation_ticket,
    observation_envelope,
)


def test_build_lark_client_uses_settings_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeBuilder:
        def __init__(self) -> None:
            self._app_id = ""
            self._app_secret = ""

        def app_id(self, value: str) -> "FakeBuilder":
            self._app_id = value
            return self

        def app_secret(self, value: str) -> "FakeBuilder":
            self._app_secret = value
            return self

        def build(self) -> dict[str, str]:
            calls.append((self._app_id, self._app_secret))
            return {"app_id": self._app_id, "app_secret": self._app_secret}

    fake_lark = SimpleNamespace(Client=SimpleNamespace(builder=lambda: FakeBuilder()))
    monkeypatch.setitem(__import__("sys").modules, "lark_oapi", fake_lark)

    settings_client = feishu_client.build_lark_client(
        SimpleNamespace(feishu_app_id="app-1", feishu_app_secret="secret-1")
    )

    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "env-app")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "env-secret")
    monkeypatch.setattr(
        config_mod, "get_settings", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    env_client = feishu_client.build_lark_client(None)

    assert settings_client == {"app_id": "app-1", "app_secret": "secret-1"}
    assert env_client == {"app_id": "env-app", "app_secret": "env-secret"}
    assert calls == [("app-1", "secret-1"), ("env-app", "env-secret")]

    monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="Feishu credentials not configured"):
        feishu_client.build_lark_client(None)


def test_observation_progress_and_ticket_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = ObservationProgress.from_dict(
        {
            "phase": "running",
            "summary": "Working",
            "detail": "step",
            "progress_percent": "bad",
            "ready": 1,
        }
    )
    ticket = ObservationTicket(
        observer_kind="sandbox",
        job_id="job-1",
        status_ref="status-1",
        poll_after_seconds=5,
        cancel_supported=True,
        resume_token="resume-1",
        topic_summary="Observe this task",
        tool_name="bash",
        tool_input={"command": "sleep 1"},
    )

    assert progress.progress_percent is None
    assert progress.signature() == ("running", "Working", "step", None, True)
    assert normalize_observation_progress(progress) is progress
    assert normalize_observation_progress({"phase": "", "summary": ""}) is None

    payload = ticket.schedule_next_poll(now=10).to_dict()
    normalized = normalize_observation_ticket(observation_envelope(payload))
    assert normalized is not None
    assert normalized.job_id == "job-1"
    assert normalized.next_poll_at is not None
    assert normalize_observation_ticket({"job_id": "missing-fields"}) is None


def test_observation_service_ticks_and_resumes_completed_attempts() -> None:
    resumed: list[str] = []
    wake_calls: list[str] = []
    poll_calls: list[str] = []
    attempt = SimpleNamespace(step_attempt_id="attempt-1")
    ticket = ObservationTicket(
        observer_kind="sandbox",
        job_id="job-1",
        status_ref="status-1",
        poll_after_seconds=1,
        cancel_supported=False,
        resume_token="resume-1",
        topic_summary="Observe this task",
    )

    class ToolExecutor:
        def poll_observation(
            self, step_attempt_id: str, now: float | None = None
        ) -> ObservationPollResult | None:
            poll_calls.append(step_attempt_id)
            return ObservationPollResult(ticket=ticket, should_resume=True)

    runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(list_step_attempts=lambda status, limit: [attempt]),
            enqueue_resume=lambda step_attempt_id: resumed.append(step_attempt_id),
        ),
        agent=SimpleNamespace(tool_executor=ToolExecutor()),
        wake_dispatcher=lambda: wake_calls.append("wake"),
    )
    service = ObservationService(runner, budget=SimpleNamespace(observation_poll_interval=0.01))

    service._tick()

    assert poll_calls == ["attempt-1"]
    assert resumed == ["attempt-1"]
    assert wake_calls == ["wake"]

    service._resuming.add("attempt-1")
    service._tick()
    assert poll_calls == ["attempt-1"]


def test_observation_service_start_and_stop_are_idempotent() -> None:
    runner = SimpleNamespace(task_controller=None, agent=None)
    service = ObservationService(runner, budget=SimpleNamespace(observation_poll_interval=0.01))

    service.start()
    first_thread = service._thread
    assert isinstance(first_thread, threading.Thread)
    service.start()
    assert service._thread is first_thread

    service.stop()
    assert not first_thread.is_alive()


def test_observation_service_loop_and_tick_cover_skip_paths(monkeypatch) -> None:
    wait_states = iter([False, True])
    runner = SimpleNamespace(task_controller=None, agent=None)
    service = ObservationService(runner, budget=SimpleNamespace(observation_poll_interval=0.01))

    tick_calls: list[str] = []
    monkeypatch.setattr(
        service,
        "_tick",
        lambda: tick_calls.append("tick") or (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(service._stop, "wait", lambda interval: next(wait_states))

    service._loop()
    assert tick_calls == ["tick"]

    poll_calls: list[str] = []
    attempts = [
        SimpleNamespace(step_attempt_id="attempt-1"),
        SimpleNamespace(step_attempt_id="attempt-2"),
    ]

    class ToolExecutor:
        def poll_observation(
            self, step_attempt_id: str, now: float | None = None
        ) -> ObservationPollResult | None:
            poll_calls.append(step_attempt_id)
            if step_attempt_id == "attempt-1":
                return ObservationPollResult(ticket=None, should_resume=False)
            return None

    skip_runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(list_step_attempts=lambda status, limit: attempts),
            enqueue_resume=lambda step_attempt_id: (_ for _ in ()).throw(
                AssertionError("should not resume")
            ),
        ),
        agent=SimpleNamespace(tool_executor=ToolExecutor()),
        wake_dispatcher=lambda: (_ for _ in ()).throw(AssertionError("should not wake")),
    )
    skip_service = ObservationService(
        skip_runner, budget=SimpleNamespace(observation_poll_interval=0.01)
    )
    skip_service._resuming.add("attempt-2")

    skip_service._tick()
    assert poll_calls == ["attempt-1"]
