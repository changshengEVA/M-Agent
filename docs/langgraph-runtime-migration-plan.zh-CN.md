# M-Agent 向 LangGraph 运行内核迁移计划（草案）

状态：讨论稿，不代表已经决定实施  
日期：2026-07-26

## 1. 背景与结论

当前 Think-life 已经形成了清晰的 Agent 领域语义，包括：

- 所有刺激统一进入 Perception；
- 一个 conversation 可以包含多个 transaction；
- task state 与 WM 按 transaction 隔离；
- execution feedback 通过 `transaction_id + delegate_id` 确定性归因；
- Scene 是 conversation 级、跨 transaction 的追加时间线；
- Thinking 只负责规划，每次 delegate 只执行一个 capability；
- 用户回复完成不等于任务语义完成。

这些语义应继续由 M-Agent 定义。迁移的目标不是回到旧的
`create_agent -> tool loop`，而是让 LangGraph 承担通用运行时职责，
减少 M-Agent 对 checkpoint、恢复、状态机执行和通用调度基础设施的重复建设。

目标边界：

```text
M-Agent
├── 领域语义
│   ├── Stimulus / Perception
│   ├── Transaction / TaskState / Delegate
│   ├── 事务归因与完成条件
│   ├── Scene 与 flush 规则
│   └── 优先级和抢占策略
│
├── LangGraph
│   ├── 事务内部状态图
│   ├── checkpoint / resume
│   ├── 节点重试与流式事件
│   └── 状态检查和运行追踪
│
└── LangChain
    ├── 模型适配
    ├── tool 协议
    └── structured output
```

## 2. 本次迁移的目标

1. 保留当前 Think-life 运行时不变量和对外行为。
2. 将单个 transaction 内部的执行循环迁移为显式 StateGraph。
3. 使用 checkpointer 逐步替代纯进程内的事务运行状态。
4. 消除 `TransactionRecord`、Thinking state 等多套状态容器之间的重复。
5. 保持 Scene 为独立领域事件日志，不把 checkpoint 当作 Scene。
6. 保留现有 tool、model provider、episodic memory 和系统插件边界。
7. 允许旧运行时和新运行时在迁移期按 transaction 灰度切换。

## 3. 非目标

- 不 fork 或直接修改 LangChain/LangGraph 源码。
- 不重新使用 `create_agent` 作为产品级主循环。
- 不在第一阶段替换 Scene 存储和 flush 生命周期。
- 不在第一阶段引入分布式 worker 或 LangSmith Agent Server。
- 不把 LangGraph Store 等同于完整的 MemoryCore、实体系统或 KG。
- 不在一个版本中同时重写运行时、记忆、工具系统和 API。
- 不为了适配图执行而弱化现有事务、反馈和完成条件。

## 4. 必须冻结为验收测试的不变量

迁移前应先把以下行为固定为黑盒或集成测试：

1. 所有用户消息、执行反馈和 schedule stimulus 都必须先入队。
2. 每个 conversation 同一时刻最多只有一个 drainer 消费入口队列。
3. 非 feedback 刺激必须先完成事务归因；feedback 不允许重新语义归因。
4. execution feedback 必须同时匹配 `transaction_id` 和当前
   `active_delegate_id`。
5. 每个 transaction 独占 task state、WM、turn count 和 episode buffer。
6. 一个 conversation 只有一条 Scene 时间线。
7. Thinking 不得直接调用 capability。
8. 每次 delegate 最多调用一个 capability。
9. 参数不足时不得误调用工具，应返回澄清反馈。
10. `reply_to_user` 必须经过正常 capability 审计路径。
11. 回复成功不能自动把 transaction 标记为 completed。
12. 高优先级刺激只在安全边界协作式抢占，不能丢失被打断刺激。
13. 重复 feedback、重复恢复和节点重放不能导致工具副作用重复发生。
14. flush 只能在没有未决刺激、在途执行和待回复结果时推进水位线。

## 5. 当前组件到目标组件的映射

