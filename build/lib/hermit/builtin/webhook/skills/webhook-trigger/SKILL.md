---
name: webhook-trigger
description: "Configure and manage inbound HTTP webhooks that trigger agent tasks — use when user asks about webhook setup, external system integrations (GitHub, Zendesk, etc.), or receiving events from third-party services."
---

## Capability

Hermit includes a built-in **webhook plugin**. When `hermit serve` starts, it also starts an HTTP service at the same time (default port 8321). This service can receive POST events from external systems such as GitHub, Zendesk, or custom internal services, trigger the agent automatically, and push the result to Feishu.

**This capability is built in. No extra plugin installation is required.**

---

## Available tools

| Tool | Description |
|------|------|
| `webhook_list` | list all configured routes |
| `webhook_add` | add a new route (writes to `webhooks.json`) |
| `webhook_update` | update a route’s template / path / signature / Feishu delivery |
| `webhook_delete` | delete a route |

**Note: after adding, updating, or deleting a route, you need to restart `hermit serve` for the change to take effect.**

---

## How to help a user configure a webhook

When the user says something like “help me configure a GitHub webhook” or “I want to receive external events”:

1. call `webhook_list` to inspect the current config
2. ask which system sends the event, what task should run, and which Feishu chat should receive the result
3. call `webhook_add` to create the route; in a Feishu conversation, read `feishu_chat_id` from the `<feishu_chat_id>` context
4. tell the user to restart `serve` and provide a `curl` test example

---

## Config file

Path: `~/.hermit/webhooks.json`

```json
{
  "host": "0.0.0.0",
  "port": 8321,
  "routes": {
    "<路由名称>": {
      "path": "/webhook/<路径>",
      "secret": "<HMAC secret，可选>",
      "signature_header": "X-Hub-Signature-256",
      "prompt_template": "<用于驱动 Agent 的提示词模板，支持 {字段} 占位符>",
      "notify": {
        "feishu_chat_id": "<飞书群聊或单聊 ID>"
      }
    }
  }
}
```

### Key fields

| Field | Required | Description |
|------|------|------|
| `path` | yes | HTTP route path, for example `/webhook/github` |
| `prompt_template` | yes | prompt sent to the agent; `{field}` placeholders can extract values from the payload, including nested paths like `{pull_request.title}` |
| `secret` | no | HMAC-SHA256 signing secret; when present, `signature_header` is validated |
| `signature_header` | no | signature header name, default `X-Hub-Signature-256` |
| `notify.feishu_chat_id` | no | Feishu destination; values starting with `oc_` are group chats, `ou_` are direct chats. If omitted, no Feishu push is sent |

---

## Typical configuration examples

### GitHub PR auto code review

```json
{
  "routes": {
    "github": {
      "path": "/webhook/github",
      "secret": "your_github_secret",
      "signature_header": "X-Hub-Signature-256",
      "prompt_template": "收到 GitHub {action} 事件。\n仓库：{repository.full_name}\nPR 标题：{pull_request.title}\nPR 描述：{pull_request.body}\n\n请对这个 PR 进行简要的 Code Review，指出潜在问题和改进建议。",
      "notify": {
        "feishu_chat_id": "oc_xxxxxx"
      }
    }
  }
}
```

### Custom system notification

```json
{
  "routes": {
    "custom": {
      "path": "/webhook/custom",
      "prompt_template": "{message}",
      "notify": {
        "feishu_chat_id": "oc_xxxxxx"
      }
    }
  }
}
```

---

## Calling it from an external system

```bash
# 无签名
curl -X POST http://your-server:8321/webhook/custom \
  -H "Content-Type: application/json" \
  -d '{"message": "部署完成，请检查生产环境状态"}'

# 有签名（GitHub 风格）
SECRET="your_secret"
BODY='{"action":"opened","pull_request":{"title":"Fix bug"},"repository":{"full_name":"org/repo"}}'
SIG="sha256=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"
curl -X POST http://your-server:8321/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$BODY"
```

On success, the server returns `HTTP 202 Accepted`. The agent processes the event asynchronously in the background, and the result is pushed to the configured Feishu conversation.

---

## Debug endpoints

| Endpoint | Description |
|------|------|
| `GET /health` | health check, returns `{"status": "ok"}` |
| `GET /routes` | list all registered routes and whether each one uses a signature |

---

## Environment variables

| Variable | Default | Description |
|------|--------|------|
| `HERMIT_WEBHOOK_ENABLED` | `true` | set to `false` to disable the webhook plugin |
| `HERMIT_WEBHOOK_HOST` | `0.0.0.0` | bind address |
| `HERMIT_WEBHOOK_PORT` | `8321` | listen port |

---

## `prompt_template` syntax

The template can extract fields from the webhook payload using `{field}` syntax:

- **top-level field**: `{action}` → the payload’s `action`
- **nested field**: `{pull_request.title}` → `payload.pull_request.title`
- **deeply nested**: `{repository.owner.login}`
- **missing field**: the placeholder such as `{missing_field}` is kept as-is without raising an error

---

## Typical conversation scenarios

### Show the current config

```python
webhook_list()
```

### Add a GitHub PR auto-review route

```python
webhook_add(
    name="github",
    prompt_template="收到 GitHub {action} 事件。\n仓库：{repository.full_name}\nPR：{pull_request.title}\n\n请进行 Code Review。",
    secret="your_github_webhook_secret",
    feishu_chat_id="<从上下文 <feishu_chat_id> 读取>",
)
```

### Change the push destination

```python
webhook_update(name="github", feishu_chat_id="oc_new_group_id")
```

### Delete a route

```python
webhook_delete(name="github")
```
