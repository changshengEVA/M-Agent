# Think-life 运行时规格

本文档记录当前 `think_life_v1` 的已实现行为（as-built），用于运行、调试和兼容性判断。
当前实现已经具备 transaction 四态、activation/delegate、revision/CAS/ledger、SQLite
Transaction+Scene+Flush，以及 Pause/Delete/Restore。Delete 已接入生产 ThinkLife、
LangGraph、Chat API 与 M-Agent-UI；runtime Store 内的依赖清理、运行中写入 fence 和
控制面投影由运行时语义测试覆盖。

未来目标语义以[总体设计架构](../overall-design-architecture.md)和
[Runtime 迁移语义测试规格](runtime-migration-semantic-test-spec.zh-CN.md)
为准；目标文档与本文不一致时，差异应由验收测试显式呈现，而不是把当前实现误当成
迁移后的最终语义。评测探针路径不在本规格范围内。

## 不变量

1. 一切刺激经 Perception 入队；**入队即触发调度**（`ThreadDrainer`），不依赖旁路 `busy` 标志。
2. 用户可见文本必须可追溯到 `reply_to_user` tool。
3. `execution_feedback` 必须带 `transaction_id + activation_id + delegate_id`。
4. WM 按 `transaction_id` 隔离。
5. 每个 `conversation_id` 只有一条 Scene 时间序列；Scene 跨事务，`transaction_id` 仅作条目标签。
6. Think 由调度器驱动，非单次 HTTP handle。

## 队列与运行时相位（OS 语义）

`effective_depth = inbox_pending + (1 if in_flight else 0)`（在途指已 pop、尚未跑完的刺激）。`pending_stimuli` 在 **pop 后** 与 **入队后** 立即按真实 inbox 刷新，避免「已 pop 仍计 pending」导致刚发一条 user 就误报 `busy`。

| effective_depth | runtime_phase | 含义 |
|-----------------|---------------|------|
| 0 | `ready` | 无排队、无在途 |
| 1 | `processing` | 恰好一个刺激在被消费 |
| ≥2 | `busy` | 有 backlog |

- `thread_runtime.busy`：`effective_depth >= 2`（兼容字段）。
- UI 处理中指示：建议用 `runtime_phase !== 'ready'`（含 `processing` 与 `busy`）。
- **日程**：与用户消息相同，lease 后 **仅入队** `scheduled_plan` 刺激；不在日程触发层因 `busy` 推迟 lease。`mark_running` / `mark_done` 在对应 transaction 开始/终态时回调 schedule store。

## 抢占（`scheduler.preempt_enabled`）

- `false`：当前刺激完整消费后才处理下一条；普通无 source 的新 user 刺激仍按归因规则创建新 transaction。
- `true`：更高优先级刺激只允许在 effect dispatch 前抢占；transaction 保持
  `continue`，同一个已 claim 刺激通过 claim-token CAS 回到 durable Inbox，并保留
  `_preempt_count` / `_checkpoint`（默认上限 3）。一旦 `begin_delegate` 已提交，普通
  抢占必须等待该 delegate 的 Feedback，避免重复外部副作用；force-stop 是独立命令。

## 核心实体

见 `src/m_agent/runtime/think_life/contracts.py`。

- `TransactionRecord.state` 是权威任务状态：`continue | pause | complete | archive`。
- `TransactionRecord.lifecycle_status` 与四态正交：`active | deleted`；Delete 写入永久
  tombstone，不伪装成 Archive。
- 内部不再定义或持久化 `TransactionStatus`；旧客户端需要的
  `running/waiting_execution/suspended/...` 仅在 API 边界由领域事实计算为字符串。
- 每次可执行期由 `ActivationRecord` 标识；Restore 保留 transaction ID 并创建新的
  activation。旧 activation 及其未完成 delegate 被完成或失效后不能继续写入。
- `DelegateRecord` 同时绑定 `transaction_id` 与 `activation_id`，状态为
  `pending | consumed | invalidated`。
- 每个 transaction 带单调递增的 `revision`，所有领域写入以 expected revision 做 CAS。

## Scene

每个 conversation 一条、跨事务并按实际接纳顺序分配单调 `seq` 的 append-only log；
`transaction_id` 仅作标签。Scene append 支持稳定 `append_id`，相同 ID 重放不会复制条目。

- **Think 规划 / 默认 `GET .../scene`**：读取当前 conversation 的 Scene，条数受 `scene_context_max_entries` 限制。
- **审计全量**：`GET .../scene?since_flush=false` 使用 `tail`。

`SceneLogStore(runtime_store=...)` 与 Transaction/Flush 共用 `SQLiteRuntimeStore`，
conversation sequence、条目与 flush watermark 均可重启恢复。生产 ThinkLife 的权威库位于
`data/memory/chat-api/<owner>/runtime/think_life.sqlite3`；旧
`scene/<conversation_id>.jsonl` 与 `.meta.json` 只作为兼容读取/一次性导入来源，不再是
新写入的第二权威。将 Schedule/Inbox/effect 与外部记忆物化纳入生产统一提交边界属于
P7，而不是由 Harness 假定已经完成。

