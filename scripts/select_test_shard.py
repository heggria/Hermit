#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_TEST_DEF_PREFIX = "test_"
_TEST_CLASS_PREFIX = "Test"


@dataclass(order=True)
class WeightedTestFile:
    sort_index: tuple[int, str] = field(init=False, repr=False)
    weight: int
    path: Path
    test_count: int
    line_count: int
    sleep_count: int

    def __post_init__(self) -> None:
        self.sort_index = (-self.weight, self.path.as_posix())


def _count_tests(tree: ast.AST) -> int:
    count = 0
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            _TEST_DEF_PREFIX
        ):
            count += 1
            continue
        if isinstance(node, ast.ClassDef) and node.name.startswith(_TEST_CLASS_PREFIX):
            for child in node.body:
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and child.name.startswith(_TEST_DEF_PREFIX):
                    count += 1
    return count


def _estimate_weight(path: Path) -> WeightedTestFile:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=path.as_posix())
    test_count = _count_tests(tree)
    line_count = len(text.splitlines())
    sleep_count = len(re.findall(r"(?:time|asyncio)\.sleep\(", text))
    subprocess_count = len(re.findall(r"subprocess\.(?:run|Popen)\(", text))
    wait_count = len(re.findall(r"\.wait\(", text))
    weight = (
        (test_count * 100)
        + line_count
        + (sleep_count * 25)
        + (subprocess_count * 8)
        + (wait_count * 5)
    )
    return WeightedTestFile(
        weight=weight,
        path=path,
        test_count=test_count,
        line_count=line_count,
        sleep_count=sleep_count,
    )


def _build_shards(files: list[WeightedTestFile], shard_total: int) -> list[list[WeightedTestFile]]:
    shards: list[list[WeightedTestFile]] = [[] for _ in range(shard_total)]
    shard_weights = [0 for _ in range(shard_total)]
    for test_file in sorted(files):
        target = min(range(shard_total), key=lambda index: (shard_weights[index], index))
        shards[target].append(test_file)
        shard_weights[target] += test_file.weight
    for shard in shards:
        shard.sort(key=lambda item: item.path.as_posix())
    return shards


def main() -> int:
    parser = argparse.ArgumentParser(description="Select a balanced pytest file shard.")
    parser.add_argument("--shard", type=int, required=True, help="1-based shard index")
    parser.add_argument("--shard-total", type=int, required=True, help="Total shard count")
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=Path("tests"),
        help="Directory containing pytest files",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a human-readable shard summary before the file list",
    )
    args = parser.parse_args()

    if args.shard < 1 or args.shard > args.shard_total:
        raise SystemExit(f"invalid shard {args.shard}/{args.shard_total}")

    files = [
        _estimate_weight(path) for path in sorted(args.tests_dir.glob("test*.py")) if path.is_file()
    ]
    if not files:
        raise SystemExit(f"no pytest files found under {args.tests_dir}")

    shards = _build_shards(files, args.shard_total)
    selected = shards[args.shard - 1]
    if not selected:
        raise SystemExit(f"no tests selected for shard {args.shard}/{args.shard_total}")

    if args.summary:
        for index, shard in enumerate(shards, start=1):
            shard_weight = sum(item.weight for item in shard)
            shard_tests = sum(item.test_count for item in shard)
            print(
                f"# shard {index}/{args.shard_total}: "
                f"{len(shard)} files, ~{shard_tests} tests, weight={shard_weight}"
            )
        print("# selected files")

    for item in selected:
        print(item.path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
