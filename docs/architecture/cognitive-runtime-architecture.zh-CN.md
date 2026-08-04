# M-Agent 认知运行时目标架构

> 文档状态：目标架构  
> 基线版本：v0.2.0  
> 对应路线图：v0.3.0—v1.0.0  
> 当前实现事实以 [`../runtime/README.md`](../runtime/README.md) 和可执行验收为准

## 1. 架构目标

M-Agent 的目标不是持续调用 LLM，而是提供一个独立于单次模型调用、能够长期维护状态并按需激活认知的运行环境。

认知运行时接收外界变化、行动结果、时间条件和内部预期，将它们转化为可解释的认知状态迁移：

```text
Signal
→ Observation
→ Stimulus
→ Admission / Attention
→ Goal / Transaction Attribution
→ Cognitive State Transition
→ Context Snapshot c
→ Thinking / Strategy
→ Action / Silence
→ Effect Feedback
```

Runtime 持续维护的是状态、目标、承诺、预期、记忆和权限；`Context Snapshot c` 是某次认知激活时编译出的工作快照。

## 2. 系统边界

### 2.1 Runtime 负责

- 刺激的接纳、身份、顺序、去重、租约和最终处置；
- 判断是否分配认知资源，以及刺激属于哪个持续事务；
- 维护 Transaction、Goal、TaskState、Expectation 和 Evidence；
- 从状态、记忆和当前刺激编译 `Context Snapshot`；
- 调用 Thinking、Strategy 和 Capability，但不把某个模型实现写死为领域语义；
- 对副作用应用权限、预算、审批和交付语义；
- 将 Effect 结果重新作为刺激进入因果闭环；
- 在重启、重复投递和并发情况下恢复并保留审计证据。

### 2.2 Runtime 不负责

- 持续不断地调用模型模拟“活着”；
- 把所有输入都升级为需要 LLM 处理的刺激；
- 把模型生成的推理文本当作事实或长期记忆；
- 承诺任意外部系统的绝对 Exactly Once；
- 将特定 Prompt、LangGraph 节点或记忆算法作为稳定公共契约。

## 3. 核心领域对象

| 对象 | 职责 | 当前状态 |
| --- | --- | --- |
| Signal | 未解释的原始变化 | v0.3 目标 |
| Observation | 带来源、事件时间、接收时间和可信度的观察 | v0.3 目标 |
| Stimulus | 可能改变 Agent 认知状态的输入 | v0.2 已有内部基础，v0.3 公开化 |
| Transaction | 跨激活持续存在的一项事务 | v0.2 已实现 |
| Activation | Transaction 的一次有效认知执行批次 | v0.2 已实现 |
| Scene | conversation 范围内按实际顺序记录的时间线 | v0.2 已实现 |
| Goal / Commitment | Agent 正在追求或承担的目标与承诺 | v0.5 目标 |
| Expectation | 对未来观察或期限的可计算预期 | v0.5 定义，v0.6 运行 |
| Evidence / Belief | 有来源的观察证据与由证据支持的当前判断 | v0.5 目标 |
| Context Snapshot | 一次模型调用所使用的可追溯认知快照 | v0.5 目标 |
| Effect | 对外行动意图、执行和交付结果 | v0.2 已有基础，v0.8 完成策略边界 |
| Strategy | 可复用的程序性知识 | v0.6 Shadow，v0.7 Opt-in |

## 4. 运行链路

### 4.1 Signal 与 Observation

Adapter 将邮件、日程、Webhook、网页变化、用户消息或系统结果规范化为 Observation。Observation 必须保留原始来源引用，不能在入口阶段把推断伪装成事实。

协议应兼容通用事件字段，并扩展认知运行所需信息：

- source、type、subject；
- occurred_at、observed_at；
- idempotency_key、causation_id；
- confidence、privacy_class、payload_ref。

### 4.2 Stimulus Admission

Observation 只有在通过来源、作用域和格式校验后才成为 Stimulus Candidate。Runtime 为已接纳刺激分配耐久身份，并保证它最终进入以下处置之一：

```text
rejected | ignored | merged | deferred | activated | failed
```

“接纳”不等于“立即调用 LLM”。

### 4.3 Attention 与 Attribution

Attention 负责回答“是否值得现在认知”，Attribution 负责回答“它属于哪件事”。

处理顺序应优先使用确定性规则：

