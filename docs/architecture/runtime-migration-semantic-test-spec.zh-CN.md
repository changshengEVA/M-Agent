# M-Agent Runtime 迁移语义测试规格

状态：当前目标语义基线  
修订日期：2026-07-28  
适用目标：`think_life_v1` 与后续 `langgraph_v1`  
说明：本文描述的是目标机制。当前实现可能与本文存在差异；差异应通过测试暴露，
而不是把当前实现自动当作正确答案。

> 三句话总览：
>
> - **TX**：同一项任务能跨多轮接着运行，并与 conversation 的共同 Scene 时间线正确配合。
> - **SP**：多种刺激能同时存放，并按确定的优先级规则一个一个取出。
> - **AT**：有来源刺激按来源类型校验后锁定原任务；无来源刺激只匹配 pause 或未归档
>   complete，匹配不到或判断不清就新建任务。

## 1. 测试目的

迁移测试不验证某个具体类、函数或框架是否仍然存在，而验证新旧 Runtime 是否满足
同一套领域功能。

测试只按以下三个功能大类组织：

1. Transaction 事务线与 Scene 情景线；
2. 感知层刺激池；
3. 无来源刺激的 Transaction 归因。

持久化、故障恢复、幂等、并发和 schema 升级不是第四类功能，而是施加在以上三类
功能上的扩展测试维度。

## 2. 领域定义

### 2.1 Transaction

Transaction 是一条持久、稳定、可能跨多轮交互的任务记录：

- 内容表现为 WM（工作记忆）；
- 状态表现为任务状态；
- 使用稳定的 `transaction_id`；
- 属于一个 conversation；
- 可以暂停后再次导入并继续；
- 可以完成、归档，也可以通过合法显式动作或有效预登记 schedule run 从归档中恢复；
- 同一个 transaction 可以先后经历多个彼此独立的执行批次。

三个身份分别回答不同问题：

| 字段 | 含义 | 生命周期 |
|---|---|---|
| `transaction_id` | 这是哪一项长期任务 | 跨多轮、暂停、恢复和归档保持稳定 |
| `activation_id` | 这是该任务的哪一次有效执行批次 | 每次新建或从已结束、失效的批次恢复时新建 |
| `delegate_id` | 这是该批次中的哪一次工具委托 | 在一个 activation 内唯一 |

Transaction Store 是 `transaction_id`、WM、任务状态、当前有效 activation 和 revision
的领域权威。Runtime checkpoint 可以保存恢复图执行所需的状态并引用 transaction 版本，
但不能静默覆盖 Transaction Store。

### 2.2 Transaction 的四种状态

| 状态 | 含义 |
|---|---|
| `continue` | 当前可以由系统自主执行下一步动作 |
| `pause` | 任务仍存在，但正在等待用户、时间或其他外部条件 |
| `complete` | 模型判断任务已完成，但尚未经过 flush 归档 |
| `archive` | flush 对 complete transaction 设置的归档标记 |

模型只能决定 `continue`、`pause`、`complete`，不能直接选择 `archive`。

UI Delete 是 transaction 生命周期终止，不是第五种任务状态，也不等同于 `archive`。

`pause` 状态本身不能单独判断 activation 是否有效，测试必须同时观察规范化
`pause_reason`。当前版最小枚举为：

| `pause_reason` | activation 语义 |
|---|---|
| `awaiting_feedback` | 当前 activation 和尚未消费的 delegate 保持有效 |
| `awaiting_user` | 当前批次结束；后续恢复开启新 activation |
| `scheduled_wait` | 当前批次结束；到期 run 按 TX-06 开启新 activation |
| `manual_hold` | UI Pause 使当前 activation 和未决 delegate 永久失效 |

合法 Feedback 到达 `awaiting_feedback` 时，在同一 activation 中继续。所有 pause 仍可
参加无来源匹配；如果无来源刺激先匹配到 `awaiting_feedback` transaction，则必须原子
失效旧 activation 及其未决 delegate、开启新 activation，再执行 `pause → continue`。
旧 Feedback 此后永久无效。

### 2.3 Scene

Scene 是 conversation 级的追加式事件时间线：

- 一个 conversation 只有一条 Scene；
- 多个 transaction 共享这条 Scene；
- Scene 记录“发生过什么”；
- Transaction 记录“任务当前执行到哪里”；
- Scene、Transaction 和 checkpoint 不能互相替代。

### 2.4 有来源和无来源刺激

测试首先根据刺激是否具有可验证的来源契约区分来源：

- 有来源刺激：不进行无来源语义匹配；先按刺激类型校验来源，校验通过后锁定指定
  transaction；
- 无来源刺激：从允许的 transaction 候选集合中匹配，匹配不到或结果模糊时新建。

“携带 `transaction_id`”本身并不等于“来源有效”。当前版本至少区分：

| 来源类型 | 必须校验的身份 | 成功后的语义 |
|---|---|---|
| Execution Feedback | `transaction_id + activation_id + delegate_id`，且尚未消费 | 回到当前有效批次，不开启新 activation |
| 到期 Scheduled Plan | 有效的 `schedule_id + schedule_run_id + schedule_delivery_id` 及其保存的 transaction/conversation 绑定 | 按 TX-06 激活或安全延期，绝不并行开启 activation |
| 显式 Transaction Restore | 合法 UI/控制动作及明确的 `transaction_id` | 恢复 pause、未 flush complete 或 archive，并开启新 activation |
| 已归因刺激重入 | 原 stimulus 与已锁定的 `transaction_id` | 保持原归因，不重新匹配 |

旧 activation 的 Feedback、无效 delegate Feedback、没有 canonical ingress 可返回但
引用已消费 delegate 的 Feedback，以及指向人工暂停或已删除 transaction 的 Feedback，
必须在感知入口以
`Expected Discard(stage=admission)` 明确废弃，不能进入刺激池、不能转做无来源匹配，
也不能创建新 transaction。若确实按预期废弃，场景结果仍为 `Passed`。

如果同一 Feedback ingress key 已经有 canonical stimulus，则重复调用只幂等返回该记录
和 duplicate evidence；canonical 本身不因 delegate 后来 consumed 而被倒改为 discard。

### 2.5 Conversation 内刺激池

每个 conversation 拥有独立的逻辑刺激池：

- 优先级只在同一个 conversation 的刺激之间比较；
- 不同 conversation 具有相互独立的逻辑排序分区；物理上可以由一个容器实现；
- 尚未到期的 schedule 只是计划记录，不属于当前刺激池；
- schedule 到期后生成 `SCHEDULED_PLAN`，此时才进入对应 conversation 的刺激池；
- 有来源刺激通过 admission 后，在真正消费前仍需原子复核来源有效性。Feedback 若在
  排队期间因 UI Pause/Delete 或其他合法转换变陈旧，则从池中移除并记录
  `Expected Discard(stage=preconsume)`；Scheduled Plan 则按 TX-06 转为 blocked、
  cancelled 或幂等结果，并把当前 delivery 终结为带原因的 `handled`。两者都不能因
  校验失败产生领域效果、调用 matcher 或新建 transaction。

## 3. 目标状态转换

```text
新建
  │
  ▼
continue ───────► pause ───────► continue
  │
  └────────────► complete
                    │
                    ├── 无来源刺激在 flush 前匹配成功 ──► continue
                    │
                    └── flush ──► archive

archive ── 合法恢复来源（显式动作或有效预登记 schedule run） ──► 恢复并进入 continue
```

需要固定的规则：

1. 新建 transaction 的初始状态是 `continue`。
2. `pause` transaction 被无来源匹配或合法显式恢复时沿用原 transaction ID、恢复 WM
   和任务状态并开启新 activation；若它原为 `awaiting_feedback`，该转换先原子失效
   旧 activation 与未决 delegate。
3. 未 flush 的 `complete` transaction 可以被无来源刺激重新匹配并转为
   `continue`，同时开启新 activation。
4. 只有 flush 可以执行 `complete → archive`。
5. `archive` 不参与无来源刺激的自动匹配。
6. 合法显式恢复动作可以按明确 ID 恢复 pause、未 flush complete 或 archive；有效且
   预先登记的 schedule run 也可按 TX-06 恢复 archive。archive 仍不参加无来源匹配，
   旧 Feedback 不能充当恢复动作。
7. 如果该 ID 在活动记录和归档存储中都不存在，则明确拒绝，不能偷偷新建。
8. 安排一次性 Scheduled Plan 后，transaction 在等待期间保持 `pause`；到期激活会
   按 TX-06 校验或延期，合法执行时开启新 activation，本次执行结束后进入
   `complete`，随后可由 flush 归档。
9. UI Pause 使当前 activation 失效、当前思考刺激中止且不重新入池；UI Delete 终止
   transaction 生命周期。两者都通过直接控制面生效，不作为普通刺激排队。
10. 工具执行与思考消费解耦：思考层发出异步委托时提交
    `pause(awaiting_feedback)`，随后即可消费下一条刺激；工具结果稍后作为 Feedback
    重新经过感知入口。存在当前有效未决 delegate 时不得进入 `complete` 或被 flush
    归档。
11. 有来源刺激在 admission 和消费前都按来源类型校验。排队期间失效的 Feedback 得到
    `Expected Discard(stage=preconsume)`；Scheduled Plan 则持久转为
    `blocked_on_activation`、`cancelled` 或幂等返回既有 disposition，不能用
    Expected Discard 代替 run 状态；无效显式恢复明确拒绝。

## 4. 共同测试方法

同一个测试场景应分别运行于：

```text
think_life_v1
langgraph_v1
```

共享测试只能观察领域结果：

- stimulus 的 ID、唯一 ingress key、类别、优先级和来源；
- stimulus 的 `effective_priority`、`accepted_seq`、claimed_by、consumer/claim
  epoch、admission/claim 状态、池快照、消费顺序和带 stage/reason 的最终
  disposition；