| 当前组件 | 目标处理方式 | 说明 |
|---|---|---|
| `ThinkLifeRuntime` | 保留为外层 runtime host | 管理入口、Scene、兼容 API 和 runtime 选择 |
| `StimulusInbox` | 第一阶段保留 | LangGraph 不负责跨 transaction 的全局优先级 |
| `ThreadDrainer` | 第一阶段保留 | 继续保证每个 conversation 单消费者 |
| `StimulusAttributor` | 保留领域逻辑 | 先选择 transaction，再恢复对应事务图 |
| `TransactionRegistry` | 逐步降级为索引和运行元数据 | task state/WM 的权威状态迁入 checkpoint |
| `ThinkLifeLoop` | 逐步替换为 transaction graph | 外层队列循环与内层事务循环分离 |
| `ThinkingAgent.handle` | 拆成图节点 | task-state update 与 planning 分开建模 |
| `ExecutionAgent` | 保留为确定性能力适配器 | 图节点直接调用 registry，不引入第二个 ReAct loop |
| `reply_to_user` | 保持普通 capability | 仍生成 feedback，仍不代表任务完成 |
| Scene system | 保持独立 | checkpoint 只保存运行状态，不替代事件日志 |
| episodic RAG | 暂时保持独立 | 后续仅评估存储/向量检索适配 |
| schedule lifecycle | 通过 stimulus 接入 | lease、running、done 仍由 M-Agent 定义 |

## 6. 建议的身份与状态模型

### 6.1 身份映射

```text
conversation_id
    ├── 拥有 StimulusInbox
    ├── 拥有 Scene
    └── 路由到一个或多个 transaction_id

transaction_id
    ├── 对应一个 LangGraph checkpoint thread
    ├── 拥有 TaskState 和 WM
    └── 同一时刻最多拥有一个 active_delegate_id

delegate_id
    └── 标识一次参数化/工具执行/回复委托及其反馈
```

不建议直接使用 `conversation_id` 作为唯一 graph thread，因为同一 conversation
可以包含多个相互隔离的任务。可以使用 `transaction_id`，或者使用
`conversation_id + transaction_id` 的稳定组合键。

### 6.2 初始 GraphState

建议只保存恢复事务所需的权威状态：

```text
GraphState
├── conversation_id
├── transaction_id
├── transaction_kind
├── priority
├── status
├── current_stimulus
├── task_state
├── wm_entries
├── episode_buffer
├── turn_count
├── think_rounds
├── delegate_count
├── active_delegate_id
├── current_decision
├── pending_tool_call
├── latest_feedback
├── cancellation
├── last_error
└── schema_version
```

以下数据不应复制进 checkpoint：

- 完整 Scene；
- 完整 dialogue history；
- capability registry；
- model client；
- schedule store 的权威记录；
- 大体积工具结果原文。

图节点运行时按需读取这些外部系统，只在 GraphState 中保存引用、摘要或稳定 ID。

## 7. 建议的事务状态图

第一版 StateGraph 可采用以下节点：

```text
START
  ↓
load_context
  ↓
update_task_state
  ↓
completion_gate ── completed ──────────────→ finalize
  │
  ├── awaiting_user ───────────────────────→ suspend
  │
  └── processing
          ↓
        plan
          ↓
        route_decision
          ├── execute
          │     ↓
          │   fill_tool_args
          │     ├── clarify → record_feedback ─┐
          │     └── invoke  → execute_tool     │
          │                        ↓           │
          │                   record_feedback ─┘
          │                        ↓
          │                 update_task_state
          │
          ├── reply
          │     ↓
          │   execute_reply_tool
          │     ↓
          │   record_feedback
          │     ↓
          │   update_task_state
          │
          └── silent
                ├── completed → finalize
                └── otherwise → suspend
```

关键约束：

- `plan` 节点只产生结构化 decision。
- `execute_tool` 是确定性节点，不允许模型自行继续调用其他工具。
- 每次工具或回复委托完成后都进入 `record_feedback`。
- task completion 只由更新后的 task state 决定。
- 工具调用前后都必须设置幂等键，避免 checkpoint 恢复造成重复副作用。

## 8. 分阶段实施

### 阶段 0：建立基线

工作内容：

- 将第 4 节的不变量补齐为自动化测试。
- 记录典型事务的 runtime event trace，作为新旧实现对照基线。
- 明确当前取消语义是边界取消还是工具调用中途取消。
- 确认现有 episode buffer、flush 和 transaction state 的唯一所有者。
- 为现有状态结构增加文档化的 schema version 约定。

完成条件：

- 可以通过测试判断新实现是否保持语义，而不是只比较最终回复文本。

### 阶段 1：事务图 PoC

范围：

- 只支持普通用户 stimulus。
- 只支持一个 transaction。
- 使用 fake/in-memory checkpointer。
- 不接入 schedule、抢占和真实持久化。
- 使用无副作用 fake tool 验证完整循环。

