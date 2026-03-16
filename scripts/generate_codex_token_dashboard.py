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


def fmt_int(value: int) -> str:
    return f"{value:,}"


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
        }
    )

    for event in sorted(events, key=lambda item: item.timestamp):
        local_ts = event.timestamp.astimezone(LOCAL_TZ)
        day_key = local_ts.date().isoformat()
        total = event.total

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

        day = daily[day_key]
        day["total"] += total
        day["uncached_input"] += event.uncached_input
        day["cache_read"] += event.cache_read
        day["cache_write"] += event.cache_write
        day["output"] += event.output
        day["reasoning"] += event.reasoning
        day["messages"] += event.activity_messages
        day["sources"][event.source] += total

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

    days = []
    current = START_LOCAL.date()
    running_total = 0
    while current <= NOW_LOCAL.date():
        key = current.isoformat()
        entry = daily[key]
        running_total += entry["total"]
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
        },
        "source_cards": source_cards,
        "days": days,
        "top_sessions": sorted(top_sessions, key=lambda item: item["total"], reverse=True)[:14],
        "hourly": [{"hour": hour, **hourly[hour]} for hour in range(24)],
        "heatmap": heatmap,
        "source_colors": source_colors,
        "jokes": jokes,
        "notes": {
            "cursor_has_tokens": any(
                card["source"] == "Cursor" and card["token_capable"] for card in source_cards
            ),
        },
    }


