#!/usr/bin/env python3
"""Generate a multi-source local agent usage dashboard."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

LOCAL_TZ = timezone(timedelta(hours=8))
START_LOCAL = datetime(2026, 3, 8, 0, 0, 0, tzinfo=LOCAL_TZ)
START_UTC = START_LOCAL.astimezone(timezone.utc)
NOW_LOCAL = datetime.now(LOCAL_TZ)
NOW_UTC = NOW_LOCAL.astimezone(timezone.utc)

OUTPUT_PATH = Path("/Users/beta/work/Hermit/reports/codex-token-dashboard.html")
CODEX_ROOTS = [
    Path("/Users/beta/.codex/archived_sessions"),
    Path("/Users/beta/.codex/sessions"),
]
CLAUDE_ROOT = Path("/Users/beta/.claude/projects")
CURSOR_ROOT = Path("/Users/beta/.cursor/projects")

# ---------------------------------------------------------------------------
# Model pricing (USD per million tokens)
# Source: https://platform.claude.com/docs/en/about-claude/pricing
#         OpenAI API pricing page (approximate for GPT-5 / Codex models)
# Update these values if pricing changes.
# ---------------------------------------------------------------------------
MODEL_PRICING: dict[str, dict[str, float]] = {
    # OpenAI GPT-5.4 (Codex CLI)
    "gpt-5.4": {
        "input": 2.50, "cache_read": 0.25, "cache_write": 0.0,
        "output": 15.0, "reasoning": 15.0,
    },
    "GPT-5 Codex": {
        "input": 2.50, "cache_read": 0.25, "cache_write": 0.0,
        "output": 15.0, "reasoning": 15.0,
    },
    # Claude 4.6
    "claude-opus-4-6": {
        "input": 5.0, "cache_read": 0.50, "cache_write": 6.25,
        "output": 25.0, "reasoning": 25.0,
    },
    "claude-sonnet-4-6": {
        "input": 3.0, "cache_read": 0.30, "cache_write": 3.75,
        "output": 15.0, "reasoning": 15.0,
    },
    # Claude 4.5
    "claude-opus-4-5": {
        "input": 5.0, "cache_read": 0.50, "cache_write": 6.25,
        "output": 25.0, "reasoning": 25.0,
    },
    "claude-sonnet-4-5": {
        "input": 3.0, "cache_read": 0.30, "cache_write": 3.75,
        "output": 15.0, "reasoning": 15.0,
    },
    # Claude 4
    "claude-opus-4-1": {
        "input": 15.0, "cache_read": 1.50, "cache_write": 18.75,
        "output": 75.0, "reasoning": 75.0,
    },
    "claude-opus-4-0": {
        "input": 15.0, "cache_read": 1.50, "cache_write": 18.75,
        "output": 75.0, "reasoning": 75.0,
    },
    "claude-opus-4-2": {
        "input": 15.0, "cache_read": 1.50, "cache_write": 18.75,
        "output": 75.0, "reasoning": 75.0,
    },
    "claude-sonnet-4-0": {
        "input": 3.0, "cache_read": 0.30, "cache_write": 3.75,
        "output": 15.0, "reasoning": 15.0,
    },
    "claude-sonnet-4-2": {
        "input": 3.0, "cache_read": 0.30, "cache_write": 3.75,
        "output": 15.0, "reasoning": 15.0,
    },
    # Claude Haiku
    "claude-haiku-4-5": {
        "input": 1.0, "cache_read": 0.10, "cache_write": 1.25,
        "output": 5.0, "reasoning": 5.0,
    },
    "claude-haiku-3-5": {
        "input": 0.80, "cache_read": 0.08, "cache_write": 1.0,
        "output": 4.0, "reasoning": 4.0,
    },
    "claude-3-haiku": {
        "input": 0.25, "cache_read": 0.03, "cache_write": 0.30,
        "output": 1.25, "reasoning": 1.25,
    },
    # Legacy Claude 3.5
    "claude-3-5-sonnet": {
        "input": 3.0, "cache_read": 0.30, "cache_write": 3.75,
        "output": 15.0, "reasoning": 15.0,
    },
    "claude-3-5-haiku": {
        "input": 0.80, "cache_read": 0.08, "cache_write": 1.0,
        "output": 4.0, "reasoning": 4.0,
    },
    "claude-3-opus": {
        "input": 15.0, "cache_read": 1.50, "cache_write": 18.75,
        "output": 75.0, "reasoning": 75.0,
    },
}
DEFAULT_PRICING: dict[str, float] = {
    "input": 3.0, "cache_read": 0.30, "cache_write": 3.75,
    "output": 15.0, "reasoning": 15.0,
}


def get_pricing(model: str) -> dict[str, float]:
    """Get pricing for a model by prefix / substring match."""
    model_lower = model.lower()
    for pattern, pricing in MODEL_PRICING.items():
        if model_lower.startswith(pattern.lower()) or pattern.lower() in model_lower:
            return pricing
    return DEFAULT_PRICING


@dataclass
class UsageEvent:
    source: str
    timestamp: datetime
    session_id: str
    model: str
    uncached_input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0
    activity_messages: int = 0

    @property
    def total(self) -> int:
        return (
            self.uncached_input + self.cache_read + self.cache_write + self.output + self.reasoning
        )

    @property
    def cost(self) -> float:
        """Estimated cost in USD based on model pricing."""
        p = get_pricing(self.model)
        return (
            self.uncached_input * p["input"]
            + self.cache_read * p["cache_read"]
            + self.cache_write * p["cache_write"]
            + self.output * p["output"]
            + self.reasoning * p["reasoning"]
        ) / 1_000_000

    @property
    def cost_breakdown(self) -> dict[str, float]:
        """Cost broken down by token type (USD)."""
        p = get_pricing(self.model)
        return {
            "input": self.uncached_input * p["input"] / 1_000_000,
            "cache_read": self.cache_read * p["cache_read"] / 1_000_000,
            "cache_write": self.cache_write * p["cache_write"] / 1_000_000,
            "output": self.output * p["output"] / 1_000_000,
            "reasoning": self.reasoning * p["reasoning"] / 1_000_000,
            # What cache reads would have cost at full input price
            "cache_read_full_price": self.cache_read * p["input"] / 1_000_000,
        }


def fmt_int(value: int) -> str:
    return f"{value:,}"


def fmt_usd(value: float) -> str:
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 100:
        return f"${value:.1f}"
    if value >= 1:
        return f"${value:.2f}"
    if value >= 0.01:
        return f"${value:.3f}"
    return f"${value:.4f}"


def fmt_short(value: int) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def safe_read_lines(path: Path) -> list[str]:
    try:
        return path.read_text().splitlines()
    except Exception:
        return []


def parse_codex_events() -> list[UsageEvent]:
    session_events: dict[str, list[dict]] = defaultdict(list)

    for root in CODEX_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            session_id: str | None = None
            seen: dict[datetime, dict] = {}
            for line in safe_read_lines(path):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                if obj.get("type") == "session_meta":
                    session_id = str(payload.get("id") or session_id or path)
                    continue
                if obj.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                usage = info.get("total_token_usage")
                if not isinstance(usage, dict):
                    continue
                ts_raw = obj.get("timestamp")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except Exception:
                    continue
                current = {
                    "ts": ts,
                    "input": int(usage.get("input_tokens", 0) or 0),
                    "cached": int(usage.get("cached_input_tokens", 0) or 0),
                    "output": int(usage.get("output_tokens", 0) or 0),
                    "reasoning": int(usage.get("reasoning_output_tokens", 0) or 0),
                }
                prev = seen.get(ts)
                if prev is None or (
                    current["input"],
                    current["cached"],
                    current["output"],
                    current["reasoning"],
                ) > (
                    prev["input"],
                    prev["cached"],
                    prev["output"],
                    prev["reasoning"],
                ):
                    seen[ts] = current
            if not seen:
                continue
            key = session_id or str(path)
            session_events[key].extend(seen.values())

    events: list[UsageEvent] = []
    for session_id, rows in session_events.items():
        rows = sorted(rows, key=lambda item: item["ts"])
        baseline = {"input": 0, "cached": 0, "output": 0, "reasoning": 0}
        for row in rows:
            if row["ts"] < START_UTC:
                baseline = row
                continue
            uncached_input = max(
                0, row["input"] - baseline["input"] - (row["cached"] - baseline["cached"])
            )
            events.append(
                UsageEvent(
                    source="Codex",
                    timestamp=row["ts"],
                    session_id=session_id,
                    model="GPT-5 Codex",
                    uncached_input=uncached_input,
                    cache_read=max(0, row["cached"] - baseline["cached"]),
                    output=max(0, row["output"] - baseline["output"]),
                    reasoning=max(0, row["reasoning"] - baseline["reasoning"]),
                    activity_messages=1,
                )
            )
            baseline = row
    return events


def extract_claude_message_payloads(obj: dict) -> list[dict]:
    payloads: list[dict] = []
    direct = obj.get("message")
    if isinstance(direct, dict) and isinstance(direct.get("usage"), dict):
        payloads.append(
            {
                "message": direct,
                "timestamp": obj.get("timestamp"),
                "sessionId": obj.get("sessionId"),
            }
        )
    nested = obj.get("data")
    if isinstance(nested, dict):
        message_wrapper = nested.get("message")
        if isinstance(message_wrapper, dict):
            nested_message = message_wrapper.get("message")
            if isinstance(nested_message, dict) and isinstance(nested_message.get("usage"), dict):
                payloads.append(
                    {
                        "message": nested_message,
                        "timestamp": message_wrapper.get("timestamp") or obj.get("timestamp"),
                        "sessionId": obj.get("sessionId"),
                    }
                )
    return payloads


def parse_claude_events() -> list[UsageEvent]:
    dedup: dict[tuple[str, str], UsageEvent] = {}
    if not CLAUDE_ROOT.exists():
        return []

    for path in CLAUDE_ROOT.rglob("*.jsonl"):
        if path.name == "sessions-index.json":
            continue
        for line in safe_read_lines(path):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            for payload in extract_claude_message_payloads(obj):
                message = payload["message"]
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                ts_raw = payload.get("timestamp")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                except Exception:
                    continue
                if ts < START_UTC or ts > NOW_UTC:
                    continue
                msg_id = str(message.get("id") or obj.get("uuid") or ts_raw)
                session_id = str(payload.get("sessionId") or obj.get("sessionId") or path.stem)
                event = UsageEvent(
                    source="Claude",
                    timestamp=ts,
                    session_id=session_id,
                    model=str(message.get("model") or "Claude"),
                    uncached_input=int(usage.get("input_tokens", 0) or 0),
                    cache_read=int(usage.get("cache_read_input_tokens", 0) or 0),
                    cache_write=int(usage.get("cache_creation_input_tokens", 0) or 0),
                    output=int(usage.get("output_tokens", 0) or 0),
                    reasoning=0,
                    activity_messages=1,
                )
                key = (session_id, msg_id)
                prev = dedup.get(key)
                if prev is None or (
                    event.uncached_input,
                    event.cache_read,
                    event.cache_write,
                    event.output,
                ) > (
                    prev.uncached_input,
                    prev.cache_read,
                    prev.cache_write,
                    prev.output,
                ):
                    dedup[key] = event
    return list(dedup.values())


def parse_cursor_events() -> list[UsageEvent]:
    events: list[UsageEvent] = []
    if not CURSOR_ROOT.exists():
        return events

    for path in CURSOR_ROOT.rglob("*.jsonl"):
        if "agent-transcripts" not in str(path):
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=LOCAL_TZ).astimezone(
                timezone.utc
            )
        except Exception:
            continue
        if mtime < START_UTC or mtime > NOW_UTC:
            continue
        user_count = 0
        assistant_count = 0
        for line in safe_read_lines(path):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            role = obj.get("role")
            if role == "user":
                user_count += 1
            elif role == "assistant":
                assistant_count += 1
        if user_count == 0 and assistant_count == 0:
            continue
        session_id = path.stem
        events.append(
            UsageEvent(
                source="Cursor",
                timestamp=mtime,
                session_id=session_id,
                model="Cursor Agent",
                activity_messages=user_count + assistant_count,
            )
        )
    return events


def aggregate_dashboard(events: list[UsageEvent]) -> dict:
    source_totals: dict[str, dict] = defaultdict(
        lambda: {
            "total": 0,
            "uncached_input": 0,
            "cache_read": 0,
            "cache_write": 0,
            "output": 0,
            "reasoning": 0,
            "sessions": set(),
            "messages": 0,
            "models": Counter(),
            "token_capable": False,
            "cost": 0.0,
            "cost_input": 0.0,
            "cost_cache_read": 0.0,
            "cost_cache_write": 0.0,
            "cost_output": 0.0,
            "cost_reasoning": 0.0,
            "cost_cache_read_full_price": 0.0,
        }
    )
    daily = defaultdict(
        lambda: {
            "total": 0,
            "uncached_input": 0,
            "cache_read": 0,
            "cache_write": 0,
            "output": 0,
            "reasoning": 0,
            "messages": 0,
            "sources": defaultdict(int),
            "cost": 0.0,
            "cost_sources": defaultdict(float),
            "cost_input": 0.0,
            "cost_cache_read": 0.0,
            "cost_cache_write": 0.0,
            "cost_output": 0.0,
            "cost_reasoning": 0.0,
        }
    )
    hourly = defaultdict(lambda: defaultdict(int))
    weekday_hour = defaultdict(lambda: defaultdict(int))
    session_rollup = defaultdict(
        lambda: {
            "source": "",
            "session_id": "",
            "first_ts": None,
            "last_ts": None,
            "total": 0,
            "uncached_input": 0,
            "cache_read": 0,
            "cache_write": 0,
            "output": 0,
            "reasoning": 0,
            "messages": 0,
            "model": Counter(),
            "cost": 0.0,
        }
    )
    # Per-model cost tracking
    model_costs: dict[str, float] = defaultdict(float)

    for event in sorted(events, key=lambda item: item.timestamp):
        local_ts = event.timestamp.astimezone(LOCAL_TZ)
        day_key = local_ts.date().isoformat()
        total = event.total
        event_cost = event.cost
        cb = event.cost_breakdown

        src = source_totals[event.source]
        src["total"] += total
        src["uncached_input"] += event.uncached_input
        src["cache_read"] += event.cache_read
        src["cache_write"] += event.cache_write
        src["output"] += event.output
        src["reasoning"] += event.reasoning
        src["messages"] += event.activity_messages
        src["sessions"].add(event.session_id)
        src["models"][event.model] += max(1, event.activity_messages)
        if total > 0:
            src["token_capable"] = True
        src["cost"] += event_cost
        src["cost_input"] += cb["input"]
        src["cost_cache_read"] += cb["cache_read"]
        src["cost_cache_write"] += cb["cache_write"]
        src["cost_output"] += cb["output"]
        src["cost_reasoning"] += cb["reasoning"]
        src["cost_cache_read_full_price"] += cb["cache_read_full_price"]

        day = daily[day_key]
        day["total"] += total
        day["uncached_input"] += event.uncached_input
        day["cache_read"] += event.cache_read
        day["cache_write"] += event.cache_write
        day["output"] += event.output
        day["reasoning"] += event.reasoning
        day["messages"] += event.activity_messages
        day["sources"][event.source] += total
        day["cost"] += event_cost
        day["cost_sources"][event.source] += event_cost
        day["cost_input"] += cb["input"]
        day["cost_cache_read"] += cb["cache_read"]
        day["cost_cache_write"] += cb["cache_write"]
        day["cost_output"] += cb["output"]
        day["cost_reasoning"] += cb["reasoning"]

        hourly[local_ts.hour][event.source] += total
        weekday_hour[local_ts.weekday()][local_ts.hour] += total

        session = session_rollup[(event.source, event.session_id)]
        session["source"] = event.source
        session["session_id"] = event.session_id
        session["first_ts"] = session["first_ts"] or local_ts
        session["last_ts"] = local_ts
        session["total"] += total
        session["uncached_input"] += event.uncached_input
        session["cache_read"] += event.cache_read
        session["cache_write"] += event.cache_write
        session["output"] += event.output
        session["reasoning"] += event.reasoning
        session["messages"] += event.activity_messages
        session["model"][event.model] += max(1, event.activity_messages)
        session["cost"] += event_cost

        model_costs[event.model] += event_cost

    days = []
    current = START_LOCAL.date()
    running_total = 0
    running_cost = 0.0
    while current <= NOW_LOCAL.date():
        key = current.isoformat()
        entry = daily[key]
        running_total += entry["total"]
        running_cost += entry["cost"]
        days.append(
            {
                "date": key,
                "label": current.strftime("%m/%d"),
                "total": entry["total"],
                "uncached_input": entry["uncached_input"],
                "cache_read": entry["cache_read"],
                "cache_write": entry["cache_write"],
                "output": entry["output"],
                "reasoning": entry["reasoning"],
                "messages": entry["messages"],
                "sources": dict(entry["sources"]),
                "cumulative": running_total,
                "cost": round(entry["cost"], 4),
                "cost_sources": dict(entry["cost_sources"]),
                "cost_cumulative": round(running_cost, 4),
                "cost_input": round(entry["cost_input"], 4),
                "cost_cache_read": round(entry["cost_cache_read"], 4),
                "cost_cache_write": round(entry["cost_cache_write"], 4),
                "cost_output": round(entry["cost_output"], 4),
                "cost_reasoning": round(entry["cost_reasoning"], 4),
            }
        )
        current += timedelta(days=1)

    source_cards = []
    for source, values in sorted(source_totals.items()):
        top_model = values["models"].most_common(1)[0][0] if values["models"] else "-"
        source_cards.append(
            {
                "source": source,
                "total": values["total"],
                "uncached_input": values["uncached_input"],
                "cache_read": values["cache_read"],
                "cache_write": values["cache_write"],
                "output": values["output"],
                "reasoning": values["reasoning"],
                "sessions": len(values["sessions"]),
                "messages": values["messages"],
                "top_model": top_model,
                "token_capable": values["token_capable"],
                "cost": round(values["cost"], 4),
                "cost_input": round(values["cost_input"], 4),
                "cost_cache_read": round(values["cost_cache_read"], 4),
                "cost_cache_write": round(values["cost_cache_write"], 4),
                "cost_output": round(values["cost_output"], 4),
                "cost_reasoning": round(values["cost_reasoning"], 4),
                "cost_cache_read_full_price": round(values["cost_cache_read_full_price"], 4),
            }
        )

    top_sessions = []
    for values in session_rollup.values():
        top_sessions.append(
            {
                "source": values["source"],
                "session_id": values["session_id"],
                "total": values["total"],
                "uncached_input": values["uncached_input"],
                "cache_read": values["cache_read"],
                "cache_write": values["cache_write"],
                "output": values["output"],
                "reasoning": values["reasoning"],
                "messages": values["messages"],
                "first_local": values["first_ts"].isoformat(timespec="minutes")
                if values["first_ts"]
                else "-",
                "last_local": values["last_ts"].isoformat(timespec="minutes")
                if values["last_ts"]
                else "-",
                "minutes": round(
                    ((values["last_ts"] - values["first_ts"]).total_seconds() / 60)
                    if values["first_ts"] and values["last_ts"]
                    else 0,
                    1,
                ),
                "top_model": values["model"].most_common(1)[0][0] if values["model"] else "-",
                "cost": round(values["cost"], 4),
            }
        )

    grand_total = sum(day["total"] for day in days)
    uncached_total = sum(day["uncached_input"] for day in days)
    cache_read_total = sum(day["cache_read"] for day in days)
    cache_write_total = sum(day["cache_write"] for day in days)
    output_total = sum(day["output"] for day in days)
    reasoning_total = sum(day["reasoning"] for day in days)
    peak_day = max(days, key=lambda item: item["total"]) if days else None
    active_sessions = [s for s in top_sessions if s["total"] > 0]
    session_totals = [s["total"] for s in active_sessions]
    session_minutes = [s["minutes"] for s in active_sessions if s["minutes"] > 0]
    cache_ratio = ((cache_read_total + cache_write_total) / grand_total) if grand_total else 0.0
    token_capable_sources = [card for card in source_cards if card["token_capable"]]
    non_token_sources = [card for card in source_cards if not card["token_capable"]]

    # Cost aggregates
    grand_cost = sum(day["cost"] for day in days)
    cost_peak_day = max(days, key=lambda item: item["cost"]) if days else None
    session_costs = [s["cost"] for s in top_sessions if s["cost"] > 0]
    total_messages = sum(card["messages"] for card in token_capable_sources)
    cost_input_total = sum(day["cost_input"] for day in days)
    cost_cache_read_total = sum(day["cost_cache_read"] for day in days)
    cost_cache_write_total = sum(day["cost_cache_write"] for day in days)
    cost_output_total = sum(day["cost_output"] for day in days)
    cost_reasoning_total = sum(day["cost_reasoning"] for day in days)
    cost_cache_read_full_price_total = sum(
        card["cost_cache_read_full_price"] for card in source_cards
    )
    # Model cost breakdown list (sorted by cost desc)
    model_cost_list = sorted(
        [{"model": m, "cost": round(c, 4)} for m, c in model_costs.items() if c > 0],
        key=lambda x: x["cost"],
        reverse=True,
    )

    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    heatmap = []
    for weekday in range(7):
        heatmap.append(
            {
                "weekday": weekday_labels[weekday],
                "values": [weekday_hour[weekday][hour] for hour in range(24)],
            }
        )

    source_colors = {"Codex": "#E36B3F", "Claude": "#D4A373", "Cursor": "#4C6EF5"}
    jokes = []
    if cache_ratio > 0.75:
        jokes.append("缓存占比已经高到像你给模型办了年卡。")
    if peak_day and peak_day["total"] > 300_000_000:
        jokes.append("峰值日像在模型背上装了涡轮增压。")
    if non_token_sources:
        jokes.append("Cursor 的本地 transcript 很勤奋，但它没有把 token 小票乖乖留下。")

    return {
        "range": {
            "start_local": START_LOCAL.isoformat(timespec="minutes"),
            "end_local": NOW_LOCAL.isoformat(timespec="minutes"),
        },
        "totals": {
            "grand_total": grand_total,
            "uncached_input": uncached_total,
            "cache_read": cache_read_total,
            "cache_write": cache_write_total,
            "output": output_total,
            "reasoning": reasoning_total,
            "cache_ratio": cache_ratio,
            "session_count": sum(card["sessions"] for card in token_capable_sources),
            "token_capable_source_count": len(token_capable_sources),
            "average_per_day": round(grand_total / max(1, len(days))),
            "median_session_tokens": round(median(session_totals)) if session_totals else 0,
            "median_session_minutes": round(median(session_minutes), 1) if session_minutes else 0.0,
            "peak_day_label": peak_day["date"] if peak_day else "-",
            "peak_day_total": peak_day["total"] if peak_day else 0,
            "grand_cost": round(grand_cost, 2),
            "average_cost_per_day": round(grand_cost / max(1, len(days)), 2),
            "median_session_cost": round(median(session_costs), 4) if session_costs else 0.0,
            "cost_per_message": round(grand_cost / max(1, total_messages), 4),
            "cost_peak_day_label": cost_peak_day["date"] if cost_peak_day else "-",
            "cost_peak_day_total": round(cost_peak_day["cost"], 2) if cost_peak_day else 0,
            "cost_input": round(cost_input_total, 2),
            "cost_cache_read": round(cost_cache_read_total, 2),
            "cost_cache_write": round(cost_cache_write_total, 2),
            "cost_output": round(cost_output_total, 2),
            "cost_reasoning": round(cost_reasoning_total, 2),
            "cache_savings_usd": round(
                cost_cache_read_full_price_total - cost_cache_read_total, 2
            ),
            "cache_savings_ratio": round(
                (cost_cache_read_full_price_total - cost_cache_read_total)
                / max(0.01, cost_cache_read_full_price_total), 3
            ) if cost_cache_read_full_price_total > 0 else 0.0,
        },
        "source_cards": source_cards,
        "days": days,
        "top_sessions": sorted(top_sessions, key=lambda item: item["total"], reverse=True)[:14],
        "hourly": [{"hour": hour, **hourly[hour]} for hour in range(24)],
        "heatmap": heatmap,
        "source_colors": source_colors,
        "model_costs": model_cost_list,
        "jokes": jokes,
        "notes": {
            "cursor_has_tokens": any(
                card["source"] == "Cursor" and card["token_capable"] for card in source_cards
            ),
        },
    }


def _build_html_template() -> str:
    return r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Agent Usage Atlas</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0c0e14;--surface:rgba(255,255,255,.04);--surface-hover:rgba(255,255,255,.07);
  --border:rgba(255,255,255,.06);--border-light:rgba(255,255,255,.10);
  --text:#e8e4df;--text-secondary:rgba(255,255,255,.50);--text-muted:rgba(255,255,255,.32);
  --accent:#f0b866;--accent-dim:rgba(240,184,102,.15);
  --codex:#ff8a50;--claude:#ffd43b;--cursor:#748ffc;
  --uncached:#f4b183;--cache-read:#51cf66;--cache-write:#b197fc;--output:#74c0fc;--reason:#e599f7;
  --cost:#ff6b6b;--savings:#51cf66;
  --radius:20px;--radius-sm:14px;--radius-xs:10px;
  --shadow:0 8px 32px rgba(0,0,0,.4);
  --page:min(1520px,calc(100vw - 48px));
  --font:'Inter',-apple-system,'PingFang SC','Noto Sans SC',sans-serif;
}
html{background:var(--bg);color:var(--text);font-family:var(--font);-webkit-font-smoothing:antialiased}
body{
  min-height:100vh;
  background:
    radial-gradient(ellipse 80% 60% at 10% 0%,rgba(255,138,80,.08),transparent),
    radial-gradient(ellipse 60% 50% at 90% 10%,rgba(116,143,252,.06),transparent),
    radial-gradient(ellipse 70% 40% at 50% 100%,rgba(81,207,102,.04),transparent),
    var(--bg);
}
.page{width:var(--page);margin:0 auto;padding:32px 0 64px}
/* ---- grid helpers ---- */
.g{display:grid;gap:16px}
.g2{grid-template-columns:repeat(2,1fr)}
.g3{grid-template-columns:repeat(3,1fr)}
.g4{grid-template-columns:repeat(4,1fr)}
.g-wide{grid-template-columns:1.25fr .75fr}
.g-story{grid-template-columns:1.1fr .9fr}
.mt{margin-top:16px}
/* ---- panel ---- */
.p{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:24px;backdrop-filter:blur(24px);transition:border-color .2s;
}
.p:hover{border-color:var(--border-light)}
/* ---- hero ---- */
.hero-wrap{position:relative;overflow:hidden;padding:40px 36px}
.hero-wrap::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(255,138,80,.06),rgba(240,184,102,.04),transparent 70%);pointer-events:none}
.hero-wrap::after{content:'';position:absolute;right:-80px;bottom:-80px;width:320px;height:320px;border-radius:50%;background:radial-gradient(circle,rgba(240,184,102,.10),transparent 70%);pointer-events:none}
.eyebrow{display:flex;align-items:center;gap:8px;color:var(--accent);font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}
h1{font-size:clamp(36px,5.2vw,64px);font-weight:900;line-height:.96;letter-spacing:-.04em;margin:16px 0 0;max-width:14ch;background:linear-gradient(135deg,var(--text),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hero-copy{color:var(--text-secondary);line-height:1.7;font-size:15px;margin-top:18px;max-width:60ch;position:relative;z-index:1}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:20px;position:relative;z-index:1}
.chip{display:inline-flex;align-items:center;gap:7px;padding:8px 14px;border-radius:999px;background:rgba(255,255,255,.06);border:1px solid var(--border);color:var(--text-secondary);font-size:12px;font-weight:500;transition:background .2s}
.chip:hover{background:rgba(255,255,255,.10)}
.chip i{font-size:11px}
/* ---- side cards ---- */
.side{display:grid;gap:12px;align-content:start}
.sc{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:var(--radius-sm);padding:18px;transition:border-color .2s}
.sc:hover{border-color:var(--border-light)}
.sc .lbl{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--text-muted)}
.sc .val{font-size:28px;font-weight:800;letter-spacing:-.03em;margin-top:6px;color:var(--text)}
.sc .hint{font-size:12px;color:var(--text-secondary);line-height:1.55;margin-top:8px}
/* ---- section head ---- */
.sh{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:14px}
.sh h2{font-size:20px;font-weight:700;letter-spacing:-.02em}
.sh span{color:var(--text-muted);font-size:12px}
/* ---- source cards ---- */
.src{position:relative;overflow:hidden}
.src::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;border-radius:var(--radius) var(--radius) 0 0}
.src.codex::before{background:linear-gradient(90deg,var(--codex),transparent)}
.src.claude::before{background:linear-gradient(90deg,var(--claude),transparent)}
.src.cursor::before{background:linear-gradient(90deg,var(--cursor),transparent)}
.src .title{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:600;font-size:14px}
.pill{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);padding:4px 10px;border-radius:999px;border:1px solid var(--border);background:rgba(255,255,255,.03)}
.src .big{font-size:32px;font-weight:800;letter-spacing:-.03em}
.src .sub{font-size:11px;color:var(--text-muted);margin-top:2px}
.mg{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}
.mi{padding:10px 12px;border-radius:var(--radius-xs);background:rgba(255,255,255,.03);border:1px solid var(--border)}
.mi .k{font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted)}
.mi .v{font-size:15px;font-weight:700;margin-top:4px}
/* ---- cost cards ---- */
.cc{position:relative;overflow:hidden}
.cc::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.cc.accent::before{background:linear-gradient(90deg,var(--cost),transparent)}
.cc.save::before{background:linear-gradient(90deg,var(--savings),transparent)}
.cc .big{font-size:30px;font-weight:800;letter-spacing:-.03em;margin-top:8px}
/* ---- chart ---- */
.chart{width:100%;height:400px}
.chart.tall{height:460px}
.chart.short{height:320px}
.chart.sm{height:360px}
/* ---- legend ---- */
.legend{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.legend span{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--text-secondary)}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
/* ---- story ---- */
.story{display:grid;gap:10px}
.si{display:grid;grid-template-columns:20px 1fr;gap:10px;align-items:start;padding:12px 14px;background:rgba(255,255,255,.02);border:1px solid var(--border);border-radius:var(--radius-xs)}
.si i{color:var(--accent);margin-top:2px;font-size:13px}
.si div{font-size:13px;color:var(--text-secondary);line-height:1.65}
/* ---- notes ---- */
.nl{display:grid;gap:8px;margin-top:12px}
.note{border-left:3px solid var(--accent-dim);padding:8px 0 8px 14px;color:var(--text-secondary);font-size:12px;line-height:1.6}
/* ---- table ---- */
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:12px 10px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--text-muted);text-transform:uppercase;font-size:10px;font-weight:700;letter-spacing:.10em}
td{color:var(--text-secondary)}
tr:hover td{background:rgba(255,255,255,.02)}
.tiny{color:var(--text-muted);font-size:11px}
.footer{margin-top:16px;color:var(--text-muted);font-size:11px;line-height:1.65}
.footer code{background:rgba(255,255,255,.06);padding:2px 6px;border-radius:4px;font-size:11px}
/* ---- section divider ---- */
.divider{display:flex;align-items:center;gap:12px;margin:28px 0 20px;color:var(--text-muted);font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}
.divider::after{content:'';flex:1;height:1px;background:var(--border)}
/* ---- responsive ---- */
@media(max-width:1100px){.g2,.g3,.g4,.g-wide,.g-story{grid-template-columns:1fr}.page{width:calc(100vw - 24px);padding:16px 0 48px}.hero-wrap{padding:28px 24px}.p{padding:18px}}
</style>
</head>
<body>
<main class="page">
  <!-- Hero -->
  <section class="g g-wide">
    <article class="p hero-wrap">
      <div class="eyebrow"><i class="fa-solid fa-chart-line"></i> Agent Usage Atlas</div>
      <h1>三个 Agent 栈的联赛积分榜</h1>
      <p class="hero-copy" id="hero-copy"></p>
      <div class="chips" id="hero-chips"></div>
    </article>
    <aside class="side" id="summary-side"></aside>
  </section>

  <!-- Sources -->
  <div class="divider"><i class="fa-solid fa-layer-group"></i> Sources</div>
  <section class="g g3" id="source-cards"></section>

  <!-- Cost Overview -->
  <div class="divider"><i class="fa-solid fa-dollar-sign"></i> Cost Analysis</div>
  <section class="g g4" id="cost-cards"></section>
  <section class="g g-wide mt">
    <article class="p"><div class="sh"><h2>每日花费趋势</h2><span>按来源堆叠 + 累计花费线</span></div><div class="chart tall" id="daily-cost-chart"></div></article>
    <article class="p"><div class="sh"><h2>花费结构拆解</h2><span>钱花在哪种 Token 上</span></div><div class="chart tall" id="cost-breakdown-chart"></div></article>
  </section>
  <section class="g g2 mt">
    <article class="p"><div class="sh"><h2>模型花费排行</h2><span>哪些模型最烧钱</span></div><div class="chart sm" id="model-cost-chart"></div></article>
    <article class="p"><div class="sh"><h2>来源花费桑基图</h2><span>从来源流到各类花费</span></div><div class="chart sm" id="cost-sankey-chart"></div></article>
  </section>
  <section class="g g2 mt">
    <article class="p"><div class="sh"><h2>每日花费结构</h2><span>哪种 Token 最烧钱</span></div><div class="chart tall" id="daily-cost-type-chart"></div></article>
    <article class="p"><div class="sh"><h2>花费日历</h2><span>每天花了多少钱</span></div><div class="chart sm" id="cost-calendar-chart"></div></article>
  </section>

  <!-- Token Analytics -->
  <div class="divider"><i class="fa-solid fa-chart-bar"></i> Token Analytics</div>
  <section class="g g-story">
    <article class="p"><div class="sh"><h2>剧情梗概</h2><span>把数字翻译成人话</span></div><div class="story" id="story-list"></div></article>
    <article class="p"><div class="sh"><h2>来源玫瑰图</h2><span>体量 + 气质一起看</span></div><div class="chart short" id="rose-chart"></div></article>
  </section>
  <section class="g g-wide mt">
    <article class="p">
      <div class="sh"><h2>每日 Token 结构</h2><span>堆叠柱 + 累计线</span></div>
      <div class="chart tall" id="daily-chart"></div>
      <div class="legend">
        <span><i class="dot" style="background:var(--uncached)"></i>Uncached Input</span>
        <span><i class="dot" style="background:var(--cache-read)"></i>Cache Read</span>
        <span><i class="dot" style="background:var(--cache-write)"></i>Cache Write</span>
        <span><i class="dot" style="background:var(--output)"></i>Output + Reason</span>
      </div>
    </article>
    <article class="p"><div class="sh"><h2>Token 流向桑基图</h2><span>从来源流到各类 token 桶</span></div><div class="chart tall" id="sankey-chart"></div><div class="nl" id="source-notes"></div></article>
  </section>

  <!-- Activity Patterns -->
  <div class="divider"><i class="fa-solid fa-clock"></i> Activity Patterns</div>
  <section class="g g2">
    <article class="p"><div class="sh"><h2>活跃热区</h2><span>星期 × 小时，越深越忙</span></div><div class="chart tall" id="heatmap-chart"></div></article>
    <article class="p"><div class="sh"><h2>来源能力雷达</h2><span>体量、缓存、输出、活跃度四维比较</span></div><div class="chart tall" id="radar-chart"></div></article>
  </section>
  <section class="g g2 mt">
    <article class="p"><div class="sh"><h2>Token 日历</h2><span>把高峰日钉在日历上</span></div><div class="chart sm" id="calendar-chart"></div></article>
    <article class="p"><div class="sh"><h2>Timeline</h2><span>峰值、拐点与累计爬坡</span></div><div class="chart sm" id="timeline-chart"></div></article>
  </section>
  <section class="g g2 mt">
    <article class="p"><div class="sh"><h2>Session 气泡图</h2><span>x=时长, y=token, 气泡=缓存</span></div><div class="chart sm" id="bubble-chart"></div></article>
    <article class="p"><div class="sh"><h2>小时节奏图</h2><span>24 小时内谁最爱开工</span></div><div class="chart sm" id="tempo-chart"></div><div class="nl" id="tempo-notes"></div></article>
  </section>

  <!-- Sessions -->
  <div class="divider"><i class="fa-solid fa-list-ol"></i> Session Leaderboard</div>
  <section>
    <article class="p">
      <table id="session-table"></table>
      <div class="footer">
        数据源：Codex <code>~/.codex</code> 累计 usage 差值 · Claude <code>~/.claude/projects</code> 响应 usage 去重求和 · Cursor transcript 仅活动计数<br/>
        花费为基于公开 API 定价的估算值 · 图表渲染 <code>Apache ECharts</code>
      </div>
    </article>
  </section>
</main>

<script>
const data = __DATA__;
const charts = [];
const fmtInt = n => n.toLocaleString('en-US');
const fmtShort = n => { const a=Math.abs(n); return a>=1e9?(n/1e9).toFixed(2)+'B':a>=1e6?(n/1e6).toFixed(2)+'M':a>=1e3?(n/1e3).toFixed(1)+'K':String(n) };
const fmtPct = v => (v*100).toFixed(1)+'%';
const fmtUSD = v => { if(v>=1000) return '$'+v.toLocaleString('en-US',{maximumFractionDigits:0}); if(v>=100) return '$'+v.toFixed(1); if(v>=1) return '$'+v.toFixed(2); if(v>=0.01) return '$'+v.toFixed(3); return '$'+v.toFixed(4) };

const C = {Codex:'#ff8a50',Claude:'#ffd43b',Cursor:'#748ffc',uncached:'#f4b183',cacheRead:'#51cf66',cacheWrite:'#b197fc',output:'#74c0fc',reason:'#e599f7',cost:'#ff6b6b',savings:'#51cf66'};
const TX = 'rgba(255,255,255,.65)';
const AX = 'rgba(255,255,255,.06)';
const TT = () => ({textStyle:{color:TX,fontFamily:"Inter, -apple-system, PingFang SC, sans-serif"},tooltip:{backgroundColor:'rgba(20,20,28,.92)',borderColor:'rgba(255,255,255,.08)',borderWidth:1,textStyle:{color:'#e8e4df',fontSize:12}},animationDuration:700,animationEasing:'cubicOut'});
const IC = id => { const c=echarts.init(document.getElementById(id),null,{renderer:'canvas'}); charts.push(c); return c };

function renderHero(){
  const t=data.totals;
  document.getElementById('hero-copy').textContent=`统计窗口 ${data.range.start_local} → ${data.range.end_local}。Token 总处理量 ${fmtShort(t.grand_total)}，估算总花费 ${fmtUSD(t.grand_cost)}，缓存占 ${fmtPct(t.cache_ratio)}。你不是在聊天，你在给 Agent 排班。`;
  document.getElementById('hero-chips').innerHTML=[
    `<span class="chip"><i class="fa-solid fa-fire" style="color:var(--codex)"></i>${fmtShort(t.grand_total)} tokens</span>`,
    `<span class="chip"><i class="fa-solid fa-dollar-sign" style="color:var(--cost)"></i>${fmtUSD(t.grand_cost)} cost</span>`,
    `<span class="chip"><i class="fa-solid fa-database" style="color:var(--cache-read)"></i>${fmtPct(t.cache_ratio)} cached</span>`,
    `<span class="chip"><i class="fa-solid fa-bolt" style="color:var(--accent)"></i>peak ${t.peak_day_label}</span>`,
  ].join('');
  const cards=[
    {lbl:'Total Tokens',val:fmtShort(t.grand_total),hint:`日均 ${fmtShort(t.average_per_day)}，持续性工业作业。`},
    {lbl:'Estimated Cost',val:fmtUSD(t.grand_cost),hint:`日均 ${fmtUSD(t.average_cost_per_day)}，每条消息 ${fmtUSD(t.cost_per_message)}。`},
    {lbl:'Cache Stack',val:fmtShort(t.cache_read+t.cache_write),hint:`缓存占比 ${fmtPct(t.cache_ratio)}，重复上下文非常重。`},
    {lbl:'Median Session',val:fmtShort(t.median_session_tokens),hint:`时长 ${t.median_session_minutes} min，花费 ${fmtUSD(t.median_session_cost)}。`},
  ];
  document.getElementById('summary-side').innerHTML=cards.map(c=>`<div class="sc"><div class="lbl">${c.lbl}</div><div class="val">${c.val}</div><div class="hint">${c.hint}</div></div>`).join('');
}

function renderSourceCards(){
  document.getElementById('source-cards').innerHTML=data.source_cards.map(c=>{
    const cls=c.source.toLowerCase();
    const icon=c.source==='Codex'?'fa-terminal':c.source==='Claude'?'fa-feather-pointed':'fa-arrow-pointer';
    return `<article class="p src ${cls}">
      <div class="title"><span><i class="fa-solid ${icon}"></i> ${c.source}</span><span class="pill">${c.token_capable?'token-tracked':'activity-only'}</span></div>
      <div class="big">${c.token_capable?fmtShort(c.total):fmtInt(c.messages)}</div>
      <div class="sub">${c.token_capable?'tracked tokens':'transcript messages'}</div>
      <div class="mg">
        <div class="mi"><div class="k">Sessions</div><div class="v">${fmtInt(c.sessions)}</div></div>
        <div class="mi"><div class="k">Est. Cost</div><div class="v" style="color:var(--cost)">${c.token_capable?fmtUSD(c.cost):'-'}</div></div>
        <div class="mi"><div class="k">Top Model</div><div class="v" style="font-size:12px">${c.top_model}</div></div>
        <div class="mi"><div class="k">Cache Read</div><div class="v">${c.token_capable?fmtShort(c.cache_read):'-'}</div></div>
      </div></article>`}).join('');
}

function renderCostCards(){
  const t=data.totals;
  const items=[
    {lbl:'Total Est. Cost',val:fmtUSD(t.grand_cost),sub:`${data.days.length} 天累计`,cls:'accent',color:'var(--cost)'},
    {lbl:'Daily Average',val:fmtUSD(t.average_cost_per_day),sub:`峰值 ${t.cost_peak_day_label}: ${fmtUSD(t.cost_peak_day_total)}`,cls:'accent',color:'var(--cost)'},
    {lbl:'Cost / Message',val:fmtUSD(t.cost_per_message),sub:`中位 Session ${fmtUSD(t.median_session_cost)}`,cls:'accent',color:'var(--cost)'},
    {lbl:'Cache Savings',val:fmtUSD(t.cache_savings_usd),sub:`省了 ${fmtPct(t.cache_savings_ratio)}，实付 ${fmtUSD(t.cost_cache_read)} vs 原价 ${fmtUSD(t.cost_cache_read+t.cache_savings_usd)}`,cls:'save',color:'var(--savings)'},
  ];
  document.getElementById('cost-cards').innerHTML=items.map(c=>`<article class="p cc ${c.cls}"><div class="title" style="font-size:12px;font-weight:600"><i class="fa-solid ${c.cls==='save'?'fa-leaf':'fa-dollar-sign'}" style="color:${c.color};margin-right:6px"></i>${c.lbl}</div><div class="big" style="color:${c.color}">${c.val}</div><div class="tiny" style="margin-top:6px">${c.sub}</div></article>`).join('');
}

function renderStory(){
  const s=Object.fromEntries(data.source_cards.map(c=>[c.source,c])),t=data.totals;
  const items=[
    {icon:'fa-bolt',text:`主力是 Codex，${fmtShort(s.Codex.total)} token，花费 ${fmtUSD(s.Codex.cost)}，占总花费 ${fmtPct(s.Codex.cost/Math.max(.01,t.grand_cost))}。`},
    {icon:'fa-feather-pointed',text:`Claude 累计 ${fmtShort(s.Claude.total)} token，花费 ${fmtUSD(s.Claude.cost)}。用量精准，精锐突击队。`},
    {icon:'fa-dollar-sign',text:`总花费 ${fmtUSD(t.grand_cost)}，日均 ${fmtUSD(t.average_cost_per_day)}。输出 token 花费 ${fmtUSD(t.cost_output+t.cost_reasoning)}，占大头。`},
    {icon:'fa-database',text:`缓存读写 ${fmtShort(t.cache_read+t.cache_write)}，缓存读花费仅 ${fmtUSD(t.cost_cache_read)}，省了 ${fmtPct(t.cache_savings_ratio)}。`},
    {icon:'fa-arrow-pointer',text:`Cursor ${fmtInt(s.Cursor.sessions)} session / ${fmtInt(s.Cursor.messages)} 条消息，token 小票没留下。`},
  ];
  document.getElementById('story-list').innerHTML=items.map(i=>`<div class="si"><i class="fa-solid ${i.icon}"></i><div>${i.text}</div></div>`).join('');
}

function renderDailyCostChart(){
  const c=IC('daily-cost-chart'),srcs=[...new Set(data.source_cards.filter(s=>s.token_capable).map(s=>s.source))];
  c.setOption({...TT(),legend:{top:6,textStyle:{color:TX}},grid:{top:58,left:68,right:68,bottom:44},tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:v=>fmtUSD(v)},
    xAxis:{type:'category',data:data.days.map(d=>d.label),axisLine:{lineStyle:{color:AX}},axisTick:{show:false},axisLabel:{color:TX}},
    yAxis:[{type:'value',name:'Daily ($)',nameTextStyle:{color:TX},splitLine:{lineStyle:{color:AX}},axisLabel:{color:TX,formatter:v=>fmtUSD(v)}},{type:'value',name:'Cumulative ($)',nameTextStyle:{color:TX},splitLine:{show:false},axisLabel:{color:TX,formatter:v=>fmtUSD(v)}}],
    series:[...srcs.map(s=>({name:s,type:'bar',stack:'c',itemStyle:{color:C[s]||'#999',borderRadius:[6,6,0,0]},data:data.days.map(d=>+(d.cost_sources[s]||0).toFixed(4))})),
      {name:'Cumulative',type:'line',yAxisIndex:1,smooth:true,symbolSize:6,lineStyle:{width:3,color:'rgba(255,255,255,.7)'},itemStyle:{color:'#fff'},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(255,255,255,.08)'},{offset:1,color:'rgba(255,255,255,0)'}]}},data:data.days.map(d=>d.cost_cumulative)}]
  });
}

function renderCostBreakdownChart(){
  const c=IC('cost-breakdown-chart'),t=data.totals;
  const items=[{name:'Uncached Input',value:t.cost_input,color:C.uncached},{name:'Cache Read',value:t.cost_cache_read,color:C.cacheRead},{name:'Cache Write',value:t.cost_cache_write,color:C.cacheWrite},{name:'Output',value:t.cost_output,color:C.output},{name:'Reasoning',value:t.cost_reasoning,color:C.reason}].filter(i=>i.value>0);
  c.setOption({...TT(),tooltip:{formatter:({name,value,percent})=>`${name}<br/>${fmtUSD(value)} (${percent}%)`},legend:{bottom:0,textStyle:{color:TX}},
    series:[{type:'pie',radius:['40%','74%'],center:['50%','44%'],avoidLabelOverlap:true,itemStyle:{borderRadius:8,borderColor:'rgba(12,14,20,.8)',borderWidth:3},
      label:{color:TX,formatter:({name,percent})=>`${name}\n${percent}%`},emphasis:{label:{fontWeight:'bold',fontSize:14}},
      data:items.map(i=>({name:i.name,value:+i.value.toFixed(4),itemStyle:{color:i.color}}))}]
  });
}

function renderModelCostChart(){
  const c=IC('model-cost-chart'),m=data.model_costs.slice(0,10);
  const mc=['#ff6b6b','#ff8a50','#ffa94d','#ffd43b','#a9e34b','#51cf66','#74c0fc','#748ffc','#b197fc','#e599f7'];
  c.setOption({...TT(),grid:{top:24,left:160,right:60,bottom:24},tooltip:{valueFormatter:v=>fmtUSD(v)},
    xAxis:{type:'value',splitLine:{lineStyle:{color:AX}},axisLabel:{color:TX,formatter:v=>fmtUSD(v)}},
    yAxis:{type:'category',data:m.map(x=>x.model).reverse(),axisLine:{lineStyle:{color:AX}},axisTick:{show:false},axisLabel:{color:TX,width:140,overflow:'truncate',fontSize:11}},
    series:[{type:'bar',barMaxWidth:22,data:m.map((x,i)=>({value:+x.cost.toFixed(4),itemStyle:{color:mc[i%mc.length],borderRadius:[0,6,6,0]}})).reverse(),label:{show:true,position:'right',color:TX,formatter:({value})=>fmtUSD(value),fontSize:11}}]
  });
}

function renderCostSankey(){
  const c=IC('cost-sankey-chart'),ts=data.source_cards.filter(s=>s.token_capable);
  const bk=[{key:'cost_input',name:'Input Cost',color:C.uncached},{key:'cost_cache_read',name:'Cache Read',color:C.cacheRead},{key:'cost_cache_write',name:'Cache Write',color:C.cacheWrite},{key:'cost_output',name:'Output',color:C.output},{key:'cost_reasoning',name:'Reasoning',color:C.reason}];
  c.setOption({...TT(),tooltip:{valueFormatter:v=>fmtUSD(v)},series:[{type:'sankey',left:8,right:8,top:24,bottom:12,nodeWidth:18,nodeGap:14,nodeAlign:'justify',
    lineStyle:{color:'gradient',curveness:.45,opacity:.3},label:{color:'#fff',position:'inside',fontWeight:600,fontSize:11},
    data:[...ts.map(s=>({name:s.source,itemStyle:{color:C[s.source]||'#999'}})),...bk.map(b=>({name:b.name,itemStyle:{color:b.color}}))],
    links:ts.flatMap(s=>bk.filter(b=>(s[b.key]||0)>.001).map(b=>({source:s.source,target:b.name,value:+s[b.key].toFixed(4)})))}]});
}

function renderDailyCostTypeChart(){
  const c=IC('daily-cost-type-chart');
  c.setOption({...TT(),legend:{top:6,textStyle:{color:TX}},grid:{top:58,left:68,right:24,bottom:44},tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:v=>fmtUSD(v)},
    xAxis:{type:'category',data:data.days.map(d=>d.label),axisLine:{lineStyle:{color:AX}},axisTick:{show:false},axisLabel:{color:TX}},
    yAxis:{type:'value',name:'Cost ($)',nameTextStyle:{color:TX},splitLine:{lineStyle:{color:AX}},axisLabel:{color:TX,formatter:v=>fmtUSD(v)}},
    series:[
      {name:'Input',type:'bar',stack:'c',itemStyle:{color:C.uncached},data:data.days.map(d=>+d.cost_input.toFixed(4))},
      {name:'Cache Read',type:'bar',stack:'c',itemStyle:{color:C.cacheRead},data:data.days.map(d=>+d.cost_cache_read.toFixed(4))},
      {name:'Cache Write',type:'bar',stack:'c',itemStyle:{color:C.cacheWrite},data:data.days.map(d=>+d.cost_cache_write.toFixed(4))},
      {name:'Output',type:'bar',stack:'c',itemStyle:{color:C.output},data:data.days.map(d=>+d.cost_output.toFixed(4))},
      {name:'Reasoning',type:'bar',stack:'c',itemStyle:{color:C.reason,borderRadius:[6,6,0,0]},data:data.days.map(d=>+d.cost_reasoning.toFixed(4))}]
  });
}

function renderCostCalendar(){
  const c=IC('cost-calendar-chart'),dd=data.days.map(d=>[d.date,+d.cost.toFixed(2)]),mx=Math.max(...data.days.map(d=>d.cost),.01);
  c.setOption({...TT(),tooltip:{formatter:({value})=>`${value[0]}<br/>${fmtUSD(value[1])}`},
    visualMap:{min:0,max:mx,orient:'horizontal',left:'center',bottom:8,textStyle:{color:TX},inRange:{color:['rgba(255,255,255,.04)','#5c3a1e','#c0392b','#ff6b6b']}},
    calendar:{top:28,left:24,right:24,cellSize:['auto',22],range:[data.range.start_local.slice(0,10),data.range.end_local.slice(0,10)],yearLabel:{show:false},monthLabel:{color:TX,margin:14},dayLabel:{color:TX,firstDay:1},splitLine:{lineStyle:{color:AX}},itemStyle:{borderWidth:3,borderColor:' var(--bg)',color:'rgba(255,255,255,.03)'}},
    series:[{type:'heatmap',coordinateSystem:'calendar',data:dd}]});
}

function renderRoseChart(){
  const c=IC('rose-chart');
  c.setOption({...TT(),legend:{bottom:0,textStyle:{color:TX}},series:[{type:'pie',radius:['24%','74%'],center:['50%','46%'],roseType:'radius',
    itemStyle:{borderRadius:8,borderColor:'rgba(12,14,20,.8)',borderWidth:3},label:{color:TX,formatter:({name,percent})=>`${name}\n${percent}%`},
    data:data.source_cards.map(s=>({name:s.source,value:s.token_capable?s.total:Math.max(s.messages,1),itemStyle:{color:C[s.source]||'#999'}}))}]});
}

function renderDailyChart(){
  const c=IC('daily-chart');
  c.setOption({...TT(),legend:{top:6,textStyle:{color:TX}},grid:{top:58,left:60,right:60,bottom:44},tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
    xAxis:{type:'category',data:data.days.map(d=>d.label),axisLine:{lineStyle:{color:AX}},axisTick:{show:false},axisLabel:{color:TX}},
    yAxis:[{type:'value',name:'Daily Tokens',nameTextStyle:{color:TX},splitLine:{lineStyle:{color:AX}},axisLabel:{color:TX,formatter:v=>fmtShort(v)}},{type:'value',name:'Cumulative',nameTextStyle:{color:TX},splitLine:{show:false},axisLabel:{color:TX,formatter:v=>fmtShort(v)}}],
    series:[
      {name:'Uncached Input',type:'bar',stack:'d',itemStyle:{color:C.uncached,borderRadius:[6,6,0,0]},data:data.days.map(d=>d.uncached_input)},
      {name:'Cache Read',type:'bar',stack:'d',itemStyle:{color:C.cacheRead,borderRadius:[6,6,0,0]},data:data.days.map(d=>d.cache_read)},
      {name:'Cache Write',type:'bar',stack:'d',itemStyle:{color:C.cacheWrite,borderRadius:[6,6,0,0]},data:data.days.map(d=>d.cache_write)},
      {name:'Output+Reason',type:'bar',stack:'d',itemStyle:{color:C.output,borderRadius:[6,6,0,0]},data:data.days.map(d=>d.output+d.reasoning)},
      {name:'Cumulative',type:'line',yAxisIndex:1,smooth:true,symbolSize:6,lineStyle:{width:3,color:'rgba(255,255,255,.7)'},itemStyle:{color:'#fff'},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(255,255,255,.06)'},{offset:1,color:'transparent'}]}},data:data.days.map(d=>d.cumulative)}]
  });
}

function renderSankey(){
  const c=IC('sankey-chart'),ts=data.source_cards.filter(s=>s.token_capable);
  const bk=[{key:'uncached_input',name:'Uncached Input',color:C.uncached},{key:'cache_read',name:'Cache Read',color:C.cacheRead},{key:'cache_write',name:'Cache Write',color:C.cacheWrite},{key:'output',name:'Output',color:C.output},{key:'reasoning',name:'Reasoning',color:C.reason}];
  c.setOption({...TT(),series:[{type:'sankey',left:8,right:8,top:24,bottom:12,nodeWidth:18,nodeGap:14,nodeAlign:'justify',
    lineStyle:{color:'gradient',curveness:.45,opacity:.28},label:{color:'#fff',position:'inside',fontWeight:600,fontSize:11},
    data:[...ts.map(s=>({name:s.source,itemStyle:{color:C[s.source]||'#999'}})),...bk.map(b=>({name:b.name,itemStyle:{color:b.color}}))],
    links:ts.flatMap(s=>bk.filter(b=>(s[b.key]||0)>0).map(b=>({source:s.source,target:b.name,value:s[b.key]})))}]});
  document.getElementById('source-notes').innerHTML=[
    ...ts.map(s=>`<div class="note" style="border-left-color:${C[s.source]||'#999'}">${s.source} 主力 ${s.top_model}，${fmtShort(s.total)} token / ${fmtUSD(s.cost)}</div>`),
    ...data.jokes.map(t=>`<div class="note">${t}</div>`)].join('');
}

function renderHeatmap(){
  const c=IC('heatmap-chart'),h=[];
  data.heatmap.forEach((r,y)=>r.values.forEach((v,x)=>h.push([x,y,v])));
  c.setOption({...TT(),grid:{top:44,left:70,right:24,bottom:34},
    xAxis:{type:'category',data:Array.from({length:24},(_,i)=>`${i}`),splitArea:{show:true,areaStyle:{color:['rgba(255,255,255,.02)','rgba(255,255,255,.01)']}},axisLine:{lineStyle:{color:AX}},axisTick:{show:false}},
    yAxis:{type:'category',data:data.heatmap.map(r=>r.weekday),splitArea:{show:true,areaStyle:{color:['rgba(255,255,255,.02)','rgba(255,255,255,.01)']}},axisLine:{lineStyle:{color:AX}},axisTick:{show:false}},
    visualMap:{min:0,max:Math.max(...h.map(i=>i[2]),1),orient:'horizontal',left:'center',bottom:0,calculable:true,inRange:{color:['rgba(255,255,255,.03)','#5c3a1e','#ff8a50','#ffd43b']},textStyle:{color:TX}},
    series:[{type:'heatmap',data:h,itemStyle:{borderRadius:6,borderColor:'var(--bg)',borderWidth:3}}]
  });
}

function renderRadar(){
  const c=IC('radar-chart'),srcs=data.source_cards.filter(s=>s.token_capable);
  c.setOption({...TT(),legend:{bottom:0,textStyle:{color:TX}},
    radar:{radius:'62%',center:['50%','46%'],splitNumber:5,axisName:{color:TX,fontSize:11},splitLine:{lineStyle:{color:AX}},splitArea:{areaStyle:{color:['rgba(255,255,255,.02)','rgba(255,255,255,.01)']}},
      indicator:[{name:'Total',max:Math.max(...srcs.map(s=>s.total),1)},{name:'Cache',max:Math.max(...srcs.map(s=>s.cache_read+s.cache_write),1)},{name:'Output',max:Math.max(...srcs.map(s=>s.output+s.reasoning),1)},{name:'Sessions',max:Math.max(...srcs.map(s=>s.sessions),1)}]},
    series:[{type:'radar',symbol:'circle',symbolSize:6,areaStyle:{opacity:.10},lineStyle:{width:2},
      data:srcs.map(s=>({name:s.source,value:[s.total,s.cache_read+s.cache_write,s.output+s.reasoning,s.sessions],itemStyle:{color:C[s.source]},lineStyle:{color:C[s.source]},areaStyle:{color:C[s.source],opacity:.08}}))}]
  });
}

function renderCalendar(){
  const c=IC('calendar-chart'),dd=data.days.map(d=>[d.date,d.total]);
  c.setOption({...TT(),tooltip:{formatter:({value})=>`${value[0]}<br/>${fmtInt(value[1])} tokens`},
    visualMap:{min:0,max:Math.max(...data.days.map(d=>d.total),1),orient:'horizontal',left:'center',bottom:8,textStyle:{color:TX},inRange:{color:['rgba(255,255,255,.03)','#3a4a2e','#51cf66','#a9e34b']}},
    calendar:{top:28,left:24,right:24,cellSize:['auto',22],range:[data.range.start_local.slice(0,10),data.range.end_local.slice(0,10)],yearLabel:{show:false},monthLabel:{color:TX,margin:14},dayLabel:{color:TX,firstDay:1},splitLine:{lineStyle:{color:AX}},itemStyle:{borderWidth:3,borderColor:'var(--bg)',color:'rgba(255,255,255,.03)'}},
    series:[{type:'heatmap',coordinateSystem:'calendar',data:dd}]});
}

function renderTimeline(){
  const c=IC('timeline-chart'),pk=[...data.days].filter(d=>d.total>0).sort((a,b)=>b.total-a.total).slice(0,4).sort((a,b)=>a.date.localeCompare(b.date));
  c.setOption({...TT(),legend:{top:4,textStyle:{color:TX}},grid:{top:54,left:56,right:24,bottom:46},tooltip:{trigger:'axis'},
    xAxis:{type:'category',data:data.days.map(d=>d.label),axisLine:{lineStyle:{color:AX}},axisTick:{show:false},axisLabel:{color:TX}},
    yAxis:[{type:'value',name:'Daily',splitLine:{lineStyle:{color:AX}},axisLabel:{color:TX,formatter:v=>fmtShort(v)},nameTextStyle:{color:TX}},{type:'value',name:'Cumulative',splitLine:{show:false},axisLabel:{color:TX,formatter:v=>fmtShort(v)},nameTextStyle:{color:TX}}],
    series:[{name:'Daily Total',type:'bar',barMaxWidth:24,itemStyle:{color:'rgba(255,138,80,.35)',borderRadius:[6,6,0,0]},data:data.days.map(d=>d.total)},
      {name:'Cumulative',type:'line',yAxisIndex:1,smooth:true,symbolSize:6,lineStyle:{width:3,color:'rgba(255,255,255,.7)'},itemStyle:{color:'#fff'},data:data.days.map(d=>d.cumulative),
        markPoint:{symbol:'pin',symbolSize:38,label:{color:'#fff',fontSize:10,formatter:({value})=>fmtShort(value)},itemStyle:{color:C.Codex},data:pk.map(d=>({name:d.label,coord:[d.label,d.cumulative],value:d.total}))}}]
  });
}

function renderBubble(){
  const c=IC('bubble-chart'),rows=data.top_sessions.slice(0,24);
  c.setOption({...TT(),grid:{top:30,left:62,right:24,bottom:54},
    xAxis:{name:'Minutes',splitLine:{lineStyle:{color:AX}},axisLabel:{color:TX},nameTextStyle:{color:TX}},
    yAxis:{name:'Total Tokens',splitLine:{lineStyle:{color:AX}},axisLabel:{color:TX,formatter:v=>fmtShort(v)},nameTextStyle:{color:TX}},
    series:['Codex','Claude'].map(s=>({name:s,type:'scatter',
      data:rows.filter(r=>r.source===s).map(r=>({value:[r.minutes||1,r.total,Math.max(8,Math.sqrt(r.cache_read+r.cache_write)/180)],session:r.session_id})),
      symbolSize:v=>v[2],itemStyle:{color:C[s],opacity:.8}}))
  });
}

function renderTempo(){
  const c=IC('tempo-chart');
  c.setOption({...TT(),legend:{top:4,textStyle:{color:TX}},grid:{top:52,left:56,right:24,bottom:46},
    xAxis:{type:'category',data:data.hourly.map(r=>`${r.hour}`),axisLine:{lineStyle:{color:AX}},axisTick:{show:false},axisLabel:{color:TX}},
    yAxis:{type:'value',splitLine:{lineStyle:{color:AX}},axisLabel:{color:TX,formatter:v=>fmtShort(v)}},
    series:['Codex','Claude','Cursor'].map(s=>({name:s,type:s==='Cursor'?'line':'bar',smooth:s==='Cursor',barMaxWidth:18,itemStyle:{color:C[s]||'#999'},lineStyle:{width:2,color:C[s]||'#999'},data:data.hourly.map(r=>r[s]||0)}))
  });
  const hot=[...data.hourly].map(r=>({hour:r.hour,total:(r.Codex||0)+(r.Claude||0)+(r.Cursor||0)})).sort((a,b)=>b.total-a.total)[0];
  document.getElementById('tempo-notes').innerHTML=[
    `<div class="note">最热小时 ${hot.hour}:00，要么在写代码，要么在让 agent 写更多代码。</div>`,
    `<div class="note">峰值日 ${data.totals.peak_day_label} 共 ${fmtShort(data.totals.peak_day_total)} token。</div>`].join('');
}

function renderSessionTable(){
  document.getElementById('session-table').innerHTML=`
    <thead><tr><th>Source</th><th>Session</th><th>Tokens</th><th>Cost</th><th>Cache</th><th>Model</th><th>Window</th></tr></thead>
    <tbody>${data.top_sessions.map(r=>{
      const sh=r.total?(r.cache_read+r.cache_write)/r.total:0;
      return `<tr><td><strong style="color:var(--text)">${r.source}</strong><div class="tiny">${fmtInt(r.messages)} events</div></td><td><strong style="color:var(--text)">${r.session_id.slice(0,10)}…</strong><div class="tiny">${r.minutes} min</div></td><td>${fmtShort(r.total)}</td><td style="color:var(--cost);font-weight:700">${fmtUSD(r.cost)}</td><td>${fmtPct(sh)}</td><td style="font-size:11px">${r.top_model}</td><td><div style="font-size:11px">${r.first_local}</div><div class="tiny">→ ${r.last_local}</div></td></tr>`}).join('')}</tbody>`;
}

renderHero();renderSourceCards();renderCostCards();renderStory();
renderDailyCostChart();renderCostBreakdownChart();renderModelCostChart();renderCostSankey();renderDailyCostTypeChart();renderCostCalendar();
renderRoseChart();renderDailyChart();renderSankey();renderHeatmap();renderRadar();renderCalendar();renderTimeline();renderBubble();renderTempo();renderSessionTable();
requestAnimationFrame(()=>{charts.forEach(c=>c.resize());setTimeout(()=>charts.forEach(c=>c.resize()),100)});
window.addEventListener('resize',()=>charts.forEach(c=>c.resize()));
</script>
</body>
</html>"""


def build_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return _build_html_template().replace("__DATA__", payload)


def main() -> None:
    events = parse_codex_events() + parse_claude_events() + parse_cursor_events()
    dashboard = aggregate_dashboard(events)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_html(dashboard))
    print(f"Wrote dashboard to {OUTPUT_PATH}")
    print(f"Tracked total tokens: {fmt_int(dashboard['totals']['grand_total'])}")
    print(f"Estimated total cost: {fmt_usd(dashboard['totals']['grand_cost'])}")
    print("Source summary:")
    for card in dashboard["source_cards"]:
        print(
            f"  - {card['source']}: total={fmt_int(card['total'])} "
            f"cost={fmt_usd(card['cost'])} "
            f"sessions={card['sessions']} messages={card['messages']} token_capable={card['token_capable']}"
        )


if __name__ == "__main__":
    main()