- transaction ID、activation ID、delegate ID、状态、`pause_reason`、
  `lifecycle_status`、revision、WM 和任务状态；
- Scene 条目、顺序和 transaction 关联；
- schedule ID、run ID、delivery ID/generation、run 状态、claimed activation、
  blocked reason、目标绑定和本次调度结果；
- flush ID、watermark、Scene 截止位置和用户可见边界；
- 是否创建了新 transaction；
- 是否导入了原 transaction；
- 刺激是 accepted、claimed、consumed、handled、aborted，还是在
  admission/preconsume 阶段 discarded，以及原因；
- UI 控制是否立即作用于指定 transaction；
- effect intent/result/Feedback outbox 是否唯一、capability 声明的 delivery guarantee，
  以及在该保证范围内是否产生了重复消费或重复外部效果。

共享 Gate 不应直接断言：

- 是否调用了 `StimulusInbox.push()`；
- 是否调用了 `ThinkLifeLoop` 的某个私有方法；
- LangGraph 有多少节点；
- checkpoint 使用哪一种存储格式；
- 内部函数调用次数，除非该次数直接代表领域效果次数。

旧实现和新实现分别通过测试适配器暴露相同的领域观察结果。

## 5. TX：Transaction 事务线与 Scene 情景线

### TX-01：新 Transaction 的正常运行

前提：

- 当前 conversation 没有可使用的 transaction；
- 一个刺激已经完成归因并创建 transaction。

操作：

- 运行该 transaction 的正常处理流程。

预期：

- 新 transaction 的状态为 `continue`；
- 全程使用同一个 `transaction_id`；
- 创建首个有效 `activation_id`；
- 若发出异步工具委托，则每个委托都具有属于该 activation 的 `delegate_id`；
- WM、任务状态和本轮记录写入该 transaction；
- 一轮结束后只能根据任务结果进入 `continue`、`pause` 或 `complete`；
- 处理过程中不能无故再创建第二个 transaction。

### TX-02：UI Pause 与原 Transaction 的后续恢复

前提：

- transaction 已有 WM 和任务状态；
- transaction 存在当前有效 activation。

操作：

1. **Thinking 中止子场景**：让刺激 S 正在 Thinking 的可控阻塞点，通过 UI 对该
   transaction 执行 Pause。
2. **等待 Feedback 子场景**：在独立 fixture 中让 Thinking 发出 delegate D、提交
   `pause(awaiting_feedback)` 并结束该次刺激消费；工具尚未返回时执行 UI Pause。
3. **恢复子场景**：分别在独立 fixture 中，用合法显式 UI Restore，以及与该任务明确
   相关的无来源刺激，恢复 `pause(manual_hold)` transaction。

预期：

- UI Pause 是 transaction 级直接控制，不进入普通刺激池，也不等待当前思考自然结束；
- Thinking 子场景的 S 被中止，标记为 aborted 且不重新入池；
- transaction 进入带有人工保持含义的 `pause`；
- 两个暂停子场景的原 `activation_id` 均失效；D 及本批其他未决 delegate 永久失效；
- 迟到的旧 Feedback 在感知入口得到 `Expected Discard`，不进入刺激池；
- 无来源刺激仍可把该 transaction 作为 `pause` 候选；
- 显式恢复绕过 matcher；无来源恢复匹配成功；
- 两种恢复都不新建 transaction；
- 完整恢复原 WM 和任务状态；
- 沿用原 `transaction_id`，但创建不同于旧批次的新 `activation_id`；
- `pause → continue`，新一轮结果继续写入原 transaction；
- 新 activation 产生的合法 Feedback 可以正常接纳，旧 activation 的 Feedback 即使此时
  到达仍然无效。

隔离反例：

- UI Pause A 不能停止同一 conversation 中的 B；
- 若对 A 执行 UI Delete 而非 Pause，则 A 不再是匹配候选，也不能恢复。

### TX-03：Complete 在 Flush 前重新激活

前提：

- transaction 状态为 `complete`；
- 尚未执行 flush。

操作：

- 分别在独立 fixture 中：
  1. 提交一个与该 transaction 匹配的无来源刺激；
  2. 提交一个明确指定该 transaction 的合法显式 UI Restore。

预期：

- 使用原 `transaction_id`；
- 导入原 WM 和任务状态；
- 创建新的 `activation_id`；
- `complete → continue`；
- 不新建 transaction；
- 显式恢复不调用 matcher；
- 已结束旧 activation 的 Feedback 不因本次恢复重新有效；
- 后续 flush 不能把已重新激活的 transaction 归档。

### TX-04：Archive 只能由 Flush 产生

前提：

- 同一 conversation 中分别存在 `continue`、`pause`、`complete` transaction，并准备
  一个用于 Delete 反例的独立 transaction。

操作：

1. 让模型决策或普通状态更新命令尝试直接写入 `archive`。
2. 对独立 transaction 执行 UI Delete。
3. 执行一次正常 flush。

预期：

- 非 Flush 的 `archive` 请求被拒绝，目标 transaction 保持原合法状态；
- UI Delete 产生永久 lifecycle tombstone，不产生 `archive`，也不伪造 flush boundary；
- 只有符合条件的 `complete` transaction 转为 `archive`；
- `continue` 和 `pause` 不变；
- archive transaction 仍可通过合法的显式恢复动作按明确 ID 恢复；
- flush 产生可观察的会话边界。

### TX-05：通过合法显式动作按 ID 恢复 Archive

前提：

- transaction 已由 flush 标记为 `archive`；
- 该 transaction 的 WM 和任务状态仍可恢复。

操作：

- 提交一个合法的显式恢复动作，并明确指定该 `transaction_id`。

预期：

- 不进行语义匹配；
- 不创建新 transaction；
- 恢复原 transaction；
- 原 ID、WM、任务状态和 conversation 关联保持不变；
- 创建新的 `activation_id`；
- 归档前的 activation 与 delegate 均保持失效；
- `archive → continue`；
- 后续事件继续写入原 conversation 的 Scene。

反例：

- 如果 ID 在活动记录和归档存储中都不存在，则明确拒绝。
- 旧 activation 的 Feedback 即使携带该 `transaction_id`，也不能恢复 archive。
- 普通无来源自然语言不能在当前版本中自动检索 archive；该能力标记为 Future。

### TX-06：Scheduled Plan 使用原 Transaction

按照当前版本的一次性计划规则，安排 schedule 时预期：

- 计划具有稳定 `schedule_id`，本次到期执行具有稳定 `schedule_run_id`；
- 计划保存 `conversation_id + transaction_id + due_at`，run ID 在重复投递和重启后不变；
- 创建 schedule/run 与对应 transaction 进入 `pause(scheduled_wait)` 必须属于同一个
  原子领域提交，不能只成功一半；
- 未到期计划只是计划记录，不进入刺激池。

激活 schedule 时预期：

- 到期转换和同一 run 的 stimulus 生成具有幂等性；
- 每次 delivery attempt 具有稳定 `schedule_delivery_id`；同一 attempt 的重复投递返回
  已有 disposition，只有前一 attempt 已持久终止且 run 重新具备资格时才创建下一代
  delivery；
- 激活刺激携带
  `schedule_id + schedule_run_id + schedule_delivery_id + conversation_id + transaction_id`；
- 计划未取消/未消费、run 未完成，且所有目标绑定一致；
- 不运行语义匹配；
- 不创建新 transaction；
- 导入原 WM 和任务状态；
- 在同一提交中把 run 从 `due/blocked_on_activation` 转为 `claimed`、记录
  `claimed_activation_id`、把本次 delivery 排他标记为 `claimed` 并开启 activation；
- 使用原 transaction，并创建新的 `activation_id`；
- 本次 Scheduled Plan 刺激完成 Thinking 与状态提交后，delivery 才进入 `consumed`
  并释放 stimulus consumer；run 继续保持 `claimed`，工具执行和后续 Feedback 不占用
  该消费者；
- 正常等待主线执行 `pause → continue`；合法 run 从 complete/archive 恢复时执行相应
  `→ continue`；
- 本次一次性计划执行正常结束时，transaction 进入 `complete` 与 run 进入
  `consumed` 必须在同一提交中发生；
- 后续 flush 可以像普通完成任务一样将其归档。

边界：

- **Core**：等待到期、仍处于 `pause` 的计划 transaction 不能被 flush 归档；
- **Robustness**：重复到期投递、消费者重试或重启不能创建第二个 activation 或重复
  执行；
- 周期性计划及其重复激活状态线标记为 Future，不进入当前 Core Gate。

到期前后竞态使用以下确定结果：

| 到期/消费时目标状态 | 预期 |
|---|---|
| `pause` 且无有效 activation，且不是 `manual_hold` | 原 transaction 开启新 activation 并执行 |
| `complete` 或 `archive`，且无有效 activation | 有效计划 run 被明确视为合法恢复来源，恢复原 transaction 并开启新 activation |
| 同一 transaction 已存在另一个 `claimed` run，即使其 `claimed_activation_id=null` | 当前 run 转为 `blocked_on_activation(reason=another_claimed_run)`；只有原 claimed run 的合法用户协作可以重新绑定它 |
| `continue` 或存在有效 activation，包括 `pause(awaiting_feedback)` | 不抢占、不并行开批次；run 转为 `blocked_on_activation`，在安全批次边界用同一 run ID 再次变为可调度 |
| `pause(manual_hold)` | 保持 blocked；自动计划不能绕过用户暂停，用户恢复后才重新调度 |
| lifecycle 已删除 | run 进入 `cancelled` 并记录拒绝，不能复活 transaction |
| 计划/run 已取消、已消费或重复 | 拒绝或幂等返回既有结果，不创建第二个 activation，也不重复消费 run |

无来源刺激可以在到期前恢复该 transaction，但不会删除计划记录。到期后按上表处理；
`blocked_on_activation` 存在于 Schedule Store，不在刺激池中热循环。安全边界到达时，
使用相同 run ID 和递增的 delivery generation 只生成一次新的可调度尝试。