验证路径：

```text
user stimulus
→ task-state update
→ plan
→ one tool
→ feedback
→ plan/reply
→ completion gate
```

完成条件：

- 新旧运行时产生等价的关键事件顺序。
- Thinking 没有直接执行工具。
- 每轮只产生一个 delegate。
- 回复与任务完成仍然分离。

### 阶段 2：持久化与恢复

工作内容：

- 引入适合部署环境的持久化 checkpointer。
- 将 task state、WM、turn count、delegate 状态迁为 checkpoint 权威数据。
- `TransactionRegistry` 只保留事务索引、状态摘要和活跃运行句柄。
- 为工具调用实现幂等键和执行结果去重。
- 增加以下故障注入点：
  - planning 完成后崩溃；
  - tool 调用前崩溃；
  - tool 成功但 feedback 入队前崩溃；
  - reply 成功但 completion update 前崩溃。

完成条件：

- 进程重启后能够恢复非终态 transaction。
- 恢复不会重复产生工具副作用或用户回复。
- 不再同时维护两份 task state/WM 权威数据。

### 阶段 3：接回现有 Perception 与 Scene

工作内容：

- 继续由现有 attributor 选择或创建 transaction。
- 选定 transaction 后调用或恢复对应 transaction graph。
- 图节点读取 conversation Scene tail，但不复制 Scene 全量数据。
- graph event 映射回现有 runtime SSE/event emitter。
- 保持现有 Scene entry 格式和单调序号。

完成条件：

- 同一 conversation 中多个 transaction 的 WM 不串扰。
- feedback 始终回到原 transaction。
- Scene 顺序、审计字段和 API 输出保持兼容。

### 阶段 4：优先级、schedule 与协作式抢占

工作内容：

- 保留外层 `StimulusInbox` 的优先级策略。
- 保留每 conversation 单 drainer 约束。
- 将取消请求传入当前运行的 transaction graph。
- 只在安全节点边界 suspend，并重新入队被中断刺激。
- schedule stimulus 使用与 user stimulus 相同的入口。
- schedule 的 lease/running/done 由 transaction 终态驱动。

完成条件：

- 高优先级用户刺激不会丢失低优先级 schedule stimulus。
- 抢占次数仍受上限控制。
- 已经开始的非幂等工具不会被不安全地重复调用。
- schedule 状态与 transaction 状态不会永久分叉。

### 阶段 5：flush、记忆和数据生命周期

工作内容：

- 明确 transaction checkpoint 的归档、删除或保留周期。
- flush 前继续检查 inbox、in-flight、pending reply 和 Scene 覆盖条件。
- flush 成功后再推进 Scene watermark。
- episode buffer 从唯一权威状态中 drain。
- 评估 LangGraph Store 是否仅替换简单 RAG 的存储/检索部分。

完成条件：

- flush 前后的 Scene 和 episodic memory 行为与当前规格一致。
- 不因删除 checkpoint 丢失尚未进入长期记忆的数据。
- checkpoint、Scene 和 episodic store 的职责没有重叠。

### 阶段 6：灰度切换与旧代码收缩

建议按 transaction 选择 runtime，不能让同一个 transaction 在中途切换：

```text
runtime_engine = "think_life_v1" | "langgraph_v1"
```

灰度顺序：

1. 测试环境和 fake tool；
2. 内部 thread；
3. 新创建的普通 user transaction；
4. schedule transaction；
5. 默认启用 LangGraph；
6. 观察稳定后删除旧事务内循环。

注意：

- shadow 模式只能比较 planning/state event，不能同时执行有副作用的工具。
- 旧运行时在迁移期保持可回退，但不继续增加新功能。
- 删除旧代码前必须确认没有旧版非终态 transaction。

## 9. 测试矩阵

| 场景 | 必须验证 |
|---|---|
| 单轮直接回复 | 回复仍经 `reply_to_user` |
| 单工具任务 | 只调用一次工具并生成 feedback |
| 多步骤任务 | 一个 delegate 完成后重新规划 |
| 参数缺失 | 不误调用工具，进入 awaiting user |
| 多 transaction | task state、WM、feedback 不串扰 |
| 重复 feedback | 被拒绝或幂等处理 |
| 错误 delegate ID | 不得写入 transaction |
| 用户消息插入执行中 | 在安全边界 suspend/requeue |
| schedule 与用户消息竞争 | 遵守优先级且不丢 schedule |
| tool 调用后崩溃 | 恢复不重复副作用 |
| reply 后崩溃 | 恢复不重复发送回复 |
| reply 成功但任务未完成 | transaction 保持 processing |
| Scene 写入失败 | 不推进 flush watermark |
| 进程重启 | 恢复非终态 transaction |
| checkpoint schema 升级 | 旧状态可迁移或明确拒绝 |

