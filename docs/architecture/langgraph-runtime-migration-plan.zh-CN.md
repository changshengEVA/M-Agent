# M-Agent 向 LangGraph 运行内核迁移计划（草案）

状态：讨论稿；领域语义与阶段入口已对齐，是否采用 LangGraph 仍由 PoC 决策
修订日期：2026-07-28

## 1. 文档定位与迁移结论

M-Agent 是面向长期人机协作、可持续积累任务状态并可高度客制化的人工智能助手。
LangGraph 如果被采用，只承担一部分 Runtime 执行职责，不定义 M-Agent 的领域语义。

迁移依据的优先级固定为：

```text
overall-design-architecture.md
→ runtime-migration-semantic-test-spec.zh-CN.md
→ current-project-progress-and-design-plan.md
→ 本迁移实施方案
→ 当前 Runtime 实现
```

因此：

- 当前 ThinkLife 是需要接受共享语义测试的旧 Runtime，不是自动正确的迁移真理；
- ThinkLife 的 Known Gap 不能被复制到 LangGraph；
- 新旧 Runtime 可以使用不同的类、存储和状态机，但必须产生等价的领域观察结果；
- P5/M0 以前先收紧 ThinkLife 领域基线；只有 M0 通过后才进入 P6 LangGraph PoC；
- PoC 若不能降低恢复和编排复杂度，或者迫使领域语义迁就框架，可以停止迁移并保留
  ThinkLife。

目标边界是：

```text
M-Agent 定义“什么行为是正确的”
LangGraph 候选实现“单个 transaction 的执行如何暂停、恢复和追踪”
LangChain 继续提供模型、工具协议和结构化输出适配
```

## 2. 目标与非目标

### 2.1 迁移目标

1. 保留并实现三大结构性功能：TX、SP、AT。
2. 将单个 transaction 内的执行流程表达为显式、可恢复的图。
3. 复用 LangGraph 的图执行、checkpoint/resume、节点重试和事件流能力。
4. 保持 Transaction Store、Scene Store 与 Flush Journal 的领域权威边界。
5. 让工具执行脱离刺激消费者调用栈，以 Feedback 重新进入 Perception。
6. 使用同一套共享语义场景比较 `think_life_v1` 与 `langgraph_v1`。
7. 迁移期按 transaction 固定 Runtime，允许灰度和可控回退。

### 2.2 非目标

- 不 fork 或直接修改 LangChain/LangGraph 源码。
- 不恢复为通用 `create_agent → tool loop` 产品主循环。
- 不把 checkpoint、Scene、Transaction 或长期记忆合并为同一概念。
- 不让 graph phase 取代 `continue/pause/complete/archive` 领域状态。
- 不在 P6 PoC 同时重写记忆系统、真实工具、Schedule、API 和全部持久化。
- 不在当前版本实现周期性 Scheduled Plan。
- 不在当前版本实现通过自然语言检索 archive 并恢复。
- 不把 UI Pause/Delete 建模为需要排队的普通刺激。
- 不要求所有现有内部单元测试成为跨 Runtime 契约。

## 3. 必须冻结的跨 Runtime 语义

共享规格以
[runtime-migration-semantic-test-spec.zh-CN.md](runtime-migration-semantic-test-spec.zh-CN.md)
为准。本节只列迁移时最容易被框架边界改变的语义。

### 3.1 三大功能与 Gate

| 领域 | 顶层场景 | 当前确定性 Core | 独立评测 |
|---|---:|---:|---:|
| TX：Transaction 与 Scene | 10 | 10 | 0 |
| SP：conversation 内刺激池 | 8 | 8 | 0 |
| AT：来源分流与无来源归因 | 10 | 9 | 1 |
| 合计 | 28 | 27 | 1 |

`AT-10` 是真实 Matcher Evaluation，不计入确定性 Core Gate。旧
`INV-01～INV-14` 继续作为 Supporting Tests 和历史差距证据，但不再定义 M0。

新增的 activation、UI 控制、Feedback Gate、异步工具、一次性 Schedule 和 Flush
可见性都是现有 TX/SP/AT 编号的子场景，不新增第四类功能，也不增加顶层编号。
顶层编号与测试层级彼此独立；每个 variant 必须另带
`layer=core|robustness|matcher_evaluation|supporting`。重启、重复投递、故障恢复、
lease takeover 等仍挂在原编号下，但不计入 27 项 Core 通过率。

### 3.2 核心语义

- 一个 conversation 有一条追加式 Scene，可以同时包含多个相互隔离的 transaction。
- `transaction_id` 是长期任务身份；`activation_id` 是一次有效执行批次；
  `delegate_id` 是该批次中的一次异步工具委托。
- 新建 transaction，或从已经结束、失效的批次恢复 transaction 时开启新 activation。
- 等待当前 activation 的合法 Feedback 不开启新 activation。
- Feedback 必须校验
  `transaction_id + activation_id + delegate_id`，并且尚未消费。
- admission 时无效的 Feedback 在正常刺激池之前废弃；接纳后才失效的 Feedback 在
  preconsume 终止。两者都以带 stage/reason 的 `Expected Discard` 作为通过场景中的
  证据，不是 Failure、Known Gap 或新的运行状态。
- Scheduled Plan 到期前只是计划记录；当前版一次性计划到期后锁定原 transaction、
  开启新 activation，执行完成进入 `complete`，再由后续 Flush 归档。
- UI Pause/Delete 是 transaction 级直接控制旁路。Pause 中止当前 Thinking，当前刺激
  不重入，当前 activation 失效；Delete 终止 transaction 生命周期。
- 工具异步执行。Thinking 发出 delegate 并提交本次 transaction 状态后即释放刺激
  消费者，结果稍后以 Feedback 重新经过 Perception。
- Flush 可以人工或自动触发，但始终是用户可见的会话语义边界；只有 Flush 可以执行
  `complete → archive`。
- 合法显式 UI/控制恢复可以定位 pause、未 flush complete 或 archive；archive 不参加
  无来源自动匹配，旧 Feedback 不能充当恢复动作。

### 3.3 结果和范围解释

| 标签 | 迁移判断 |
|---|---|
| `Passed` | 当前版目标语义成立 |
| `Expected Discard` | 预期输入在 admission 被拒收，或接纳后在 preconsume 因来源失效而终止；所属场景仍为 Passed |
| `Known Gap` | 运行前已登记、当前 Runtime 明确尚未满足的目标语义 |
| `Failure` | 原本应通过，却发生未登记错误或回归 |
| `Not Implemented` | adapter 或能力尚未实现 |
| `Not Covered` | 目标语义尚无可执行场景或断言 |
| `Future` | 不属于当前版本，不运行也不计入 Gap |