Scheduled Plan delivery 在 preconsume 没有开启 activation 时，也必须在同一 UoW 中
离开 claimed：

- run 进入 blocked 时，delivery 最终为
  `handled(stage=preconsume, reason=run_blocked)`；
- run 进入 cancelled 时，delivery 最终为
  `handled(stage=preconsume, reason=run_cancelled)`；
- 重复/已处理 generation 返回原 delivery 的既有终态，记为
  `idempotent_existing`，不创建第二条 stimulus；
- 以上都不是 Expected Discard，也不能把 delivery 永久留在 claimed。

run 已进入 `claimed` 后还必须覆盖以下控制竞态：

| claimed 后事件 | 同一原子提交的预期 |
|---|---|
| 当前 activation 发出工具并进入 `pause(awaiting_feedback)` | run 保持 `claimed`，继续绑定同一 activation；合法 Feedback 只继续本批次 |
| 当前 activation 进入 `pause(awaiting_user)` | 当前 activation 正常结束；run 保持 `claimed` 但暂时清空 `claimed_activation_id`，不生成新的 schedule delivery |
| 与 `pause(awaiting_user)` 明确相关的无来源刺激或合法显式 Restore 到达 | 新开 activation 并在同一提交中重新写入 `claimed_activation_id`；run 保持 `claimed`，用户 stimulus 本身就是本次恢复输入，不额外生成 schedule delivery |
| claimed activation 尝试为同一 transaction 再注册一次性计划并进入 `pause(scheduled_wait)` | 当前版本明确拒绝该 command，A 与 transaction 保持提交前状态且不产生半条 run B；schedule-driven chained/periodic planning 属于 Future |
| `pause(awaiting_feedback)` 被无来源匹配或合法显式 Restore 抢先恢复 | 旧 activation/delegate 失效并开启新的非 schedule activation；已经 consumed 的旧 delivery 保持原 disposition；run 转为 `blocked_on_activation(reason=superseded_by_new_activation)` 并清除 `claimed_activation_id`，等新 activation 到达安全边界后再生成下一代 delivery |
| UI Pause | 若 delivery 仍在 Thinking 中，则标记为 `aborted`；若已完成本次 Thinking，则保留其 `consumed` 事实。两种情况都不重入旧 delivery；activation/delegate 失效；transaction 进入 `pause(manual_hold)`；run 转为 `blocked_on_activation(reason=manual_hold)` 并清除当前 activation 绑定 |
| UI Delete | 尚在 Thinking 的 delivery 终止；已 consumed 的 delivery 不倒改；transaction 写 lifecycle tombstone；activation/delegate 失效；该 transaction 的当前 run 及其他未消费 run 全部转为 `cancelled` |
| 正常完成 | transaction 进入 `complete`；run 转为 `consumed` |

UI Pause 不等于取消尚未消费的一次性计划，也不能把已经 consumed 的 delivery 倒改为
aborted。用户后来恢复 transaction 时，blocked run
仍不能抢占刚恢复的新 activation；只有该 activation 到达安全边界后，才用原
`schedule_run_id` 和新的 `schedule_delivery_id` 生成下一次尝试。新 delivery 是由耐久
run 产生的后续尝试，不是把被用户中止的旧 stimulus 重新入池。UI Delete 则永久取消
该 run，任何重放都不能复活 transaction。

同一 transaction 在任一时刻最多存在一个 `claimed` schedule run；该约束不依赖
`claimed_activation_id` 是否为空。两个一次性 run 同时到期，或一个 run 正在
`pause(awaiting_user)` 等待协作时另一个 run 到期，只有一个可以 claim，其他 run
必须耐久 blocked。run A 不能因 run B 到期而被遗忘、错误 consumed 或错误重新绑定。
当前版本也不允许 run A 在执行中通过登记 run B 来绕过这一约束；链式或周期性计划留待
后续版本统一设计。

### TX-07：Feedback 因果校验与 Expected Discard

前提：

- transaction 存在一个当前有效的 `activation_id`；
- 该 activation 存在一个仍有效且尚未消费的 `delegate_id`；
- Feedback 携带完整的 `transaction_id + activation_id + delegate_id`。

正确输入预期：

- 不运行语义匹配；
- Feedback 在感知入口通过来源校验后才进入刺激池；
- transaction 即使因等待该 Feedback 而暂时处于 pause，只要 activation/delegate
  仍有效，Feedback 仍应被接纳；
- 未决 delegate 有效期间 transaction 不得进入 `complete`；
- Feedback 更新原 transaction；
- Feedback 沿用当前 activation，不开启新 activation；
- 对应 delegate 被标记为已消费；
- 不创建新 transaction；
- 其他 transaction 不发生变化。

错误输入预期：

- 三个身份任一缺失或不匹配时拒绝；
- transaction 不存在、已删除或处于人工 `pause` 时拒绝；
- activation 已被 UI Pause/Delete 失效，或不是当前有效批次时拒绝；
- delegate 不属于该 activation 或已经失效时拒绝；
- delegate 已消费时，若相同 ingress key 的 canonical Feedback 已存在，则幂等返回该
  canonical evidence；只有不存在 canonical ingress 的异常/迁移数据才以
  `delegate_consumed` Expected Discard 拒绝；
- 除 canonical idempotent 返回外，上述无效输入都在正常刺激池之前拒绝，并记录带
  原因的 `Expected Discard`；
- 被拒绝后不能修改任何 transaction 的 WM 和任务状态；
- 被拒绝后不能退化为无来源匹配、恢复 transaction 或新建 transaction；
- transaction 后来通过无来源匹配恢复并开启新 activation，也不能让旧 Feedback 重新
  有效；
- **Robustness**：重复或并发 Feedback 只能产生一次 Feedback 消费和一次 transaction
  状态推进。

排队失效竞态：

1. 让一条当时合法的 Feedback 通过 admission 并进入刺激池，但暂不消费。
2. 通过 UI Pause 或 UI Delete 使其 activation 失效。
3. 再让消费者取得这条 Feedback。

预期：

- 消费前的原子复核发现来源已失效；
- Feedback 从池中移除并记录
  `Expected Discard(stage=preconsume, reason=stale_activation)`；
- 它不修改 WM/任务状态、不调用 matcher、不恢复或新建 transaction，也不产生外部
  领域效果；
- 若 tombstone/失效事实和队列均已持久化，重启后结果仍相同。

### TX-08：多个 Transaction 状态隔离

前提：

- 同一 conversation 中存在 transaction A 和 B。

操作：

- 交错推进 A、B；
- 分别写入不同 WM、任务状态和 Feedback；
- 在独立 fixture 中，让 A 的刺激停在 Thinking 阻塞点后执行 UI Delete；
- 再在 A 为 `pause(awaiting_feedback)`、存在未决 delegate 的 fixture 中执行 UI Delete。

预期：

- A、B 的 WM 不串；
- A、B 的任务状态不串；
- A pause/complete 不改变 B；
- A 的 Feedback 不写入 B；
- A、B 的 activation 和 delegate 不能交叉验证；
- UI Pause/Delete A 不影响 B 的执行批次；
- UI Delete 是直接控制：A 当前 Thinking 立即中止，当前刺激 aborted 且不重入；
- A 的 lifecycle 写入永久 deleted tombstone，当前 activation 和所有未决 delegate 失效；
- A 的后续 Feedback 永久得到 Expected Discard；
- Delete A 后，A 不再参加匹配或恢复，且系统不能悄悄创建一个“替代 A”；
- 任何显式恢复 A 的动作都明确拒绝；
- 后续独立无来源输入仍可按 AT-05 合法创建新 transaction，但该新建必须由无来源归因
  决定，不能由无效 Delete/Feedback/Restore 暗中触发；
- flush 或归档 A 不影响 B。

### TX-09：一个 Conversation 只有一条 Scene

操作：

- transaction A、B 交错产生用户输入、动作、结果和回复；
- 暂停、恢复或归档其中部分 transaction。

预期：

- Scene 只有一条；
- Scene 序号严格递增；
- 读取顺序与事件发生顺序一致；
- 凡已经归属于某个事务的 Scene 记录，都关联正确的 transaction ID；
- conversation 级事件或归因前事件可以没有 transaction ID；
- transaction 状态变化不会删除或拆分 Scene；
- 恢复 transaction 后继续写入原 Scene；
- UI Delete 不反向改写已经发生的 Scene。连同历史记录清除属于独立的隐私清除语义，
  不在本测试中把 Delete 等同为擦除；
- 异步工具结果按实际到达顺序写入 Scene，不能伪装为委托发出顺序；
- flush 在 Scene 中或等价可观察结果中形成会话边界；边界前后的历史事实仍保留，但
  语义上不再默认自然连续。

### TX-10：正常 Flush 生命周期

前提：

- conversation 的上次 `flush_watermark=m`；
- Scene 在 m 之后包含来自多个 transaction 和 conversation 级事件的增量，固定本次
  `through_seq=n`；
- conversation 中同时存在 `continue`、`pause`、eligible `complete` 以及其他不符合
  归档条件的 transaction。

操作：

- **Core variants**：分别触发一次人工 Flush 和一次遵循相同契约的自动 Flush；
- **Robustness variant**：对同一个 `flush_id` 执行重试，并在第 8.3 节定义的提交点
  注入崩溃。

预期：

- 人工或自动 flush 都产生用户和系统可观察的会话边界；
- flush 处理当前 conversation 的 Scene 增量 `(m, n]`，不是某一个 transaction 的
  episode；
- 增量内来自不同 transaction 和 conversation 级事件的 Scene 条目均按原顺序进入
  同一个耐久 payload；
- 只有 transaction 状态转换被限制为“本批符合 state/revision 条件的 eligible
  complete → archive”；
