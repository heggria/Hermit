# 为什么选择 Hermit

> [English version](./why-hermit.md)

Hermit 存在的原因是：很多 agent 优化的是响应速度，而不是持久信任。

大多数 agent 把请求当作一次对话回合来处理。Hermit 把有意义的工作当作带有状态、权限、证据和结果的任务。

大多数 agent 把工具执行看作关键事件。Hermit 把工具执行看作更大的受治理路径中的一个阶段：

`request -> task -> step attempt -> policy -> approval -> scoped execution -> receipt -> proof / rollback`

当工作是长期运行的、可中断的、需要审批的、或者事后值得审计的时候，这种差异就很重要了。

Hermit 没有试图成为 agent 平台可以做的一切。它试图让一类 agent 工作变得异常可读：

- local-first
- 跨时间保持状态
- 在执行边界处受治理
- 事后可检查
- 出问题时可恢复

## Session-First Agent 的问题

Session-first agent 通常很擅长保持对话感。但它们在保持可问责性方面远没有那么一致。

在很多系统中：

- 工作单元是一个聊天回合
- 副作用在宽泛的进程权限下发生
- 上下文主要是消息历史
- 记忆是松散挂靠的
- 可审计性是事后从日志中重建的

这对轻量级助理工作来说没问题。但当操作者事后问以下问题时就不行了：

- 到底发生了什么？
- 为什么会发生？
- 使用了什么证据？
- 什么权限允许了这个操作？
- 什么发生了变化？
- 这个操作能否被验证或回滚？

## Hermit 的核心论点

Hermit 构建在一组不同的论点之上。

### 1. 任务是持久的工作单元

Hermit 不是 session-first 的。工作应该落入持久的 task 语义中，可以跨越暂停、审批、后续跟进和检查。

这就是为什么 kernel 以这些记录为中心：

- `Task`
- `Step`
- `StepAttempt`
- `Ingress`
- `Conversation`

### 2. 执行必须被治理

模型可以推理、规划和提议。但它不应该默默地继承宽泛的执行权限。

Hermit 把有副作用的工作推过：

- policy evaluation（策略评估）
- decision recording（决策记录）
- approval（需要时的审批）
- scoped authority records，如 capability grant 和 workspace lease

重点不仅仅是说"人在环路中"。重点是让权限变得显式。

### 3. Artifact 比单纯的 Transcript 更重要

消息历史是有用的。但它不够。

Hermit 把 artifact 当作上下文和证据的一等单元。一个任务应该可以用它读取了什么、产出了什么、观察了什么、引用了什么来解释。

这就是为什么 Hermit 的上下文正在向以下方向发展：

- artifact 引用
- working state
- belief
- 持久 memory record
- task 和 step 摘要

### 4. 记忆必须和证据绑定

Hermit 不把记忆当作通用的便签系统。

它区分了：

- bounded working state（有界工作状态）
- revisable belief（可修订的信念）
- durable memory record（持久记忆记录）

持久记忆的提升应该引用证据，并遵守作用域、保留期和失效规则。这很重要，因为没有出处的记忆会变成隐藏的权限来源。

### 5. 重要操作以 Receipt 结束

工具执行不是终点。

对于重要操作，Hermit 希望 kernel 保留结构化的记录：

- 输入
- 输出
- policy 结果
- approval
- capability grant 和 workspace lease
- 执行环境
- 结果摘要
- 支持时的 rollback 关系

这就是 receipt 和 proof 路径的作用。

## Hermit 已经有什么

Hermit 还很早，但仓库不是空洞的口号。

当前代码库已经包含：

- 一个本地 kernel ledger
- task、approval、principal、capability grant、workspace lease、receipt、belief、memory record、rollback、conversation 和 ingress 的一等记录
- 带 policy 和 approval handling 的 governed executor 路径
- proof summary、proof export，以及对已支持 receipt 的 rollback 支持
- context compilation 和 memory governance 原语

这意味着：

- Hermit 已经可以被描述为一个 local-first governed agent kernel
- 但还不应该被描述为每个运行时表面都完全匹配目标 spec

## Hermit 不是什么

Hermit 最好不要被理解为：

- 又一个 chat-plus-tools 壳
- 一个 cloud-first 的不透明 agent 服务
- 一个没有取舍的自主 agent 平台
- 一个已完成的 `1.0` kernel

Hermit 更好的理解方式是：一个 alpha 阶段的系统，有强烈的 kernel 论点，并且代码库已经让这个论点可见。

## 继续阅读

- [architecture.md](./architecture.md) — 当前实现
- [kernel-spec-v0.1.md](./kernel-spec-v0.1.md) — 目标架构
- [governance.md](./governance.md) — policy、approval 和 scoped authority
- [receipts-and-proofs.md](./receipts-and-proofs.md) — 完成、验证和回滚语义
