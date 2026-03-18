---
id: a2a-task-federation
title: "Federated task execution across multiple Hermit instances"
priority: high
trust_zone: low
---

## Goal

Enable Hermit instances to federate task execution: an operator-local Hermit can delegate governed sub-tasks to remote Hermit instances via A2A protocol, with proof chain continuity across federation boundaries. The local instance retains governance authority while the remote instance provides execution capacity.

## Steps

1. Create `src/hermit/kernel/execution/federation/client.py`:
   - `FederationClient` class:
     - `delegate_remote(remote_url, task_goal, scope_constraints, parent_task_id)` → FederatedTask
       - Sends A2A task request to remote Hermit's /a2a/tasks endpoint
       - Includes delegation scope (allowed actions, budget, timeout)
       - Stores federation record locally with remote_task_ref
     - `poll_status(federated_task)` → FederatedTaskStatus
       - Polls remote /a2a/tasks/{id}/status for updates
       - Returns status + remote proof summary when available
     - `collect_proof(federated_task)` → RemoteProofBundle
       - Downloads remote proof bundle and verifies hash chain integrity
       - Links remote proof hash into local proof chain as an artifact
     - `recall_remote(federated_task, reason)` → bool
       - Sends cancellation to remote instance

2. Create `src/hermit/kernel/execution/federation/models.py`:
   - `FederatedTask`: local_task_id, remote_url, remote_task_id, delegation_scope, status, remote_proof_hash, created_at
   - `FederatedTaskStatus`: remote_status, progress_summary, remote_receipts_count, last_polled_at
   - `RemoteProofBundle`: remote_task_id, proof_hash, anchor_ref, receipts_summary, verified_locally
   - `FederationScope`: allowed_action_classes, max_steps, budget_tokens, timeout_seconds

3. Create `src/hermit/kernel/execution/federation/verifier.py`:
   - `FederationVerifier`:
     - `verify_remote_proof(bundle)` → VerificationResult
       - Checks hash chain integrity within the remote proof
       - Verifies proof hash matches what was advertised
       - Does NOT trust remote receipts — records them as "remote_attested" trust tier
     - `link_to_local_chain(bundle, local_task_id, store)` → artifact_id
       - Creates a local artifact with the remote proof hash
       - Links it into the local task's evidence case

4. Wire federation into TaskDelegationService:
   - Add `delegate_remote()` option alongside local delegation
   - Federation decisions go through policy evaluation (action_class="federation")
   - Each federation operation produces a receipt

5. Write tests in `tests/unit/kernel/test_task_federation.py`:
   - Test federation client creates correct A2A request
   - Test poll_status parses remote response
   - Test proof collection and hash verification
   - Test remote proof is linked as local artifact with "remote_attested" trust
   - Test federation scope constraints are sent to remote
   - Test recall sends cancellation
   - Test federation produces receipts locally

## Constraints

- Remote proof trust tier MUST be "remote_attested" — never "observed" or "derived"
- Federation MUST go through policy evaluation — no direct remote calls
- Do NOT trust remote identity without HMAC verification
- Network failures must not leave local task in inconsistent state
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/execution/federation/client.py` exists
- [ ] `src/hermit/kernel/execution/federation/models.py` exists
- [ ] `src/hermit/kernel/execution/federation/verifier.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_task_federation.py -q` passes with >= 7 tests
- [ ] Remote proofs are stored with "remote_attested" trust tier

## Context

- A2A endpoint: `src/hermit/plugins/builtin/hooks/webhook/a2a.py`
- TaskDelegationService: `src/hermit/kernel/task/services/delegation.py`
- ProofService: `src/hermit/kernel/verification/proofs/proofs.py`
- ArtifactStore: `src/hermit/kernel/artifacts/models/artifacts.py`