## 事务生命周期

权威领域生命周期为：

- 创建 transaction：`continue`，同时创建首个 activation；
- Pause：进入 `pause` 并记录 `awaiting_user | scheduled_wait | manual_hold |
  runtime_error`；等待 Feedback 不占用 transaction state，而由 pending delegate 表达；
  `manual_hold` / `runtime_error` 会失效当前 activation/delegate；
- Complete：`continue → complete`，结束当前 activation，但在 Flush 前仍保留原
  transaction；
- Archive：只能由带稳定 `flush_id` 的 Flush 将 eligible `complete` 转为 `archive`，
  不能由普通控制命令直接产生；
- Restore：`pause | complete | archive → continue`，保留 transaction ID、WM 与
  TaskState，创建新的 activation；
- Delete：写入正交的 `lifecycle_status=deleted` tombstone，失效活跃
  activation/delegate，且不能 Restore；只有显式控制面/API/UI Delete 走这条路径，
  运行时不会自动推断删除。删除同时取消
  transaction-side schedule run、abort 已绑定的待处理 stimulus、终结 feedback outbox，
  并定向取消占用 CPU 的同一 transaction。已经发往第三方系统的外部副作用不回滚，
  但其后续 WM、Scene、reply 与 feedback 写入会被 fence。

运行失败写 `pause(runtime_error) + last_error`，用户停止思考写
`pause(manual_hold)`，两者都可显式 Restore；它们不再伪装成永久删除。Flush 只负责把
已经明确 `complete` 的记录归档，不能替 open transaction 决定完成。

旧 `pending → running → waiting_execution → running →
completed|failed|cancelled` 与 `suspended` 不再进入 Transaction 聚合；API 兼容字段
由 `state + lifecycle_status + active_delegate_id + pause_reason` 即时计算。

### 一次性 Schedule 的 transaction-side 原语

`TransactionScheduleCoordinator` 在同一个 SQLite UoW 中实现 P2 所需的五组配对转换：

- register：创建稳定 `schedule_run_id`，原 transaction 进入 `pause(scheduled_wait)`；
- claim：按 run revision 和 transaction revision 做双 CAS，为原 transaction 创建稳定
  delivery generation、`schedule_delivery_id` 和新 activation；
- complete：transaction 进入 `complete`，run/delivery 同时进入 `consumed`；
- manual Pause：activation 失效，run 进入 `blocked_on_activation`，未消费 delivery
  进入 `aborted`；
- Delete：写 transaction tombstone，并把该 transaction 的未终结 run 置为
  `cancelled`。

Store 用唯一约束保证一个 transaction 同时最多有一个 `claimed` run，run 的
transaction binding 与 revision 不可静默改写。这里仅负责 transaction-side
Schedule/activation 原子事实；到期扫描、durable Inbox admission、consumer claim 和
最终 stimulus disposition 仍属于 P3/P7 的生产接线。

同一 conversation 可包含多条事务。领域层用
`transaction_id + activation_id + delegate_id` 校验 Feedback 来源，并在 durable
admission/preconsume Gate 拒绝 deleted 或已失效的来源。无 source 刺激只在当前
conversation 的 Pause/未 Flush Complete 集合中匹配；deleted 事务的用户可见 Scene
仍以稳定的 `deprecated_N` 标签进入 matcher prompt 维持对话连续性，但该标签及其
durable transaction ID 都不可选择、恢复或重新参与执行。`reply_to_user` 不自动合并或
结束其他事务。

归因完成后，运行时会在 Scene/CPU 工作之前把 claimed stimulus 的
`transaction_id` 持久绑定；该绑定与 Delete 共用 registry lock。绑定先提交时 Delete
能在同一清理事务中 abort 它，Delete 先提交时绑定步骤直接写
`expected_discard/aborted`，因此 lease 恢复不会把该刺激重新匹配到另一条事务。带来源的
Feedback 与 Schedule admission 也使用同一线性化规则。

## 一致性、ledger 与 Flush

`SQLiteRuntimeStore` 是 P2 reference 路径的持久化权威，保存 Transaction、
Activation、Delegate、transition ledger、Scene、conversation watermark、Flush journal
和物化 outbox。`TransactionRegistry` 使用同一 Store，并提供 revision-fenced CAS：

