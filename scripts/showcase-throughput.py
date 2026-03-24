#!/usr/bin/env python3
"""Hermit Throughput Showcase — high-concurrency governed task execution demo.

Usage:
    # One-command run (auto-starts dev service if needed):
    uv run python scripts/showcase-throughput.py

    # Custom options:
    uv run python scripts/showcase-throughput.py --max-tasks 512 --batch-size 512
    uv run python scripts/showcase-throughput.py --monitor          # monitor only
    uv run python scripts/showcase-throughput.py --dry-run          # preview tasks

The script will:
1. Check if the dev MCP server is running (port 8322)
2. If not, ensure required env vars are in ~/.hermit-dev/.env and start the service
3. Submit tasks and monitor progress

Required env vars (auto-added to ~/.hermit-dev/.env if missing):
    HERMIT_MCP_SERVER_ENABLED=true
    HERMIT_LLM_CONCURRENCY=256
    HERMIT_DISPATCH_THREAD_MAX=1024
    HERMIT_POOL_EXECUTOR_MAX=1024
    HERMIT_MAX_SAME_WORKSPACE=1024
    HERMIT_SQLITE_BUSY_TIMEOUT=300000
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

MCP_URL = os.environ.get("HERMIT_MCP_URL", "http://127.0.0.1:8322/mcp")
SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
ATLAS_ROOT = Path(
    os.environ.get("SHOWCASE_TARGET_ROOT", str(PROJECT_ROOT.parent / "agent-usage-atlas"))
)
CODEBASE_ROOT = ATLAS_ROOT / "src" / "agent_usage_atlas"
STATE_FILE = Path("/tmp/hermit_showcase_state.json")
DEV_ENV_FILE = Path.home() / ".hermit-dev" / ".env"

# Required env vars for high-concurrency showcase
_REQUIRED_ENV = {
    "HERMIT_MCP_SERVER_ENABLED": "true",
    "HERMIT_LLM_CONCURRENCY": "256",
    "HERMIT_DISPATCH_THREAD_MAX": "1024",
    "HERMIT_POOL_EXECUTOR_MAX": "1024",
    "HERMIT_MAX_SAME_WORKSPACE": "1024",
    "HERMIT_SQLITE_BUSY_TIMEOUT": "300000",
}


def _ensure_dev_env() -> None:
    """Ensure ~/.hermit-dev/.env has all required showcase settings."""
    DEV_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if DEV_ENV_FILE.exists():
        for line in DEV_ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            existing[k] = v

    added = []
    for key, default in _REQUIRED_ENV.items():
        if key not in existing:
            existing[key] = default
            added.append(f"  {key}={default}")

    if added:
        lines = [f"{k}={v}" for k, v in existing.items()]
        DEV_ENV_FILE.write_text("\n".join(lines) + "\n")
        print(f"Added to {DEV_ENV_FILE}:")
        print("\n".join(added))


def _is_mcp_running() -> bool:
    """Check if MCP server is reachable."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", "2", "-o", "/dev/null", "-w", "%{http_code}", MCP_URL],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() != "000"
    except Exception:
        return False


def _start_dev_service() -> None:
    """Start the dev service via hermit-envctl.sh."""
    envctl = PROJECT_ROOT / "scripts" / "hermit-envctl.sh"
    if not envctl.exists():
        print(f"ERROR: {envctl} not found. Start Hermit manually.")
        sys.exit(1)

    print("Starting dev service...")
    subprocess.run([str(envctl), "dev", "restart"], capture_output=True, timeout=30)

    # Wait for MCP to come up
    for _ in range(20):
        time.sleep(1)
        if _is_mcp_running():
            print("MCP server ready.\n")
            return
    print("ERROR: MCP server did not start within 20s.")
    sys.exit(1)


def ensure_service() -> None:
    """Ensure dev environment is configured and MCP server is running."""
    _ensure_dev_env()
    if not _is_mcp_running():
        _start_dev_service()


# ── MCP client ──────────────────────────────────────────────────────────────