- Scene watermark 只推进到已经安全提交的 `n`；
- pause、continue 以及仍在等待到期的 schedule transaction 不被错误归档；
- 存在当前有效未决 delegate 的 transaction 不具备 complete/归档资格；
- 已完成的一次性 schedule transaction 与普通 complete 一样可以归档；
- Flush 不清空任何 transaction 的 WM/任务状态，也不删除增量范围外 Scene；
- flush 后 archive 不再参与无来源匹配；此时无法自然续接属于该可见边界的预期结果；
- 当前版本可通过合法显式 ID 或有效预登记 schedule run 恢复 archive，但自然语言
  检索历史 transaction 属于 Future；
- **Robustness**：重复同一次 flush 不产生第二个可见边界，也不重复推进 watermark
  或归档。

## 6. SP：感知层刺激池

### SP-01：接纳多种合法刺激并拒绝无效 Feedback

向刺激池放入：

- 无来源用户刺激；
- 已通过来源校验、带完整 transaction/activation/delegate 身份的 Feedback；
- 由有效计划记录生成、带 transaction ID 的到期 Scheduled Plan；
- 无来源 Observation；
- 一个已经在当前 Runtime 注册、具有明确默认优先级的扩展类型 fixture。

预期：

- 所有成功通过 admission 的当前版/已注册 fixture 刺激均可取回；
- ID、类别、payload、conversation、来源身份和优先级保持不变；
- 刺激池不负责语义匹配或创建 transaction。

Admission 反例：

- 旧 activation、无效 delegate、人工暂停或已删除 transaction 的 Feedback 在
  入池前得到带原因的 `Expected Discard`；
- 此类 Feedback 在池中数量为 0，也不能挤占合法刺激的排序位置；
- “预期废弃并实际废弃”时场景状态是 `Passed`，`Expected Discard` 只是证据。

重复 ingress 与无效新输入不同：

- 第二次提交同一 Feedback ingress key 不创建第二条 stimulus，不分配新的
  `accepted_seq`，返回带 `canonical_stimulus_id` 的 duplicate/idempotent evidence；
- canonical stimulus 保持原来的 ready、claimed、consumed 或其他 disposition，不能因
  重复调用被倒改成 admission discard；
- 如果 canonical 仍在 ready 队列，该 ingress 在池中数量是 1 而不是 0；并发重复只需
  证明“没有第二条”。

Preconsume 竞态子场景：

1. 创建处于 `pause(awaiting_feedback)` 的 transaction、有效 activation 与 pending
   delegate；
2. 将引用三者的合法 Feedback 接纳入池，并确认它尚未被消费；
3. 通过 UI Pause、UI Delete，或其他合法状态转换使该 activation/delegate 失效；
4. 轮到该 Feedback 出池时，感知层重新校验来源；
5. 该刺激以 `Expected Discard(stage=preconsume)` 终止，不进入思考层、不参与归因、
   不创建新 transaction，也不产生 Scene、状态推进、工具调用或其他领域副作用；

步骤 1～5 是 SP-01 自身的 Core 证明；重启后仍得到同一 disposition 属于第 8.1 节
Robustness。TX-07 中由暂停或删除触发的同类检查属于跨域联动，不能替代感知层退出门。

### SP-02：按优先级消费

操作：

- 在同一个 conversation 中放入最终解析优先级为高、中、低的多个刺激。

预期：

- 消费顺序为高、中、低；
- 消费者依据最终解析后的优先级排序；
- 只有通过 admission 的刺激获得排序资格；
- `Expected Discard(stage=admission)` 从未入池，不参与优先级比较；
- `Expected Discard(stage=preconsume)` 在失效前已经合法入池并取得排序位置；它被
  选中后终止，但不进入 Thinking，也不重新占据队列位置；
- 默认优先级遵守第 10.2 节的映射；
- 其他 conversation 的刺激不参与本次排序。

### SP-03：相同优先级按接纳顺序 FIFO

操作：

- 按 A、B、C 顺序放入相同优先级刺激。

预期：

- A、B、C 在接纳时获得严格递增的 `accepted_seq`；
- 消费顺序固定为 A、B、C；
- 只有通过 admission 的刺激取得 `accepted_seq`，在 admission 被拒绝的 Feedback
  不占用序号；
- `occurred_at` 只用于业务记录，不能改变同级调度顺序；
- 多生产者并发时，以刺激池锁内完成接纳并取得 `accepted_seq` 的顺序为准。

### SP-04：严格逐个消费

操作：

1. 在 transaction A 的刺激 A 上使用带完成闩锁的 fake tool。
2. A 的 Thinking 发出 delegate、提交 `pause(awaiting_feedback)` 并结束刺激消费，但
   保持 fake tool 未完成。
3. 向同一 conversation 提交刺激 B；fake attributor 明确把 B 归给另一 transaction
   或新建 transaction B，避免改变 A 的 activation。
4. 在 fake tool 闩锁仍未释放时，等待并观察 B 已完成 Thinking 消费。
5. 释放 fake tool，使 A 的结果作为 Feedback 重新经过 admission 和刺激池。

预期：

- 每次只向思考层交付一条刺激；
- 同一 conversation 同时只有一个 Thinking 消费者；
- 正常路径中，已经交付的刺激不会无故再次从池中出现；
- 未消费刺激仍保留；
- 全部消费后池为空；
- 一条刺激的消费边界是“思考完成并提交本次 transaction 状态”，不是“所有异步工具
  都执行完毕”；
- 思考层发出带 activation/delegate 身份的工具委托后可以结束本次消费，消费者可以
  继续选择下一条刺激；
- B 在 fake tool 完成前已经被消费，且 A、B 的 Thinking 没有并行；
- 工具结果稍后作为 Feedback 重新经过 admission Gate 和刺激池，不能绕过调度直接
  修改 transaction；
- 本测试只验证正常路径的逐个交付，不在此承诺崩溃重投下的 exactly-once。

### SP-05：多生产者并发放入

操作：

- 多个来源同时提交不同类型、不同优先级的刺激。

预期：

- 最终数量等于成功提交数量；
- “成功提交”只统计通过 admission 的刺激；
- 不覆盖、不丢失；
- 每条刺激的内容和元数据保持不变；
- 合法 Feedback 的 transaction/activation/delegate 身份不被并发生产者覆盖；
- 无效 Feedback 并发到达时不取得 `accepted_seq`，也不污染池；
- 消费顺序仍符合优先级和 `accepted_seq` FIFO。

### SP-06：消费者退出窗口到达新刺激

操作：

- 消费者认为池已空、准备退出时放入新刺激。

预期：

- 新刺激不会永久留在池中；
- 当前消费者继续处理，或者新的消费者被正确启动；
- 异步工具完成后形成的合法 Feedback 也能从退出窗口正确唤醒消费者；
- 正常退出竞争中，同一刺激不能被两个消费者同时取得。

### SP-07：下一次选择时优先取出高优先级刺激

操作：

- 一个 transaction 正在处理；
- 新的高优先级刺激进入池。

预期：

- 新刺激不会倒流进正在进行的那一次选择；当前 Thinking 消费完成提交后才进行下一次
  选择；
- 消费者下一次从池中选择刺激时，优先取出高优先级刺激；
- 工具仍在异步执行不阻止消费者进行下一次选择；
- 已归因刺激保留原 `transaction_id`；
- 无来源刺激交给 AT 归因流程；
- 如果因明确的可恢复调度策略把原刺激重新放回池中，它仍视为已归因刺激，不再重新
  匹配 transaction；
- UI Pause 不走这种重入路径：它中止当前刺激，且该刺激不重新入池。

### SP-08：Conversation 之间相互隔离

预期：

- 一个 conversation 的消费者不能取走另一个 conversation 的刺激；
- 一个 conversation 阻塞时，不错误改变另一个 conversation 的刺激顺序和状态；
- 每个 conversation 的单消费者约束独立成立；
- 一个 conversation 存在未完成异步工具时，不阻塞其他 conversation 的 Thinking；
- 不同 conversation 的 activation/delegate 不能交叉通过 Feedback admission。

## 7. AT：无来源刺激的 Transaction 归因

### AT-01：有来源刺激先校验，合法者绕过语义匹配

有来源刺激只有通过其来源类型对应的校验后：

- 不调用无来源匹配算法；
- 直接锁定并导入指定 transaction；
- 不创建新 transaction。

当前来源类型分别验证：

- Feedback 必须匹配当前有效的
  `transaction_id + activation_id + delegate_id`，且尚未消费；
- Scheduled Plan 必须来自有效且已到期的 schedule/run/delivery 记录，同一
  `run + delivery_generation` 只允许一个规范 stimulus，并严格按 TX-06 的状态表激活、
  延期、取消或幂等返回；预先登记且仍有效的 run 可以合法恢复 complete/archive；
- 显式 Transaction Restore 只能来自合法 UI/控制动作，可定位 pause、未 flush
  complete 或 archive，恢复原 transaction 并开启新 activation；
- 已归因重入刺激必须保持原 stimulus 与 transaction 关联。

校验失败时明确拒绝。它不能退化为无来源匹配、不能恢复 transaction，也不能偷偷
新建 transaction。尤其是，仅有一个真实存在的 `transaction_id` 不能使旧 Feedback
变成有效来源。

### AT-02：候选集合正确

无来源刺激的候选只能来自当前 conversation 中：

- 所有 `pause` transaction，包括 `awaiting_feedback` 和 `manual_hold`；
- 尚未 flush 的 `complete` transaction。

以下记录不能进入候选：

- `continue`；
- `archive`；
- 已删除、生命周期已终止的 transaction；
- 其他 conversation 的 transaction；

其中 complete 一旦成功 flush，就应当表现为 `archive`，不再以 complete 身份存在。

### AT-03：匹配 Pause Transaction

前提：

- 一个 pause transaction 与新刺激明确相关。

预期：

- 返回原 transaction ID；
- 不创建新 transaction；
- 导入原 WM 和任务状态；
- 如果旧批次已经结束或失效，直接开启新的 activation；
- 如果目标为 `pause(awaiting_feedback)` 且旧 activation 仍有效，则在同一个 revision
  转换中先失效旧 activation 与未决 delegate，再开启新 activation；
