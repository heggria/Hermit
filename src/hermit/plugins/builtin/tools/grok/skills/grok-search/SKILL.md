---
name: grok-search
description: When to use grok_search vs web_search — Grok has real-time live web access and synthesizes answers directly, making it the preferred tool for news, stock prices, current events, or any time-sensitive query.
---

## `grok_search` vs `web_search`

| Feature | `grok_search` | `web_search` |
|------|---------------|--------------|
| Freshness | ✅ Grok reads live web pages directly | ⚠️ DuckDuckGo index, which may lag |
| Answer quality | synthesized analysis with inline citations | search-result snippets |
| Best for | news, stock prices, current events, breaking stories | docs, code, encyclopedia-style reference |
| Speed | slower because an LLM is involved | faster because it is just retrieval |
| Dependency | requires `XAI_API_KEY` | no API key required |

---

## When to use `grok_search` first

Use `grok_search` when:
- the user asks about **today / latest / recent** news, events, or prices
- the query involves **stocks, finance, or market** movement
- the user wants the **latest update on a person or company**
- `web_search` results are too old or not relevant enough

## When to use `web_search` as fallback

Use `web_search` when:
- looking up technical docs, API usage, or code examples
- looking up historical events or encyclopedia knowledge
- `grok_search` fails or `XAI_API_KEY` is not configured
- you only need a few quick links

---

## Examples

### Stock price / finance

```json
{"query": "MiniMax 01912.HK 今日股价大涨原因分析"}
```

### Current events

```json
{"query": "以色列袭击伊朗油库最新进展 2026年3月"}
```

### Force live search mode

```json
{"query": "当前比特币价格", "search_mode": "on"}
```

---

## If `XAI_API_KEY` is not set

The tool returns an error. In that case, fall back to the `web_search` escalation strategy (`day → week → month`).
