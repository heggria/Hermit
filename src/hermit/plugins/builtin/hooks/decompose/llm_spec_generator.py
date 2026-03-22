"""LLM-native spec generator — replaces regex-based SpecGenerator with LLM inference.

Calls an LLM provider to generate structured specs from goals and research
findings. Falls back to the deterministic SpecGenerator on any failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec
from hermit.plugins.builtin.hooks.decompose.spec_generator import SpecGenerator, _make_spec_id
from hermit.runtime.provider_host.execution.vision_services import _parse_json_response
from hermit.runtime.provider_host.shared.contracts import Provider, ProviderRequest

if TYPE_CHECKING:
    from hermit.plugins.builtin.hooks.research.models import ResearchReport

log = structlog.get_logger()

_FINDING_MAX_CHARS = 600
_FINDING_MAX_COUNT = 20

_SYSTEM_PROMPT = """\
You are a spec generator for a governed agent kernel called Hermit. Given a goal \
(and optionally research findings about the codebase), produce a structured JSON \
specification that an autonomous agent can execute.

Your output MUST be a single JSON object with exactly these keys:

{
  "title": "<concise title, max 80 chars>",
  "file_plan": [
    {"path": "<relative file path>", "action": "create|modify|delete", "reason": "<why>"}
  ],
  "constraints": ["<constraint 1>", "<constraint 2>"],
  "acceptance_criteria": ["<criterion 1>", "<criterion 2>"],
  "trust_zone": "normal|sensitive|critical",
  "risk_assessment": "<one-sentence risk summary>"
}

Rules for each field:

**file_plan** (CRITICAL):
- List EVERY file that needs to be created, modified, or deleted.
- Infer files from the research findings, not just from the goal text.
- If research shows related test files, include them.
- If modifying a module, include its __init__.py if exports change.
- Each entry needs path, action (create/modify/delete), and reason.

**constraints**:
- Include both explicit constraints from the goal AND implicit ones.
- If research shows existing tests, add: "Existing tests must continue to pass".
- If research shows public APIs, add: "Preserve backward compatibility of public APIs".
- If changes span multiple modules, add coupling constraints.
- Always include: "Follow project Ruff formatting and linting rules".

**acceptance_criteria**:
- FIRST criterion MUST always be: "`make check` passes".
- Every criterion must be VERIFIABLE — no subjective language.
- Bad: "Code is clean". Good: "All new functions have docstrings".
- Bad: "Tests are good". Good: "New tests achieve >= 80% coverage of changed code".
- Include file-specific criteria (e.g., "X is importable from Y").
- If the goal involves bug fixes, require a regression test.

**trust_zone**:
- "normal" — standard code changes (most common).
- "sensitive" — changes to policy, security, auth, trust, or governance modules.
- "critical" — changes to kernel execution, ledger, proof, or receipt systems.
- Infer from the file paths in the research findings.

**risk_assessment**:
- One sentence summarizing the primary risk of this change.

