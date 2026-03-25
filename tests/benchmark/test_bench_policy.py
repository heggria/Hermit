"""Policy evaluation performance benchmarks."""

from __future__ import annotations

import pytest

from hermit.kernel.policy.evaluators.engine import PolicyEngine
from hermit.kernel.policy.models.models import ActionRequest

pytestmark = pytest.mark.benchmark


class TestPolicyBenchmarks:
    """Benchmark policy evaluation operations."""

    def test_policy_evaluate_low_risk(self, benchmark):
        """Benchmark policy evaluation for a low-risk action."""
        engine = PolicyEngine()
        request = ActionRequest(
            request_id="bench-req-1",
            tool_name="read_file",
            action_class="read",
            resource_scopes=["filesystem"],
            risk_hint="low",
        )

        def evaluate():
            return engine.evaluate(request)

        result = benchmark(evaluate)
        assert result.verdict is not None

    def test_policy_evaluate_high_risk(self, benchmark):
        """Benchmark policy evaluation for a high-risk action."""
        engine = PolicyEngine()
        request = ActionRequest(
            request_id="bench-req-2",
            tool_name="bash",
            action_class="execute_command",
            resource_scopes=["filesystem", "network", "process"],
            risk_hint="high",
            tool_input={"command": "rm -rf /tmp/test"},
        )

        def evaluate():
            return engine.evaluate(request)

        result = benchmark(evaluate)
        assert result.verdict is not None

    def test_policy_build_action_request(self, benchmark):
        """Benchmark action request building and derivation."""
        from hermit.runtime.capability.registry.tools import ToolSpec

        engine = PolicyEngine()
        tool = ToolSpec(
            name="write_file",
            description="Write a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
            handler=lambda x: None,
            action_class="file_write",
            risk_hint="high",
            requires_receipt=True,
        )
        payload = {"path": "/tmp/test.txt", "content": "hello world"}

        def build_request():
            return engine.build_action_request(tool, payload)

        result = benchmark(build_request)
        assert result.request_id is not None