结构化报告使用 `passed`、`known_gap`、`failed`、`not_implemented`、
`not_covered` 等规范化 token；上表是 CLI/UI 展示名。

## 4. 目标 Runtime 边界

### 4.1 组件关系

```text
Perception Gateway
  ├── Feedback Admission Gate
  │     └── invalid → Expected Discard evidence
  └── accepted stimulus
          ↓
Durable Conversation Stimulus Inbox
  ├── unique ingress + accepted_seq + disposition
  └── one Thinking consumer per conversation
          ↓
Preconsume Source Recheck
  ├── stale Feedback → Expected Discard evidence
  ├── Schedule conflict → run blocked/cancelled/idempotent + delivery handled
  └── valid
          ↓
Source Router / Transaction Attributor
  ├── valid sourced stimulus → deterministic target
  └── source-less stimulus → pause / unflushed complete matcher
          ↓
Transaction Runtime Host
  ├── Runtime Unit of Work      # 跨领域相关 mutation 的提交边界
  ├── Transaction Store        # 领域权威
  ├── Scene Store              # Scene 权威
  ├── Flush Journal            # Flush 提交权威
  ├── Schedule Store           # 未来计划与到期记录权威
  ├── UI Transaction Control   # Pause/Delete 旁路
  └── Runtime Engine
        ├── think_life_v1
        └── langgraph_v1
               ├── Transaction StateGraph
               └── Checkpointer # 仅图执行恢复

StateGraph
  └── delegate intent
          ↓
Async Effect Executor + Effect Ledger
          ↓
Execution Feedback
          └── 回到 Feedback Admission Gate
```

优先级只存在于单个 conversation 的刺激集合中。它属于外层 SP 调度，不下沉给某个
transaction graph 决定。

### 4.2 状态权威

| 数据 | 唯一领域权威 | checkpoint 中允许保存的内容 |
|---|---|---|
| transaction 身份、WM、任务状态、四状态、lifecycle tombstone、`runtime_engine`、revision | Transaction Store | 稳定 ID、engine/schema 版本、期望 revision、只读运行快照或摘要 |
| activation/delegate 因果归属、有效性、Feedback 消费状态 | Transaction Store | 当前图恢复所需的 activation/delegate ID 和执行阶段 |
| stimulus 唯一 ingress、conversation 内 accepted_seq、partition owner/lease、claim fencing 与最终 disposition | Stimulus Inbox Store | 当前 stimulus ID/claim token 引用，不复制队列权威 |
| 外部 effect intent、dispatch、执行结果、Feedback outbox 与声明的 delivery guarantee | Effect Ledger | effect ID 和待恢复 phase |
| conversation Scene | Scene Store | watermark 或 Scene 引用，不复制完整历史 |
| Flush 提交、payload、outbox | Flush Journal | 当前图与 flush 无关的恢复引用 |
| 图节点位置、中断点、临时计算状态 | LangGraph checkpointer | 权威内容 |
| Schedule 计划、到期和幂等运行记录 | Schedule Store | schedule ID/run ID 引用 |

checkpoint 可以引用 `transaction_id + transaction_revision + activation_id`。图恢复后
提交领域状态时必须使用 revision 条件更新；若 Transaction Store 已因 UI Pause/Delete
或其他合法操作产生新 revision，旧 checkpoint 不能静默覆盖。重新读取后必须先区分：

- 若 transaction 已删除、activation 已因 UI Pause/Delete 失效，或 control token 已
  改变，则当前 graph run 永久终止，不得 reload 后以旧刺激继续，也不得自动创建新
  activation；
- 只有 activation 仍有效、且冲突属于可兼容的普通 revision 推进时，才允许基于新
  snapshot 重新计算并使用新的 expected revision 提交。

`TransactionRegistry` 在目标结构中可以保留为索引、缓存和活跃运行句柄，但不是
Transaction Store 的替代权威。

### 4.3 身份与生命周期

```text
conversation_id
└── transaction_id                    # 长期稳定任务
    ├── activation_id A               # 已结束或已失效
    │   └── delegate_id A1, A2
    └── activation_id B               # 当前有效批次
        └── delegate_id B1
```

领域任务状态：

```text
新建 → continue
continue → continue | pause | complete
pause → continue
complete → continue | archive
archive → continue                    # 合法显式控制或有效预登记 Schedule
```

约束：

- `archive` 只能由 Flush 产生。
- Graph 的 `planning`、`committing`、`dispatching`、`waiting_feedback` 等只是内部
  phase，不能替代领域四状态。
- UI Pause 使当前 activation 失效并进入人工 `pause`。
- UI Delete 是生命周期终止，不是第五种任务状态，也不等同于 archive。
- 已失效 activation 的 Feedback 在 transaction 后来恢复后仍然无效。
- `pause` 不能脱离 `pause_reason` 判断批次有效性：等待本批 Feedback 时保持当前
  activation；`manual_hold` 使其失效；等待用户新协作或计划时间的任务在后续恢复时
  开启新 activation。

### 4.4 Perception、控制面与异步工具

正常数据路径：

```text
stimulus 到达
→ Feedback 先做三层身份及重复消费校验
→ 用稳定 ingress key 幂等接纳；合法刺激取得 accepted_seq，非法 Feedback 只留下
  admission disposition
→ 持有 conversation lease 的唯一 consumer 按 effective_priority + accepted_seq
  排他 claim 一条，并取得 consumer/claim epoch fencing token
→ 有来源刺激在 preconsume 原子复核；失效/冲突时按来源类型 discard、block 或 cancel，
  同时终结当前 stimulus disposition，且无副作用
→ 有来源按类型确定性锁定；无来源调用 matcher
→ 加载 Transaction Store 的 snapshot + revision
→ 在一个 activation 内执行 Thinking
→ 提交 WM、任务状态和领域状态
→ 如需工具，进入 pause(awaiting_feedback)，并耐久创建 delegate/effect intent
→ 本次图运行返回并释放 stimulus consumer
→ 外部 executor 异步执行
→ effect result 与 Feedback ingress outbox 原子记录
→ outbox 以同一 ingress key 把结果作为 Feedback 重新走 admission
```

用户控制旁路：

