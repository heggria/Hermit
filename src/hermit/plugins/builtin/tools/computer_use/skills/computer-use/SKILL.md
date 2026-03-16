---
name: computer-use
description: Observe the current screen with a screenshot before using macOS Computer Use tools to click, type, press keys, or control apps.
---

## When to use

Use this toolset only when a task truly requires directly inspecting the current screen, operating desktop apps, automating clicks and typing, or continuing the reasoning process based on visible screen content.

Prefer native tools first. If the target system already has a dedicated tool or API, use that instead of Computer Use.

Examples:
- use Feishu messaging or document tools instead of clicking around the Feishu desktop app
- use filesystem, shell, web, or MCP tools instead of copying text through the UI
- use provider-native integrations instead of driving a browser or chat window manually

Only fall back to Computer Use when the job is genuinely UI-only, or when the user explicitly asked you to operate the desktop UI.

## Basic workflow

1. Call `computer_screenshot` first to inspect the current state.
2. Use the screenshot to identify the target app, control positions, and the next action.
3. Then call tools for clicking, moving, typing, shortcuts, scrolling, or opening apps.
4. After each important group of actions, prefer taking another screenshot to confirm the result.

## Do not use for

- routine messaging in apps that already have native message tools
- tasks where the required target id, URL, file path, or API exists and can be used directly
- "reply in the current Feishu thread" or "send a message to a known Feishu chat_id" scenarios

## Typical scenarios

- automating local macOS applications
- inspecting screen content and continuing analysis
- controlling app launch, switching, typing, clicking, and scrolling
