#!/usr/bin/env python3
"""Parse JUnit XML test results and report flaky tests (those that passed after reruns)."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_junit_xml(path: Path) -> list[dict[str, str]]:
    """Find test cases that have rerun entries (flaky tests)."""
    tree = ET.parse(path)
    root = tree.getroot()

    flaky: list[dict[str, str]] = []

    for testcase in root.iter("testcase"):
        reruns = testcase.findall("rerun")
        if reruns:
            name = testcase.get("name", "unknown")
            classname = testcase.get("classname", "unknown")
            rerun_count = len(reruns)
            messages = [r.get("message", "") for r in reruns if r.get("message")]
            flaky.append(
                {
                    "name": name,
                    "classname": classname,
                    "reruns": str(rerun_count),
                    "messages": "; ".join(messages),
                }
            )

    return flaky


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse JUnit XML for flaky tests.")
    parser.add_argument("xml_path", help="Path to JUnit XML file")
    parser.add_argument(
        "--output",
        default="flaky-report.json",
        help="Path to write flaky report JSON",
    )
    args = parser.parse_args()

    xml_path = Path(args.xml_path)
    if not xml_path.exists():
        print(f"JUnit XML not found: {xml_path}", file=sys.stderr)
        return 1

    flaky_tests = parse_junit_xml(xml_path)

    # Emit GitHub Actions warnings
    for test in flaky_tests:
        fqn = f"{test['classname']}::{test['name']}"
        print(f"::warning::Flaky test detected: {fqn} (rerun {test['reruns']} time(s))")

    # Write report
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "flaky_count": len(flaky_tests),
        "tests": flaky_tests,
    }
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if flaky_tests:
        print(f"\n{len(flaky_tests)} flaky test(s) detected. Report: {output_path}")
    else:
        print("No flaky tests detected.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