Output ONLY the JSON object. No markdown fences, no commentary.\
"""


def _format_research(report: ResearchReport) -> str:
    """Format research findings for LLM context.

    Truncates each finding to _FINDING_MAX_CHARS and caps total count
    at _FINDING_MAX_COUNT to stay within reasonable token budgets.
    """
    if not report.findings:
        return ""

    sections: list[str] = []
    sections.append(f"## Research Report for: {report.goal}")

    if report.suggested_approach:
        sections.append(f"\n### Suggested Approach\n{report.suggested_approach}")

    if report.knowledge_gaps:
        gaps = "; ".join(report.knowledge_gaps[:5])
        sections.append(f"\n### Knowledge Gaps\n{gaps}")

    sections.append("\n### Findings")
    for idx, finding in enumerate(report.findings[:_FINDING_MAX_COUNT]):
        content = finding.content
        if len(content) > _FINDING_MAX_CHARS:
            content = content[:_FINDING_MAX_CHARS] + "..."

        header = f"\n**[{idx + 1}] {finding.title}** (source={finding.source}"
        if finding.file_path:
            header += f", file={finding.file_path}"
        header += f", relevance={finding.relevance:.2f})"
        sections.append(header)
        sections.append(content)

    return "\n".join(sections)


def _validate_spec_output(data: dict[str, Any]) -> list[str]:
    """Validate the LLM-generated spec JSON and return a list of issues.

    Returns an empty list if the output is valid.
    """
    issues: list[str] = []

    if not isinstance(data.get("title"), str) or not data["title"].strip():
        issues.append("missing or empty 'title'")

    file_plan = data.get("file_plan")
    if not isinstance(file_plan, list):
        issues.append("'file_plan' must be a list")
    else:
        for idx, entry in enumerate(file_plan):
            if not isinstance(entry, dict):
                issues.append(f"file_plan[{idx}] is not a dict")
                continue
            if not entry.get("path"):
                issues.append(f"file_plan[{idx}] missing 'path'")
            if entry.get("action") not in ("create", "modify", "delete"):
                issues.append(f"file_plan[{idx}] invalid action: {entry.get('action')}")

    constraints = data.get("constraints")
    if not isinstance(constraints, list):
        issues.append("'constraints' must be a list")

    criteria = data.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        issues.append("'acceptance_criteria' must be a non-empty list")
    elif not any("make check" in c.lower() for c in criteria):
        issues.append("first acceptance criterion must include '`make check` passes'")

    if data.get("trust_zone") not in ("normal", "sensitive", "critical"):
        issues.append(f"invalid trust_zone: {data.get('trust_zone')}")

    return issues


def _infer_trust_zone(file_plan: list[dict[str, str]]) -> str:
    """Infer trust zone from file paths when LLM output is missing or invalid."""
    sensitive_patterns = ("policy", "security", "auth", "trust", "governance")
    critical_patterns = ("execution", "ledger", "proof", "receipt", "verification")

    for entry in file_plan:
        path = entry.get("path", "").lower()
        if any(p in path for p in critical_patterns):
            return "critical"

    for entry in file_plan:
        path = entry.get("path", "").lower()
        if any(p in path for p in sensitive_patterns):
            return "sensitive"

    return "normal"


class LLMSpecGenerator:
    """LLM-native spec generator.

    Calls an LLM provider to produce structured specs from goals and research
    findings. Falls back to the deterministic SpecGenerator on any failure
    (parse error, validation failure, provider error).
    """

    def __init__(
        self,
        provider: Provider,
        *,
        model: str,
        max_tokens: int = 4096,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens
        self._fallback = SpecGenerator()

    def generate(
        self,
        goal: str,
        research_report: ResearchReport | None = None,
        constraints: tuple[str, ...] | None = None,
    ) -> GeneratedSpec:
        """Generate a spec from the goal, optionally enriched by research.

        On any LLM or parsing failure, falls back to the deterministic
        SpecGenerator to ensure a spec is always produced.
        """
        try:
            return self._generate_via_llm(goal, research_report, constraints)
        except Exception:
            log.warning(
                "llm_spec_generation_failed",
                goal_preview=goal[:120],
                exc_info=True,
            )
            return self._fallback.generate(
                goal,
                research_report=research_report,
                constraints=constraints,
            )

    def _generate_via_llm(
        self,
        goal: str,
        research_report: ResearchReport | None,
        constraints: tuple[str, ...] | None,
    ) -> GeneratedSpec:
        """Call the LLM and parse its structured output into a GeneratedSpec."""
        user_content = self._build_user_message(goal, research_report, constraints)

        response = self._provider.generate(
            ProviderRequest(
                model=self._model,
                max_tokens=self._max_tokens,
                system_prompt=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        )

        if response.error:
            raise RuntimeError(f"Provider error: {response.error}")

        data = _parse_json_response(response)
        if data is None:
            raise ValueError("Failed to parse JSON from LLM response")

        issues = _validate_spec_output(data)
        if issues:
            log.warning("llm_spec_validation_issues", issues=issues, goal_preview=goal[:80])
            data = self._patch_spec_data(data, goal, research_report)
            remaining = _validate_spec_output(data)
            if remaining:
                raise ValueError(f"Spec validation failed after patching: {remaining}")

        return self._build_spec(data, goal, research_report)

    def _build_user_message(
        self,
        goal: str,
        research_report: ResearchReport | None,
        constraints: tuple[str, ...] | None,
    ) -> str:
        """Assemble the user message sent to the LLM."""
        parts: list[str] = [f"## Goal\n{goal}"]

        if constraints:
            constraint_text = "\n".join(f"- {c}" for c in constraints)
            parts.append(f"\n## Explicit Constraints\n{constraint_text}")

        if research_report is not None:
            formatted = _format_research(research_report)
            if formatted:
                parts.append(f"\n{formatted}")

        return "\n".join(parts)

    def _patch_spec_data(
        self,
        data: dict[str, Any],
        goal: str,
        research_report: ResearchReport | None,
    ) -> dict[str, Any]:
        """Attempt to fix common validation issues in-place.

        Returns a new dict with patches applied.
        """
        patched = dict(data)

        if not isinstance(patched.get("title"), str) or not patched["title"].strip():
            patched["title"] = goal.split("\n")[0][:80]

        if not isinstance(patched.get("file_plan"), list):
            patched["file_plan"] = []

        if not isinstance(patched.get("constraints"), list):
            patched["constraints"] = []

        criteria = patched.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            patched["acceptance_criteria"] = ["`make check` passes"]
        elif not any("make check" in c.lower() for c in criteria):
            patched["acceptance_criteria"] = ["`make check` passes", *criteria]

        if patched.get("trust_zone") not in ("normal", "sensitive", "critical"):
            file_plan = patched.get("file_plan", [])
            patched["trust_zone"] = _infer_trust_zone(
                file_plan if isinstance(file_plan, list) else []
            )

        return patched

    def _build_spec(
        self,
        data: dict[str, Any],
        goal: str,
        research_report: ResearchReport | None,
    ) -> GeneratedSpec:
        """Convert validated JSON data into an immutable GeneratedSpec."""
        spec_id = _make_spec_id(goal)

        file_plan_raw = data.get("file_plan", [])
        file_plan = tuple(
            {
                "path": str(entry.get("path", "")),
                "action": str(entry.get("action", "modify")),
                "reason": str(entry.get("reason", "")),
            }
            for entry in file_plan_raw
            if isinstance(entry, dict) and entry.get("path")
        )

        constraints = tuple(
            str(c) for c in data.get("constraints", []) if isinstance(c, str) and c.strip()
        )

        acceptance_criteria = tuple(
            str(c) for c in data.get("acceptance_criteria", []) if isinstance(c, str) and c.strip()
        )

        trust_zone = data.get("trust_zone", "normal")
        if trust_zone not in ("normal", "sensitive", "critical"):
            trust_zone = "normal"

        research_ref = ""
        if research_report is not None:
            research_ref = f"research:{spec_id}"

        risk_assessment = str(data.get("risk_assessment", ""))
        metadata: dict[str, Any] = {"generator": "llm"}
        if risk_assessment:
            metadata["risk_assessment"] = risk_assessment

        return GeneratedSpec(
            spec_id=spec_id,
            title=str(data.get("title", goal.split("\n")[0][:80])),
            goal=goal,
            constraints=constraints,
            acceptance_criteria=acceptance_criteria,
            file_plan=file_plan,
            research_ref=research_ref,
            trust_zone=trust_zone,
            metadata=metadata,
        )