def curl_mcp(method: str, params: dict, session_id: str = "") -> tuple[str, str]:
    """Send MCP request via curl; return (body, session_id)."""
    payload = {
        "jsonrpc": "2.0",
        "id": f"req-{method}-{int(time.time() * 1000)}",
        "method": method if method == "initialize" else "tools/call",
        "params": (params if method == "initialize" else {"name": method, "arguments": params}),
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
    ]
    if session_id:
        cmd += ["-H", f"Mcp-Session-Id: {session_id}"]
    cmd += ["-d", json.dumps(payload)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        error_text = (result.stderr or result.stdout or "").strip() or "empty curl error output"
        raise RuntimeError(
            f"Failed to reach Hermit MCP server at {MCP_URL} "
            f"(curl exit {result.returncode}): {error_text}"
        )

    output = result.stdout
    if not output.strip():
        raise RuntimeError(f"Empty response from Hermit MCP server at {MCP_URL}")

    sid = session_id
    for line in output.split("\n"):
        if line.lower().startswith("mcp-session-id:"):
            sid = line.split(":", 1)[1].strip()
            break

    parts = re.split(r"\r?\n\r?\n", output, maxsplit=1)
    body = parts[1] if len(parts) > 1 else output
    if not body.strip():
        raise RuntimeError("Hermit MCP response body is empty")
    return body, sid


def parse_mcp_response(body: str) -> dict:
    """Parse either SSE or plain JSON MCP response bodies."""
    for line in body.strip().split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    try:
        return json.loads(body.strip())
    except json.JSONDecodeError:
        return {"raw": body[:500]}


def init_session() -> str:
    """Initialize MCP session."""
    body, sid = curl_mcp(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "hermit-showcase", "version": "1.0"},
        },
    )
    response = parse_mcp_response(body)
    if response.get("error"):
        raise RuntimeError(f"MCP initialize failed: {json.dumps(response['error'])}")
    if not sid:
        raise RuntimeError(
            "MCP initialize succeeded but no session id was returned. "
            "Make sure `hermit serve` is running with `HERMIT_MCP_SERVER_ENABLED=true`."
        )
    return sid


def extract_tool_payload(response: dict) -> dict:
    """Extract the JSON payload returned by a FastMCP tool call."""
    if response.get("error"):
        raise RuntimeError(f"MCP tool call failed: {json.dumps(response['error'])}")

    result = response.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected MCP response shape: {json.dumps(response)[:500]}")

    content = result.get("content", [])
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text", "") or "").strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw_text": text}

    return result


# ── Task generation ──────────────────────────────────────────────────────────


ANALYSIS_TEMPLATES = [
    (
        "improve",
        "Read the file {module_path} and find one concrete issue to fix. "
        "Focus on edge cases: missing None checks, unhandled exceptions, "
        "empty collection access without guards, or integer overflow risks. "
        "If the file looks fine and there is no real issue, do nothing and stop. "
        "Do not invent problems. Only apply a fix if a genuine issue exists.",
    ),
]


def scan_modules() -> list[Path]:
    """Scan codebase for Python module directories with substantial code."""
    modules: list[Path] = []
    for py_dir in sorted(CODEBASE_ROOT.rglob("*")):
        if not py_dir.is_dir():
            continue
        py_files = list(py_dir.glob("*.py"))
        if len(py_files) >= 2:  # At least 2 Python files
            # Skip __pycache__ and test directories
            if "__pycache__" in str(py_dir) or "test" in py_dir.name:
                continue
            modules.append(py_dir)
    return modules