def build_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Agent Usage Atlas</title>
  <link rel="preconnect" href="https://cdnjs.cloudflare.com" />
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css" />
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js"></script>
  <style>
    :root {
      --bg: #f6f1e8;
      --panel: rgba(255, 250, 244, 0.86);
      --ink: #261f1a;
      --muted: #6d645b;
      --line: rgba(38,31,26,.08);
      --codex: #e36b3f;
      --claude: #d5a476;
      --cursor: #4c6ef5;
      --cache-read: #2f9e78;
      --cache-write: #7e57c2;
      --uncached: #f4b183;
      --output: #3653b3;
      --reason: #9153c5;
      --shadow: 0 22px 56px rgba(38,31,26,.09);
      --radius-xl: 28px;
      --radius-lg: 22px;
      --page: min(1440px, calc(100vw - 28px));
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 0% 0%, rgba(227,107,63,.14), transparent 24%),
        radial-gradient(circle at 100% 12%, rgba(76,110,245,.12), transparent 24%),
        radial-gradient(circle at 50% 100%, rgba(47,158,120,.08), transparent 30%),
        linear-gradient(180deg, #f8f3eb 0%, #f4efe7 35%, #f7f2ea 100%);
      font-family: ui-rounded, "SF Pro Display", "PingFang SC", sans-serif;
      min-height: 100vh;
    }
    .page { width: var(--page); margin: 18px auto 48px; }
    .hero, .two-col, .story-strip, .subgrid, .three-col {
      display: grid;
      gap: 20px;
    }
    .hero { grid-template-columns: 1.3fr .9fr; }
    .story-strip { grid-template-columns: 1.15fr .85fr; margin-top: 20px; }
    .two-col { grid-template-columns: 1.18fr .82fr; margin-top: 20px; }
    .subgrid { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 20px; }
    .three-col { grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 20px; }
    .panel {
      background: var(--panel);
      border: 1px solid rgba(255,255,255,.55);
      border-radius: var(--radius-xl);
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }
    .hero-main, .section, .side { padding: 24px; }
    .hero-main { position: relative; overflow: hidden; }
    .hero-main:after {
      content: "";
      position: absolute;
      width: 260px;
      height: 260px;
      right: -60px;
      bottom: -80px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(213,164,118,.22), transparent 70%);
    }
    .eyebrow { display: inline-flex; align-items: center; gap: 10px; color: var(--muted); letter-spacing: .12em; text-transform: uppercase; font-size: 12px; }
    h1 { margin: 14px 0 10px; max-width: 10ch; font-size: clamp(40px, 6vw, 72px); line-height: .94; letter-spacing: -.05em; }
    .hero-copy { max-width: 60ch; color: #433a33; line-height: 1.7; font-size: 16px; margin-top: 18px; }
    .chip-row, .legend { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }
    .chip, .legend span {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,.72);
      border: 1px solid var(--line);
      color: #4f4741;
      font-size: 13px;
    }
    .side { display: grid; gap: 14px; }
    .side-card, .source-card, .story-item {
      background: rgba(255,255,255,.64);
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      padding: 16px;
    }
    .side-card .label, .mini .k {
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .08em;
      font-size: 11px;
    }
    .side-card .value, .source-card .big { font-size: 30px; font-weight: 800; letter-spacing: -.04em; margin-top: 6px; }
    .side-card .hint { color: #504943; line-height: 1.55; font-size: 13px; margin-top: 8px; }
    .section-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 16px; }
    .section-head h2 { margin: 0; font-size: 24px; letter-spacing: -.03em; }
    .section-head span { color: var(--muted); font-size: 13px; }
    .chart { width: 100%; min-height: 360px; }
    .chart.tall { min-height: 430px; }
    .chart.short { min-height: 300px; }
    .source-card .title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 700; }
    .pill {
      font-size: 11px;
      text-transform: uppercase;
      color: var(--muted);
      padding: 6px 9px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.75);
    }
    .mini-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
    .mini { padding: 12px; border-radius: 14px; background: rgba(255,255,255,.72); border: 1px solid var(--line); }
    .mini .v { margin-top: 5px; font-size: 18px; font-weight: 700; }
    .story { display: grid; gap: 12px; }
    .story-item { display: grid; grid-template-columns: auto 1fr; gap: 12px; align-items: start; }
    .story-item i { color: var(--codex); margin-top: 3px; }
    .note-list { display: grid; gap: 12px; }
    .note { border-left: 4px solid var(--claude); padding: 10px 0 10px 14px; color: #4b443e; line-height: 1.6; }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; padding: 12px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { color: var(--muted); text-transform: uppercase; font-size: 12px; letter-spacing: .08em; }
    .tiny { color: var(--muted); font-size: 12px; }
    .footer { margin-top: 16px; color: var(--muted); font-size: 12px; line-height: 1.65; }
    @media (max-width: 1024px) {
      .hero, .story-strip, .two-col, .subgrid, .three-col { grid-template-columns: 1fr; }
      .page { width: min(100vw - 16px, 1440px); }
      .hero-main, .section, .side { padding: 18px; }
      h1 { max-width: none; }
    }
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <article class="panel hero-main">
        <div class="eyebrow"><i class="fa-solid fa-chart-line"></i><span>Agent Usage Atlas</span></div>
        <h1>从 3 月 8 日起 你把三个 Agent 栈打成了联赛积分榜</h1>
        <p class="hero-copy" id="hero-copy"></p>
        <div class="chip-row" id="hero-chips"></div>
      </article>
      <aside class="panel side" id="summary-side"></aside>
    </section>

    <section class="three-col" id="source-cards"></section>

    <section class="story-strip">
      <article class="panel section">
        <div class="section-head"><h2>这周剧情梗概</h2><span>把数字翻译成人话</span></div>
        <div class="story" id="story-list"></div>
      </article>
      <article class="panel section">
        <div class="section-head"><h2>来源玫瑰图</h2><span>体量 + 气质一起看</span></div>
        <div class="chart short" id="rose-chart"></div>
      </article>
    </section>

    <section class="two-col">
      <article class="panel section">
        <div class="section-head"><h2>每日 Token 结构</h2><span>堆叠柱 + 累计线</span></div>
        <div class="chart tall" id="daily-chart"></div>
        <div class="legend">
          <span><i class="dot" style="background:var(--uncached)"></i>Uncached Input</span>
          <span><i class="dot" style="background:var(--cache-read)"></i>Cache Read</span>
          <span><i class="dot" style="background:var(--cache-write)"></i>Cache Write</span>
          <span><i class="dot" style="background:var(--output)"></i>Output + Reason</span>
        </div>
      </article>
      <article class="panel section">
        <div class="section-head"><h2>Token 流向桑基图</h2><span>从来源流到各类 token 桶</span></div>
        <div class="chart tall" id="sankey-chart"></div>
        <div class="note-list" id="source-notes"></div>
      </article>
    </section>

    <section class="two-col">
      <article class="panel section">
        <div class="section-head"><h2>活跃热区</h2><span>星期 × 小时，越深越忙</span></div>
        <div class="chart tall" id="heatmap-chart"></div>
      </article>
      <article class="panel section">
        <div class="section-head"><h2>来源能力雷达</h2><span>体量、缓存、输出、活跃度四维比较</span></div>
        <div class="chart tall" id="radar-chart"></div>
      </article>
    </section>

    <section class="subgrid">
      <article class="panel section">
        <div class="section-head"><h2>Calendar Heatmap</h2><span>把高峰日钉在日历上</span></div>
        <div class="chart" id="calendar-chart"></div>
      </article>
      <article class="panel section">
        <div class="section-head"><h2>Timeline</h2><span>峰值、拐点与累计爬坡</span></div>
        <div class="chart" id="timeline-chart"></div>
      </article>
    </section>

    <section class="subgrid">
      <article class="panel section">
        <div class="section-head"><h2>Session 气泡图</h2><span>x=时长, y=token, 气泡=缓存</span></div>
        <div class="chart" id="bubble-chart"></div>
      </article>
      <article class="panel section">
        <div class="section-head"><h2>小时节奏图</h2><span>24 小时内谁最爱开工</span></div>
        <div class="chart" id="tempo-chart"></div>
        <div class="note-list" id="tempo-notes"></div>
      </article>
    </section>

    <section style="margin-top:20px;">
      <article class="panel section">
        <div class="section-head"><h2>Session 龙虎榜</h2><span>只列 token 可统计的 session</span></div>
        <table id="session-table"></table>
        <div class="footer">
          数据源说明：Codex 来自 <code>~/.codex</code> 累计 usage 事件差值；Claude 来自 <code>~/.claude/projects</code> 的响应 usage 去重求和；Cursor 来自 transcript 活动文件，仅统计会话/消息活跃度，未发现稳定 token 字段。<br />
          图表渲染采用 <code>Apache ECharts</code>，适合热力图、桑基图、雷达、散点与混合图这种多来源分析面板。
        </div>
      </article>
    </section>
  </main>

  <script>
    const data = __DATA__;
    const charts = [];
    const fmtInt = (n) => n.toLocaleString('en-US');
    const fmtShort = (n) => {
      const abs = Math.abs(n);
      if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
      if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
      if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'K';
      return String(n);
    };
    const fmtPct = (v) => (v * 100).toFixed(1) + '%';
    const colors = { Codex: '#E36B3F', Claude: '#D5A476', Cursor: '#4C6EF5', uncached: '#F4B183', cacheRead: '#2F9E78', cacheWrite: '#7E57C2', output: '#3653B3', reason: '#9153C5' };
    const textColor = '#564d45';
    const axisLine = 'rgba(38,31,26,.08)';

    function themeBase() {
      return {
        textStyle: { color: textColor, fontFamily: 'ui-rounded, SF Pro Display, PingFang SC, sans-serif' },
        tooltip: { backgroundColor: 'rgba(36,30,26,.92)', borderWidth: 0, textStyle: { color: '#fff' } },
        animationDuration: 900
      };
    }

    function initChart(id) {
      const chart = echarts.init(document.getElementById(id), null, { renderer: 'canvas' });
      charts.push(chart);
      return chart;
    }

    function renderHero() {
      const t = data.totals;
      document.getElementById('hero-copy').textContent = `统计窗口 ${data.range.start_local} 到 ${data.range.end_local}。目前可确认的 token 总处理量 ${fmtShort(t.grand_total)}，其中缓存相关占 ${fmtPct(t.cache_ratio)}。结论很简单：你不是在偶尔用 Agent，而是在把它们排班上工。`;
      document.getElementById('hero-chips').innerHTML = [
        `<span class="chip"><i class="fa-solid fa-fire"></i>${fmtShort(t.grand_total)} total tokens</span>`,
        `<span class="chip"><i class="fa-solid fa-database"></i>${fmtPct(t.cache_ratio)} cache-heavy</span>`,
        `<span class="chip"><i class="fa-solid fa-mountain-sun"></i>peak day ${t.peak_day_label}</span>`,
        `<span class="chip"><i class="fa-solid fa-layer-group"></i>${fmtInt(t.token_capable_source_count)} token-capable sources</span>`
      ].join('');
      const cards = [
        { label: 'Total Tokens', value: fmtShort(t.grand_total), hint: `平均每天 ${fmtShort(t.average_per_day)}。这已经不是“聊一聊”，这是持续性工业作业。` },
        { label: 'Cache Stack', value: fmtShort(t.cache_read + t.cache_write), hint: `缓存读写合计占比 ${fmtPct(t.cache_ratio)}，说明重复上下文非常重。` },
        { label: 'Median Session', value: fmtShort(t.median_session_tokens), hint: `中位 session 时长 ${t.median_session_minutes} 分钟，驾驶席有人，而且一直有人。` }
      ];
      document.getElementById('summary-side').innerHTML = cards.map(card => `<section class="side-card"><div class="label">${card.label}</div><div class="value">${card.value}</div><div class="hint">${card.hint}</div></section>`).join('');
    }

    function renderStory() {
      const sourceTotals = Object.fromEntries(data.source_cards.map(card => [card.source, card]));
      const items = [
        { icon: 'fa-bolt', text: `主力依然是 Codex，单源 ${fmtShort(sourceTotals.Codex.total)} token，占可统计 token 的 ${fmtPct(sourceTotals.Codex.total / Math.max(1, data.totals.grand_total))}。` },
        { icon: 'fa-feather-pointed', text: `Claude 这段时间累计 ${fmtShort(sourceTotals.Claude.total)} token，更像“精锐突击队”而不是“全天值班室”。` },
        { icon: 'fa-database', text: `缓存读写合计 ${fmtShort(data.totals.cache_read + data.totals.cache_write)}，省钱程度已经从“优化”进入“信仰”。` },
        { icon: 'fa-arrow-pointer', text: `Cursor 本地能稳定拿到 ${fmtInt(sourceTotals.Cursor.sessions)} 个 session 和 ${fmtInt(sourceTotals.Cursor.messages)} 条活动消息，但 token 小票它没留下。` }
      ];
      document.getElementById('story-list').innerHTML = items.map(item => `<div class="story-item"><i class="fa-solid ${item.icon}"></i><div>${item.text}</div></div>`).join('');
    }

    function renderSourceCards() {
      document.getElementById('source-cards').innerHTML = data.source_cards.map(card => `
        <article class="panel source-card">
          <div class="title"><span><i class="fa-solid ${card.source === 'Codex' ? 'fa-terminal' : card.source === 'Claude' ? 'fa-feather-pointed' : 'fa-arrow-pointer'}"></i> ${card.source}</span><span class="pill">${card.token_capable ? 'token-tracked' : 'activity-only'}</span></div>
          <div class="big">${card.token_capable ? fmtShort(card.total) : fmtInt(card.messages)}</div>
          <div class="tiny">${card.token_capable ? 'tracked tokens' : 'tracked transcript messages'}</div>
          <div class="mini-grid">
            <div class="mini"><div class="k">Sessions</div><div class="v">${fmtInt(card.sessions)}</div></div>
            <div class="mini"><div class="k">Messages</div><div class="v">${fmtInt(card.messages)}</div></div>
            <div class="mini"><div class="k">Top Model</div><div class="v">${card.top_model}</div></div>
            <div class="mini"><div class="k">Cache Read</div><div class="v">${card.token_capable ? fmtShort(card.cache_read) : '-'}</div></div>
          </div>
        </article>`).join('');
    }

    function renderRoseChart() {
      const chart = initChart('rose-chart');
      chart.setOption({
        ...themeBase(),
        legend: { bottom: 0, textStyle: { color: textColor } },
        series: [{
          type: 'pie',
          radius: ['24%', '74%'],
          center: ['50%', '46%'],
          roseType: 'radius',
          itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
          label: { color: textColor, formatter: ({ name, percent }) => `${name}\n${percent}%` },
          data: data.source_cards.map(card => ({ name: card.source, value: card.token_capable ? card.total : Math.max(card.messages, 1), itemStyle: { color: colors[card.source] || '#999' } }))
        }]
      });
    }

    function renderDailyChart() {
      const chart = initChart('daily-chart');
      chart.setOption({
        ...themeBase(),
        legend: { top: 6, textStyle: { color: textColor } },
        grid: { top: 58, left: 60, right: 60, bottom: 44 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'category', data: data.days.map(d => d.label), axisLine: { lineStyle: { color: axisLine } }, axisTick: { show: false }, axisLabel: { color: textColor } },
        yAxis: [
          { type: 'value', name: 'Daily Tokens', nameTextStyle: { color: textColor }, splitLine: { lineStyle: { color: axisLine } }, axisLabel: { color: textColor, formatter: (v) => fmtShort(v) } },
          { type: 'value', name: 'Cumulative', nameTextStyle: { color: textColor }, splitLine: { show: false }, axisLabel: { color: textColor, formatter: (v) => fmtShort(v) } }
        ],
        series: [
          { name: 'Uncached Input', type: 'bar', stack: 'daily', itemStyle: { color: colors.uncached, borderRadius: [8,8,0,0] }, data: data.days.map(d => d.uncached_input) },
          { name: 'Cache Read', type: 'bar', stack: 'daily', itemStyle: { color: colors.cacheRead, borderRadius: [8,8,0,0] }, data: data.days.map(d => d.cache_read) },
          { name: 'Cache Write', type: 'bar', stack: 'daily', itemStyle: { color: colors.cacheWrite, borderRadius: [8,8,0,0] }, data: data.days.map(d => d.cache_write) },
          { name: 'Output + Reason', type: 'bar', stack: 'daily', itemStyle: { color: colors.output, borderRadius: [8,8,0,0] }, data: data.days.map(d => d.output + d.reasoning) },
          { name: 'Cumulative', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 8, lineStyle: { width: 4, color: '#231f1b' }, itemStyle: { color: '#231f1b' }, areaStyle: { color: 'rgba(35,31,27,.05)' }, data: data.days.map(d => d.cumulative) }
        ]
      });
    }

    function renderSankey() {
      const chart = initChart('sankey-chart');
      const tokenSources = data.source_cards.filter(card => card.token_capable);
      const buckets = [
        { key: 'uncached_input', name: 'Uncached Input', color: colors.uncached },
        { key: 'cache_read', name: 'Cache Read', color: colors.cacheRead },
        { key: 'cache_write', name: 'Cache Write', color: colors.cacheWrite },
        { key: 'output', name: 'Output', color: colors.output },
        { key: 'reasoning', name: 'Reasoning', color: colors.reason }
      ];
      chart.setOption({
        ...themeBase(),
        series: [{
          type: 'sankey',
          left: 8, right: 8, top: 24, bottom: 12,
          nodeWidth: 18, nodeGap: 16,
          nodeAlign: 'justify',
          lineStyle: { color: 'gradient', curveness: 0.45, opacity: 0.34 },
          label: { color: '#fff', position: 'inside', fontWeight: 600 },
          data: [...tokenSources.map(card => ({ name: card.source, itemStyle: { color: colors[card.source] || '#999' } })), ...buckets.map(b => ({ name: b.name, itemStyle: { color: b.color } }))],
          links: tokenSources.flatMap(card => buckets.filter(b => (card[b.key] || 0) > 0).map(b => ({ source: card.source, target: b.name, value: card[b.key] })))
        }]
      });
      document.getElementById('source-notes').innerHTML = [
        ...tokenSources.map(card => `<div class="note" style="border-left-color:${colors[card.source] || '#999'}">${card.source} 主力模型是 ${card.top_model}，总量 ${fmtShort(card.total)}。</div>`),
        ...data.jokes.map(text => `<div class="note">${text}</div>`)
      ].join('');
    }

    function renderHeatmap() {
      const chart = initChart('heatmap-chart');
      const heat = [];
      data.heatmap.forEach((row, y) => row.values.forEach((value, x) => heat.push([x, y, value])));
      chart.setOption({
        ...themeBase(),
        grid: { top: 44, left: 70, right: 24, bottom: 34 },
        xAxis: { type: 'category', data: Array.from({ length: 24 }, (_, i) => `${i}`), splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,.46)', 'rgba(255,255,255,.2)'] } }, axisLine: { lineStyle: { color: axisLine } }, axisTick: { show: false } },
        yAxis: { type: 'category', data: data.heatmap.map(row => row.weekday), splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,.46)', 'rgba(255,255,255,.2)'] } }, axisLine: { lineStyle: { color: axisLine } }, axisTick: { show: false } },
        visualMap: { min: 0, max: Math.max(...heat.map(item => item[2]), 1), orient: 'horizontal', left: 'center', bottom: 0, calculable: true, inRange: { color: ['#fff8f1', '#f1d4b7', '#d39a78', '#9a5b3f', '#4c6ef5'] }, textStyle: { color: textColor } },
        series: [{ type: 'heatmap', data: heat, itemStyle: { borderRadius: 8, borderColor: '#f6f1e8', borderWidth: 3 } }]
      });
    }

    function renderCalendar() {
      const chart = initChart('calendar-chart');
      const dailyData = data.days.map(day => [day.date, day.total]);
      chart.setOption({
        ...themeBase(),
        tooltip: {
          formatter: ({ value }) => `${value[0]}<br/>${fmtInt(value[1])} tokens`
        },
        visualMap: {
          min: 0,
          max: Math.max(...data.days.map(day => day.total), 1),
          orient: 'horizontal',
          left: 'center',
          bottom: 8,
          textStyle: { color: textColor },
          inRange: { color: ['#fff8f1', '#f1d4b7', '#d39a78', '#9a5b3f', '#3653b3'] }
        },
        calendar: {
          top: 28,
          left: 24,
          right: 24,
          cellSize: ['auto', 22],
          range: [data.range.start_local.slice(0, 10), data.range.end_local.slice(0, 10)],
          yearLabel: { show: false },
          monthLabel: { color: textColor, margin: 14 },
          dayLabel: { color: textColor, firstDay: 1 },
          splitLine: { lineStyle: { color: '#f0e7dc' } },
          itemStyle: { borderWidth: 3, borderColor: '#f6f1e8', color: 'rgba(255,255,255,.5)' }
        },
        series: [{
          type: 'heatmap',
          coordinateSystem: 'calendar',
          data: dailyData
        }]
      });
    }

    function renderTimeline() {
      const chart = initChart('timeline-chart');
      const peakDays = [...data.days]
        .filter(day => day.total > 0)
        .sort((a, b) => b.total - a.total)
        .slice(0, 4)
        .sort((a, b) => a.date.localeCompare(b.date));
      chart.setOption({
        ...themeBase(),
        legend: { top: 4, textStyle: { color: textColor } },
        grid: { top: 54, left: 56, right: 24, bottom: 46 },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category',
          data: data.days.map(day => day.label),
          axisLine: { lineStyle: { color: axisLine } },
          axisTick: { show: false },
          axisLabel: { color: textColor }
        },
        yAxis: [
          {
            type: 'value',
            name: 'Daily',
            splitLine: { lineStyle: { color: axisLine } },
            axisLabel: { color: textColor, formatter: (v) => fmtShort(v) },
            nameTextStyle: { color: textColor }
          },
          {
            type: 'value',
            name: 'Cumulative',
            splitLine: { show: false },
            axisLabel: { color: textColor, formatter: (v) => fmtShort(v) },
            nameTextStyle: { color: textColor }
          }
        ],
        series: [
          {
            name: 'Daily Total',
            type: 'bar',
            barMaxWidth: 28,
            itemStyle: { color: 'rgba(213,164,118,.45)', borderRadius: [8, 8, 0, 0] },
            data: data.days.map(day => day.total)
          },
          {
            name: 'Cumulative',
            type: 'line',
            yAxisIndex: 1,
            smooth: true,
            symbolSize: 7,
            lineStyle: { width: 4, color: '#231f1b' },
            itemStyle: { color: '#231f1b' },
            data: data.days.map(day => day.cumulative),
            markPoint: {
              symbol: 'pin',
              symbolSize: 42,
              label: { color: '#fff', formatter: ({ value }) => fmtShort(value) },
              itemStyle: { color: colors.Codex },
              data: peakDays.map(day => ({
                name: day.label,
                coord: [day.label, day.cumulative],
                value: day.total
              }))
            }
          }
        ]
      });
    }

    function renderRadar() {
      const chart = initChart('radar-chart');
      const sources = data.source_cards.filter(card => card.token_capable);
      chart.setOption({
        ...themeBase(),
        legend: { bottom: 0, textStyle: { color: textColor } },
        radar: {
          radius: '62%',
          center: ['50%', '46%'],
          splitNumber: 5,
          axisName: { color: textColor },
          splitLine: { lineStyle: { color: axisLine } },
          splitArea: { areaStyle: { color: ['rgba(255,255,255,.5)', 'rgba(255,255,255,.3)'] } },
          indicator: [
            { name: 'Total', max: Math.max(...sources.map(c => c.total), 1) },
            { name: 'Cache', max: Math.max(...sources.map(c => c.cache_read + c.cache_write), 1) },
            { name: 'Output', max: Math.max(...sources.map(c => c.output + c.reasoning), 1) },
            { name: 'Sessions', max: Math.max(...sources.map(c => c.sessions), 1) }
          ]
        },
        series: [{ type: 'radar', symbol: 'circle', symbolSize: 7, areaStyle: { opacity: .12 }, lineStyle: { width: 3 }, data: sources.map(card => ({ name: card.source, value: [card.total, card.cache_read + card.cache_write, card.output + card.reasoning, card.sessions], itemStyle: { color: colors[card.source] }, lineStyle: { color: colors[card.source] }, areaStyle: { color: colors[card.source], opacity: .11 } })) }]
      });
    }

    function renderBubble() {
      const chart = initChart('bubble-chart');
      const rows = data.top_sessions.slice(0, 24);
      chart.setOption({
        ...themeBase(),
        grid: { top: 30, left: 62, right: 24, bottom: 54 },
        xAxis: { name: 'Minutes', splitLine: { lineStyle: { color: axisLine } }, axisLabel: { color: textColor }, nameTextStyle: { color: textColor } },
        yAxis: { name: 'Total Tokens', splitLine: { lineStyle: { color: axisLine } }, axisLabel: { color: textColor, formatter: (v) => fmtShort(v) }, nameTextStyle: { color: textColor } },
        series: ['Codex', 'Claude'].map(source => ({
          name: source,
          type: 'scatter',
          data: rows.filter(row => row.source === source).map(row => ({ value: [row.minutes || 1, row.total, Math.max(8, Math.sqrt(row.cache_read + row.cache_write) / 180)], session: row.session_id, source })),
          symbolSize: (value) => value[2],
          itemStyle: { color: colors[source], opacity: .8 }
        }))
      });
    }

    function renderTempo() {
      const chart = initChart('tempo-chart');
      chart.setOption({
        ...themeBase(),
        legend: { top: 4, textStyle: { color: textColor } },
        grid: { top: 52, left: 56, right: 24, bottom: 46 },
        xAxis: { type: 'category', data: data.hourly.map(row => `${row.hour}`), axisLine: { lineStyle: { color: axisLine } }, axisTick: { show: false }, axisLabel: { color: textColor } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: axisLine } }, axisLabel: { color: textColor, formatter: (v) => fmtShort(v) } },
        series: ['Codex', 'Claude', 'Cursor'].map(source => ({ name: source, type: source === 'Cursor' ? 'line' : 'bar', smooth: source === 'Cursor', barMaxWidth: 20, itemStyle: { color: colors[source] || '#999' }, lineStyle: { width: 3, color: colors[source] || '#999' }, data: data.hourly.map(row => row[source] || 0) }))
      });
      const hottestHour = [...data.hourly].map(row => ({ hour: row.hour, total: (row.Codex || 0) + (row.Claude || 0) + (row.Cursor || 0) })).sort((a, b) => b.total - a.total)[0];
      document.getElementById('tempo-notes').innerHTML = [
        `<div class="note">最热小时大约在 ${hottestHour.hour}:00，说明那会儿不是在写代码，就是在让 agent 代你写更多代码。</div>`,
        `<div class="note">峰值日 ${data.totals.peak_day_label} 共 ${fmtShort(data.totals.peak_day_total)} token，已经接近一些团队周报的精神污染总量。</div>`
      ].join('');
    }

    function renderSessionTable() {
      document.getElementById('session-table').innerHTML = `
        <thead><tr><th>Source</th><th>Session</th><th>Total</th><th>Cache Share</th><th>Model</th><th>Window</th></tr></thead>
        <tbody>${data.top_sessions.map(row => {
          const share = row.total ? (row.cache_read + row.cache_write) / row.total : 0;
          return `<tr><td><strong>${row.source}</strong><div class="tiny">${fmtInt(row.messages)} events</div></td><td><strong>${row.session_id.slice(0, 10)}…</strong><div class="tiny">${row.minutes} min</div></td><td>${fmtShort(row.total)}</td><td>${fmtPct(share)}</td><td>${row.top_model}</td><td><div>${row.first_local}</div><div class="tiny">to ${row.last_local}</div></td></tr>`;
        }).join('')}</tbody>`;
    }

    renderHero();
    renderStory();
    renderSourceCards();
    renderRoseChart();
    renderDailyChart();
    renderSankey();
    renderHeatmap();
    renderCalendar();
    renderTimeline();
    renderRadar();
    renderBubble();
    renderTempo();
    renderSessionTable();
    window.addEventListener('resize', () => charts.forEach(chart => chart.resize()));
  </script>
</body>
</html>
""".replace("__DATA__", payload)


def main() -> None:
    events = parse_codex_events() + parse_claude_events() + parse_cursor_events()
    dashboard = aggregate_dashboard(events)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_html(dashboard))
    print(f"Wrote dashboard to {OUTPUT_PATH}")
    print(f"Tracked total tokens: {fmt_int(dashboard['totals']['grand_total'])}")
    print("Source summary:")
    for card in dashboard["source_cards"]:
        print(
            f"  - {card['source']}: total={fmt_int(card['total'])} "
            f"sessions={card['sessions']} messages={card['messages']} token_capable={card['token_capable']}"
        )


if __name__ == "__main__":
    main()
