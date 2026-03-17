# 快速上手

> [English version](./getting-started.md)

本指南将带你从安装到完成第一个任务、第一次审批和第一次 proof 导出。

Hermit 是 local-first 的。默认情况下，所有状态都保存在 `~/.hermit` 下，包括配置、任务、artifact、receipt 和 memory。

## 运行要求

- Python `3.13+`
- 推荐使用 [`uv`](https://docs.astral.sh/uv/)
- 一个 LLM provider 配置

可选：

- 如果需要 Feishu 长运行 channel ingress，需要 Feishu 凭据
- 如果需要 macOS 菜单栏伴侣，需要 `rumps`

## 安装

最简路径：

```bash
make install
```

这会初始化本地工作区并安装 Hermit。

手动安装：

```bash
uv sync --group dev --group typecheck --group docs --group security --group release
uv run hermit init
```

## 配置 Provider

Hermit 支持 `claude`、`codex` 和 `codex-oauth`。

使用 OpenAI 的示例：

```bash
export HERMIT_PROVIDER=codex
export OPENAI_API_KEY=sk-...
export HERMIT_MODEL=gpt-5.4
```

你也可以将长期配置保存在 `~/.hermit/.env` 或 `~/.hermit/config.toml` 中。

检查当前生效的配置：

```bash
hermit config show
hermit auth status
```

更多配置细节请参阅 [configuration.md](./configuration.md)。

## 运行第一个任务

交互式对话：

```bash
hermit chat
```

一次性任务：

```bash
hermit run "Summarize the current repository"
```

长运行服务：

```bash
hermit serve --adapter feishu
```

## 检查 Task Kernel

Hermit 不只是一个会话壳。它已经在本地 kernel ledger 中记录持久化的 task 对象。

列出所有任务：

```bash
hermit task list
```

查看单个任务：

```bash
hermit task show <task_id>
```

查看任务事件：

```bash
hermit task events <task_id>
```

查看 receipt：

```bash
hermit task receipts --task-id <task_id>
```

查看 proof 摘要：

```bash
hermit task proof <task_id>
```

导出 proof bundle：

```bash
hermit task proof-export <task_id>
```

## 审批与回滚

当一个关键动作因需要审批而被阻塞时，Hermit 会记录一个 approval 对象，并通过 task CLI 暴露。

批准：

```bash
hermit task approve <approval_id>
```

拒绝：

```bash
hermit task deny <approval_id> --reason "not safe"
```

如果某个 receipt 支持回滚：

```bash
hermit task rollback <receipt_id>
```

回滚目前还不是通用功能。它仅对已支持的 receipt class 有效，而非一个全面的保证。

## 状态存储位置

`~/.hermit` 下的常见路径：

- `.env`
- `config.toml`
- `kernel/state.db`
- `sessions/`
- `memory/`
- `schedules/`
- `plugins/`

Kernel 数据库是 Hermit 记录 task、step、approval、receipt、proof 和 memory 相关状态的地方。

## 如何阅读文档

建议从这里开始：

- [why-hermit.md](./why-hermit.md)
- [architecture.md](./architecture.md)
- [governance.md](./governance.md)
- [receipts-and-proofs.md](./receipts-and-proofs.md)
- [roadmap.md](./roadmap.md)

如果你正在评估 Hermit，最重要的区分是：

- `architecture.md` 描述的是仓库当前的实现
- `kernel-spec-v0.1.md` 描述的是 Hermit 正在收敛的目标架构