```text
UI Pause
→ 直接中断指定 transaction 的当前图运行
→ 若存在仍在 Thinking 的当前 stimulus，则标记为 aborted 且不重新入池；已经 final
  的 stimulus 不倒改
→ 当前 activation 失效
→ transaction = pause(manual_hold)
→ 该批次未决 Feedback 永久 Expected Discard
→ 若当前 activation 来自未消费 schedule run，则同一提交把 run 标为
  blocked_on_activation(reason=manual_hold)；仍在 Thinking 的 delivery aborted，
  已 consumed 的 delivery 保持不变

UI Delete
→ 直接中断指定 transaction 的当前图运行
→ 当前 activation 失效
→ transaction 生命周期终止
→ 不再匹配或恢复，后续关联 Feedback 永久 Expected Discard
→ 所有未消费 schedule run 在同一提交中 cancelled
```

高优先级普通刺激不会倒流中断正在执行的 Thinking；它在下一次选择边界优先。UI
Pause/Delete 不依赖刺激优先级。

`pause(awaiting_feedback)` 仍保留当前 activation：

- 对应的合法 Feedback 进入后，在同一 activation 中执行 `pause → continue`；
- 它仍属于“所有 pause 都可参与无来源匹配”的候选。若无来源刺激先匹配成功，Runtime
  必须在同一原子转换中使旧 activation 及其未决 delegate 失效、创建新 activation，
  再执行 `pause → continue`；旧 Feedback 此后永久 Expected Discard；
- 存在当前有效的未决 delegate 时，不允许 transaction 进入 `complete`，因此也不能
  被 Flush 归档。

### 4.5 一次性 Schedule 与 Flush

当前版一次性 Schedule：

```text
同一 UoW 保存 schedule + run(scheduled) 与原 transaction_id
→ transaction = pause(scheduled_wait)，等待到期
→ 到期后用稳定 schedule_run_id + delivery_generation 生成唯一 SCHEDULED_PLAN
→ delivery 进入所属 conversation 的耐久 Inbox
→ 校验有效计划记录并锁定原 transaction
→ 同一 UoW：run = claimed + claimed_activation_id、delivery = claimed、开启新 activation
→ 本次 Thinking 与状态提交完成后：delivery = consumed、释放 stimulus consumer，
  run 仍为 claimed
→ pause → continue
→ 本次计划执行完成
→ 同一 UoW：transaction = complete、run = consumed
→ 后续用户可见 Flush
→ archive
```

周期性 Schedule 标记为 Future。

计划到期时使用以下确定性规则，避免产生两个并行 activation：

| 到期时目标 transaction | 处理结果 |
|---|---|
| `pause` 且没有有效 activation，暂停原因不是 `manual_hold` | 锁定原 transaction，开启新 activation 并执行 |
| `complete` 或 `archive`，且没有有效 activation | 由该有效计划记录合法恢复原 transaction，开启新 activation |
| 同一 transaction 已有另一个 claimed run，即使它正在 `pause(awaiting_user)` 且没有有效 activation | 当前 run 进入 `blocked_on_activation(reason=another_claimed_run)`；不得覆盖或遗忘原 run |
| 存在有效 activation，包括 `continue` 或 `pause(awaiting_feedback)` | 不抢占、不新建并行 activation；把 run 持久标记为 `blocked_on_activation`，在安全批次边界重新变为可调度 |
| `pause(manual_hold)` | 保持 blocked，不能用自动 Schedule 绕过用户暂停；用户恢复后再调度 |
| lifecycle 已删除 | 取消本次 run 并记录拒绝，不得复活 transaction |
| 计划已取消、已消费或 run ID 重复 | 拒绝或幂等返回既有结果，不创建第二个 activation，也不重复消费 run |

如果 transaction 在计划到期前被无来源刺激恢复，计划记录仍然存在；到期 run 按上表
判断，而不是废弃 transaction 当前批次。`blocked_on_activation` 是 Schedule Store 中的
耐久运行状态，不在刺激池中反复热重入；安全边界到达后使用同一 run ID、递增
delivery generation，并生成一个新的稳定 delivery ID。相同 generation 的重复投递只
返回已有 disposition。

run 已经 claimed 后，控制动作仍必须与 Schedule Store 原子协调：

| claimed 后事件 | 处理结果 |
|---|---|
| 进入 `pause(awaiting_feedback)` | run 继续 `claimed` 并绑定当前 activation，合法 Feedback 继续本批次 |
| 进入 `pause(awaiting_user)` | 当前 activation 正常结束；run 继续 `claimed` 但清空 `claimed_activation_id`，不生成新的 schedule delivery |
| 相关用户 stimulus 或合法 Restore 恢复 `pause(awaiting_user)` | 新 activation 与同一 run 原子重新绑定；run 保持 `claimed`，不额外生成 schedule delivery |
| claimed activation 尝试为同一 transaction 再注册 run 并进入 `pause(scheduled_wait)` | 当前版本拒绝且整体不提交；schedule-driven chained/periodic planning 属于 Future |
| `pause(awaiting_feedback)` 被无来源匹配或合法显式 Restore 抢先恢复 | 旧 activation/delegate 失效并开启新的非 schedule activation；已 consumed 的旧 delivery 不倒改；run 转为 `blocked_on_activation(reason=superseded_by_new_activation)`，清除旧 activation 绑定并等待新批次安全边界 |
| UI Pause | delivery 若仍在 Thinking 中则永久 aborted，若已完成本次 Thinking 则保持 consumed；旧 delivery 都不重入；activation/delegate 失效；transaction 进入 `pause(manual_hold)`；run 转为 `blocked_on_activation(reason=manual_hold)` 并清除 activation 绑定 |
| UI Delete | 尚在 Thinking 的 delivery 终止，已 consumed 的不倒改；transaction 写 tombstone；该 transaction 的当前 run 及其他未消费 run 原子转为 `cancelled` |
| 正常完成 | transaction 进入 `complete` 与 run 进入 `consumed` 同时提交 |

UI Pause 保留尚未消费的 run，但不倒改已经 consumed 的 delivery；用户恢复 transaction
时，run 不得立刻抢占恢复产生的
新 activation。只有该 activation 到达安全边界，才生成下一代 delivery。它来自原耐久
run，不是把被中止的旧 stimulus 重新入池。UI Delete 则取消 run，重放不得复活
transaction。

Store/UoW 必须保证同一 transaction 任一时刻最多一个 claimed run；这个约束必须在提交
时执行，不能依赖“先查后写”。两个 run 同时到期，或一个 run 正在
`pause(awaiting_user)` 等待协作时另一个到期，其他 run 一律 blocked。
claimed run 在当前版本中也不能通过注册下一 run 把自身隐式留在 claimed；链式或周期性
计划作为 Future 整体设计。