- `pause → continue`。

人工暂停的 transaction 仍可参加本测试。来源未知的新刺激匹配成功后可以恢复它，
不受 Feedback admission Gate 限制；任何被本次匹配 supersede/失效的旧 activation
Feedback 都永久无效，新 activation 的合法 Feedback 可以接纳。

### AT-04：匹配未 Flush 的 Complete Transaction

前提：

- 一个未 flush 的 complete transaction 与新刺激明确相关。

预期：

- 返回原 transaction ID；
- 不创建新 transaction；
- 导入原 WM 和任务状态；
- 开启新的 activation；
- `complete → continue`。

### AT-05：无匹配时新建

前提：

- 候选 transaction 与新刺激均不相关，或者没有候选。

预期：

- 不修改任何候选；
- 新建一个 transaction；
- 新 transaction 初始状态为 `continue`；
- 新 transaction 创建首个 activation；
- archive 或已删除记录即使语义相似，也不能因此成为候选；当前版不自动检索 archive。

### AT-06：语义模糊时新建

前提：

- 多个候选看起来都可能相关；
- matcher 无法给出唯一明确结果。

预期：

- 不随意选择候选；
- 不修改任何候选；
- 新建 transaction；
- 新 transaction 初始状态为 `continue`；
- 新 transaction 创建首个 activation。

### AT-07：不能跨 Conversation 匹配

预期：

- matcher 只能看到当前 conversation 的候选；
- 其他 conversation 中语义相似的 transaction 不得被选中；
- 当前 conversation 无匹配时在当前 conversation 新建，并创建首个 activation。

### AT-08：归因结果在后续流程中保持稳定

一旦刺激被锁定到 transaction：

- 后续处理不能再次重新归因；
- 归属于该事务的 Scene 记录、WM、任务状态和 Feedback 使用同一个 transaction ID；
- transaction ID 的稳定不等于 activation ID 永远不变；恢复已结束或失效的批次必须
  新建 activation；
- Feedback 只能属于产生相应 delegate 的 activation，不能跨 activation 复用；
- 只有明确允许重试的已归因刺激才保持原 transaction 关联重新入池；
- UI Pause 中止的刺激不重新入池；
- 恢复时仍回到原 transaction。

### AT-09：匹配算法的确定性结构测试

使用可控制的 fake matcher 分别返回：

- 明确匹配某个候选；
- 无匹配；
- 模糊结果；
- 非候选 transaction ID。

验证候选范围、选择结果、新建逻辑和状态转换，不依赖真实模型波动。

还必须断言：

- fake matcher 只接收无来源刺激；
- 有来源但校验失败的刺激直接拒绝，不能调用 fake matcher；
- matcher 只返回候选选择或新建决定，匹配 pause/complete 后的新 activation 由 Runtime
  创建。

### AT-10：真实语义匹配评测

建立人工标注的数据集，每条样本包含：

- 新刺激；
- 候选 transaction；
- 按最终 matcher 契约构造的 `matcher_context`；
- 期望匹配的 transaction，或“应新建”。

`matcher_context` 使用第 10.4 节的 Matcher v1 结构；将来结构发生变化时通过
`schema_version` 显式升级。首版按第 10.5 节的合成数据执行。

分别统计：

- 应复用且正确复用；
- 应复用但错误新建；
- 应新建但错误复用；
- 错误匹配到其他 transaction；
- 模糊场景是否按规则新建。

真实 matcher 的质量评测应独立于确定性 Runtime Gate，避免模型波动导致结构测试不稳定。

AT-10 只评估无来源刺激在允许候选中的语义选择质量，不负责验证 Feedback admission、
activation/delegate、UI 控制或 Flush，也不包含自然语言 Archive 检索与周期性 Schedule。

## 8. 扩展测试维度

以下内容不新增功能大类，而是扩展 TX、SP、AT 场景。

### 8.1 持久化与重启

- pause transaction 重启后仍可导入；
- archive transaction 重启后仍可按 ID 恢复；
- Scene 重启后顺序连续；
- complete 在 flush 前重启后仍保持可匹配；
- transaction 恢复后 WM 和任务状态不丢失；
- 当前有效 activation、delegate 消费状态和 transaction revision 在重启后不丢失；
- UI Delete 的 lifecycle tombstone、activation/delegate 失效事实重启后仍然有效，
  transaction 仍不能匹配或恢复；
- `scheduled/due/blocked_on_activation/claimed/consumed/cancelled` 计划运行状态，以及
  schedule/run/delivery/claimed activation/transaction/conversation 绑定重启后不丢失；
- 已接纳 stimulus、conversation 内 `accepted_seq`、claim 状态和最终 disposition
  均可恢复；崩溃时处于 claimed 但尚未最终提交的 stimulus 以同一 stimulus ID、
  更高的 consumer/claim epoch 重新取得处理资格，旧 token 永久失效；
- 将 SP-01 的合法 Feedback 入池后使来源失效，在消费前或 claimed 恢复时重启；重启后
  必须对同一 stimulus 复核来源，并得到同一个幂等的
  `Expected Discard(stage=preconsume)`；
- checkpoint 只能恢复图执行状态，不能以旧快照覆盖 Transaction Store 的新 revision。

### 8.2 重复投递与幂等

- 同一有来源刺激重复提交不会重复修改 transaction；
- 同一 Feedback ingress key 只保留一个规范 stimulus；并发重复 admission 不能在
  delegate 尚未 consumed 时同时产生两条池内记录；
- 同一 Feedback 不会重复消费或重复推进 transaction；
- schedule 重复激活不会错误创建新 transaction；
- 同一 `schedule_run_id + delivery_generation` 的重复到期、重复入池和重复 claimed
  返回既有 disposition；只有已 blocked 的 run 在安全边界才能递增 generation；
- transaction 恢复不会创建第二个逻辑 effect intent；外部可见结果遵守下述 capability
  guarantee；
- 同一 delegate 只接受一次有效 Feedback；
- 同一 Scene `append_id` 不会生成第二条 Scene entry；
- 同一 effect intent 和 result 只产生一个规范记录；dispatcher 重试始终携带同一个
  idempotency key；
- 只有 capability 明确实现该 key 的耐久去重时，才承诺一次可观察外部效果；不支持
  幂等的 capability 必须显式选择 `at_most_once` 或 `at_least_once` 策略，并把崩溃
  窗口中的 `uncertain` 暴露给审计和人工处理，不能同时承诺“不丢且不重”；
- effect result 与待投递 Feedback 的 outbox 形成一个提交；relay 重试不能丢失结果，
  也不能凭重复投递在 Inbox 中创建第二个规范 Feedback；
- 旧 activation 的迟到 Feedback 在 transaction 恢复后仍保持无效。

### 8.3 故障注入

- transaction 状态保存失败；
- Scene 写入失败；
- flush 中途失败；
- archive 持久化记录读取失败；
- transaction 导入失败；
- Transaction Store 已提交但 graph checkpoint 尚未推进；
- delegate/effect intent 已提交但异步 dispatch 尚未发生；
- capability 已接收请求但 executor 在记录结果前失联；按 capability 声明的 guarantee
  得到幂等重试、`uncertain` 或明确的 `at_most_once`/`at_least_once` 结果；
- effect result 与 Feedback outbox 的提交边界，以及 outbox relay 与 Inbox 唯一接纳
  之间分别发生崩溃；
- result/outbox 已提交后 UI Pause/Delete 先使 activation 失效；relay 得到 admission
  Expected Discard 后必须 terminal ack，并保留 discard evidence，不能永久重试；
- Inbox 已 claim 但 stimulus 的最终 disposition/transaction transition 尚未提交；
- 旧 consumer 的 lease 过期，新 consumer 以更高 epoch 接管同一 stimulus 后，旧
  consumer 恢复并尝试提交；
- 旧 consumer 的 transition 已提交但响应丢失，随后 lease/epoch 推进；相同 ID/digest
  的重放必须返回原结果，而不是误报 `StaleClaim`；
- UI Pause/Delete 与思考状态提交同时发生；
- 工具 Feedback 在 activation 失效的边界到达；
- Schedule 到期与 transaction 新 activation、manual hold 或 Delete 同时发生；
- schedule/register、claim、complete、Pause 或 Delete 在其配对 transaction mutation
  之前发生故障，验证两者整体回滚或按同一 transition 重放；
- 消费者退出窗口到达新刺激。

每个故障点都应有明确的可重试、拒绝或恢复结果，不能出现无法判断刺激或
transaction 去向的半完成状态。

### 8.4 并发

- 多生产者同时写刺激池；
- 同一 conversation 单消费者；
- 不同 conversation 独立消费；
- flush 与 transaction 激活不能发生错误交错；
- complete 被匹配激活时不能同时被 flush 归档；
- UI Pause/Delete 与 in-flight Thinking 不能提交出相互矛盾的 transaction 状态；
- lease takeover 后，旧 claimant 携带 stale consumer/claim epoch 的提交必须得到
  `StaleClaim` 且没有任何 transaction、Scene、schedule、effect/outbox 或 disposition
  mutation；新 claimant 最多提交一次领域转换。若旧 transition 在 takeover 前已经
  提交，则相同 ID/digest 只读取原结果，不再次提交；
- 同一 delegate 的重复或并发 Feedback 最多产生一次 Feedback 消费和一次 transaction
  状态推进；
- Schedule 到期不得与已有 activation 并行创建第二个 activation。

### 8.5 Schema 升级

- 旧版本 pause/archive transaction 可迁移；
- 无法迁移时明确拒绝并保留原持久化数据；
- Scene 与 transaction schema 分别管理；
- schema 升级不能改变 transaction ID 和 conversation 关联；
- 缺少 activation/delegate 字段的旧数据必须通过显式迁移或明确拒绝，不能把未知旧
  Feedback 默认视为当前有效；
