"""Additional coverage tests for src/hermit/runtime/capability/contracts/hooks.py

Targets the ~8 missed statements not covered by the existing test_hooks_engine.py.
"""

from __future__ import annotations

from enum import Enum, StrEnum

from hermit.runtime.capability.contracts.hooks import HooksEngine, _event_key

# ---------------------------------------------------------------------------
# _event_key
# ---------------------------------------------------------------------------


class TestEventKey:
    def test_string_value_enum(self) -> None:
        class E(StrEnum):
            FOO = "foo_event"

        assert _event_key(E.FOO) == "foo_event"

    def test_non_string_value_uses_str(self) -> None:
        class E(Enum):
            BAR = 42

        assert _event_key(E.BAR) == "E.BAR"

    def test_plain_string(self) -> None:
        assert _event_key("dispatch") == "dispatch"

    def test_object_without_value(self) -> None:
        class X:
            pass

        x = X()
        result = _event_key(x)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# HooksEngine._safe_call (instance method)
# ---------------------------------------------------------------------------


class TestModuleSafeCall:
    def test_accepts_matching_kwargs(self) -> None:
        engine = HooksEngine()

        def handler(a: int, b: str) -> str:
            return f"{a}-{b}"

        result = engine._safe_call(handler, {"a": 1, "b": "x", "extra": True})
        assert result == "1-x"

    def test_var_keyword_receives_all(self) -> None:
        engine = HooksEngine()

        def handler(**kwargs):
            return kwargs

        result = engine._safe_call(handler, {"a": 1, "b": 2})
        assert result == {"a": 1, "b": 2}

    def test_signature_error_falls_through(self, monkeypatch) -> None:
        """When inspect.signature raises TypeError, handler is called with all kwargs."""
        import inspect

        original = inspect.signature

        call_count = 0

        def bad_signature(fn):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise TypeError("bad")
            return original(fn)

        monkeypatch.setattr(
            "hermit.runtime.capability.contracts.hooks.inspect.signature",
            bad_signature,
        )
        engine = HooksEngine()
        result = engine._safe_call(lambda **kw: kw["x"], {"x": 42})
        assert result == 42


# ---------------------------------------------------------------------------
# HooksEngine._safe_call (instance method with caching)
# ---------------------------------------------------------------------------


class TestHooksEngineSafeCallCaching:
    def test_caches_signature(self) -> None:
        engine = HooksEngine()

        def handler(a: int) -> int:
            return a * 2

        # Call twice - second call should use cached signature
        r1 = engine._safe_call(handler, {"a": 5, "extra": True})
        r2 = engine._safe_call(handler, {"a": 10, "extra": True})
        assert r1 == 10
        assert r2 == 20
        assert id(handler) in engine._sig_cache

    def test_caches_none_for_bad_signature(self) -> None:
        engine = HooksEngine()

        class WeirdCallable:
            def __call__(self, **kwargs):
                return kwargs.get("x", 0)

        wc = WeirdCallable()
        # Force signature to fail by patching
        import inspect

        orig = inspect.signature

        def fail_for_wc(fn):
            if fn is wc:
                raise ValueError("nope")
            return orig(fn)

        engine._sig_cache.clear()
        # Manually test the None path
        engine._sig_cache[id(wc)] = None
        result = engine._safe_call(wc, {"x": 99})
        assert result == 99


# ---------------------------------------------------------------------------
# HooksEngine.fire_first
# ---------------------------------------------------------------------------


class TestHooksEngineFireFirst:
    def test_returns_first_non_none(self) -> None:
        engine = HooksEngine()
        engine.register("evt", lambda: None, priority=0)
        engine.register("evt", lambda: "found", priority=1)
        engine.register("evt", lambda: "also", priority=2)
        assert engine.fire_first("evt") == "found"

    def test_all_none_returns_none(self) -> None:
        engine = HooksEngine()
        engine.register("evt", lambda: None, priority=0)
        engine.register("evt", lambda: None, priority=1)
        assert engine.fire_first("evt") is None


# ---------------------------------------------------------------------------
# HooksEngine priority ordering
# ---------------------------------------------------------------------------


class TestHooksEnginePriority:
    def test_handlers_fire_in_priority_order(self) -> None:
        engine = HooksEngine()
        order: list[int] = []
        engine.register("evt", lambda: order.append(2), priority=20)
        engine.register("evt", lambda: order.append(1), priority=10)
        engine.register("evt", lambda: order.append(3), priority=30)
        engine.fire("evt")
        assert order == [1, 2, 3]
