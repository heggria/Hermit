#!/usr/bin/env python3
"""Monitor 32 Hermit iterations — check status, identify failures, report progress."""

import json
import subprocess
import time

MCP_URL = "http://127.0.0.1:8322/mcp"
SESSION_ID = ""


def curl_mcp(method: str, params: dict) -> dict:
    global SESSION_ID
    if not SESSION_ID:
        SESSION_ID = init_session()

    payload = {
        "jsonrpc": "2.0",
        "id": f"mon-{int(time.time() * 1000)}",
        "method": "tools/call",
        "params": {"name": method, "arguments": params},
    }
    cmd = [
        "curl",
        "-s",
        "-D",
        "-",
        "-X",
        "POST",
        MCP_URL,
        "-H",
        "Content-Type: application/json",
        "-H",
        "Accept: application/json, text/event-stream",
        "-H",
        f"Mcp-Session-Id: {SESSION_ID}",
        "-d",
        json.dumps(payload),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    output = result.stdout
    parts = output.split("\r\n\r\n", 1)
    body = parts[1] if len(parts) > 1 else output

    for line in body.strip().split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    try:
        return json.loads(body.strip())
    except json.JSONDecodeError:
        return {"raw": body[:500]}


def init_session() -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": "mon-init",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "monitor", "version": "1.0"},
        },
    }
    cmd = [
        "curl",
        "-s",
        "-D",
        "-",
        "-X",
        "POST",
        MCP_URL,
        "-H",
        "Content-Type: application/json",
        "-H",
        "Accept: application/json, text/event-stream",
        "-d",
        json.dumps(payload),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    for line in result.stdout.split("\n"):
        if line.lower().startswith("mcp-session-id:"):
            return line.split(":", 1)[1].strip()
    return ""


def get_iteration_status(iteration_ids: list[str]) -> list[dict]:
    """Get status for a batch of iterations."""
    result = curl_mcp(
        "hermit_iteration_status",
        {
            "iteration_ids": iteration_ids,
            "include_findings": False,
            "include_spec": False,
        },
    )
    try:
        content = result.get("result", {}).get("content", [])
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                data = json.loads(item["text"])
                return data.get("iterations", [])
    except Exception:
        pass
    return []


def main():
    # Load iteration IDs
    with open("/tmp/hermit_32_iterations.json") as f:
        data = json.load(f)
    iteration_ids = data["iteration_ids"]
    print(f"Monitoring {len(iteration_ids)} iterations\n")

    # Query in batches of 8
    batch_size = 8
    all_statuses = []

    for i in range(0, len(iteration_ids), batch_size):
        batch = iteration_ids[i : i + batch_size]
        statuses = get_iteration_status(batch)
        all_statuses.extend(statuses)

    # Categorize
    by_phase = {}
    failed = []
    completed = []
    active = []

    for s in all_statuses:
        phase = s.get("phase", s.get("status", "unknown"))
        iid = s.get("iteration_id", "?")
        goal = s.get("goal", "")[:60]

        by_phase.setdefault(phase, []).append(s)

        if phase in ("failed", "rejected"):
            failed.append(s)
        elif phase in ("completed", "accepted", "pr_created", "merge_approved"):
            completed.append(s)
        else:
            active.append(s)

    # Report
    print(f"{'=' * 70}")
    print(f"  STATUS REPORT — {len(all_statuses)} iterations")
    print(f"{'=' * 70}")
    print(f"  Completed: {len(completed)}")
    print(f"  Active:    {len(active)}")
    print(f"  Failed:    {len(failed)}")
    print()

    for phase, items in sorted(by_phase.items()):
        print(f"  [{phase}] ({len(items)})")
        for s in items:
            iid = s.get("iteration_id", "?")[:16]
            goal = s.get("goal", "")[:55]
            print(f"    {iid}  {goal}")
        print()

    if failed:
        print(f"\n{'!' * 70}")
        print("  FAILED ITERATIONS — need diagnosis")
        print(f"{'!' * 70}")
        for s in failed:
            iid = s.get("iteration_id", "?")
            goal = s.get("goal", "")[:80]
            error = s.get("error", s.get("failure_reason", "unknown"))
            print(f"\n  ID: {iid}")
            print(f"  Goal: {goal}")
            print(f"  Error: {error}")

    # Save report
    report = {
        "timestamp": time.time(),
        "total": len(all_statuses),
        "completed": len(completed),
        "active": len(active),
        "failed": len(failed),
        "by_phase": {k: len(v) for k, v in by_phase.items()},
        "failed_ids": [s.get("iteration_id") for s in failed],
        "all_statuses": all_statuses,
    }
    with open("/tmp/hermit_32_monitor.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print("\nReport saved to /tmp/hermit_32_monitor.json")
    return report


if __name__ == "__main__":
    main()