任何没有开启 activation 的 Schedule preconsume 分支都必须在同一 UoW 中终结当前
delivery：run blocked 对应 `handled(reason=run_blocked)`，run cancelled 对应
`handled(reason=run_cancelled)`，重复 generation 返回原 delivery 的
`idempotent_existing`。不得使用 Expected Discard，也不得把 delivery 留在 claimed。

Flush 的一个语义提交必须同时保证：

1. 固定的 Scene 截止范围已耐久保存为可重建 payload；
2. `flush_watermark` 推进到相同截止位置；
3. 本批符合 revision 条件的 `complete` transaction 变为 `archive`；
4. 同一 `flush_id` 的用户可见会话边界已经提交；
5. 后续 Dialogue/RAG 等物化工作可由 outbox 幂等续做。

提交前失败整体回滚；提交后重试不能重复推进、归档或产生第二个可见边界。仍在
`pause` 等待的 Schedule 不归档，已完成的一次性 Schedule 与普通 complete 一样归档。

## 5. 当前 ThinkLife 与目标组件的映射

当前实现状态是迁移输入，不是正确性声明。

| 当前组件/行为 | 当前事实或缺口 | 目标处理 |
|---|---|---|
| `ThinkLifeRuntime` | 已承担入口、Scene、运行状态和 flush 等多项职责 | 抽取 engine-neutral `RuntimeHost`；现有类收缩为 `think_life_v1` engine/adapter，避免 `langgraph_v1` 反向依赖旧实现类 |
| `PerceptionGateway` | 当前接纳所有输入；用户消息先写 ingress Scene 再 push，Feedback 没有前置 activation Gate | 增加 Feedback admission；非法 Feedback 不入池，并明确 Scene 记录拒收/接纳边界 |
| `StimulusInbox` | 按 `thread_id` 分桶，排序键为 `priority + occurred_at + counter`，主要是进程内状态 | 抽象为耐久 `StimulusInboxStore`；按 `conversation_id` 分区，以唯一 ingress key 接纳，排序为 `effective_priority + accepted_seq`，保存 claim 与最终 disposition |
| `ThreadDrainerService` | 当前按 thread 保证一个 worker | 改成并证明每 conversation 只有一个 Thinking consumer |
| `TransactionAttributor` | Feedback 出池后只校验 transaction + active delegate；候选和 Schedule 路径不符合目标 | 拆成来源校验/直达与无来源 matcher；候选仅 pause + 未 flush complete |
| `TransactionRegistry` | 主要为进程内记录，尚无 activation 领域模型 | 降级为索引/缓存/句柄；Transaction Store 成为领域权威 |
| `ThinkLifeLoop` | 事务循环与外层消费、同步工具路径仍有耦合 | 先在 ThinkLife 修正语义，再由 transaction graph 替换内层循环 |
| `ExecutionAgent.invoke_tool_direct` | 可能在刺激处理调用栈中同步等待工具 | graph 只耐久发出一个 delegate；executor 异步执行，result 与 Feedback ingress outbox 原子记录，再幂等返回 Feedback |
| `force_stop_thread` | thread 级清池和取消多个 transaction | 新增 transaction 级 UI Pause/Delete，不清空同 conversation 的其他任务 |
| Scene JSONL/flush | Scene、watermark 与 archive 尚未形成完整逻辑原子提交 | Scene Store + Flush Journal + outbox |
| Schedule lifecycle | 到期路径可能新建 transaction，尚未稳定绑定原任务 | 计划保存原 ID；run/delivery/activation 耐久绑定；Schedule 与 Transaction/Inbox 相关 mutation 通过同一 UoW 提交 |
| 旧验收目录 | CLI/UI 可运行，但以 `INV-01～14` 为主且仍有历史 Known Gap | 保留为 Supporting；P1 建立 27 Core + AT-10 共享目录 |

需要新增或明确的目标组件：

- `TransactionStore`
- `RuntimeUnitOfWork`
- `StimulusInboxStore`
- `SceneStore`
- `FlushJournal`
- `FeedbackAdmissionGate`
- `TransactionControlService`
- `ActivationManager`
- `AsyncEffectExecutor`
- `EffectLedger`
- `ScheduleStore`
- `RuntimeHarness` 与双 Runtime adapter

## 6. LangGraph Transaction Graph 设计约束

### 6.1 GraphState 不是 Transaction 权威

建议的首版 GraphState：

```text
GraphState
├── conversation_id
├── transaction_id
├── transaction_revision
├── activation_id
├── current_stimulus_ref
├── transition_id
├── graph_phase
├── task_state_snapshot
├── wm_snapshot
├── scene_tail_ref
├── current_decision
├── pending_delegate_intent
├── latest_feedback_ref
├── cancellation_token
├── last_error
└── schema_version
```

`task_state_snapshot` 与 `wm_snapshot` 是加载某个 revision 后的运行快照。最终提交仍写入
Transaction Store，并使用 `expected_revision`；它们不能成为第二份可独立覆盖领域状态的
权威数据。

以下内容不复制进 checkpoint：

- 完整 Scene 或 dialogue history；
- capability registry、model client；
- Schedule Store 与 Flush Journal 的权威记录；
- 大体积工具结果原文；
- 已由 Effect Ledger 耐久保存的完整副作用记录。

checkpoint 与领域 Store 之间采用可重放 UoW 提交协议：

1. 每次领域转换使用稳定 `transition_id`，携带可选的
   `transaction_id + expected_revision` 和
   `stimulus_id + claimed_by + expected_consumer_epoch + expected_claim_epoch`，并对
   完整 command 计算 `command_digest`。
2. UoW 先查 transition ledger：相同 ID/digest 已存在时直接返回第一次完整结果，即使
   revision 或 claim epoch 后来推进；相同 ID、不同 digest 立即报 idempotency
   conflict。
3. 只有 transition 尚不存在时，才校验 conversation partition owner/lease、
   consumer/claim epoch、stimulus owner 和 transaction revision。stale claimant 返回
   `StaleClaim` 且零领域 mutation/effect/outbox；旧 claimant 不能用新 transition
   提交，但可读取失去 lease 前已经成功提交的原 transition。
4. 图节点调用 `RuntimeUnitOfWork.apply_transition(...)`；一次提交可以同时更新
   Transaction/activation/delegate、Inbox disposition、Schedule run、Scene append 与
   effect/outbox mutation，不能让相关 Store 各自成功一半。
5. transition ledger 保存第一次提交的完整 result payload/ref 及 digest。节点结果随后
   才由 checkpointer 保存；如果在两者之间崩溃，同一 transition ID 与相同 command
   digest 的重放返回原 transaction/activation/delegate/delivery/stimulus ID，不能根据
   当前 revision 重新生成。
