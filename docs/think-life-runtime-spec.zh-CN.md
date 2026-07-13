# Think-life 运行时规格

本文档是 Think-life 运行时的权威说明（与实现计划同步）。评测探针路径不在本规格范围内。

## 不变量

1. 一切刺激经 Perception 入队；**入队即触发调度**（`ThreadDrainer`），不依赖旁路 `busy` 标志。
2. 用户可见文本必须可追溯到 `reply_to_user` tool。
3. `execution_feedback` 必须带 `delegate_id` + `transaction_id`。
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

- `thread_runtime.busy`（think_life）：`effective_depth >= 2`（兼容字段）。
- UI 处理中指示：建议用 `runtime_phase !== 'ready'`（含 `processing` 与 `busy`）。
- **日程**：与用户消息相同，lease 后 **仅入队** `scheduled_plan` 刺激；不在日程触发层因 `busy` 推迟 lease。`mark_running` / `mark_done` 在对应 transaction 开始/终态时回调 schedule store。

## 抢占（`scheduler.preempt_enabled`）

- `false`：当前刺激完整消费后才处理下一条；新 user 刺激归入当前 active user transaction。
- `true`：更高优先级刺激入队可协作式取消正在执行的 `ExecutionAgent`（`stream` + `cancel_event`），被打断刺激 **重新入队**（`max_preempt_per_stimulus` 上限，默认 3）。

## 核心实体

见 `src/m_agent/runtime/think_life/contracts.py`。

## Scene

每个 conversation 一条、跨事务并按 `occurred_at` 排序的 append-only log；`transaction_id` 仅作标签。磁盘 jsonl 保留该 conversation 的历史。

- **Think 规划 / 默认 `GET .../scene`**：读取当前 conversation 的 Scene，条数受 `scene_context_max_entries` 限制。
- **审计全量**：`GET .../scene?since_flush=false` 使用 `tail`。

持久化：`data/memory/chat-api/<owner>/scene/<conversation_id>.jsonl`；水位：`scene/<stem>.meta.json`。

## 事务生命周期

`pending` → `running` → `waiting_execution` → `running` → `completed|failed|cancelled`；可 `suspended`（抢占）。

同一 conversation 可包含多条事务。`execution_feedback` 通过 `transaction_id` + `delegate_id` 确定性回到原事务；其他刺激先按规则匹配候选事务，语义不明确时由 `resolve_transaction` 判断继续已有事务还是创建新事务。`reply_to_user` 不自动合并或结束其他事务。conversation flush 时完成该 conversation 的 active user 事务并进入下一 conversation。

## WM

`TransactionRecord` 是任务状态的唯一所有者：`task_state` 保存 goal/completed/remaining，`wm_entries` 保存该事务的工具证据。Think 同时读取当前事务状态、当前 conversation 的共享 Scene 与 dialogue history；事务之间不复制 WM。

## Think 层（plan-only）

非 feedback 刺激先经过 `resolve_transaction`（确定性规则不足时才调用模型）；选定事务后执行 `pre_gen_task_state`，最后执行 `make_decision`。只有 `make_decision` 可见能力列表与 persona。

启用 `runtime.profile: think_life` 时，`ThinkingAgent.max_executions_per_turn` 在运行时置为 `0`：Think **只规划**。工具执行与 `reply_to_user` 均由 `ThinkLifeLoop._delegate_and_wait` 委托；**每次 delegate 仅调用一个** `tool_name`，经 **registry 直调**（`invoke_tool_direct`，无 execution-layer ReAct LLM）。参数化阶段使用 structured output（`fill_tool_args`）：可 `invoke`（填齐 args 后直调）或 `clarify`（信息缺失时**不调用工具**，合成 `execution_feedback` 回到感知层）。`get_current_time`、`shallow_recall`、`deep_recall` 等登记在 `THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG` 的工具跳过 param LLM，`instruction` 直接映射为 invoke 参数（recall：`instruction` → `question`）。

**execution_feedback 语义**：user 消息标明「仅完成一个 delegate 步骤」，优先注入 Structured tool result（`count`/`action`/`answer`/`partial`/`needs_clarification`/`stage`/`tool_invoked`）。`stage=param_fill` 且 `tool_invoked=false` 表示参数化短路：思考层可先用其他 tool 补参，无法补参再 `answer_directly` 追问。多步用户诉求下，若 `schedule_create` 返回 `count=1` 或 `partial=true`，Loop 会 **completion gate** 拦截过早的 `answer_directly` 并 nudge 再 plan（最多 2 次）。plan 可选字段 `request_complete`：多步任务未完成时应为 false。

`execution_feedback` 规划时从 Scene 取最近 user utterance 作为 `pending_user_request`。

## 配置

`chat_controller.yaml` → `runtime.profile: think_life` 与 `runtime.think_life` 块。

## HTTP：运行中输入与 Scene

- `POST /v1/chat/threads/{thread_id}/stimuli`：用户消息入队（202），不阻塞 HTTP；每 thread 至多一个 `ThreadDrainer` 后台线程调用 `run_thread` 直至 inbox 清空。
- `GET /v1/chat/threads/{thread_id}/scene`：只读 Scene 时间轴（`limit` / `before_seq`）。
- Thread SSE：`stimulus_queued`、`reply_emitted`、`scene_entry_appended`、`thread_runtime_updated`。
- `GET .../schedules/heartbeat` 与 `GET /healthz` 含 `thread_runtime`（含 `runtime_phase`、`effective_depth`）/ `think_life` 健康字段。
- Legacy profile 下 `schedule_busy_retries_total` 仍可能因 `thread_runtime.busy` 推迟 lease；**think_life** 日程不走该门控。
