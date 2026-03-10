# Hermit 与 OpenClaw 的比较

这份文档只做高层定位比较，不试图完整描述 OpenClaw 的全部实现细节。

Hermit 相关结论以当前仓库源码为准；OpenClaw 相关信息只引用其公开官方来源：

- [OpenClaw 官网](https://openclaw.ai/)
- [OpenClaw FAQ](https://docs.openclaw.ai/help/faq)
- [OpenClaw GitHub 仓库](https://github.com/openclaw/openclaw)

## 一段话总结

两者都属于本地优先 agent 系统，但关注点不同：

- Hermit 偏向小而清晰的个人 runtime
- OpenClaw 偏向更完整的平台与多通道产品面

## Hermit 的现实定位

从当前源码看，Hermit 的中心是：

- `AgentRunner`
- `ClaudeAgent`
- `PluginManager`
- `~/.hermit` 状态目录

它把大量能力放在 builtin plugin，而不是堆进 core。

当前仓库里真实存在的 surface 主要是：

- CLI
- Feishu
- scheduler
- webhook
- MCP

## OpenClaw 的公开定位

基于 OpenClaw 官方 FAQ 和 GitHub 仓库，可以确认它公开强调的能力面明显更宽，例如：

- Gateway / dashboard 一类控制面
- 更多消息通道与 channel 文档
- 本地模型与 OpenAI-compatible provider 支持

我这里的判断来自官方公开资料，而不是对其内部实现做推断。

## 架构中心差异

### Hermit

Hermit 是 runtime-first：

```text
AgentRunner -> ClaudeAgent -> ToolRegistry -> 本地工具 / 插件工具 / MCP
```

它的优势是：

- 代码路径短
- 源码容易读透
- 状态目录简单
- 插件装配关系清楚

### OpenClaw

OpenClaw 从公开资料看更接近 platform-first：

- 有 gateway 运维面
- 有更多 channels
- 有更广的 provider / surface 范围

这类设计通常带来更大的能力面，也带来更高的理解和部署复杂度。

## 状态模型差异

### Hermit

Hermit 的长期状态主要放在 `~/.hermit`，并与当前 workspace 分离。

### OpenClaw

OpenClaw FAQ 明确提到默认状态目录是 `~/.openclaw`，日志与服务状态也围绕该目录和 gateway 服务展开。

## 扩展模型差异

### Hermit

Hermit 的扩展核心是 `plugin.toml`，当前已用入口维度是：

- `tools`
- `hooks`
- `commands`
- `subagents`
- `adapter`
- `mcp`

### OpenClaw

OpenClaw 的公开资料展示了更大的平台生态与更多运行面，但它的整体结构也因此更重。

## 通道与模型策略

### Hermit

当前源码里实际落地的通道和外部入口比较克制：

- CLI
- Feishu
- webhook
- scheduler

模型层当前围绕 Anthropic Messages API 实现，没有通用 provider abstraction。

### OpenClaw

OpenClaw FAQ 中能明确看到 Telegram、WhatsApp、Slack、Discord、iMessage 等 channel 文档和运维说明，也公开支持本地模型与 OpenAI-compatible provider。

## 结论

如果你的目标是：

- 自己能快速读懂并改造 runtime
- 保持单机和个人工作流优先
- 依赖清晰的文件化状态

Hermit 更合适。

如果你的目标是：

- 更完整的平台能力
- 更宽的通道覆盖
- 更大的运维与控制面

OpenClaw 的公开能力面明显更大。