6. 如果 revision 已被 UI Pause/Delete、删除 tombstone 或 activation 失效推进，旧
   graph run 立即终止，不得 reload 后继续旧刺激；只有 activation 仍有效的普通兼容
   冲突才允许 reload/recompute。
7. P6 必须故障注入“UoW 已提交、checkpoint 尚未推进”这一窗口；P7 另测 lease
   takeover 前已提交/未提交两种分支。

### 6.2 首版图流程

```text
START
  ↓
load_transaction_revision
  ↓
validate_activation_and_control
  ↓
load_context_refs
  ↓
update_task_state
  ↓
completion_gate
  ├── continue → plan
  ├── pause    → commit_transaction → END
  └── complete → commit_transaction → END

plan
  ↓
route_decision
  ├── no_effect
  │     → commit_progress
  │     → continuation_gate
  │          ├── continue + budget → update_task_state / plan
  │          ├── pause/complete    → END
  │          └── budget exhausted → explicit suspend/error
  └── delegate
        → build_one_delegate_intent
        → commit_pause_awaiting_feedback_and_effect_intent
        → dispatch_async
        → END
```

工具完成后的处理是另一次刺激运行：

```text
tool result
→ Effect Ledger 原子保存 result + Feedback ingress outbox
→ outbox relay 使用稳定 ingress key
→ Feedback Admission Gate
→ accepted Feedback 幂等进入耐久 Inbox
→ 锁定同 transaction / activation / delegate
→ 恢复 transaction graph
→ record_feedback
→ pause(awaiting_feedback) → continue
→ update_task_state
→ 继续 plan 或进入 pause/complete
```

图本身不能产生 `archive`。`record_feedback` 只在合法 Feedback 作为新刺激被消费时发生，
不能在原工具节点中提前伪造。

领域状态仍为 `continue` 时，图不能无声结束并让任务永久搁置：它必须继续执行下一轮，
或者留下可恢复的显式 suspend/error 结果。P6 首版优先使用有上限的图内循环；上限耗尽
不是任务完成。

### 6.3 工具与幂等边界

- `plan` 只产生结构化 decision，不直接调用 capability。
- 一次 delegate 最多描述一个 capability 调用或一个回复动作。
- delegate 因果记录、transaction revision 与 effect intent 必须形成可恢复的提交边界，
  再异步 dispatch，不能用两个无补偿的独立写入假装原子。
- 首个 SQLite 参考实现可在一个本地事务中写 Transaction Store、delegate 和 effect
  outbox；若部署时 Effect Ledger 独立，则以 Transaction Store 侧 transactional outbox
  为提交事实，executor 按稳定 effect ID 幂等写入 Ledger。提交前失败不 dispatch，
  提交后崩溃可从 outbox 续做。
- executor 保存 result 时同时写 Feedback ingress outbox；relay 可以 at-least-once
  投递，但 Inbox 的唯一 ingress key 只能形成一个规范 Feedback。
- relay 得到 accepted、idempotent existing 或 admission Expected Discard 都视为已有
  规范领域 disposition，记录引用并 terminal ack；只有基础设施错误重试。UI
  Pause/Delete 导致的 stale Feedback 不能在 outbox 中永久热循环。
- 每个外部 effect 具有稳定 `effect_key/idempotency_key`，但稳定 key 本身不能在两个
  独立系统之间凭空提供 exactly-once。只有 capability 对该 key 做耐久去重时，Runtime
  才承诺一次可观察外部效果。
- capability 注册必须声明并兑现一种 guarantee：
  - `idempotent`：Runtime 可用同一 key 重试至确认，attempt 可多次，外部可见效果一次；
  - `at_most_once`：耐久记录 attempt 后最多 dispatch 一次，失联不自动重试，结果可为
    `uncertain`，外部效果允许零或一次；
  - `at_least_once`：用同一 key 重试至 destination 最终恢复可达并确认；在最终可达
    前提下保证至少一次外部效果并允许重复，未确认期间显示 `retrying/uncertain`。
    当前版本不定义独立的 effect cancel，UI Pause/Delete 也不等价于撤销外部 effect。
- P1 冻结三种 fake sink 的 attempts/result/visible-effect 断言；P6 使用 idempotent
  fake，P7 才开放按声明 guarantee 验证过的真实 capability。
- `reply_to_user` 仍走受审计的 capability/effect 路径，回复成功不自动代表任务
  `complete`。
- P6 使用可控 fake tool；真实工具、完整 Effect Ledger 和故障恢复门放在 P7。

## 7. P0～P8 实施顺序

这是一条单一执行顺序。旧计划的“阶段 1～6”不再与本节并列使用。

### P0：冻结设计总线并同步下游文档

产出：

- Overall Design Architecture；
- 本轮同步后的详细语义测试规格；
- 统一状态权威、异步边界和阶段入口后的迁移计划。

退出条件：

- 三大功能及最新子语义不再冲突；
- checkpoint 不再被写成 WM/task state 的领域权威；
- 下一阶段明确为测试契约实现，而不是直接写 LangGraph PoC。

### P1：冻结跨 Runtime 测试契约

工作：

1. 将 27 个 Core 顶层场景及其子场景数据化。
2. `AT-10` 作为独立 Matcher Evaluation。
3. 为第 8 节当前版本条目建立带
   `parent_scenario_id + variant_id + layer=robustness` 的 manifest；至少实现测试规格
   第 9 节阻断清单中的共享场景；具体断言来自测试规格第 8 节与第 10.3 节，包括
   durable Inbox/lease fencing、UoW replay、Schedule 原子控制、result→Feedback
   outbox、三类 capability guarantee 与 Flush fault。
4. 定义 Runtime Harness 命令和 normalized observation schema。
5. 实现 `think_life_v1` adapter，真实运行新目录。
6. CLI 和 UI 共用同一目录、Runner、报告与结果分类。
7. UI 按 `TX/SP/AT → 测试层级 → Runtime → 场景 → 证据` 展示。
8. 尚未实现的 `langgraph_v1` 明确返回 `Not Implemented`；缺少可执行场景或断言的
   目标显示 `Not Covered`，两者均不得隐藏或计入通过率。

退出条件：

- 新目录能运行，而不是仅显示用例说明；
- 能观察 transaction/activation/delegate、stimulus ingress/accepted_seq/claim/
  disposition、conversation consumer owner/lease、consumer/claim epoch、
  Schedule run/delivery/claimed activation、admission、aborted stimulus、effect
  guarantee/attempts/ack/visible-effect range/outbox、异步消费和 Flush boundary；