- `runtime_engine` 与 engine schema version 随 transaction 全生命周期保存；
- 可恢复 archive 若跨 engine 迁移，必须在无活跃 activation 时显式、可审计地转换，
  保留原 transaction ID、WM、任务状态和 Scene 关联。

## 9. 建议的测试层级

### Core Gate

只包含确定性、必须阻断迁移的正常功能：

- TX 的 10 个顶层场景：状态转换、activation、UI Pause/Delete、一次性 Schedule、
  Feedback、隔离、Scene 和 Flush；
- SP 的 8 个顶层场景：admission、存放、排序、逐个 Thinking 消费、异步工具解耦和
  conversation 隔离；
- AT-01～AT-09：来源类型校验、候选范围、复用、新建和已归因稳定性。

Core Gate 共 27 个确定性顶层场景。顶层功能编号与测试层级是两个独立维度：每个可执行
variant 都必须保存 `parent_scenario_id + variant_id + layer`，其中
`layer=core|robustness|matcher_evaluation|supporting`。新增细节可以继续挂在原
TX/SP/AT 编号下，但不能因为“不新增顶层编号”就自动算入 Core。

Core variant 包含正常确定性路径和为证明结构功能所必需的可控顺序交错，例如：

- TX-06 的正常安排/claim/complete、busy/manual/delete/supersede/awaiting_user 分支；
- TX-07 与 SP-01 的 admission 及由测试闩锁确定顺序的 preconsume 失效；
- SP-05/SP-06 用 barrier 确定重现的多生产者与 lost-wakeup 路径。

同一 Core variant 必须能够通过 adapter 运行于新旧 Runtime；不满足目标语义的旧
Runtime 应得到明确的 `Known Gap` 或 `Failure`；adapter/能力尚不存在时显示
`Not Implemented`，而不是改写测试预期或把空结果算作通过。

因此本文总计 **28 个顶层编号 = 27 个确定性 Core Gate + 1 个 AT-10 Matcher
Evaluation**。所谓 Full 不能再被解释为“仍只有 14 个旧 INV 编号”。

### Robustness

Robustness variant 仍挂在所属 TX/SP/AT 场景下，包含：

- 重启；
- 重复投递；
- 故障注入；
- lease 过期、接管与 stale claimant；
- 非确定性并发/压力竞态；可控且属于基本功能的 SP-05/SP-06 不因此移出 Core；
- schema 升级。

例如 TX-06 的重复 generation、重启和半提交故障，TX-07 的并发重复 Feedback 与
result→Feedback outbox 故障，TX-10 的重复 Flush/崩溃恢复，以及 SP-01 的 durable
ingress、重启、lease takeover 都必须标为 `layer=robustness`，不能混入 27 项 Core
通过率。

P1 必须生成当前版本的 Robustness manifest，并至少把以下阻断迁移的 variant 做成共享
可执行场景：

- `TX-01/uow_replay_and_result`；
- `TX-06/schedule_atomicity_and_control_recovery`；
- `TX-07/effect_result_feedback_outbox`；
- `TX-07/capability_delivery_guarantees`；
- `TX-10/flush_fault_recovery`；
- `SP-01/durable_ingress_restart`；
- `SP-01/lease_takeover_fencing`。

第 8 节其他当前版条目也必须在 manifest 中映射到 parent ID、variant ID、layer 和
状态；P1 退出时不能仍为 `Not Covered`。实现尚不满足可以报告 Known Gap，Runtime
adapter 尚无能力可以报告 Not Implemented，但测试输入和断言本身必须存在。Future
项目不进入该 manifest。

### Matcher Evaluation

`AT-10` 使用人工标注数据集评估真实语义匹配算法，不与 27 个确定性 Core Gate 混为
一个通过率。

### Supporting Unit Tests

保留当前针对 `StimulusInbox`、`TransactionRegistry`、`ThinkLifeLoop`、Scene store
等具体实现的单元测试，以及旧 `INV-01～INV-14` 目录，用于定位问题和保留历史映射，
但不作为跨架构语义成立的唯一证据。

### 结果与范围标签

| 名称 | 含义 | 是否算当前场景通过 |
|---|---|---|
| `Passed` | 所有当前版预期均成立 | 是 |
| `Expected Discard` | 某条输入按目标语义在 admission 被拒收，或接纳后因来源失效在 preconsume 被终止；它是带 `stage`/`reason` 的证据标签，不是独立运行状态 | 若处置阶段、原因和无副作用断言均正确，则场景为 `Passed` |
| `Known Gap` | 运行前已经登记、当前 Runtime 明确尚未满足的目标语义 | 否 |
| `Failure` | 原本应通过，却发生未登记错误或回归 | 否 |
| `Not Implemented` | adapter 或能力尚未实现，因而不能完成场景 | 否 |
| `Not Covered` | 当前目标语义尚无可执行场景或断言 | 否 |
| `Future` | 明确不属于当前版本，例如周期性 Schedule、自然语言 Archive 检索 | 不运行，也不计为 Gap |

结构化报告中的规范化 token 建议固定为 `passed`、`known_gap`、`failed`、
`not_implemented`、`not_covered`；表中的文字是 CLI/UI 展示名。

## 10. 已确定的设计方案

### 10.1 刺激池作用范围

刺激池按 `conversation_id` 分区，优先级只在 conversation 内生效。

所谓“全局池”原本指所有 conversation 共用一个优先级堆；本方案明确不采用这种
设计。实现可以在一个容器中维护多个分区，但任何一次 `push`、`peek`、`pop` 和
单消费者协调都必须带 `conversation_id`，不同分区之间不进行优先级比较。

未来的 schedule 在到期前只是 schedule 记录，物理上不在当前刺激池。只有到期并
生成 `SCHEDULED_PLAN` 后，才进入所属 conversation 的池。

Feedback 在取得池中位置之前先经过来源 admission。只有校验通过的 Feedback 才能
获得 `accepted_seq`。`Expected Discard(stage=admission)` 从未进入正常队列，
`accepted_seq=null`；`Expected Discard(stage=preconsume)` 则表示 Feedback 在接纳时
合法、已经取得序号并参与过排序，但在被选中后、进入 Thinking 前因来源失效而终止。

### 10.2 默认优先级与 Override

约定数字越小，优先级越高：

| 刺激类型 | 默认优先级 | 原因 |
|---|---:|---|
| `USER_MESSAGE` | 10 | 用户当前明确输入，优先保证交互响应 |
| `EXECUTION_FEEDBACK` | 20 | 继续已有事务的因果链，优先于后台事件 |
| `SCHEDULED_PLAN` | 30 | 到期后需要执行，但默认不抢过当前用户输入 |
| `OBSERVATION_TRIGGER` | 40 | 环境或后台观察，默认最低 |

该顺序保留现有 User/Feedback 的相对关系，并把当前混在同一默认值中的 Schedule 与
Observation 分开。数值间隔为 10，便于以后插入新类别。

规则：

1. 新刺激类型必须显式登记默认优先级，不能静默归入“其他”。
2. 同一优先级按 conversation 内原子接纳时分配的 `accepted_seq` FIFO。
3. `occurred_at` 只作业务时间和审计信息，不参与同优先级排序。
4. `priority_override` 合法范围为整数 `0..100`，其中 0 有效，只有 `None` 表示未覆盖。
5. Override 只影响当前 stimulus，并在入池时解析成 `effective_priority` 后冻结。
6. 已入池刺激不会因配置变化而重新排序。
7. 被明确允许重试的已归因刺激重入时，保留原 stimulus ID、transaction ID、
   `effective_priority` 和 `accepted_seq`；UI Pause 中止的刺激不适用此规则，也不
   重新入池。
8. 只有可信的 Runtime/Scheduler 入口可以设置 override；普通用户输入不能自行提升
   调度权限。
9. Schedule 如需特殊紧迫度，在计划记录中保存优先级，到期生成刺激时复制为 override。

### 10.3 Transaction、Scene 与运行因果持久化

先定义与具体后端无关的领域接口：

```text
TransactionStore
├── load(transaction_id)
└── list_match_candidates(conversation_id)

RuntimeUnitOfWork
└── apply_transition(
      transition_id,
      transaction=null {
        transaction_id,
        expected_revision,
        command
      },
      inbox_claim=null {
        stimulus_id,
        claimed_by,
        expected_consumer_epoch,
        expected_claim_epoch
      },
      scene_appends=[],
      schedule_mutations=[],
      inbox_mutations=[],
      effect_mutations=[]
    )

SceneStore
├── append(conversation_id, entry, append_id)
├── read_after(conversation_id, seq)
├── current_seq(conversation_id)
└── flush_watermark(conversation_id)

ScheduleStore
├── load(schedule_id)
└── load_run(schedule_run_id)

StimulusInboxStore
├── load(stimulus_id)
├── load_by_ingress_key(ingress_key)
├── list_ready(conversation_id)
└── load_partition_state(conversation_id)

EffectLedger
├── load(effect_id)
└── list_pending()

FlushJournal
├── prepare(flush_operation)
├── mark_effect_written(flush_id)
├── commit(flush_id)
└── load_unfinished(conversation_id)
```

所有领域 mutation 都通过 `apply_transition` 协调；各 Store 可以提供便捷命令，但不能
绕过 UoW 独立提交一半状态。`transaction=null` 只用于没有 transaction 状态变更的
Inbox、Schedule 或 Effect 转换。UoW 至少覆盖：

- 所有 transaction/activation/delegate 变更，包括 create、状态更新、begin/invalidate
  activation、register delegate、consume Feedback、restore、archive、UI Pause/Delete；
- schedule 注册与 `pause(scheduled_wait)`；
- run claim、`claimed_activation_id`、schedule delivery claim 与 begin
  activation；