## 10. 回退策略

1. 每个 transaction 创建时固定 `runtime_engine`。
2. 旧 runtime 保留到新 runtime 完成完整灰度周期。
3. Scene、tool 和 API 数据格式在迁移期保持兼容。
4. 新 runtime 出现问题时，仅将后续新 transaction 路由回旧 runtime。
5. 已经由 LangGraph 管理的非终态 transaction 优先修复或恢复，不能直接交给旧
   runtime 读取未经转换的 checkpoint。
6. 所有 checkpoint schema 变更必须具有版本号和迁移函数。

## 11. 主要风险

### 11.1 把 checkpoint 误当作 Scene

checkpoint 是可恢复状态，Scene 是领域事件历史。两者合并会破坏审计和 flush 语义。

### 11.2 同一状态出现两个权威来源

迁移期最危险的情况是 `TransactionRegistry` 和 GraphState 同时修改 task state/WM。
每个阶段必须明确唯一写入者。

### 11.3 节点重放导致副作用重复

LangGraph 恢复可能重新进入节点。外部工具、schedule 更新和用户回复必须使用稳定
幂等键，并将执行结果持久化在可恢复边界。

### 11.4 将外层优先级错误地下沉到单事务图

优先级是多个 stimulus/transaction 之间的调度关系，不应由某个 transaction graph
自行决定。第一阶段继续保留外层 inbox。

### 11.5 为了使用框架而改变领域语义

如果公开 API 不能直接表达某项约束，应先使用自定义节点、外层 host 或 adapter；
不能默认删除 transaction、Scene 或 completion gate。

### 11.6 依赖版本耦合

当前项目锁定的 LangChain/LangGraph 版本应与实际 PoC API 对齐。升级必须在独立
阶段完成，并通过运行时不变量测试，不能和业务迁移混为一次提交。

## 12. 里程碑与决策门

| 里程碑 | 结果 | 是否继续的判断 |
|---|---|---|
| M0：基线完成 | 不变量测试和事件 trace | 无可靠基线则不开始迁移 |
| M1：PoC 完成 | 单事务图行为等价 | 图结构明显更复杂则重新评估边界 |
| M2：恢复完成 | 崩溃恢复且无重复副作用 | 无法保证幂等则不接真实工具 |
| M3：多事务完成 | Perception、Scene、feedback 接通 | 串扰或语义漂移则暂停灰度 |
| M4：调度完成 | 优先级、抢占、schedule 接通 | 不优于旧 runtime 则保留外层实现 |
| M5：默认切换 | 新 transaction 默认走 LangGraph | 观察期稳定后才删除旧循环 |

## 13. 开始实施前需要确定的问题

1. checkpoint 后端选用 SQLite、PostgreSQL 还是其他实现。
2. graph thread ID 使用 `transaction_id` 还是稳定组合键。
3. 当前同步工具接口是否继续保留，还是逐步统一为 async。
4. 哪些 capability 有外部副作用，以及它们的幂等键如何构造。
5. checkpoint 的保留时间和已完成 transaction 的归档策略。
6. 是否需要继续支持进程内轻量部署。
7. 何时评估分布式 worker/Agent Server，而不是继续扩张本地 drainer。

## 14. 建议的第一个实验

第一个实验不连接真实 schedule、邮件或其他外部副作用系统，只实现：

1. 一个用户 stimulus；
2. 一个需要参数化的 fake capability；
3. 一次结构化 execution feedback；
4. 一次 `reply_to_user` fake adapter；
5. processing、awaiting user、completed 三种 task state；
6. 每个节点之间可注入进程崩溃；
7. 新旧运行时输出同一套规范化 event trace。

该实验的核心评价标准不是代码是否更短，而是：

- 状态是否只有一个权威来源；
- 故障恢复是否更可靠；
- 图结构是否忠实表达当前领域语义；
- 外层 runtime 是否明显减少通用基础设施职责；
- 后续 LangGraph 升级是否只影响 adapter，而不影响 M-Agent 领域模型。

只有这个实验通过后，才进入真实代码迁移。
