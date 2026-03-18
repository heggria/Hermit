"""A2A (Agent-to-Agent) protocol models and handler for governed task exchange."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import AgentRunner

_log = structlog.get_logger()

# ---------------------------------------------------------------------------
# A2A Models
# ---------------------------------------------------------------------------

_DEFAULT_TRUST_LEVEL = "untrusted"
_TRUSTED_POLICY = "default"
_SUPERVISED_POLICY = "supervised"


@dataclass
class A2ATaskRequest:
    sender_agent_id: str
    sender_agent_url: str
    task_description: str
    required_capabilities: list[str] = field(default_factory=list[str])
    priority: str = "normal"
    context_artifacts: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    reply_to_url: str = ""


@dataclass
class A2ATaskResponse:
    task_id: str
    status: str
    result_summary: str = ""
    proof_ref: str = ""
    receipts_summary: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


@dataclass
class A2ACapabilityCard:
    agent_id: str
    agent_name: str
    capabilities: list[str] = field(default_factory=list[str])
    supported_actions: list[str] = field(default_factory=list[str])
    trust_level: str = "standard"


# ---------------------------------------------------------------------------
# Trust resolution
# ---------------------------------------------------------------------------


def resolve_sender_trust(sender_agent_id: str, trust_records: dict[str, str]) -> str:
    """Return trust level for a sender from the trust records map.

    Unknown senders get ``untrusted`` which maps to the supervised policy.
    """
    return trust_records.get(sender_agent_id, _DEFAULT_TRUST_LEVEL)


def policy_for_trust(trust_level: str) -> str:
    """Map a trust level string to a kernel policy profile name."""
    if trust_level in ("trusted", "known"):
        return _TRUSTED_POLICY
    return _SUPERVISED_POLICY


# ---------------------------------------------------------------------------
# A2AHandler
# ---------------------------------------------------------------------------


class A2AHandler:
    """Processes incoming A2A task requests through the governed execution path."""

    def __init__(
        self,
        agent_id: str = "",
        agent_name: str = "Hermit",
        capabilities: list[str] | None = None,
        trust_records: dict[str, str] | None = None,
    ) -> None:
        self.agent_id = agent_id or f"hermit-{uuid4().hex[:8]}"
        self.agent_name = agent_name
        self.capabilities = capabilities or ["task_execution", "governed_workflow"]
        self.trust_records: dict[str, str] = trust_records or {}

    # -- Capability card ---------------------------------------------------

    def build_capability_card(self) -> A2ACapabilityCard:
        """Return this Hermit instance's capability advertisement."""
        return A2ACapabilityCard(
            agent_id=self.agent_id,
            agent_name=self.agent_name,
            capabilities=list(self.capabilities),
            supported_actions=["task_request", "status_poll"],
            trust_level="standard",
        )

    # -- Task request ------------------------------------------------------

    def handle_task_request(
        self,
        request: A2ATaskRequest,
        runner: AgentRunner,
    ) -> A2ATaskResponse:
        """Validate sender and create a governed task via the runner's ingress.

        Returns an ``A2ATaskResponse`` with the created task_id and initial
        status.
        """
        trust = resolve_sender_trust(request.sender_agent_id, self.trust_records)
        policy = policy_for_trust(trust)

        session_id = f"a2a-{request.sender_agent_id}-{uuid4().hex[:8]}"

        _log.info(  # type: ignore[call-arg]
            "a2a_task_request",
            sender=request.sender_agent_id,
            trust=trust,
            policy=policy,
            session_id=session_id,
        )

        metadata: dict[str, object] = {
            "a2a_sender_id": request.sender_agent_id,
            "a2a_sender_url": request.sender_agent_url,
            "a2a_reply_to": request.reply_to_url,
            "a2a_priority": request.priority,
            "a2a_required_capabilities": request.required_capabilities,
            "a2a_trust_level": trust,
        }

        try:
            runner.enqueue_ingress(
                session_id,
                request.task_description,
                source_channel="a2a",
                source_ref=f"a2a/{request.sender_agent_id}",
                requested_by=request.sender_agent_id,
                ingress_metadata=metadata,
            )
        except Exception as exc:
            _log.exception(  # type: ignore[call-arg]
                "a2a_task_request_failed",
                sender=request.sender_agent_id,
                error=str(exc),
            )
            return A2ATaskResponse(
                task_id="",
                status="failed",
                result_summary=f"Task creation failed: {exc}",
            )

        return A2ATaskResponse(
            task_id=session_id,
            status="accepted",
        )

    # -- Result callback ---------------------------------------------------

    @staticmethod
    def send_result(reply_to_url: str, response: A2ATaskResponse) -> bool:
        """POST a task result back to the sender agent.

        Returns True on success, False on failure.  Uses a short timeout to
        avoid blocking the dispatch thread.
        """
        if not reply_to_url:
            return False

        import json
        import urllib.request

        payload = json.dumps(
            {
                "task_id": response.task_id,
                "status": response.status,
                "result_summary": response.result_summary,
                "proof_ref": response.proof_ref,
                "sent_at": time.time(),
            }
        ).encode()

        req = urllib.request.Request(
            reply_to_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
            _log.info("a2a_result_sent", url=reply_to_url)  # type: ignore[call-arg]
            return True
        except Exception as exc:
            _log.warning(  # type: ignore[call-arg]
                "a2a_result_send_failed",
                url=reply_to_url,
                error=str(exc),
            )
            return False