- schedule activation 正常完成时的 `transaction → complete + run → consumed`；
- UI Pause 时仍在 Thinking 的 stimulus aborted（已 consumed 的则保持原 disposition）、
  activation/delegate 失效、manual hold 与
  `run → blocked_on_activation`，以及 UI Delete 时的 tombstone 与所有未消费
  `run → cancelled`；
- claimed schedule activation 被新的合法 activation 取代时，旧 activation/delegate
  失效、旧 delivery disposition 不倒改，run 转 blocked 并清除旧 activation 绑定；
- claimed schedule execution 进入 `pause(awaiting_user)` 时，run 保持 claimed 但解绑
  已结束 activation；用户协作恢复时把同一 run 原子绑定到新 activation；
- Inbox admission/claim/final disposition、Scene append、effect intent/result 和
  Feedback ingress outbox。

UoW 对完整 command 进行规范化并计算 `command_digest`。同一 `transition_id` 和相同
digest 的重放必须返回第一次提交的完整结果，包括已创建的 transaction、activation、
delegate、schedule delivery 和 stimulus ID；同一 ID 携带不同 digest 时返回明确的
idempotency conflict。不能只保存结果摘要后根据当前 revision 猜测第一次结果。

校验顺序固定为：

1. 先查询 transition ledger；
2. 已存在且 `command_digest` 相同时，直接返回第一次提交结果，即使 revision 或 claim
   epoch 后来已经推进；
3. 已存在但 digest 不同时，返回 idempotency conflict；
4. 只有 transition 尚不存在时，才校验 claim fencing token、transaction revision 和
   其他 CAS 条件，然后提交或返回 `StaleClaim`/revision conflict。

因此，“提交成功但响应丢失，随后 lease 被接管”的旧调用可以重放已经提交的事实；旧
claimant 不能用过期 token 创建一个新的 transition。

`scene_appends` 使用稳定 `append_id`，effect intent 在同一提交中写入 effect outbox。
这样 Transaction Store 已提交但 graph checkpoint 尚未推进时可以安全重放；外部
executor 也不会遇到“transaction 已声明委托、但没有任何可恢复 effect intent”的双写
空洞。

每个 ingress 都先取得稳定 `stimulus_id + ingress_key`：

- Feedback 首版使用
  `feedback:{transaction_id}:{activation_id}:{delegate_id}`；一个 delegate 只允许一个
  最终 Feedback；
- Scheduled Plan 使用
  `schedule:{schedule_run_id}:{delivery_generation}`；
- 其他生产者使用自身稳定事件 ID，不得在重试时重新生成。

`ingress_key` 在 Store 中唯一。Feedback 的来源校验、唯一键保留和 admission disposition
必须在同一提交中完成，所以两个并发重复 Feedback 即使都看到 delegate 尚未 consumed，
也只能有一个规范 stimulus 进入队列；其他调用幂等返回既有记录并产生 duplicate 证据。
admission 被拒绝的记录可以进入 ingress ledger 供审计，但
`accepted_seq=null`，不属于 ready 队列。

成功接纳时，UoW 原子推进当前 conversation 的 `next_accepted_seq` 并把结果写入
stimulus。claim 必须耐久且排他；消费者崩溃后使用 consumer epoch/lease 恢复同一
stimulus ID，而不是创建新刺激。preconsume 失效时，claim 与最终
`Expected Discard(stage=preconsume)` 在一个提交中完成。合法 stimulus 的最终
`consumed` 与相应 transaction 状态提交同属一个 UoW；UI Pause 可在同一边界把当前
claimed stimulus 标记为 `aborted`。

`consumer_epoch + claim_epoch + claimed_by` 是提交 fencing token，不只是观察字段。
`conversation_state` 必须保存 partition-level
`consumer_owner + consumer_lease_until`；同一 conversation 任一时刻只允许该 owner
claim，且最多有一个未最终处置的 claimed stimulus。新的 conversation consumer 只能
在 lease 到期或显式交接后取得所有权，并推进 `consumer_epoch`；同一 stimulus 被重新
claim 时还必须推进 `claim_epoch`。任何 claim 以及由该 stimulus 导致的 transaction、
Scene、schedule、effect intent 或最终 disposition 提交，都必须携带加载时的完整 token。
对于 ledger 中尚不存在的新 transition，UoW 发现 partition owner、lease、consumer
epoch、stimulus owner 或 claim epoch 任一不匹配时返回 `StaleClaim`，并保证零领域
mutation、零 effect intent、零 outbox 写入。旧 claimant 即使后来恢复并完成了
Thinking，也不能覆盖新 claimant、claim 同一 conversation 的第二条 stimulus 或提交
外部效果；它只能用原 transition ID/digest 读取自己在失去 lease 前已经成功提交的结果。

首个参考实现使用 Python 内置 SQLite，采用一个本地数据库文件，但保持逻辑 Repository
分离。选择 SQLite 的原因：

- Transaction、Inbox、Schedule、Scene 与 effect/outbox 的相关表可以参加同一个本地
  数据库事务，直接兑现首版 UoW；
- pause/archive 可稳定保存并按 ID 恢复；
- 可以按 conversation 和状态查询归因候选；
- 支持 revision 条件更新，避免恢复与 flush 互相覆盖；
- Scene watermark 与 transaction archive 可以在同一数据库事务中提交；
- 自带 schema version 和 migration 能力；
- 无需增加外部服务，适合当前本地 Runtime 和 PoC。

建议的核心表：

```text
transactions
  transaction_id, conversation_id, kind, state,
  wm_json, task_state_json, pause_reason, current_activation_id,
  lifecycle_status, runtime_engine, engine_schema_version,
  revision, schema_version,
  created_at, updated_at, archived_at, deleted_at

activations
  activation_id, transaction_id, source_stimulus_id,
  source_schedule_run_id, status, started_at,
  invalidated_at, invalidation_reason, completed_at

delegates
  delegate_id, activation_id, transaction_id, status,
  effect_key, created_at, consumed_at, invalidated_at

runtime_transitions
  transition_id, transaction_id nullable,
  command_digest, from_revision, to_revision,
  result_json, result_ref, result_digest, committed_at

scene_entries
  append_id, conversation_id, seq, entry_type, actor, text,
  transaction_id, payload_json, created_at

conversation_state
  conversation_id, scene_seq, flush_watermark,
  next_accepted_seq, consumer_owner, consumer_lease_until,
  consumer_epoch, revision

stimuli
  stimulus_id, ingress_key unique, conversation_id, kind,
  source_transaction_id, source_activation_id, source_delegate_id,
  source_schedule_run_id, source_schedule_delivery_id,
  payload_json, effective_priority, accepted_seq nullable,
  status, disposition_stage, disposition_reason,
  claimed_by, claim_epoch, created_at, claimed_at, finalized_at

schedules
  schedule_id, conversation_id, transaction_id, due_at,
  priority_override, status, schema_version, created_at, updated_at

schedule_runs
  schedule_run_id, schedule_id, conversation_id, transaction_id,
  status, due_at, delivery_generation, current_delivery_id,
  claimed_activation_id nullable, blocked_reason,
  claimed_at, consumed_at, last_transition_id

effect_outbox
  effect_id, transaction_id, activation_id, delegate_id,
  idempotency_key, delivery_guarantee, payload_json,
  status, attempts, updated_at

effect_results
  effect_id, idempotency_key, result_digest,
  result_payload_json, result_ref, committed_at

feedback_ingress_outbox
  event_id, effect_id, stimulus_id, ingress_key unique,
  payload_json, status, attempts, final_disposition_ref,
  last_error, updated_at

flush_operations
  flush_id, conversation_id, from_seq, through_seq,
  eligible_transactions_json, normalized_payload_json,
  status, payload_digest, created_at, committed_at

flush_outbox
  event_id, flush_id, destination, status,
  attempts, last_error, updated_at
```

Store 还必须以事务约束保证：同一 transaction 最多一个
`schedule_runs.status=claimed`，同一 conversation 最多一个尚未最终处置的 claimed
stimulus。不能只依靠调用方先查询再写入。

`stimuli.status` 至少区分 `rejected`、`ready`、`claimed`、`consumed`、`handled`、
`expected_discard` 和 `aborted`。`handled` 表示来源型 stimulus 已在 preconsume 通过其权威来源状态得到完整
处理，但没有进入 Thinking；当前主要用于 Schedule 的 blocked/cancelled/idempotent
分支，并必须同时保存 disposition stage/reason。

LangGraph checkpoint 仍然只保存 Graph 恢复状态，不作为 Transaction 或 Scene 的权威
存储。旧 Runtime 通过 adapter 映射当前 Registry/JSONL，目标 Runtime 使用 SQLite
Repository；共享测试只验证恢复结果，不验证底层文件格式。checkpoint 至少引用
`transaction_id + transaction_revision + activation_id`，恢复提交时必须进行 revision
校验，不能用旧运行快照覆盖 Transaction Store 中已经由 UI 控制或其他合法操作提交的
新状态。若 Transaction Store 已提交、checkpoint 尚未推进时崩溃，节点必须用同一
`transition_id` 幂等取得原提交结果，不能再次修改 WM、状态或创建第二个逻辑 effect
intent；外部可见效果仍受 capability 声明的 guarantee 约束。
若 revision 冲突来自 UI Pause/Delete、deleted tombstone 或 activation 失效，旧 graph
run 必须永久终止，不能 reload 后以旧刺激继续；只有 activation 仍有效的普通兼容冲突
才允许重新计算。

上表是参考结构，不要求所有后端采用相同表名；但当前 activation 的有效性、delegate
归属与消费状态、UI Pause/Delete 导致的失效事实，以及重启后判定陈旧 Feedback 所需
的信息都必须耐久保存并可观察。Stimulus 的唯一 ingress、accepted/claimed/final
disposition 与 conversation 序号也必须耐久，不能把进程内优先级堆当作可靠性权威。
一次性计划的 run 状态至少包括
`scheduled/due/blocked_on_activation/claimed/consumed/cancelled`；状态转换必须由
稳定 `schedule_run_id + delivery_generation + transition_id` 去重，run/transaction/
Inbox 的相关 mutation 必须共享提交边界。

