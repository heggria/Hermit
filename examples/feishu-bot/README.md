# Feishu Bot

Integration guide for running Hermit as a Feishu (Lark) messaging bot. The
Feishu adapter connects Hermit's governed kernel to a Feishu app, letting
users interact with the agent through group or direct messages.

## How It Works

```
Feishu Cloud → Webhook POST → hermit serve --adapter feishu
                                  → Kernel task pipeline
                                      → Policy / approval checks
                                      → Tool execution
                                  → Response sent back to Feishu
```

Messages from Feishu are received via webhook, converted into kernel tasks,
executed through the full governed pipeline (including policy evaluation and
approval gates), and the results are sent back as Feishu messages.

## Requirements

- A Feishu developer account and a custom app (create one at
  [open.feishu.cn](https://open.feishu.cn/))
- The app must have the **Bot** capability enabled
- Event subscription URL pointed at your Hermit instance
- An LLM provider configured (Anthropic or OpenAI)

## Setup

### 1. Create the Feishu App

1. Go to the [Feishu Open Platform](https://open.feishu.cn/) and create a new
   app.
2. Enable the **Bot** capability under "Add capabilities".
3. Under **Event Subscriptions**, set the request URL to where Hermit will be
   running (e.g., `https://your-host:8080/feishu/webhook`).
4. Subscribe to the `im.message.receive_v1` event.
5. Note down the App ID, App Secret, Verification Token, and Encrypt Key.

### 2. Configure Environment

Copy the example env file and fill in your credentials:

```bash
cp env.example ~/.hermit/.env
# Edit ~/.hermit/.env with your actual values
```

Or export them directly:

```bash
export FEISHU_APP_ID=cli_xxxxxxxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
export FEISHU_VERIFICATION_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxx
export FEISHU_ENCRYPT_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
export HERMIT_PROVIDER=codex
export OPENAI_API_KEY=sk-...
export HERMIT_MODEL=gpt-5.4
```

### 3. Start the Adapter

```bash
hermit serve --adapter feishu
```

Or use the environment controller for managed operation:

```bash
scripts/hermit-envctl.sh prod up
# or for development with auto-reload:
scripts/hermit-watch.sh dev --adapter feishu
```

### 4. Verify

- Send a message to the bot in Feishu.
- Check the logs at `~/.hermit/logs/feishu-stdout.log`.
- Run `hermit task list` to see the task created by your message.

## Message Flow

1. User sends a message in Feishu (direct or group mention).
2. Feishu Cloud delivers the event to the configured webhook URL.
3. The Feishu adapter verifies the signature and decrypts the payload.
4. A kernel task is created from the message content.
5. The task goes through the governed pipeline: policy evaluation, tool
   execution (if needed), receipt generation.
6. The result is sent back to the Feishu conversation.

## Troubleshooting

| Issue | Check |
|-------|-------|
| Bot does not respond | Verify the webhook URL is reachable from Feishu Cloud |
| Signature verification failed | Confirm `FEISHU_VERIFICATION_TOKEN` matches the app config |
| Decryption error | Confirm `FEISHU_ENCRYPT_KEY` matches the app config |
| No task created | Check `~/.hermit/logs/feishu-stderr.log` for errors |
| Adapter won't start | Ensure no other process is using the same port; check the PID file at `~/.hermit/serve-feishu.pid` |