def scan_individual_files() -> list[Path]:
    """Scan codebase for individual Python files (for file-level tasks)."""
    files: list[Path] = []
    for py_file in sorted(CODEBASE_ROOT.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        if py_file.name == "__init__.py":
            continue
        if py_file.stat().st_size > 500:  # Skip tiny files
            files.append(py_file)
    return files


def generate_tasks() -> list[dict[str, str]]:
    """Generate analysis task descriptions from codebase scan.

    Strategy: maximize tasks by combining module-level + file-level analysis.
    Each task is designed to be token-intensive: reads multiple files and
    produces detailed output.
    """
    tasks: list[dict[str, str]] = []
    files = scan_individual_files()

    # File-level analysis using read-only operations (bypasses deliberation)
    for py_file in files:
        for _analysis_type, template in ANALYSIS_TEMPLATES:
            desc = template.format(module_path=py_file)
            tasks.append(
                {
                    "description": desc,
                    "priority": "normal",
                }
            )

    return tasks


# ── Submission ───────────────────────────────────────────────────────────────


def submit_tasks(tasks: list[dict], batch_size: int = 50) -> dict:
    """Submit tasks in batches via MCP."""
    print("Initializing MCP session...")
    session_id = init_session()
    print(f"Session: {session_id}\n")

    all_task_ids: list[str] = []
    total_batches = (len(tasks) + batch_size - 1) // batch_size
    start_time = time.time()

    for batch_num in range(total_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, len(tasks))
        batch = tasks[start:end]

        elapsed = time.time() - start_time
        rate = len(all_task_ids) / elapsed if elapsed > 0 else 0
        print(
            f"[Batch {batch_num + 1}/{total_batches}] "
            f"Submitting tasks {start + 1}-{end} "
            f"({len(all_task_ids)} submitted, {rate:.1f} tasks/sec)"
        )

        body, session_id = curl_mcp(
            "hermit_submit",
            {
                "tasks": batch,
                "policy_profile": "autonomous",
                "workspace_root": str(ATLAS_ROOT),
            },
            session_id,
        )

        response = parse_mcp_response(body)
        payload = extract_tool_payload(response)
        task_ids = payload.get("task_ids", [])
        submitted = int(payload.get("submitted", len(task_ids)))

        if submitted <= 0 or not task_ids:
            raise RuntimeError(
                "Hermit MCP accepted the request but returned no task ids. "
                f"Payload: {json.dumps(payload, ensure_ascii=False)[:500]}"
            )

        all_task_ids.extend(task_ids)
        print(f"  → {submitted} tasks accepted")

        # Brief pause between batches to avoid overwhelming MCP server
        time.sleep(0.5)

    elapsed = time.time() - start_time
    state = {
        "session_id": session_id,
        "task_ids": all_task_ids,
        "count": len(all_task_ids),
        "submitted_at": time.time(),
        "start_time": start_time,
        "elapsed_seconds": elapsed,
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))

    print(f"\n{'=' * 70}")
    print("SUBMISSION COMPLETE")
    print(f"{'=' * 70}")
    print(f"Total submitted: {len(all_task_ids)}")
    print(f"Elapsed: {elapsed:.1f}s ({len(all_task_ids) / elapsed:.1f} tasks/sec)")
    print(f"State saved to {STATE_FILE}")
    return state


# ── Monitoring ───────────────────────────────────────────────────────────────


def _launch_dashboard(task_ids: list[str], session_id: str) -> None:
    """Launch the Textual dashboard as a subprocess."""
    dashboard = SCRIPTS_DIR / "showcase-dashboard.py"
    if not dashboard.exists():
        print("showcase-dashboard.py not found; falling back to text monitor.")
        monitor_throughput(session_id, task_ids=task_ids or None)
        return
    cmd = [sys.executable, str(dashboard)]
    if task_ids:
        cmd += ["--task-ids", ",".join(task_ids)]
    if session_id:
        cmd += ["--session-id", session_id]
    subprocess.run(cmd)