- 生成 ThinkLife 的完整差距表；
- 当前版本 27 个 Core variant 和 Robustness manifest 中的必测 variant 均已有共享输入
  与断言，不得仍为 `Not Covered`；实现差异可显示 Known Gap，adapter 能力缺失可显示
  Not Implemented；
- UI 可按 Runtime 层级看见 `Not Implemented` 和 `Not Covered`；
- `Expected Discard` 作为中性证据展示，符合预期时场景为 Passed。

### P2：完成 ThinkLife TX 基础

工作：

- Transaction Store、四状态、revision；
- 定义 RuntimeUnitOfWork 契约与保存 command/result 的 transition ledger，并以 fake
  Inbox/Schedule ports 验证 transaction-side 原语；本阶段不冒充生产耐久跨 Store
  UoW；
- activation/delegate 原语；
- transaction 级 UI Pause/Delete；
- 在 transaction ID 已知或预绑定时提供 pause/complete/archive、一次性 Schedule 和
  Feedback 的 Store 加载、状态转换与持久恢复原语；
- 用户可见 Flush 和逻辑原子提交。

退出条件：

- 不依赖 SP/AT 的 TX Foundation Gate 全绿；
- 已预绑定 ID 的状态、activation 失效事实和恢复原语在重启后仍成立；
- 本阶段不把 stimulus 来源分流、matcher 或端到端 Feedback admission 算作通过；
- 依赖 SP/AT 的 `TX-02/03/05/06/07` 端到端子场景在 P4 收口。

### P3：完成 ThinkLife SP

工作：

- conversation 内刺激池；
- 实现 engine-neutral 逻辑 Inbox port、稳定 stimulus/ingress identity 与唯一接纳；
  P3 先让逻辑 Store/内存 adapter 通过 Core，跨进程耐久 Store/UoW 与 lease fencing
  放到 P7 Robustness；
- admission 后分配 `accepted_seq`；
- `effective_priority + accepted_seq` 排序；
- 每 conversation 单 Thinking consumer；
- 有来源刺激在 preconsume 原子复核，排队后失效时无副作用终止；
- 工具异步执行，不阻塞刺激选择；
- Feedback 重新进入 admission；
- Schedule preconsume 的 blocked/cancelled/idempotent 分支把 delivery 终结为带原因的
  `handled`；
- UI Pause/Delete 控制旁路。

退出条件：

- ThinkLife SP Core Gate 全绿；
- 非法 Feedback 不入池；
- 同一 delegate 的并发重复 Feedback 只能形成一个规范 stimulus；
- 合法入池后变陈旧的 Feedback 得到
  `Expected Discard(stage=preconsume)`，不进入 AT；
- 未开启 activation 的 Schedule delivery 不悬挂在 claimed，run 与 handled
  disposition 一致；
- 工具未完成时下一条刺激仍可在合法选择边界进入 Thinking。

### P4：完成 ThinkLife AT

工作：

- 按来源类型确定性校验和直达；
- 合法显式 UI/控制恢复可定位 pause、未 flush complete 或 archive，并开启新
  activation；
- Feedback 三层因果 Gate；
- pause 与未 flush complete 候选过滤；
- 无来源刺激匹配 `pause(awaiting_feedback)` 时原子失效旧 activation/delegate，再
  开启新 activation；
- fake matcher 确定性场景；
- 模糊或失败时安全新建；
- 删除、archive、continue 和其他 conversation 的候选排除。

退出条件：

- ThinkLife AT-01～AT-09 全绿；
- 收口 P2 留待来源接入的 `TX-02/03/05/06/07` 端到端子场景；
- `AT-10` 能独立运行并报告 matcher 指标；
- 恢复 transaction 后旧 activation Feedback 仍为 Expected Discard。

### P5：完成 M0 领域基线

工作：

- 打通 `SP → AT → TX`；
- 执行全部共享场景、Robustness 和必要 Supporting Tests；
- 修复 P1 差距表中的当前版 Known Gap。

M0 决策门：

- `think_life_v1` 的 27 个 Core Gate 全绿；
- `AT-10` 独立达到冻结阈值；
- 旧 `INV-01～14` 即使全绿也不能单独证明 M0；
- M0 未通过时不得开始 P6。

### P6：LangGraph 单 Transaction PoC

范围：

- 一条已完成归因的合法 stimulus；
- 一个 transaction，按 PoC 子场景顺序经历多个 activation，任一时刻最多一个有效；
- Transaction Store 为领域权威；
- 临时 checkpointer；
- 两个可控、顺序执行的异步 fake effect：一个普通 fake capability，随后一个 fake
  `reply_to_user`；每次只存在一个当前 delegate；
- Feedback 携带 transaction/activation/delegate；
- 只验证共享 `TX-01` 和明确列出的恢复/Feedback PoC slices。

不接入：

- 多 transaction SP/AT；
- 真实 Schedule 和真实外部副作用；
- 完整持久恢复与生产 checkpointer；
- 完整 Effect Ledger、Flush 物化和灰度。

决策门：

- 双 Runtime 在所选共享场景上产生等价领域结果；
- GraphState/Transaction Store 的 revision 边界清晰；
- 图没有同步等待 fake tool；
- 普通 capability 与 reply 分别产生 delegate 和 Feedback，没有把两个 effect 塞入
  同一次 delegate；
- 图不产生 archive；
- 使用 LangGraph 确实降低执行恢复复杂度，否则重新评估是否继续。

### P7：持久恢复与完整外层接入

工作：

- 生产适配的 checkpointer；
- 耐久 Stimulus Inbox、唯一 ingress、claim/lease、accepted_seq 与 disposition 恢复；
- conversation partition owner/lease、consumer/claim epoch fencing 和 `StaleClaim`
  零 mutation/effect；
- Effect Ledger、capability guarantee、幂等 dispatch、result→Feedback outbox 和崩溃恢复；
- SP、AT、Scene、Schedule 和 Flush 接入；
- Schedule/Transaction/Inbox 相关 mutation 接入统一 UoW，覆盖 register、claim、
  complete、Pause 和 Delete 竞态；
- 将该生产耐久 Inbox/UoW 作为两个 Runtime 共用的外层接入，而不是 LangGraph 私有层；
- graph event 映射到现有 SSE/审计；
- checkpoint schema 迁移；
- 故障注入和并发测试。

退出条件：

- 共享耐久 Inbox/UoW 接入后，`think_life_v1` 与 `langgraph_v1` 都重新运行 27 个 Core
  Gate；两者的 SP Core、Store conformance 与 Inbox/UoW 必测 Robustness 全绿；
