---
name: web-search
description: How to use web_search effectively — recency-first strategy, parameter selection, and fallback escalation. Read when performing any web search, especially for current events or news.
---

## Prefer `grok_search` first

If the `grok_search` tool is available (`XAI_API_KEY` is configured), prefer it for news, current events, stock prices, and other time-sensitive queries. It can read live web content directly and is usually more current than DuckDuckGo-based results.

Use `web_search` as the fallback when `grok_search` is unavailable, when the query is about technical docs or encyclopedic knowledge, or when you only need a few quick links.

---

## Search strategy: start recent, then widen only if needed

**Core rule**: for anything time-sensitive, such as news, current events, prices, releases, or ongoing incidents, start with recent results first. Only expand the time window if the first pass does not find useful results. Do not begin with an unrestricted global search.

---

## Parameter quick reference

| Parameter | Values | Description |
|------|------|------|
| `search_type` | `"news"` / `"web"` | use `news` for current events; use `web` for docs, code, and reference content |
| `time_filter` | `"day"` / `"week"` / `"month"` / `"year"` | result time window; leave empty for no limit |
| `region` | `"cn-zh"` / `"us-en"` / `"wt-wt"` | use `cn-zh` for Chinese content; default is global |

---

## Escalation strategy

For **time-sensitive** questions, widen the search in this order and stop as soon as you get useful results:

```
Pass 1: search_type="news", time_filter="day"     ← today
Pass 2: search_type="news", time_filter="week"    ← last week
Pass 3: search_type="news", time_filter="month"   ← last month
Pass 4: search_type="web"（不限时间）              ← global fallback
```

When to decide there are “no useful results”: fewer than 2 results, or all titles/snippets are irrelevant to the question. In that case, move to the next level.

---

## How to judge whether a query is time-sensitive

**Time-sensitive** (use the escalation strategy):
- news events: war, disasters, policy, diplomacy, markets
- product / version questions: latest release, changelog, price changes
- people or company updates: statements, appointments, arrests, deaths
- sports / entertainment: results, release schedules

**Not time-sensitive** (go straight to `search_type="web"` with no `time_filter`):
- technical docs, API references, how to use a library
- historical events, geography, science facts
- programming questions, syntax, concept explanations

---

## Examples

### Latest news (with escalation)

```json
// 第 1 次
{"query": "伊朗油库 以色列袭击", "search_type": "news", "time_filter": "day"}

// 第 1 次无结果 → 第 2 次
{"query": "伊朗油库 以色列袭击", "search_type": "news", "time_filter": "week"}
```

### Technical documentation (go straight to web)

```json
{"query": "Python asyncio event loop tutorial", "search_type": "web"}
```

### Chinese news (set the region)

```json
{"query": "A股 今日涨跌", "search_type": "news", "time_filter": "day", "region": "cn-zh"}
```
