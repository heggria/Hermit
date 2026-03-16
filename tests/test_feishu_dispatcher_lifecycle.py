# ruff: noqa: F403,F405
from tests.feishu_dispatcher_support import *


def test_feishu_adapter_stop_shuts_down_background_resources(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    shutdown_called: list[bool] = []
    join_called: list[float] = []
    flush_called: list[bool] = []

    class FakeTimer:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class FakeExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[bool, bool]] = []

        def shutdown(self, wait: bool, cancel_futures: bool) -> None:
            self.calls.append((wait, cancel_futures))

    async def fake_shutdown_ws() -> None:
        shutdown_called.append(True)

    def fake_join_ws_thread(timeout_seconds: float = 2.0) -> None:
        join_called.append(timeout_seconds)

    def fake_flush_all_sessions() -> None:
        flush_called.append(True)

    timer = FakeTimer()
    executor = FakeExecutor()
    adapter._sweep_timer = timer
    adapter._executor = executor  # type: ignore[assignment]
    adapter._shutdown_ws = fake_shutdown_ws  # type: ignore[method-assign]
    adapter._join_ws_thread = fake_join_ws_thread  # type: ignore[method-assign]
    adapter._flush_all_sessions = fake_flush_all_sessions  # type: ignore[method-assign]

    asyncio.run(adapter.stop())

    assert adapter._stopped is True
    assert timer.cancelled is True
    assert adapter._sweep_timer is None
    assert shutdown_called == [True]
    assert executor.calls == [(False, True)]
    assert join_called == [2.0]
    assert flush_called == [True]


def test_feishu_adapter_start_raises_when_ws_thread_crashes() -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    adapter._app_id = "app-id"
    adapter._app_secret = "app-secret"

    def fake_run_ws_client() -> None:
        adapter._ws_error = ValueError("boom")
        adapter._ws_exited.set()

    adapter._run_ws_client = fake_run_ws_client  # type: ignore[method-assign]

    try:
        asyncio.run(adapter.start(runner=object()))  # type: ignore[arg-type]
    except RuntimeError as exc:
        assert str(exc) == "Feishu adapter stopped unexpectedly"
        assert isinstance(exc.__cause__, ValueError)
    else:
        raise AssertionError("adapter.start() should propagate WebSocket thread failures")


def test_feishu_adapter_receive_loop_treats_normal_close_as_graceful(monkeypatch) -> None:
    import hermit.builtin.feishu.adapter as adapter_module
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    adapter._stopped = True
    error_logs: list[str] = []
    disconnect_calls: list[bool] = []
    reconnect_calls: list[bool] = []
    loop_tasks: list[str] = []
    info_logs: list[str] = []

    class FakeConnectionClosed(Exception):
        pass

    class FakeConn:
        async def recv(self) -> str:
            raise FakeConnectionClosed("sent 1000 (OK); then received 1000 (OK) bye")

    class FakeClient:
        def __init__(self) -> None:
            self._conn = FakeConn()
            self._auto_reconnect = False

        async def _disconnect(self) -> None:
            disconnect_calls.append(True)

        async def _reconnect(self) -> None:
            reconnect_calls.append(True)

        async def _handle_message(self, msg: str) -> None:
            loop_tasks.append(msg)

        def _fmt_log(self, template: str, exc: BaseException) -> str:
            return template.format(exc)

    class FakeLoop:
        def create_task(self, task: Any) -> Any:
            loop_tasks.append("created")
            return task

    class FakeLogger:
        def error(self, message: str) -> None:
            error_logs.append(message)

    class FakeClientClass:
        pass

    fake_module = SimpleNamespace(
        Client=FakeClientClass,
        loop=FakeLoop(),
        logger=FakeLogger(),
        ConnectionClosedException=FakeConnectionClosed,
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.log.info", lambda message: info_logs.append(message)
    )

    monkeypatch.setattr(adapter_module, "_LARK_RECEIVE_LOOP_PATCHED", False)
    adapter_module._patch_lark_receive_loop(fake_module)

    client = FakeClient()
    client._hermit_adapter_ref = adapter

    asyncio.run(fake_module.Client._receive_message_loop(client))

    assert disconnect_calls == [True]
    assert reconnect_calls == []
    assert error_logs == []
    assert any("closed cleanly" in message for message in info_logs)


def test_feishu_adapter_cancel_ws_receive_task_cancels_pending_task() -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    cancelled: list[bool] = []

    async def fake_receive_loop() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def run() -> None:
        task = asyncio.create_task(fake_receive_loop())
        adapter._ws_client = SimpleNamespace(_hermit_receive_task=task)
        await asyncio.sleep(0)
        await adapter._cancel_ws_receive_task()
        assert task.cancelled()

    asyncio.run(run())

    assert cancelled == [True]


def test_feishu_adapter_connect_patch_tracks_receive_task_and_consumes_graceful_close(
    monkeypatch,
) -> None:
    import hermit.builtin.feishu.adapter as adapter_module

    info_logs: list[str] = []
    unhandled_contexts: list[dict[str, Any]] = []

    class FakeConnectionClosed(Exception):
        pass

    class FakeInvalidStatusCode(Exception):
        pass

    class FakeLogger:
        def info(self, _message: str) -> None:
            return None

    async def fake_connect(_url: str) -> object:
        return object()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled_contexts.append(context))

        try:
            fake_module = SimpleNamespace(
                Client=type("FakeClientClass", (), {}),
                loop=loop,
                logger=FakeLogger(),
                websockets=SimpleNamespace(
                    connect=fake_connect,
                    InvalidStatusCode=FakeInvalidStatusCode,
                ),
                _parse_ws_conn_exception=lambda exc: None,
                urlparse=lambda url: SimpleNamespace(query="device_id=dev-1&service_id=svc-1"),
                parse_qs=lambda query: {"device_id": ["dev-1"], "service_id": ["svc-1"]},
                DEVICE_ID="device_id",
                SERVICE_ID="service_id",
            )

            monkeypatch.setattr(adapter_module, "_LARK_CONNECT_PATCHED", False)
            monkeypatch.setattr(
                "hermit.builtin.feishu.adapter.log.info", lambda message: info_logs.append(message)
            )

            adapter_module._patch_lark_connect(fake_module)

            class FakeClient:
                def __init__(self) -> None:
                    self._lock = asyncio.Lock()
                    self._conn = None
                    self._conn_url = ""
                    self._conn_id = ""
                    self._service_id = ""
                    self._hermit_adapter_ref = SimpleNamespace(_stopped=True)

                def _get_conn_url(self) -> str:
                    return "wss://example.test/ws?device_id=dev-1&service_id=svc-1"

                def _fmt_log(self, template: str, value: object) -> str:
                    return template.format(value)

                async def _receive_message_loop(self) -> None:
                    raise FakeConnectionClosed("sent 1000 (OK); then received 1000 (OK) bye")

            client = FakeClient()
            client._connect = fake_module.Client._connect.__get__(client, type(client))

            await client._connect()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert client._hermit_receive_task.done()
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())

    assert unhandled_contexts == []
    assert any("receive task exception during shutdown" in message for message in info_logs)