- `AT-10` 与 Runtime 无关地独立达标；
- 重启不会丢失已接纳 stimulus、accepted_seq、claim/disposition 或 effect result；
- Feedback outbox 对 accepted、idempotent existing 与 admission Expected Discard
  terminal ack；只有基础设施失败重试，不热循环 stale Feedback；
- lease takeover 后旧 claimant 的新 transition 得到 `StaleClaim` 且零 mutation/effect；
  takeover 前已成功提交但丢响应的同 ID/digest 重放返回原结果；
- 重放和重复 Feedback 不重复推进 transaction；支持幂等 key 的 capability 不产生
  第二次可观察外部效果，不支持者按已声明的
  `at_most_once`/`at_least_once` guarantee 暴露 `uncertain`，不得伪报 exactly-once；
- guarantee conformance 证明：`idempotent` 可多 attempt 但可见效果一次；
  `at_most_once` attempts 等于 1 且失联不重试；`at_least_once` 在 destination
  恢复可达时以同 key 重试至确认并至少产生一次效果；
- Transaction Store 始终是领域权威；
- 一次性 Schedule 与 Flush 在两个 Runtime 中语义一致；claimed run 遇到 UI Pause
  进入 blocked、遇到 UI Delete 进入 cancelled、被新 activation supersede 时解除旧
  绑定、awaiting_user 恢复时重新绑定；多 run 不并行 claim，transaction/run/delivery
  disposition 不出现半提交。

### P8：灰度与旧 Runtime 收缩

创建 transaction 时固定：

```text
runtime_engine = "think_life_v1" | "langgraph_v1"
```

`runtime_engine` 默认跨 transaction 全生命周期保持稳定。若选择把旧 Runtime 的
archive 迁到新 Runtime，只能在无活跃 activation 的显式、版本化迁移操作中进行：
保留原 transaction ID、WM、任务状态和 Scene 关联，记录 engine/schema 迁移审计，
并在之后的合法恢复动作中开启新 activation；这不是运行中的静默切换。

灰度顺序：

1. 测试环境与 fake tool；
2. 内部 conversation；
3. 新建的普通 user transaction；
4. 一次性 Schedule transaction；
5. 默认路由新 transaction 到 LangGraph；
6. 完整观察期后收缩旧事务内循环。

退出条件：

- 同一 transaction 不在中途切换 Runtime；
- shadow 只比较规划/状态事件，不重复执行有副作用工具；
- 回退只影响后续新 transaction；
- 不存在只能由即将删除的旧代码恢复的非终态 transaction；
- 所有仍可显式恢复的旧 Runtime archive 都具有经测试的版本化迁移路径，或由保留的
  只读兼容恢复层继续支持；不能因为它们当前是 archive 就忽略；
- 持久 schema 的迁移与回退路径已经验证。

## 8. 共享测试与可观察性

### 8.1 Core 摘要

| 领域 | 必须覆盖 |
|---|---|
| TX | 四状态、activation、UI Pause/Delete、合法恢复、Feedback 因果、一次性 Schedule、多事务隔离、单 Scene、用户可见 Flush |
| SP | 多类型 admission、conversation 内优先级、accepted FIFO、单 Thinking consumer、异步工具不阻塞、并发唤醒、conversation 隔离 |
| AT | 来源类型校验、合法直达、无来源候选范围、pause/complete 复用、模糊新建、归因稳定 |
| AT-10 | 真实 matcher 的独立人工标注评测 |

旧“用户消息插入执行中后 suspend/requeue”只可作为可恢复调度的 Robustness 场景，不能
代替 UI Pause 的直接控制语义。

### 8.2 Harness 最小观察结果

两个 Runtime adapter 至少规范化输出：

- stimulus ID、ingress key、kind、conversation、来源身份、effective priority、
  accepted_seq、consumer owner/lease、consumer/claim epoch、池快照与消费顺序；
- admission/preconsume 的 accepted/discarded 及 stage/reason、最终 disposition；
- transaction ID、state、pause_reason、lifecycle status、revision、WM/task state 摘要；
- activation/delegate ID 及有效性；
- stimulus consumed/handled/aborted/retried；
- delegate intent、dispatch、effect result、Feedback ingress outbox/接纳、capability
  guarantee、attempts、ack/result、visible-effect count/range 与 uncertain；
- schedule ID、run ID、delivery ID/generation、run status、claimed activation、
  blocked reason 与目标绑定；
- Scene seq、transaction 关联、flush ID/boundary/watermark；
- 新建、恢复、归档或生命周期终止；
- 外部领域效果及幂等键。

共享 Gate 不断言具体类名、LangGraph 节点数量、checkpoint 文件格式或私有方法调用。

## 9. 回退与数据兼容

1. transaction 创建时固定 `runtime_engine`。
2. Scene、Transaction、Feedback 和 API 的领域 schema 在迁移期保持可版本化兼容。
3. LangGraph 非终态 transaction 不直接交给旧 Runtime 读取未经转换的 checkpoint。
4. 新 Runtime 发生问题时，只把后续新 transaction 路由回 ThinkLife。
5. checkpoint、Transaction Store、Stimulus Inbox、Scene Store、Schedule Store 和
   Effect Ledger 的 schema 都有独立版本。
6. 无法迁移的数据明确拒绝并保留原记录，不能猜测 activation 或把旧 Feedback视为有效。
7. `runtime_engine` 与 engine schema version 随 transaction 全生命周期耐久保存，包括
   archive。
8. 删除旧代码前完成非终态与可恢复 archive 的清点、转换和恢复演练；archive 若未迁移，
   必须保留能够显式恢复它的兼容层。

## 10. 主要风险与控制