def monitor_throughput(session_id: str = "", task_ids: list[str] | None = None) -> None:
    """Poll task status and health metrics to display real-time progress."""
    if not session_id:
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            session_id = state.get("session_id", "")
            task_ids = task_ids or list(state.get("task_ids", []))
        if not session_id:
            session_id = init_session()

    if not task_ids:
        raise RuntimeError(
            f"No tracked task ids found. Submit tasks first or inspect {STATE_FILE}."
        )

    print(f"\n{'=' * 70}")
    print("THROUGHPUT MONITOR (Ctrl+C to stop)")
    print(f"{'=' * 70}\n")

    interval = 2  # seconds between polls

    while True:
        try:
            status_body, session_id = curl_mcp(
                "hermit_task_status",
                {"task_ids": task_ids},
                session_id,
            )
            health_body, session_id = curl_mcp(
                "hermit_metrics",
                {"kind": "health", "window_hours": 1.0},
                session_id,
            )

            status_payload = extract_tool_payload(parse_mcp_response(status_body))
            health_payload = extract_tool_payload(parse_mcp_response(health_body))

            task_rows = status_payload.get("tasks", [])
            counts = {
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
                "blocked": 0,
                "running": 0,
                "queued": 0,
                "unknown": 0,
            }

            for row in task_rows:
                if isinstance(row, dict):
                    task = row.get("task", row)
                else:
                    task = {}
                status = str(task.get("status", "unknown") or "unknown")
                if status in counts:
                    counts[status] += 1
                else:
                    counts["unknown"] += 1

            total_tasks = len(task_ids)
            completed = counts["completed"]
            failed = counts["failed"]
            cancelled = counts["cancelled"]
            blocked = counts["blocked"]
            running = counts["running"]
            queued = counts["queued"]

            throughput = health_payload.get("throughput") or {}
            throughput_per_hour = float(throughput.get("throughput_per_hour", 0) or 0)
            stale_tasks = int(health_payload.get("total_stale_tasks", 0) or 0)
            health_score = int(health_payload.get("health_score", 0) or 0)

            now = time.strftime("%H:%M:%S")
            sys.stdout.write("\r\033[K")  # Clear line
            print(
                f"[{now}] "
                f"Tasks: {completed}/{total_tasks} done, "
                f"{running} running, {queued} queued, {blocked} blocked, "
                f"{failed} failed, {cancelled} cancelled | "
                f"Health: {health_score} | "
                f"Global throughput: {throughput_per_hour:.1f}/hr | "
                f"Stale: {stale_tasks}"
            )

            # Check if all done
            terminal_count = completed + failed + cancelled
            if total_tasks > 0 and terminal_count >= total_tasks:
                print(
                    f"\nAll tracked tasks reached terminal state! "
                    f"({completed} completed, {failed} failed, {cancelled} cancelled)"
                )
                break

            time.sleep(interval)

        except KeyboardInterrupt:
            print("\nMonitor stopped.")
            break
        except Exception as e:
            print(f"\nError polling metrics: {e}")
            time.sleep(interval)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermit Throughput Showcase")
    parser.add_argument(
        "--submit", action="store_true", help="Submit tasks (default: submit + monitor)"
    )
    parser.add_argument("--monitor", action="store_true", help="Monitor only (use saved state)")
    parser.add_argument(
        "--batch-size", type=int, default=50, help="Tasks per MCP batch (default 50)"
    )
    parser.add_argument(
        "--max-tasks", type=int, default=500, help="Max tasks to generate (default 500)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate tasks without submitting")
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Use text-only monitor (default: Textual dashboard)",
    )
    args = parser.parse_args()

    # Default: both submit and monitor
    do_submit = args.submit or not args.monitor
    do_monitor = args.monitor or not args.submit

    if args.dry_run:
        tasks = generate_tasks()
        tasks = tasks[: args.max_tasks]
        print(f"Generated {len(tasks)} tasks (dry run)\n")
        # Show sample
        for i, t in enumerate(tasks[:5]):
            desc = t["description"][:100].replace("\n", " ")
            print(f"  [{i + 1}] {desc}...")
        if len(tasks) > 5:
            print(f"  ... and {len(tasks) - 5} more")

        # Token estimate
        avg_desc_tokens = sum(len(t["description"]) // 4 for t in tasks) // len(tasks)
        est_input_per_task = 250_000  # ~250K context per task (5 turns, growing context)
        est_output_per_task = 14_000  # ~14K output per task (5 turns)
        est_total = len(tasks) * (est_input_per_task + est_output_per_task)
        concurrency = 100
        est_time_per_task_min = 2.0  # ~2 min per task
        est_throughput = (
            concurrency * (est_input_per_task + est_output_per_task) * (60 / est_time_per_task_min)
        )
        print("\nEstimated token consumption:")
        print(f"  Tasks generated:        {len(tasks)}")
        print(f"  Avg description:        ~{avg_desc_tokens} tokens")
        print(f"  Est. input/task (5 turns): ~{est_input_per_task:,} tokens")
        print(f"  Est. output/task:       ~{est_output_per_task:,} tokens")
        print(f"  Total estimated:        ~{est_total:,} tokens ({est_total / 1e9:.2f}B)")
        print(f"\nThroughput projection (HERMIT_LLM_CONCURRENCY={concurrency}):")
        print(f"  Concurrent API calls:   {concurrency}")
        print(f"  Est. tokens/hour:       ~{est_throughput:,.0f} ({est_throughput / 1e9:.2f}B/hr)")
        print("\nTo reach 2B/hour, set HERMIT_LLM_CONCURRENCY=150-200")
        print(
            f"  with 150 concurrent:    ~{est_throughput * 1.5:,.0f} ({est_throughput * 1.5 / 1e9:.2f}B/hr)"
        )
        print(
            f"  with 200 concurrent:    ~{est_throughput * 2:,.0f} ({est_throughput * 2 / 1e9:.2f}B/hr)"
        )
        return

    if do_submit:
        print("Scanning codebase...")
        tasks = generate_tasks()
        tasks = tasks[: args.max_tasks]
        print(f"Generated {len(tasks)} analysis tasks\n")

        ensure_service()

        state = submit_tasks(tasks, batch_size=args.batch_size)

    if do_monitor:
        session_id = ""
        task_ids: list[str] | None = None
        if do_submit:
            session_id = state.get("session_id", "")  # type: ignore[possibly-undefined]
            task_ids = state.get("task_ids", [])  # type: ignore[possibly-undefined]
        if args.no_dashboard:
            monitor_throughput(session_id, task_ids=task_ids)
        else:
            _launch_dashboard(task_ids or [], session_id)


if __name__ == "__main__":
    main()