- 调用方可传 `expected_revision`；过期写入在 mutation 前拒绝，且不产生副作用；
- 每次成功领域 mutation 将 revision 精确推进一次；
- `RuntimeUnitOfWork` 以稳定 transition ID 与 canonical command digest 记录完整结果；
- 相同 transition ID + digest 重放返回第一次提交的完整结果，不再次执行 mutation；
- 相同 transition ID + 不同 digest 明确返回 idempotency conflict；
- Flush 在一次 SQLite 事务中提交 Scene watermark、eligible archive、稳定
  `flush_id` 和可见边界；commit 后未完成物化可由 outbox 在重启后恢复。
- Chat API 的每次成功 Flush（包括没有待写对话的 `noop`）都会推进
  `conversation_id`。因此默认 Transaction/Scene 投影与 matcher 不跨 conversation；
  历史 transaction 仅通过显式 `include_history=true` 审计视图保留。

上述原子性是 P2 reference Store 的已实现保证。生产 Chat/Schedule/Inbox/effect
跨 store 的最终一致性、effect intent/result 与 Feedback relay 属于 P7。

## WM

`TransactionRecord` 是任务状态的唯一所有者：`task_state` 保存 `goal/completion_status/completed/remaining`，其中 `completion_status` 为 `processing | awaiting_user | completed`；`wm_entries` 保存该事务的工具证据。Think 同时读取当前事务状态、当前 conversation 的共享 Scene 与 dialogue history；事务之间不复制 WM。

## Think 层（plan-only）

非 feedback 刺激先经过 `resolve_transaction`（确定性规则不足时才调用模型）；选定事务后执行 `pre_gen_task_state`，最后执行 `make_decision`。只有 `make_decision` 可见能力列表与 persona。

Think 层固定为 **plan-only**：`ThinkingAgent` 不直接执行工具。工具执行与 `reply_to_user` 均由 `ThinkLifeLoop._delegate_and_wait` 委托；**每次 delegate 仅调用一个** `tool_name`，经 **registry 直调**（`invoke_tool_direct`，无 execution-layer ReAct LLM）。参数化阶段使用 structured output（`fill_tool_args`）：可 `invoke`（填齐 args 后直调）或 `clarify`（信息缺失时**不调用工具**，合成 `execution_feedback` 回到感知层）。`get_current_time`、`shallow_recall`、`deep_recall` 等登记在 `THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG` 的工具跳过 param LLM，`instruction` 直接映射为 invoke 参数（recall：`instruction` → `question`）。

**execution_feedback 语义**：user 消息标明「仅完成一个 delegate 步骤」，优先注入 Structured tool result（`count`/`action`/`answer`/`partial`/`needs_clarification`/`stage`/`tool_invoked`）。`stage=param_fill` 且 `tool_invoked=false` 表示参数化短路：思考层可先用其他 tool 补参，无法补参再 `answer_directly` 追问。多步用户诉求下，若 `schedule_create` 返回 `count=1` 或 `partial=true`，Loop 会 **completion gate** 拦截过早的 `answer_directly` 并 nudge 再 plan（最多 2 次）。`reply_to_user(finalize=true)` 只结束本条用户可见消息流，也必须生成 `execution_feedback`，不得直接表示任务完成。

`request_complete` 由任务状态派生：`completion_status=completed` 时运行时强制 `silent + request_complete=true`；`awaiting_user` 时强制 `silent + request_complete=false`；`processing` 时所有规划结果均为 `request_complete=false`。工具完成但仍需向用户传达结果时保持 `processing`，澄清问题已发送时进入 `awaiting_user`，完整结果已发送且目标满足后才进入 `completed`。

`execution_feedback` 规划时从 Scene 取最近 user utterance 作为 `pending_user_request`。

## 配置

`chat_controller.yaml` → `runtime.think_life` 块。Think-life 是唯一运行时，无需配置 profile。

## HTTP：运行中输入与 Scene

- `POST /v1/chat/threads/{thread_id}/stimuli`：用户消息入队（202），不阻塞 HTTP；每 thread 至多一个 `ThreadDrainer` 后台线程调用 `run_thread` 直至 inbox 清空。
- `GET /v1/chat/threads/{thread_id}/scene`：只读 Scene 时间轴（`limit` / `before_seq`）。
- Thread SSE：`stimulus_queued`、`reply_emitted`、`scene_entry_appended`、`thread_runtime_updated`。
- `GET .../schedules/heartbeat` 与 `GET /healthz` 含 `thread_runtime`（含 `runtime_phase`、`effective_depth`）/ `think_life` 健康字段。
- 日程 lease 不因 `thread_runtime.busy` 推迟；到期任务直接作为 `scheduled_plan` 刺激入队。

## 验证与剩余边界

P2 Foundation Gate 不在文档中写死测试条数，以 marker 选择当前完整集合：

```powershell
python -m pytest -q -m p2_foundation
```

当前验收口径为 35 `Executable` / 35 `Passed` / 0 `Registered Known Gaps`。
P7 已接入 effect ledger、Feedback ingress outbox、capability delivery guarantee
与 Schedule delivery 控制恢复。
