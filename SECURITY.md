# Security Policy

Hermit is a **kernel-first governed agent runtime** designed around the principle that no AI model should directly execute tools or mutate state. Every action flows through a governed execution pipeline with policy evaluation, approval workflows, scoped authority, and cryptographic audit trails. Security is not a bolt-on feature -- it is the architectural foundation.

## Security Model Overview

Hermit's security model mirrors traditional OS kernel security: capability-based access control, mandatory audit logging, and process isolation -- applied to AI agent workloads.

### Governed Execution Pipeline

Every mutation in Hermit follows a strict authorization chain:

```
ActionRequest → PolicyEngine → Approval → WorkspaceLease → CapabilityGrant → Execution → Receipt → Proof
```

No direct model-to-tool execution is permitted. The kernel interposes on every action, evaluating policy rules, requiring approvals for sensitive operations, scoping authority via capability grants, and recording receipts for every executed action.

### Policy Evaluation

The **PolicyEngine** evaluates every action request against a layered guard chain before execution is permitted:

- **Readonly guard** -- enforces read-only constraints when active
- **Filesystem guard** -- restricts file operations by path, detects writes to sensitive locations (`.env`, `.ssh`, `.gnupg`, system directories)
- **Shell guard** -- blocks dangerous shell patterns (`sudo`, `curl | sh`), denies writes to protected paths outside the workspace
- **Network guard** -- evaluates network access requests
- **Attachment guard** -- governs file attachment operations
- **Planning guard** -- enforces planning-mode restrictions
- **Governance guard** -- applies governance-level policy constraints
- **Adjustment guard** -- applies dynamic risk adjustments based on context
- **Budget guard** -- enforces execution budget limits

Each guard produces a verdict (`allow`, `deny`, `require_approval`) with structured reasons, risk levels, and policy obligations (e.g., `require_receipt`, `require_approval`, `require_evidence`).

### Approval Workflows

Sensitive operations are not silently permitted. When the policy engine determines that an action requires approval, execution is suspended until an authorized principal explicitly approves or denies the request. Approval records include the approver identity, timestamp, and any conditions attached to the approval.

### Receipt-Based Audit Trails

Every executed action produces a **ReceiptRecord** containing the action type, inputs, outputs, result code, and an optional HMAC-SHA256 signature. Receipts are the atomic unit of auditability -- they prove what was done, when, and under what authority.

### Hash-Chained Proof Bundles

Receipts are aggregated into **proof bundles** with hash-chained integrity verification. The proof system supports multiple tiers:

- **Hash-only** -- baseline verifiability via SHA-256 content hashing
- **Hash-chained** -- sequential integrity via chained hashes across receipts
- **Signed** -- HMAC-SHA256 signatures using a configurable signing secret (`HERMIT_PROOF_SIGNING_SECRET`)
- **Signed with inclusion proof** -- Merkle inclusion proofs for individual receipt verification within a proof bundle

Proof bundles can be exported at three detail levels (`summary`, `standard`, `full`) and anchored to external systems (local log files, git notes).

### Workspace Isolation

**WorkspaceLeases** enforce spatial isolation for task execution. A mutable lease grants exclusive write access to a workspace directory, preventing concurrent tasks from conflicting. Lease conflicts are detected and either queued or denied. Each lease captures the execution environment (OS, Python version, working directory) for forensic reproducibility.

### Scoped Authority

**CapabilityGrants** implement least-privilege execution. Each grant is:

- Scoped to a specific task, step, and step attempt
- Bound to a specific action class and resource scope
- Constrained by policy-derived limitations
- Time-bounded with explicit expiration
- Traceable to the approval and policy decision that authorized it
- Revocable, with cascade revocation for child grants

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.3.x   | Yes                |
| 0.2.x   | Security fixes only |
| < 0.2   | No                 |
| `main`  | Yes (development)  |

Security fixes are applied to the latest release line and backported to the previous release line on a best-effort basis.

## Reporting Vulnerabilities

**Do not open public GitHub issues for security vulnerabilities.**

### Preferred Reporting Channels

1. **GitHub Security Advisories** -- Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) for this repository. This is the preferred channel.
2. **Email** -- If GitHub Security Advisories are not available, contact the maintainers directly via the email address listed in the repository profile.

### What to Include

A well-structured vulnerability report should contain:

- **Affected component** -- file path, module, or subsystem (e.g., `kernel/policy/guards/`, `runtime/provider_host/execution/sandbox.py`)
- **Vulnerability type** -- e.g., policy bypass, privilege escalation, information disclosure, injection
- **Attack scenario** -- description of how an attacker could exploit the vulnerability, including required privileges and preconditions
- **Reproduction steps** -- minimal steps or proof of concept to demonstrate the issue
- **Impact assessment** -- what an attacker gains (e.g., unauthorized command execution, data exfiltration, policy bypass)
- **Affected versions** -- specific version(s) or commit range
- **Suggested mitigation** -- if you have a fix or workaround, include it

### Response Timeline

| Action                          | Target    |
|---------------------------------|-----------|
| Acknowledge receipt             | 48 hours  |
| Initial severity assessment     | 5 days    |
| Fix development and validation  | 14 days   |
| Coordinated disclosure          | After fix |

Response times are best-effort targets. The maintainers will communicate proactively if timelines need adjustment.

## Security Features

### Policy Engine with Guard Rules

The policy engine dispatches every action request through a chain of specialized guard rule modules. Each guard evaluates the request against its domain-specific rules and produces structured verdicts with risk levels (`low`, `medium`, `high`, `critical`).

Key protections enforced by the guard chain:

