from __future__ import annotations

from hermit.runtime.capability.contracts.hooks import HooksEngine, _safe_call


def test_hooks_engine_safe_call_and_fire_first(monkeypatch) -> None:
    engine = HooksEngine()
    calls: list[str] = []

    def first_handler(source: str) -> None:
        calls.append(f"first:{source}")

    def second_handler(**kwargs):
        calls.append(f"second:{kwargs['source']}")
        return "handled"

    engine.register("dispatch", second_handler, priority=10)
    engine.register("dispatch", first_handler, priority=0)

    assert engine.has_handlers("dispatch") is True
    assert engine.has_handlers("missing") is False
    assert engine.fire("dispatch", source="webhook/test") == [None, "handled"]
    assert engine.fire_first("dispatch", source="webhook/test") == "handled"
    assert engine.fire_first("missing", source="webhook/test") is None
    assert calls == [
        "first:webhook/test",
        "second:webhook/test",
        "first:webhook/test",
        "second:webhook/test",
    ]

    monkeypatch.setattr(
        "hermit.runtime.capability.contracts.hooks.inspect.signature",
        lambda handler: (_ for _ in ()).throw(ValueError()),
    )
    assert _safe_call(lambda **kwargs: kwargs["value"], {"value": 3}) == 3