1. 来源与权限校验；
2. 去重、过期和时序修正；
3. 相关性、新颖性、紧迫性与信息价值；
4. Goal / Transaction 候选生成；
5. 必要时才使用语义模型解决歧义；
6. 无法安全归因时保持隔离，而不是污染既有事务。

### 4.4 Cognitive State Transition

刺激不能只被追加到 Prompt。被激活的刺激必须通过受约束的状态迁移更新：

- 当前 Goal 与 TaskState；
- 未完成 Commitment；
- 新增、满足、过期或失效的 Expectation；
- Observed Evidence；
- 由证据支持、可被后续证据修订的 Belief。

模型输出不得在没有证据时创建 Completed 或把推断升级为事实。

### 4.5 Context Compiler

Context Compiler 根据当前刺激和持续状态构造最小充分的 `Context Snapshot c`。每个 `ContextItem` 必须包含：

- 内容类型与来源；
- 发生时间、有效时间和新鲜度；
- 置信度与冲突标记；
- 用户与事务作用域；
- 敏感级别与 token 成本；
- 是否必须保留、可摘要或可丢弃。

当前刺激、活动目标和最新有效证据不得因为简单的从旧到新截断而丢失。

### 4.6 Thinking 与 Strategy

Thinking 消费冻结的 Context Snapshot，并输出结构化 TaskState 更新与 Decision。Strategy 是可选的程序性指导，不是事实来源。

Strategy 的接入顺序为：

```text
v0.6 Shadow
→ v0.7 Opt-in
→ v0.8 评测达标后成为默认候选
→ v1.0 稳定接口
```

任何 Strategy 都不能越过 Evidence Gate、TaskState Guard 或权限策略。

### 4.7 Action、Effect 与 Feedback

Decision 可以是行动、回答、等待、请求批准或保持沉默。外部副作用必须经过确定性的 Policy 层；模型只能提出意图，不能自行修改审批结果。

Effect 执行结果以带有 transaction、activation 和 delegate 因果身份的 Feedback 重新入队。旧 Activation 的反馈不能污染新的认知状态。

## 5. 记忆架构

### 5.1 工作记忆

Working Memory 隶属于 Transaction，保存当前任务继续执行所需的热状态。

### 5.2 工具记忆

工具记忆由模型显式调用，用于搜索和扩展查询。当前 Simple RAG 属于工具记忆，不是目标 System Memory。

### 5.3 系统记忆

系统记忆由 Runtime 自动投影、整合、召回，并通过 Context Compiler 评估是否进入 `c`：

```text
Committed Scene / TaskState / Evidence / Effect
→ Memory Projector
→ System Memory Backend
→ SystemMemoryProvider
→ Context Compiler
```

完整记忆算法可以由 WorkspaceMem 等外部后端实现；M-Agent Core 只稳定协议、生命周期、证据与隔离语义。

## 6. 时间与内生刺激

Heartbeat 只是当前到期计划的调度机制，不等同于时间认知。目标 Temporal Runtime 使用可注入 Clock，并维护事件时间、接收时间、截止时间、持续时间与 Expectation。

内生刺激由明确状态条件生成，例如：

- 预期事件在期限内没有发生；
- Commitment 临近或超过期限；
- 事务长期没有进展；
- 新 Evidence 与现有 Belief 冲突。

内生刺激必须具有去重键、冷却时间、资源配额和因果来源，防止无限自唤醒。

## 7. 可扩展边界

目标插件接口包括：

- Observation Source；
- Stimulus Normalizer；
- Attention Policy；
- Attribution Policy；
- Context Provider；
- System Memory Backend；
- Strategy System；
- Capability 与 Approval Policy。

插件不能绕过 Stimulus Admission、用户隔离、Context 证据规则和 Effect Policy。

## 8. 当前 v0.2 与目标差距

当前 Runtime 已实现 Transaction、Scene、Stimulus Inbox、基础归因、Schedule、Effect/Feedback、checkpoint、flush journal 和语义验收。尚未完成的关键目标包括：

- 通用公开 Observation/Stimulus SDK；
- Attention 的忽略、合并、延迟与信息价值语义；
- 正式 Cognitive State 与 Context Compiler；
- Runtime 自动控制的 System Memory；
- Expectation 与内生刺激；
- 实际可用的 StrategySystem；
- 统一审批和受控自主性。

因此，对外应使用“正在构建 Stimulus-Native Cognitive Runtime”，不能把目标能力描述成 v0.2 已实现事实。

