"""Tests for TestGenerator — deterministic test skeleton generation."""

from __future__ import annotations

import textwrap
from pathlib import Path

from hermit.plugins.builtin.hooks.quality.models import TestPlan
from hermit.plugins.builtin.hooks.quality.test_generator import (
    TestGenerator,
    _extract_public_api,
    _source_to_import_path,
    _source_to_test_path,
)


class TestExtractPublicApi:
    def test_extracts_functions(self) -> None:
        source = "def foo():\n    pass\ndef bar():\n    pass\n"
        functions, classes = _extract_public_api(source)
        assert functions == ["foo", "bar"]
        assert classes == []

    def test_skips_private_functions(self) -> None:
        source = "def _private():\n    pass\ndef public():\n    pass\n"
        functions, _ = _extract_public_api(source)
        assert functions == ["public"]

    def test_extracts_classes_with_methods(self) -> None:
        source = textwrap.dedent("""\
            class MyClass:
                def method_a(self):
                    pass
                def _private(self):
                    pass
                def method_b(self):
                    pass
        """)
        _, classes = _extract_public_api(source)
        assert len(classes) == 1
        assert classes[0][0] == "MyClass"
        assert classes[0][1] == ["method_a", "method_b"]

    def test_skips_private_classes(self) -> None:
        source = "class _Internal:\n    pass\n"
        _, classes = _extract_public_api(source)
        assert classes == []

    def test_syntax_error_returns_empty(self) -> None:
        functions, classes = _extract_public_api("def bad(\n")
        assert functions == []
        assert classes == []


class TestSourceToImportPath:
    def test_strips_src_prefix(self) -> None:
        assert _source_to_import_path("src/hermit/foo.py") == "hermit.foo"

    def test_no_src_prefix(self) -> None:
        assert _source_to_import_path("hermit/foo.py") == "hermit.foo"

    def test_nested_path(self) -> None:
        result = _source_to_import_path("src/hermit/plugins/bar.py")
        assert result == "hermit.plugins.bar"


class TestSourceToTestPath:
    def test_basic_conversion(self) -> None:
        result = _source_to_test_path("src/hermit/foo.py")
        assert result == "tests/unit/hermit/test_foo.py"

    def test_nested_conversion(self) -> None:
        result = _source_to_test_path("src/hermit/plugins/bar.py")
        assert result == "tests/unit/hermit/plugins/test_bar.py"


class TestTestGenerator:
    def test_generate_with_functions(self, tmp_path: Path) -> None:
        source = tmp_path / "example.py"
        source.write_text(
            textwrap.dedent("""\
            def greet(name):
                return f"Hello, {name}"

            def farewell():
                return "Goodbye"
        """)
        )
        gen = TestGenerator()
        plan = gen.generate(str(source))
        assert isinstance(plan, TestPlan)
        assert plan.source_file == str(source)
        assert "greet" in plan.functions
        assert "farewell" in plan.functions
        assert "test_greet_basic" in plan.skeleton
        assert "test_farewell_basic" in plan.skeleton

    def test_generate_with_class(self, tmp_path: Path) -> None:
        source = tmp_path / "my_class.py"
        source.write_text(
            textwrap.dedent("""\
            class Calculator:
                def add(self, a, b):
                    return a + b
                def subtract(self, a, b):
                    return a - b
        """)
        )
        gen = TestGenerator()
        plan = gen.generate(str(source))
        assert "Calculator" in plan.functions
        assert "TestCalculator" in plan.skeleton
        assert "test_add" in plan.skeleton
        assert "test_subtract" in plan.skeleton

    def test_generate_no_public_api(self, tmp_path: Path) -> None:
        source = tmp_path / "empty.py"
        source.write_text("_PRIVATE = 42\n")
        gen = TestGenerator()
        plan = gen.generate(str(source))
        assert plan.functions == ()
        assert "No public API found" in plan.skeleton

    def test_generate_nonexistent_file(self) -> None:
        gen = TestGenerator()
        plan = gen.generate("/nonexistent/path.py")
        assert plan.functions == ()
        assert plan.skeleton == ""

    def test_frozen_test_plan(self, tmp_path: Path) -> None:
        source = tmp_path / "x.py"
        source.write_text("def f(): pass\n")
        gen = TestGenerator()
        plan = gen.generate(str(source))
        try:
            plan.skeleton = "mutated"  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass

    def test_skeleton_has_imports(self, tmp_path: Path) -> None:
        source = tmp_path / "mod.py"
        source.write_text("def helper(): pass\n")
        gen = TestGenerator()
        plan = gen.generate(str(source))
        assert "import" in plan.skeleton
        assert "pytest" in plan.skeleton
