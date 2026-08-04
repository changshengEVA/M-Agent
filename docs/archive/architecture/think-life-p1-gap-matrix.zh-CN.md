# ThinkLife P1 Known Gap 矩阵

> **历史基线（冻结，不是当前差距表）**
>
> 本文完整保留 **2026-07-28 pre-P2** 的 P1 执行证据和 35 个 Known Gap，
> 下方表格及结论不随实现进展改写。当前 post-P2 状态以
> [`scenario_catalog.py`](../../../src/m_agent/acceptance/scenario_catalog.py) 和实时 Gate
> 为准：35 个 variant 均为 `Executable`，**post-P7 当前为 35 `Passed` /
> 0 `Registered Known Gaps`**。P7 已收口
> `TX-06/schedule_atomicity_and_control_recovery`、
> `TX-07/effect_result_feedback_outbox`、
> `TX-07/capability_delivery_guarantees`。
> AT-01～AT-04 的 post-P2 剩余原因见
> [验收平台“当前 AT-01～AT-04 剩余差距”](semantic-acceptance-platform.zh-CN.md#41-当前-at-01at-04-剩余差距)；
> 下方旧理由仍按历史证据保留。
>
> ```powershell
> python -m pytest -q -m p2_foundation
> python -m m_agent.acceptance contract run --runtime think_life_v1 --scenario TX-01 --scenario TX-04 --scenario TX-09 --scenario TX-10 --scenario AT-05 --scenario AT-06 --scenario AT-07 --all-layers
> ```

状态：2026-07-28 pre-P2 P1 完整执行基线（冻结）

验证运行：`run_0e47745ff65c4d989e2c`

结果：35 个 variant 全部执行；35 xfailed；0 unexpected failure

## 1. 如何阅读

P1 共 28 个顶层场景、35 个 variant：

- 27 个 `core`；
- 7 个 `robustness`；
- 1 个 `matcher_evaluation`。

`think_life_v1` 的 35 个 binding 均为 `Executable`。每个 variant 的目标断言真实执行；
当前目标语义尚未满足时，失败 check 携带下表所列的显式 gap key。只有登记过的失败才会
动态 xfail；任何未登记失败都是 Failure，已登记 gap 不再复现也会失败。

`langgraph_v1` 的同一组 35 个 binding 当前全部为 `Not Implemented`。

## 2. TX：15 个 variant

| Variant | Layer | Gap key | 当前精炼差距 |
|---|---|---|---|
| `TX-01/core` | `core` | `tx_01.core` | 生命周期仍是 `pending/running`，缺 activation、revision 与 transition UoW。 |
| `TX-02/core` | `core` | `tx_02.core` | 只有 thread 级停止/抢占，缺 transaction Pause/Delete、manual hold 与新 activation 恢复。 |
| `TX-03/core` | `core` | `tx_03.core` | `completed` 是不可恢复终态，缺 flush 前 Complete 候选与重开 activation。 |
| `TX-04/core` | `core` | `tx_04.core` | 缺 Archive、Deleted tombstone，以及由 Flush 独占的归档转换。 |
| `TX-05/core` | `core` | `tx_05.core` | 缺显式 Restore、archive store 与跨 activation 失效记录。 |
| `TX-06/core` | `core` | `tx_06.core` | Scheduled Plan 会建独立 transaction，缺稳定 run/delivery、原子 claim 与原 transaction activation。 |
| `TX-07/core` | `core` | `tx_07.core` | Feedback 仅校验 transaction+delegate，缺 activation、唯一 ingress 与两阶段 disposition。 |
| `TX-08/core` | `core` | `tx_08.core` | WM 基本隔离，但缺 transaction Delete/tombstone、activation/delegate 隔离和永久拒绝。 |
| `TX-09/core` | `core` | `tx_09.core` | Scene 基本单时间线，但缺 append idempotency 与带 flush ID 的用户可见边界。 |
| `TX-10/core` | `core` | `tx_10.core` | Flush 不是 Scene、eligible archive、watermark 与可见边界的同一原子提交。 |
| `TX-01/uow_replay_and_result` | `robustness` | `tx_01.uow_replay_and_result` | 缺 transition ledger、command digest、revision/fencing 与完整结果重放。 |
| `TX-06/schedule_atomicity_and_control_recovery` | `robustness` | `tx_06.schedule_atomicity_and_control_recovery` | Schedule/Transaction/Inbox 缺统一 UoW、generation 与控制竞态恢复。 |
| `TX-07/effect_result_feedback_outbox` | `robustness` | `tx_07.effect_result_feedback_outbox` | 缺 effect result 与 Feedback ingress outbox 的原子提交及 terminal acknowledgement。 |
| `TX-07/capability_delivery_guarantees` | `robustness` | `tx_07.capability_delivery_guarantees` | Capability 未声明或执行 idempotent、at-most-once、at-least-once delivery guarantee。 |
| `TX-10/flush_fault_recovery` | `robustness` | `tx_10.flush_fault_recovery` | 缺 FlushJournal、稳定 flush ID、fault injection 与 commit 后 outbox 恢复。 |

## 3. SP：10 个 variant

| Variant | Layer | Gap key | 当前精炼差距 |
|---|---|---|---|
| `SP-01/core` | `core` | `sp_01.core` | Inbox 缺 admission Gate、ingress key、accepted sequence、claim/final disposition 与 preconsume 复核。 |
| `SP-02/core` | `core` | `sp_02.core` | 优先级堆可排序，但缺 admission/disposition、conversation 分区和冻结 effective priority。 |
| `SP-03/core` | `core` | `sp_03.core` | 同优先级先比较 occurred time，且无原子 accepted sequence，不能保证接纳顺序 FIFO。 |
| `SP-04/core` | `core` | `sp_04.core` | 工具在 Thinking 消费调用栈内同步执行，无法释放消费者后异步返回 Feedback。 |
| `SP-05/core` | `core` | `sp_05.core` | Push 虽线程安全，但 admission、accepted sequence 与 Feedback 身份缺并发原子性。 |
| `SP-06/core` | `core` | `sp_06.core` | 空队列检查与 active worker 移除之间仍有 lost-wakeup 窗口。 |
| `SP-07/core` | `core` | `sp_07.core` | 基本优先选择存在，但重入会重新归因，且工具同步、UI Pause 语义缺失。 |
| `SP-08/core` | `core` | `sp_08.core` | Inbox 按 thread 而非 conversation 分区，缺独立 consumer lease 与 activation Gate。 |
| `SP-01/durable_ingress_restart` | `robustness` | `sp_01.durable_ingress_restart` | Inbox/transaction 为进程内结构，重启会丢 ingress、claim、disposition 和来源失效事实。 |
| `SP-01/lease_takeover_fencing` | `robustness` | `sp_01.lease_takeover_fencing` | 缺持久 consumer lease/epoch、claim epoch 与 StaleClaim 零副作用 fencing。 |

## 4. AT：10 个 variant

| Variant | Layer | Gap key | 当前精炼差距 |
|---|---|---|---|
| `AT-01/core` | `core` | `at_01.core` | 来源校验缺 activation、schedule run/delivery、显式 Restore 与已归因重入校验。 |
| `AT-02/core` | `core` | `at_02.core` | 当前候选排除 suspended 且只取非终态，与 Pause+未 Flush Complete 目标集合相反。 |
| `AT-03/core` | `core` | `at_03.core` | Suspended 不入候选，缺 pause reason、旧 activation 失效和新 activation 恢复。 |
| `AT-04/core` | `core` | `at_04.core` | Completed 被视为不可匹配终态，不能在 Flush 前用原 ID 重新激活。 |
| `AT-05/core` | `core` | `at_05.core` | 无匹配可新建，但新 transaction 缺 Continue 目标状态和首个 activation。 |
| `AT-06/core` | `core` | `at_06.core` | 模糊时可新建，但新 transaction 同样缺 Continue 状态和首个 activation。 |
| `AT-07/core` | `core` | `at_07.core` | 候选按 conversation 过滤，但新建 transaction 没有目标 activation 语义。 |
| `AT-08/core` | `core` | `at_08.core` | 已归因的非 Feedback 重入仍会再次归因，缺 activation 级归属稳定性。 |
| `AT-09/core` | `core` | `at_09.core` | Fake matcher 仅覆盖部分路径；异常/超时安全新建、来源 Gate 与 activation 转换不完整。 |
| `AT-10/matcher_evaluation` | `matcher_evaluation` | `at_10.matcher_evaluation` | 30 条数据虽已冻结，当前 Runtime 仍无可复现的离线真实 matcher 执行与完整指标。 |

## 5. 基线结论与下一步

本次运行的 `35 xfailed / 0 unexpected` 表明：

- P1 共享合同、Harness、Runner 和证据链已覆盖完整矩阵；
- 35 个差距都由对应的显式 check key 解释；
- 没有被已有 Known Gap 吞掉的未登记新失败；
- `p1_exit_ready=true`。

P1 退出不等于 M0 通过。P1 证明“差距已经可执行、可定位、可比较”；M0 要求
ThinkLife Core Gate 真正全绿。

下一步进入 P2 Transaction 基础，优先实现 transaction 状态机、activation、revision、
transition UoW 与合法控制动作，并用同一批检查逐项移除 TX Known Gap。
