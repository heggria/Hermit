"""TestGenerator — deterministic test skeleton generation from source files."""

from __future__ import annotations

import ast
from pathlib import Path

from hermit.plugins.builtin.hooks.quality.models import TestPlan

_TEST_TEMPLATE = '''\
"""Tests for {module_name}."""

from __future__ import annotations

import pytest
from {import_path} import ({imports})

{test_functions}
'''

_TEST_FUNC_TEMPLATE = """\
class Test{class_name}:
    def test_{func_name}_basic(self) -> None:
        # TODO: implement test
        pass
"""

_TEST_CLASS_TEMPLATE = """\
class Test{class_name}:
    def test_init(self) -> None:
        # TODO: implement test
        pass
{methods}"""

_TEST_METHOD_TEMPLATE = """\
    def test_{method_name}(self) -> None:
        # TODO: implement test
        pass
"""


def _extract_public_api(source: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Extract public functions and classes with their public methods.

    Returns (function_names, [(class_name, [method_names])]).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []

    functions: list[str] = []
    classes: list[tuple[str, list[str]]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = [
                n.name
                for n in ast.iter_child_nodes(node)
                if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
            ]
            classes.append((node.name, methods))

    return functions, classes


def _source_to_import_path(source_file: str) -> str:
    """Convert a source file path to a Python import path."""
    path = Path(source_file)
    parts = list(path.with_suffix("").parts)
    # Strip leading 'src/' if present
    if parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


def _source_to_test_path(source_file: str) -> str:
    """Convert a source file path to a test file path."""
    path = Path(source_file)
    name = path.stem
    test_name = f"test_{name}.py"
    # Place tests in tests/unit/ mirroring the source structure
    parts = list(path.parent.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    return str(Path("tests", "unit", *parts, test_name))


class TestGenerator:
    """Generates deterministic test skeletons for Python source files.

    Parses the public API (functions and classes) and produces a
    pytest-compatible test file with TODO placeholders.
    """

    def generate(self, source_file: str) -> TestPlan:
        """Generate a TestPlan for the given source file."""
        try:
            source = Path(source_file).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return TestPlan(
                test_file=_source_to_test_path(source_file),
                source_file=source_file,
            )

        functions, classes = _extract_public_api(source)
        all_names = list(functions) + [cls_name for cls_name, _ in classes]

        import_path = _source_to_import_path(source_file)
        test_file = _source_to_test_path(source_file)

        # Build test function bodies
        test_bodies: list[str] = []
        for func_name in functions:
            test_bodies.append(
                _TEST_FUNC_TEMPLATE.format(
                    class_name=func_name.title().replace("_", ""),
                    func_name=func_name,
                )
            )
        for cls_name, methods in classes:
            method_tests = "\n".join(
                _TEST_METHOD_TEMPLATE.format(
                    class_name=cls_name,
                    method_name=m,
                )
                for m in methods
            )
            test_bodies.append(
                _TEST_CLASS_TEMPLATE.format(
                    class_name=cls_name,
                    methods=method_tests,
                )
            )

        imports_str = (
            ",\n".join(f"    {n}" for n in all_names) if all_names else "    # nothing to import"
        )
        skeleton = _TEST_TEMPLATE.format(
            module_name=Path(source_file).stem,
            import_path=import_path,
            imports=imports_str,
            test_functions="\n\n".join(test_bodies) if test_bodies else "# No public API found",
        )

        return TestPlan(
            test_file=test_file,
            source_file=source_file,
            functions=tuple(all_names),
            skeleton=skeleton,
        )