Effect intent、dispatch 和 result 必须通过稳定 effect/idempotency ID 可恢复。executor
记录 result 时，必须在同一数据库事务中同时写入 `feedback_ingress_outbox`；若 Effect
Ledger 与 Inbox 分属不同系统，则 relay 采用 transactional outbox，并依赖唯一
`ingress_key` 做幂等接纳。这样“结果已经保存但 Feedback 尚未入池”的崩溃窗口可以续做，
重复 relay 也不会产生第二个规范 Feedback。

relay 的终态规则必须区分领域结果与基础设施失败：

- Feedback 被 accepted、幂等返回已有 ingress，或因为 activation/delegate 已失效而
  得到 `Expected Discard(stage=admission)`，都表示该 outbox event 已经得到一个规范
  领域 disposition；保存其引用并把 event 标记为 terminal/delivered；
- 只有超时、Store 不可达、事务失败等尚未形成规范 disposition 的基础设施错误才保留
  retryable；
- UI Pause/Delete 先发生导致的 Expected Discard 不能让 outbox 永久重投陈旧 Feedback。

每个 capability 在注册时必须声明以下一种 delivery guarantee；其含义同时是可执行
断言，不只是 UI 标签：

| guarantee | dispatch/retry 规则 | 可观察承诺 |
|---|---|---|
| `idempotent` | 每次重试都携带同一 idempotency key；capability 对该 key 做耐久去重，Runtime 可重试直到 terminal acknowledgement | dispatch attempt 可以多次，但同一 key 的外部可见效果恰好一次 |
| `at_most_once` | Runtime 在调用前耐久写入 attempt；一次 effect 最多发起一次 dispatch，调用后失联也不自动重试 | 允许外部效果为零或一次；无确认时结果为 `uncertain`，不能声称成功 |
| `at_least_once` | 每次使用同一 key 重试，直到 destination 最终恢复可达并返回 terminal acknowledgement | 在 destination 最终恢复可达的前提下保证至少一次外部可见效果；capability 不去重时允许重复，未确认期间显示 `retrying/uncertain` |

首版测试分别提供三种可控 fake sink：

- `idempotent` sink 制造重复 dispatch attempt，但断言外部可见计数等于 1；
- `at_most_once` sink 在首次 attempt 后丢失 acknowledgement，断言 attempts 等于 1、
  状态为 `uncertain` 且没有自动重试；
- `at_least_once` sink 在前若干次失败后恢复可达，断言持续使用同一 key、attempts
  递增并最终至少出现一次可见效果；测试允许重复且不得伪报 exactly-once。当前版本
  不定义独立的 effect cancel，也不能把 UI Pause/Delete 解释为已经撤销外部 effect。

真实 side-effecting capability 没有声明 guarantee 时不得启用。测试和 UI 必须同时展示
guarantee、attempts、ack/result/uncertain 与外部可见计数范围，不能把稳定 effect ID
误写成跨系统 exactly-once。

### 10.4 Matcher v1

现有 `ThinkingAgent.resolve_transaction` 的结构化输出方式可以保留，但候选集合和上下文
需要按目标机制重建。

Runtime 在调用 matcher 前必须先完成：

1. 只查询当前 conversation；
2. 只保留 `pause` 与未 flush 的 `complete`；
3. 为候选分配本轮临时引用 `candidate_1`、`candidate_2`；
4. 不把真实、持久的 transaction ID 交给模型构造。

Matcher v1 输入：

```text
schema_version
stimulus
  kind
  text

conversation_tail
  最近的 conversation Scene 条目，带可用的候选归属标签

candidates[]
  ref
  state                    # pause / complete
  pause_reason             # awaiting_feedback / awaiting_user / scheduled_wait / manual_hold / null
  lifecycle_status         # active；deleted 已在 Runtime 过滤
  kind
  task_state
    goal
    completed
    remaining
  wm_facts[]
  recent_activity[]
```

为控制上下文大小，首版建议：

- conversation tail 最多 12 条；
- 每个候选的 goal 最多 500 字；
- completed、remaining 各最多 5 项；
- WM 最近或最相关的 6 条，每条最多 300 字；
- 候选近期 Scene 最多 6 条。

Matcher v1 输出：

```text
action: continue | create
transaction_id: candidate_N | null
reason_code:
  explicit_reference
  answers_pending_need
  same_task_revision
  same_task_continuation
  no_match
  ambiguous
reasoning: string          # 仅用于审计
```

校验规则：

- `continue` 只能引用输入候选；
- `create` 必须返回 `transaction_id=null`；
- 无候选时不调用模型，直接新建；
- 模糊、非法引用、格式错误、超时和模型异常一律新建；
- 即使只有一个候选，模型失败时也不能自动复用；
- 通过来源类型有效性校验的有来源刺激绕过 matcher；校验失败的刺激直接拒绝，不能
  调用 matcher 或退化为新建。

设计偏向“宁可错误新建，也不要错误复用”，因为错误新建只会拆分任务，而错误复用会
污染现有 WM 和任务状态。

Matcher v1 不查询 archive，也不承担自然语言历史 transaction 检索。

### 10.5 首版合成 Matcher 数据

不要求用户准备真实数据。首版在仓库中维护 30 条人工构造、人工标注的 JSONL 样本：

| 场景 | 数量 |
|---|---:|
| 明确续接 pause | 8 |
| 重新激活 complete | 6 |
| 无关任务，应新建 | 6 |
| 语义模糊，应新建 | 5 |
| 多候选但可唯一判断 | 5 |

领域覆盖邮件、日程、信息查询、文档修改和出行，并使用最小对照样本，例如：

```text
候选：给王老师写改期邮件，pause，等待确认时间

“改到周五下午三点”
→ continue candidate_1

“顺便订周五去北京的票”
→ create

存在多个可能候选时：“继续刚才那个”
→ create
```

首版评测标准：

- 结构化输出有效率 100%；
- 引用候选集合之外的 ID：0；
- 匹配到错误候选：0；
- 应新建却错误复用：0；
- 总准确率至少 90%；
- 应复用场景召回率至少 85%。

确定性 Core Gate 使用 fake matcher；真实模型评测独立运行。真实评测使用确定性模型
设置（支持时 temperature=0），可重复运行 3 次并报告稳定率。后续再从匿名化失败
案例扩充数据集，不要求一开始就有生产数据。

这 30 条样本只属于 `AT-10`，不覆盖 Feedback admission、activation/delegate、UI
控制、自然语言 Archive 检索或周期性 Schedule。

### 10.6 Flush 的原子提交边界

通俗地说，假设上次 watermark 为 80，本次 flush 要处理 Scene 第 81～100 条，同时
transaction A 已经 `complete` 且没有有效未决 delegate：

```text
成功结果必须同时表示：

1. Scene 81～100 已进入耐久、可重建长期记忆的本次 flush payload；
2. flush_watermark = 100；
3. transaction A = archive；
4. 同一个 `flush_id` 对应的用户可见会话边界已经提交。
```

这四个结果属于同一次语义提交：

- 不能只推进 watermark，否则系统以后不会再处理 81～100，可能造成数据丢失；
- 不能只 archive transaction，否则重试时可能重复导出 Scene；
- 不能只写可见边界而不提交 Scene 与 archive，否则用户看到的会话分段与系统事实矛盾；
- 不能在 transaction 已被新刺激恢复为 `continue` 后，仍把它归档。

建议使用 conversation 级锁、稳定 `flush_id`、SQLite 原子事务和持久化 outbox：

```text
Build Plan
  ├── 锁定 conversation
  ├── 固定 through_seq
  ├── 记录无有效未决 delegate 的候选 complete transaction ID + revision
  └── 构造规范化 flush payload

SQLite Commit
  ├── 再次检查 watermark、transaction state 和 revision 未改变
  ├── 持久保存 flush payload、digest 和 flush_id
  ├── 将本批符合条件的 complete 标记为 archive
  ├── 推进 watermark
  ├── 写入同一 flush_id 的可见会话边界
  ├── 写入 Dialogue/RAG 等物化任务的 outbox
  └── 在同一个 SQLite transaction 中 COMMIT

Materialize
  ├── outbox 使用 flush_id 作为幂等键生成 Dialogue/RAG 等产物
  └── 成功后标记对应 outbox 项完成
```

恢复规则：

- SQLite Commit 前任一步失败：全部回滚，watermark 和 transaction 均不改变；
- Commit 后、物化前崩溃：重启后从 outbox 继续，不重新推进 watermark 或重复 archive；
- Dialogue/RAG 写入成功但响应丢失：使用同一 `flush_id` 幂等确认，不生成第二份；
- transaction 在 Build Plan 后被恢复：revision 改变，Commit 必须中止并重新计算；
- Build Plan 后新增的 Scene 条目序号大于 `through_seq`，留给下一次 flush；
- Commit 完成后重复请求：返回同一 flush 结果，不产生第二个可见边界；
- 等待到期、仍为 `pause` 的 Scheduled Plan transaction 保持不变；已执行完的一次性
  Scheduled Plan 若为 `complete`，则按普通规则归档。

SQLite Commit 是唯一的语义提交边界。Commit 成功表示 Scene 数据已经以规范化
payload 耐久保存，用户可见边界、watermark 与 archive 同时生效；Dialogue 文件、
RAG 索引等属于可从 payload 重建的物化结果。Flush 后 archive 不再参加无来源自动
匹配是预期行为；当前版本的恢复依靠合法显式 ID 或有效预登记 schedule run，自然语言
检索历史 transaction 属于 Future。

因此，“原子提交边界”不是要求所有外部系统共享一个数据库事务，而是要求系统先把
完整、可恢复的事实一次性提交，再通过 outbox 生成其他产物。这样即使物化阶段失败，
也不会丢失 Scene 数据或形成部分 archive。