| 风险 | 控制 |
|---|---|
| checkpoint 覆盖新领域状态 | Transaction Store 固定为权威；所有图提交使用 expected revision |
| checkpoint 被误当成 Scene | Scene Store 独立，GraphState 只保存引用或 tail snapshot |
| 工具阻塞 stimulus consumer | 先提交 delegate intent，异步 executor 执行，结果作为新 Feedback |
| 节点重放或网络超时产生重复副作用 | Effect Ledger 与稳定 key；只对支持幂等 key 的 capability 承诺一次可见效果，其他 capability 明示 `at_most_once`/`at_least_once` 与 `uncertain` |
| result 已保存但 Feedback 丢失或重复入池 | result 与 Feedback ingress outbox 同提交；Inbox 以唯一 ingress key 幂等接纳 |
| 进程重启丢失池中刺激或 FIFO | 耐久 Inbox 保存 accepted_seq、claim epoch 和 disposition |
| lease 接管后旧 consumer 仍提交 | conversation owner/lease + consumer/claim epoch fencing；新 transition 返回 `StaleClaim`，已提交 transition 仅幂等读回 |
| Schedule 与 Transaction 只更新一半 | 统一 UoW 原子提交 register/claim/complete/Pause/Delete 及对应 run/delivery |
| UI Pause/Delete 排队太晚 | 独立控制旁路直接中断指定 transaction |
| 旧 Feedback 在新 activation 中污染 WM | admission 与 preconsume 均校验完整三层身份并保存失效事实 |
| Flush 不可见或部分提交 | flush_id、原子事实提交、可见 boundary、outbox |
| 优先级下沉到 transaction graph | 优先级只由 conversation 池在选择边界处理 |
| 将旧 ThinkLife Gap 复制进新图 | 共享目标规格优先于当前实现 |
| 真实 matcher 波动阻塞结构 Gate | fake matcher 用于 Core；AT-10 独立评测 |
| 为使用框架而改变领域语义 | adapter/外层 host 兜底；必要时终止 LangGraph 迁移 |
| 框架版本耦合 | P6 固定版本做隔离 PoC；升级独立验证 |

## 11. 已确定决策与剩余实现选择

### 11.1 已确定

- Transaction Store，而不是 checkpoint，是 WM/task state/生命周期的领域权威。
- Transaction 与 Scene 的首个本地参考持久化后端采用 SQLite，逻辑 Repository 分离。
- Stimulus Inbox 的唯一 ingress、accepted_seq、claim 和 disposition 是耐久领域事实。
- Transaction、Schedule、Inbox、Scene 与 effect/outbox 的相关 mutation 通过统一 UoW
  协调，transition ledger 保存 command digest 与完整结果引用。
- 工具在领域边界上异步，不占用刺激消费者。
- Feedback 使用 transaction/activation/delegate 三层因果校验。
- UI Pause/Delete 是 transaction 级直接控制。
- 当前版 Schedule 是一次性计划，结束后 `complete`。
- 只有用户可见 Flush 执行 `complete → archive`。
- 自然语言 Archive 检索与周期性 Schedule 属于 Future。

### 11.2 仍可在对应阶段选择

1. LangGraph checkpointer 在 P6 与生产部署分别采用哪种后端。
2. graph thread key 使用 `transaction_id` 还是包含版本前缀的稳定组合键。
3. Async Effect Executor 使用本地 worker、线程池、async task 还是未来外部 worker。
4. 各 capability 的稳定 effect key 如何构造。
5. checkpoint 保留周期；该周期不得决定 transaction 的 archive 语义。
6. 本地轻量部署与未来分布式部署的共同接口。
7. 何时评估 Agent Server 或分布式 worker。
8. 每个真实 capability 采用哪种幂等实现与
   `idempotent/at_most_once/at_least_once` guarantee。

## 12. 里程碑与决策门

| 里程碑 | 对应阶段 | 继续条件 |
|---|---|---|
| 设计与契约门 | P0～P1 | 文档无冲突，Core 与必测 Robustness 无 Not Covered，新共享目录可运行并生成分层差距表 |
| ThinkLife TX/SP/AT | P2～P4 | 各领域 Gate 依次全绿 |
| M0 领域基线 | P5 | ThinkLife 27 Core 全绿，AT-10 独立达标 |
| LangGraph PoC 决策 | P6 | 所选共享场景等价且恢复边界更清晰 |
| 共享耐久外层与 LangGraph 完整 Runtime | P7 | 双 Runtime 27 Core、Store conformance 与必测 Robustness 全绿 |
| 默认路由与收缩 | P8 | 灰度稳定、可回退，无未处置的旧 Runtime 非终态或可恢复 archive |

## 13. P6 的首个 LangGraph 实验

这个实验只能在 P5/M0 通过后开始。

实验输入：

1. 一条已经由 fake AT 完成归因的用户 stimulus；
2. 一个 transaction；正常路径从首个 activation 开始，恢复切片顺序创建后续
   activation，任一时刻最多一个有效；
3. 一个需要参数化、支持稳定 idempotency key 的无副作用 fake capability；
4. 该 capability 的异步 delegate 与携带完整三层身份的 Feedback；
5. 随后一个独立的 fake `reply_to_user` delegate 与 Feedback；
6. `continue/pause/complete` 三种领域结果；
7. 新旧 Runtime 共用的 normalized observation。

实验注入点：

- Transaction snapshot 加载后；
- planning 完成后；
- delegate intent 提交前后；
- UoW 已提交、checkpointer 尚未推进；
- fake tool 完成、Feedback admission 前；
- Feedback 消费和 Transaction 提交前后。

P6 只要求临时 checkpointer 下的图中断/恢复切片。生产崩溃恢复、完整 Effect Ledger 和
真实副作用留在 P7。

评价标准：

- Transaction Store 的领域权威没有被 GraphState 稀释；
- transaction/activation/delegate 因果链完整；
- 原 stimulus 消费不等待 fake tool；
- 两个顺序 delegate 各只执行一个 effect，合法 Feedback 均恢复同一 transaction 和
  activation；
- 同一 transition 重放返回第一次创建的完整 ID/结果，不生成第二个 activation、
  delegate 或 effect intent；
- 失效 Feedback 得到 Expected Discard；重复 Feedback 幂等返回 canonical stimulus
  evidence，不创建第二条 Inbox 记录或第二次 transaction 推进；
- 图不产生 archive；
- LangGraph 升级影响被限制在 engine adapter；
- 相比 ThinkLife 内层循环，恢复和执行阶段更清晰，而非仅代码形式不同。

## 14. 当前下一步

P0 文档同步完成后，下一步是 P1，而不是直接实现 LangGraph。

具体顺序：

1. 将 27 个确定性 TX/SP/AT 场景及本轮新增子场景转成共享数据目录；
2. 建立当前版 Robustness manifest，并把测试规格第 9 节阻断清单做成可执行 variant；
   P1 退出时测试本身不得仍为 `Not Covered`；
3. 定义能观察 activation、UI 控制、stimulus ingress/claim/disposition、Schedule
   delivery、Expected Discard、effect guarantee/outbox、异步工具和 Flush boundary 的
   Harness 协议；
4. 实现 `think_life_v1` adapter；
5. 让 CLI 与 UI 运行同一目录，并按层级展示结果和证据；
6. 运行 ThinkLife，形成 Core/Robustness/Matcher 分层差距表；
7. 再按 P2 → P5 修复领域基线；
8. M0 通过后才执行第 13 节 P6 实验。
