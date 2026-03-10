---
name: github
description: Use GitHub MCP for GitHub-hosted repo operations such as issues, pull requests, code search, file reads, and workflow inspection.
---

## 何时使用

优先使用 GitHub MCP，当任务主要发生在 GitHub 平台上：

- 查询或更新 issue、pull request、review、comment
- 搜索 GitHub 仓库、代码、文件、工作流运行结果
- 读取远端仓库文件或元数据
- 需要跨仓库定位 owner、标签、讨论、提交记录

## 何时不要用

- 修改当前工作区代码：优先直接读写本地仓库文件
- 纯网页信息检索：优先 `web_search` 或 `grok_search`
- 需要 git 历史细节且本地仓库已存在：优先本地 `git` 命令

## 使用原则

- 能用 GitHub MCP 直接完成的平台操作，不要退化成手写 GitHub REST URL
- 涉及仓库上下文时，先确认 `owner/repo`，避免在错误仓库创建 issue 或 PR
- 涉及写操作时，先读取现有 issue/PR/文件内容，避免重复创建或覆盖
- 如果 GitHub MCP 不可用或认证失败，再回退到本地 `git` 或普通网页检索

## 工具识别

GitHub MCP 工具会以 `mcp__github__*` 形式出现，例如：

- `mcp__github__search_repositories`
- `mcp__github__search_code`
- `mcp__github__get_file_contents`
- `mcp__github__create_issue`
- `mcp__github__create_pull_request`

先看工具清单和参数，再调用最小必要工具。