- **Dangerous shell pattern blocking** -- `sudo`, `curl | sh`, and similar patterns are unconditionally denied
- **Sensitive path protection** -- writes to `.env`, `.ssh`, `.gnupg`, `/etc`, `/usr`, `/Library`, `/System` are denied when outside the workspace
- **Python write detection** -- static analysis of Python code in shell commands to detect filesystem mutations via `open()`, `Path.write_text()`, `shutil.rmtree()`, etc.
- **Command flag analysis** -- shell commands are decomposed and analyzed for network access, disk writes, package installation, and other observable behaviors

### Trust Scoring

The **TrustScorer** computes trust scores from historical kernel execution data using the formula:

```
composite = 0.5 * success_rate + 0.3 * (1 - rollback_rate) + 0.2 * avg_reconciliation_confidence
```

Trust scores inform policy decisions by adjusting risk levels based on an action class's historical reliability. A minimum execution threshold (5 receipts) is required before trust scores influence policy.

### Sandbox Execution

Commands are executed through the **CommandSandbox**, which provides:

- **Budget-aware execution** -- commands respect execution budgets and deadlines
- **Timeout enforcement** -- configurable per-command timeouts prevent runaway processes
- **Output observation** -- stdout/stderr are captured with pattern matching for ready, failure, and progress signals
- **Process isolation** -- each command runs in a subprocess with controlled environment

### No Direct Model-to-Tool Execution

This is a fundamental architectural invariant. LLM models propose actions; the kernel evaluates, authorizes, and executes them. The model never directly invokes tools. This separation ensures that:

- Every action is subject to policy evaluation
- Approval workflows cannot be bypassed
- All actions produce receipts
- Rollback is always possible for receipted actions
- The operator retains control over what the agent can do

### Webhook Signature Verification

Inbound webhooks support HMAC-SHA256 signature verification. When a webhook route is configured with a secret, the server validates the `X-Hub-Signature-256` header against the request body before processing. Timing-safe comparison (`hmac.compare_digest`) is used to prevent timing attacks.

### Receipt Signing

Receipts can be cryptographically signed using HMAC-SHA256 when `HERMIT_PROOF_SIGNING_SECRET` is configured. The signature covers the receipt ID, task ID, step ID, action type, and result code, providing tamper-evident audit records.

## Security Best Practices

### API Key Management

- **Never hardcode API keys** in source code, configuration files, or commit history
- Store secrets in `~/.hermit/.env` with appropriate file permissions (`chmod 600`)
- Use environment variables for sensitive configuration: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `HERMIT_PROOF_SIGNING_SECRET`
- Rotate keys immediately if exposure is suspected
- Configure `HERMIT_PROOF_SIGNING_SECRET` to enable receipt signing and strong proof verification

### Policy Profile Selection

Hermit supports multiple policy profiles that control the level of autonomy granted to the agent:

| Profile        | Autonomy | When to Use                                  |
|----------------|----------|----------------------------------------------|
| `autonomous`   | High     | Trusted, well-defined tasks in safe environments |
| `default`      | Medium   | General-purpose work with standard guardrails |
| `supervised`   | Low      | Sensitive operations requiring human oversight |
| `readonly`     | None     | Inspection-only, no mutations permitted       |

**Recommendations:**

- Start with `supervised` or `default` in production environments
- Use `autonomous` only for well-understood, low-risk task patterns
- Use `readonly` for inspection, debugging, and audit workflows
- Review pending approvals regularly when using `supervised` or `default` profiles

### Network Exposure

- Hermit is designed as a **local-first** runtime. The kernel and ledger operate on the local filesystem.
- The webhook server, when enabled, binds to a configurable host and port. **Do not expose the webhook server to the public internet without authentication and TLS termination.**
- MCP server endpoints (Streamable HTTP) should be protected by network-level access controls.
- Adapter connections (Feishu, Slack, Telegram) use outbound connections to vendor APIs. Ensure API credentials are stored securely and rotated according to vendor recommendations.

### Plugin Trust

- Plugins are loaded from two discovery paths: the builtin directory (`src/hermit/plugins/builtin/`) and the user plugin directory (`~/.hermit/plugins/`)
- **Only install plugins from trusted sources.** Plugins can register tools, hooks, commands, subagents, adapters, and MCP servers -- a malicious plugin has broad access.
- Review `plugin.toml` manifests before installing third-party plugins. Pay attention to entry points, declared variables, and requested capabilities.
- Builtin plugins are maintained as part of the Hermit repository and follow the same review process as kernel code.
- Plugin variables can reference environment variables via template syntax. Ensure that variable resolution does not inadvertently expose secrets.

### Ledger and State Protection

- The kernel ledger (`~/.hermit/kernel/state.db`) contains the complete task history, receipts, approvals, and proof chains. Protect this file with appropriate filesystem permissions.
- Artifact storage (`~/.hermit/kernel/artifacts/`) may contain sensitive data referenced by tasks. Apply the same access controls as the ledger.
- Session state (`~/.hermit/memory/session_state.json`) and memory files may contain conversation content. Ensure the `~/.hermit/` directory is not world-readable.

### Deployment Checklist

- [ ] API keys stored in `~/.hermit/.env` with `chmod 600`
- [ ] `HERMIT_PROOF_SIGNING_SECRET` configured for receipt signing
- [ ] Webhook server not exposed to public internet without TLS and authentication
- [ ] `~/.hermit/` directory permissions restricted to the owning user
- [ ] Policy profile selected appropriate to the deployment context
- [ ] Third-party plugins reviewed before installation
- [ ] Pending approvals monitored when using `supervised` or `default` profiles

## Safe Harbor

Good-faith security research is welcome. We ask that researchers:

- Avoid accessing other users' data
- Avoid destructive testing against third-party services
- Refrain from publishing exploit details before maintainers have had reasonable time to respond
- Follow responsible disclosure practices

Security researchers acting in good faith will not face legal action for their research activities.
