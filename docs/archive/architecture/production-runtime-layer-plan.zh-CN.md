# 生产 Runtime 层实施计划

> 状态快照：2026-08-02（**P8 验收矩阵已完成**；**R1～R3 已完成**；**R4 生产接线已落地、观察期进行中**；产品默认只把**新 transaction** 路由到 `langgraph_v1`，ThinkLife 兼容 host 仍必须保留到 R5 退出门满足）<br>
> 范围：把 acceptance 已证明的双 Runtime 语义，接到可承载真实对话的生产 Runtime 层与 Chat API 灰度入口。

本文回答三个问题：

1. P8 之后「生产 Runtime 层」与 acceptance 层差在哪里；
2. 应按什么顺序把 LangGraph 接到真实流量；
3. 每一阶段的产出、验收门与回退方式是什么。

> **阶段定位**
>
> 官方 P0～P8 已在 **验收平台** 收口。当前实质阶段为 **P8 后 · 生产 Runtime 接线期**：
> 目标不是再扩测试矩阵，而是让 **HTTP / 内部 thread** 能按 `runtime_engine` 稳定执行。

---

## 1. 当前位置

### 1.1 已完成

| 层级 | 状态 | 说明 |
| --- | --- | --- |
| 设计总线与 P1 契约 | ✅ | TX/SP/AT 语义、38 variant、双 Runtime binding |
| ThinkLife 兼容 Runtime | ✅ | `ThinkLifeRuntime`；继续承接其存量 transaction 与回退后新建 transaction |
| P8 acceptance LangGraph | ✅ | 38/38 variant；持久 SQLite checkpointer；`routing.py` |
| 稳定观察期（首轮） | ✅ | 双 Runtime 全矩阵各 3 轮 + Robustness 重点切片全绿 |
| RuntimeHost 协议 | ✅ | `src/m_agent/runtime/host/` |
| LangGraphRuntime MVP | ✅ | `src/m_agent/runtime/langgraph/runtime.py`；最小 inbox→graph 链路 |
| R1 内部 thread 验证 | ✅ | `scripts/smoke_langgraph_runtime.py`；5 轮 × 116 检查全绿 |
| R2 graph 内完整循环 | ✅ | `turn_graph.py`；`scripts/smoke_langgraph_turn_loop.py` 3 轮 × 78 检查全绿 |
| R3 Chat API RuntimeHost 分发 | ✅ | `chat_api_runtime.py` 持 `CompositeRuntimeHost`；两引擎并存并按 transaction 归属分发 |
| R4 默认路由与可靠性接线 | ✅ | 新 transaction 默认 `langgraph_v1`；checkpoint fail-fast；原 transaction Schedule；原子 Flush |

### 1.2 尚未完成

| 缺口 | 影响 |
| --- | --- |
| R4 稳定观察期 | 默认路由已切换，但仍需持续运行双 Runtime gate、演练回退并观察真实流量 |
| 历史 ThinkLife 可恢复工作 | 可能仍有非终态 transaction、Inbox、Schedule、effect/outbox，必须由只读 audit 判定 |
| R5 旧 loop 收缩 | 尚未开始；观察期通过且 `legacy_retirement_ready=true` 前禁止删除 ThinkLife |

### 1.3 三层结构（当前）

```text
Acceptance 层     langgraph_v1 adapter · 38 variant · 已全绿
       ↓
生产 Runtime 层   ThinkLifeRuntime ✅  |  LangGraphRuntime（turn loop）✅  |  RuntimeHost ✅
       ↓
产品入口          Chat API → CompositeRuntimeHost（新 TX 默认 LangGraph；旧 TX 按归属续跑）✅
```

> **关键边界**
>
> - **Transaction Store** 仍是 WM/任务状态/四态的权威；LangGraph checkpoint 只保存图执行恢复态。
> - 已开始运行的 transaction **不在中途切换** Runtime；回退只影响 **后续新建** transaction 的默认路由。

---

## 2. 目标与成功标准

### 2.1 总目标

在 **不迁移进行中的 ThinkLife transaction** 的前提下，让 LangGraph 成为新
transaction 的生产默认引擎，并保留按 transaction 归属执行与只影响新 transaction
的可回退能力。

### 2.2 阶段成功标准（汇总）

| 里程碑 | 完成条件 |
| --- | --- |
| R1 内部验证 | 非 acceptance script 下，LangGraphRuntime 跑通 fake 对话链路 |
| R2 完整 graph 循环 | thinking → delegate → feedback → Scene 在 LangGraph host 内闭环 |
| R3 Chat API 分发 | HTTP 入口按 `runtime_engine` 选择 RuntimeHost；health 可观测 |
| R4 生产灰度 | 新 transaction 默认 LangGraph；双引擎门禁、真实流量观察与回退演练持续通过 |
| R5 旧 loop 收缩 | 观察期完成且 audit 输出 `legacy_retirement_ready=true`，之后才删除/归档旧循环 |

---

## 3. 架构要点

### 3.1 RuntimeHost（engine-neutral 门面）

位置：`src/m_agent/runtime/host/`

| 组件 | 职责 |
| --- | --- |
| `protocol.py` | `RuntimeHost` 协议：submit / run_thread / health / shutdown |
| `think_life_adapter.py` | 包装现有 `ThinkLifeRuntime` |
| `composite.py` | 同时持有两个 engine host；按持久化 `runtime_engine` 与现有 conversation 归属分发 |
| `factory.py` | `create_runtime_host(..., multiplex=True)` 创建 Composite host；所选 engine 仅作为新 transaction 默认值 |

Chat API 与未来 orchestrator **应依赖 RuntimeHost**，而不是直接依赖具体 engine 类。
生产 Chat API 使用 Composite host：显式 `transaction_id` 先查其持久化归属；已有活跃
conversation 继续交给拥有该 transaction 的 engine；只有确实需要新建 transaction
时才使用当前默认 engine。两个 engine 在整个 R4/R5 过渡期同时打开，不能因为改变
默认值而关闭旧 engine。

### 3.2 LangGraphRuntime（生产引擎）

位置：`src/m_agent/runtime/langgraph/`

| 组件 | 职责 |
| --- | --- |
| `runtime.py` | 生产 host：Store / Inbox / Gateway / Attributor / Scene / Drainer |
| `inbox_loop.py` | drain：归因 → turn graph（或回退 MVP graph step）→ Scene |
| `engine.py` + `checkpointer.py` | 持久 graph 执行与 SQLite checkpoint |
| `transaction_graph.py` | MVP StateGraph（`record_progress`，R2 回退目标） |
| `turn_graph.py` | R2 Turn StateGraph：`think → plan_delegate → delegate → settle_turn` |
| `turn_ports.py` | 出边口：`TurnPlanner` / `DelegateExecutor` / `DelegateEffectLedger` |
| `config.py` | engine 本地开关（turn loop、delegate executor、delivery guarantee） |

持久化（按 user）：

- `{persist_root}/runtime/langgraph.sqlite3` — Transaction Store
- `{persist_root}/runtime/langgraph-checkpoints.sqlite3` — MVP graph checkpoint
- `{persist_root}/runtime/langgraph-turn-checkpoints.sqlite3` — turn graph checkpoint

两张 checkpoint 表**分文件、分 thread 命名空间**（turn graph 用 `turn:{transaction_id}`），
避免 state schema 不同的两张图互相覆盖。

生产 checkpoint 是严格耐久契约：`persistent=True` 时若路径缺失、
`langgraph-checkpoint-sqlite` 不可用或 M-Agent checkpoint schema marker 不兼容，
进程必须启动失败，不能静默降级到内存。`InMemorySaver` 只允许调用方显式
`persistent=False` 的测试场景。LangGraph 的 per-engine health 必须暴露已验证的
checkpoint `backend`、`durable` 与 `schema_version`，运维应确认生产值为 SQLite、
`durable=true`。

新建 transaction 默认 `runtime_engine=langgraph_v1`；该值创建时写入 Transaction
Store，此后是该 transaction 全生命周期的引擎归属依据。

### 3.3 灰度路由

位置：`src/m_agent/runtime/routing.py`

- 创建 transaction 时固定 `runtime_engine`
- checked-in `runtime.default_engine: langgraph_v1` 控制产品默认值
- `M_AGENT_DEFAULT_RUNTIME_ENGINE=langgraph_v1|think_life_v1` 是部署级覆盖，优先于 YAML，且只控制 **新 transaction** 默认归属
- 回退 = 设置 `M_AGENT_DEFAULT_RUNTIME_ENGINE=think_life_v1` 并重启服务；**不影响** 进行中或可恢复 transaction

> **回退不是 transaction 迁移**
>
> 环境变量只改变“没有现有归属时”的默认选择。Composite host 仍按 Store 中的
> `runtime_engine` 把 ThinkLife transaction 送回 ThinkLife、把 LangGraph
> transaction 送回 LangGraph；禁止在中途改写归属来实现回退。

### 3.4 一次性 Schedule：恢复原 transaction

- `schedule_create` 保存 `origin.transaction_id` 与 `origin.conversation_id`，并在
  transaction-side 注册等待关系；原 transaction 进入 `SCHEDULED_WAIT` pause。
- heartbeat 使用 `schedule_id + 原 transaction_id + due_at` 生成稳定 `run_id`，并从
  `run_id + generation` 生成稳定 `delivery_id`；重试不能创建一个随机的新 run。
- 到期 delivery 通过 Composite host 回到原 transaction 所属 engine，claim 原 run、
  为原 transaction 开启新 activation，完成后同步收口 transaction 与 Schedule
  lifecycle。无 origin 的外部 Schedule 才按普通无来源规则处理。

### 3.5 Flush：SQLite 原子提交 + 耐久物化

单引擎内部仍由 `FlushCoordinator` 在一个 SQLite 事务中提交 Scene watermark 与精确
revision 的 eligible transaction archive。Composite 灰度期不能把两个引擎各自独立的
Scene `seq` 合并成一个最大值：`CompositeRuntimeHost` 会先冻结不可变 FlushSnapshot，
为每个 engine 分别保存 entries、`through_seq` 与 `eligible_revisions`。

跨两个 SQLite 的提交由 `{persist_root}/runtime/composite-flush.sqlite3` 中的耐久 saga
journal 协调：每个 engine 独立记录 `pending/committed`，第二个 engine 失败或进程重启后
继续同一 snapshot 和 `flush_id`。实际 Dialogue（包括 Scene 缺失时的 buffered fallback）
在领域提交前写入 journal materialization outbox；Dialogue/本地 RAG 成功后才标记
`delivered` 并完成 journal。重放使用稳定 `dialogue_id`，不得重复 archive、重复 RAG
chunk，或把 snapshot 之后的新 Scene entry 越过 watermark。

---

## 4. 实施阶段（R1～R5）

### R1：内部 thread 验证（优先，约 1～2 天）✅ 已完成

**目标**：证明 LangGraphRuntime 在 **非 acceptance** 路径可稳定运行。

**任务**：

1. ✅ 新增 `scripts/smoke_langgraph_runtime.py`（或同等 dev 入口）
2. ✅ 使用 `create_runtime_host(..., runtime_engine=langgraph_v1)`
3. ✅ 覆盖：`submit_user_message` → `run_thread` → 检查 Store / Scene / checkpoint
4. ✅ 可选：进程重启后 checkpoint + Store 一致性抽查

**退出条件**：

- ✅ 脚本可重复运行 5 次无失败（`--rounds 5`，5/5 通过，每轮 116 项检查）
- ✅ `runtime_engine`、WM、revision 与 graph_phase 一致（Store × checkpoint 逐 transaction 对齐）

**运行方式**：

```bash
python scripts/smoke_langgraph_runtime.py --rounds 5
python scripts/smoke_langgraph_runtime.py --rounds 5 --verbose --json-out .tmp/r1.json
```

脚本使用 stub ThinkingAgent（无模型调用），每轮独立 persist_root；stdout 为 JSON 汇总，
进度行走 stderr；全绿退出码 0，否则 1。`tests/runtime/test_smoke_langgraph_runtime.py`
以单轮跑同一入口，防止脚本随实现漂移。

**回退**：仅 dev 脚本，不影响 Chat API。

---

### R2：LangGraph 内完整循环（核心，约 1～2 周）✅ 已完成

**目标**：用真实 **Thinking + delegate + Feedback** 替代 MVP 的 `record_progress` 占位。

**任务**：

1. ✅ 新增 `turn_graph.py` 并由 `LangGraphInboxLoop` 驱动：
   - ✅ `think` 节点调 `ThinkingAgent.handle`，复用 ThinkLife 的 think_context / WM / Scene tail
   - ✅ `plan_delegate` 节点把决策落成 delegate intent（含 `reply_to_user` 归一化）
   - ✅ `delegate` 节点执行并把结构化 Feedback 经 Gateway 送回 inbox，下一次 drain resume graph
   - ✅ `settle_turn` 节点按 revision fence 落 Store，并写回 checkpoint
2. ✅ 复用 P7 外层：`EffectCoordinator` 登记 effect intent、Feedback outbox、capability delivery guarantee
3. ✅ 对照 acceptance TX-07 切片做内部回归（契约未改）

**关键设计**：

| 主题 | 处理 |
| --- | --- |
| 一次 drain = 一个 turn | graph 不在节点内自旋等 Feedback；Feedback 作为新 stimulus 触发下一 turn，故 host 重启可续跑 |
| 状态权威 | Store 仍是唯一权威；graph 每次提交带 expected revision（`RuntimeUnitOfWork`） |
| delegate 链上限 | `max_delegate_chain`（默认 32），超限即 settle，避免自触发死循环 |
| Scene 因果 | thought / action / reply 均绑定 `transaction_id` + `delegate_id`，seq 严格递增 |
| USER_TASK 收口 | turn 结束不 COMPLETE，交给 flush；非 USER_TASK 当轮闭环 |

**退出条件**：

- ✅ 内部 thread 完成：用户消息 → 思考 → fake tool → 结构化 Feedback → Scene → reply
- ✅ 与 ThinkLife 同输入、同 scripted 决策下，**领域观察面**（transaction 四态/状态、delegate
  status、stimulus disposition、Scene 序列）逐字段相同
- ✅ host 重启后 Store × Scene × turn checkpoint 三方一致（revision / WM / graph_phase）
- ✅ 回退开关关闭后行为退回 R1 `record_progress`，且 `ThinkingAgent` 一次未被调用

**运行方式**：

```bash
python scripts/smoke_langgraph_turn_loop.py --rounds 3
python scripts/smoke_langgraph_turn_loop.py --rounds 3 --verbose --json-out .tmp/r2.json
python scripts/smoke_langgraph_turn_loop.py --rounds 3 --skip-compare
```

脚本使用 stub Thinking/Execution Agent（无模型调用），一个入口同时跑两道门：**turn loop
冒烟**（含 host 重启与回退验证）与 **ThinkLife 领域对比抽样**。每轮 78 项检查，
`tests/runtime/test_langgraph_turn_loop.py` 以单测覆盖同一批不变量防漂移。

**回退**：`M_AGENT_LANGGRAPH_TURN_LOOP=0`，或 agent config
`runtime.langgraph.turn_loop=false`，即回退 MVP `record_progress` 路径（`turn_engine=None`，
`health()["turn_loop"]=false`）。优先级与 `resolve_runtime_engine` 一致：显式 config > env > 默认开。

---

### R3：Chat API RuntimeHost 分发（约 3～5 天，依赖 R2）✅ 已完成

**目标**：HTTP 入口不再硬编码 `ThinkLifeRuntime`。

**任务**：

1. ✅ `chat_api_runtime.py` 持有 `CompositeRuntimeHost`（双 engine + 新 transaction 默认值）
2. ✅ `submit_user_message` / `run_thread` / health 走 host 协议
3. ✅ Scene / flush / schedule / force_stop 等产品扩展在双 engine 上通过共享 `product_views` + duck-typed engine
4. ✅ snapshot / health 增加 `runtime_engine_id` 与 per-engine 指标

**退出条件**：

- ✅ 内部 conversation 可通过 Chat API 走 LangGraph（`runtime.default_engine=langgraph_v1` 或 env）
- ✅ Composite host 可同时恢复两种 engine 的 transaction；默认值不会迁移既有 transaction
- ✅ 单 transaction 全生命周期 engine 不变（创建时固定 `runtime_engine`）

**回退**：配置默认 engine 切回 `think_life_v1`；进行中 LangGraph transaction 继续由 LangGraph 完成。

---

### R4：生产灰度（机制已接线；约 1～2 周观察，依赖 R3）

**顺序**（与 P8 文档一致）：

1. ✅ fake tool 环境
2. ✅ 内部 conversation
3. ✅ 新建普通 user transaction
4. ✅ Scheduled Plan 恢复原 transaction（稳定 run/delivery ID）
5. ✅ 默认新 transaction 路由 LangGraph
6. ⏳ 稳定观察期

**任务**：

1. ✅ checked-in 产品配置默认 `langgraph_v1`；部署可用
   `M_AGENT_DEFAULT_RUNTIME_ENGINE` 覆盖新 transaction 默认值
2. 每日/每次发布运行 `python scripts/run_runtime_migration_gate.py --rounds 3`
3. 运行只读 `python scripts/audit_runtime_cutover.py`，记录 legacy blockers 的变化
4. 监控：重复 effect、Scene/flush 漂移、checkpoint backend/schema 与 Store revision 冲突
5. 演练 `M_AGENT_DEFAULT_RUNTIME_ENGINE=think_life_v1` 回退，并确认旧 LangGraph
   transaction 仍由 LangGraph 完成

**退出条件**：

- 灰度期间无未登记 semantic failure
- 无「Store 与 checkpoint 静默覆盖」 incident
- 回退演练通过（env 切回 ThinkLife，新 transaction 不再进 LangGraph）
- 达到约定观察期；这项时间条件不能由一次测试运行替代

---

### R5：旧 Runtime 收缩（最后）

**前置**：R4 观察期结束，且对实际持久化根目录运行 audit 后
`legacy_retirement_ready=true`。当前尚未满足 R5，ThinkLife host 不能删除。

**任务**：

1. 用只读脚本清点 ThinkLife Store 中的非终态 transaction、Stimulus、Schedule、
   effect 与 Feedback/Flush outbox：

   ```powershell
   python scripts/audit_runtime_cutover.py --root data/memory/chat-api
   python scripts/audit_runtime_cutover.py --root data/memory/chat-api --require-legacy-retired
   ```

   第二条命令在仍有 legacy recoverable work 时以退出码 2 失败。
2. 版本化迁移或只读兼容层
3. 删除/归档 ThinkLife transaction 内循环中已被 LangGraph 替代的路径

**退出条件**：

- `legacy_retirement_ready=true` 且不存在只能由即将删除代码恢复的工作
- R4 观察期、回退演练与发布门禁均有记录
- 文档与运维 runbook 更新

---

## 5. 测试与观察策略

### 5.1 必跑门禁

| 频率 | 命令 / 范围 |
| --- | --- |
| 每次 PR | `python scripts/run_runtime_migration_gate.py --rounds 3` |
| 每日 / 发布前 | 同一 migration gate；需要时加 `--include-full-suite`，归档输出 JSON |
| R1 后 | `python scripts/smoke_langgraph_runtime.py --rounds 5` |
| R2 后 | `python scripts/smoke_langgraph_turn_loop.py --rounds 3`（内部 smoke + TX-07 领域对比抽样） |
| R4 观察期 | `python scripts/audit_runtime_cutover.py --root <实际持久化根目录>`（只读） |
| R5 删除前 | 上述 audit 加 `--require-legacy-retired`，必须退出 0 且报告 `legacy_retirement_ready=true` |

### 5.2 已固化的观察脚本

`scripts/run_runtime_migration_gate.py` 依次运行 Runtime 单测、ThinkLife acceptance、
LangGraph acceptance 与 LangGraph turn-loop smoke，并输出包含每个 slice 的退出码、耗时
与总结果的 JSON；默认失败即停，可用 `--continue-on-failure` 收集全部结果。

`scripts/audit_runtime_cutover.py` 只读扫描 `think_life.sqlite3`、
`langgraph.sqlite3` 与 `composite-flush.sqlite3`，统计 transaction、Stimulus、Schedule、
effect、Feedback/Flush outbox、Composite engine commit 与 materialization blocker，并校验
Store 与声明的 `runtime_engine` 是否错配。它是 R5 删除门，不是迁移工具，也不会改写
用户数据。

---

## 6. 风险与回退

| 风险 | 控制 |
| --- | --- |
| 默认值变化导致旧 transaction 换 engine | Composite host 每次按持久化 `runtime_engine`/现有归属分发；默认值仅用于新建 |
| checkpoint 覆盖 Store 权威 | 所有 graph 提交带 expected revision；Pause/Delete 后旧 checkpoint 不得 silent reload |
| SQLite checkpointer 缺包或 schema 不兼容 | 生产启动 fail-fast；health 报告 backend/durable/schema；禁止静默 memory fallback |
| turn graph 与 MVP graph checkpoint 串味 | 两图分 checkpoint 文件；turn graph thread 命名空间为 `turn:{transaction_id}` |
| Feedback 自触发死循环 | `max_delegate_chain` 上限后强制 settle；每轮 delegate 必须匹配 pending intent 才推进 |
| Schedule 重试新建 transaction / 重复执行 | 保存 origin，使用稳定 run/delivery ID，claim 原 transaction 并幂等收口 lifecycle |
| 双 engine Scene/flush 漂移 | 逐 engine FlushSnapshot + 本地原子 commit；Composite saga/outbox 固定 payload、独立水位并支持重启续跑 |
| 灰度 incident | 只回退 **新 transaction** 默认路由；保留 audit 与 engine 字段 |

---

## 7. 与现有文档的关系

| 文档 | 关系 |
| --- | --- |
| [current-project-progress-and-design-plan.md](current-project-progress-and-design-plan.md) | P0～P8 已完成；本计划是 P8 后的生产接线专篇 |
| [langgraph-runtime-migration-plan.zh-CN.md](langgraph-runtime-migration-plan.zh-CN.md) | 状态权威与 Runtime Host 总线；本计划是其生产落地时间表 |
| [semantic-acceptance-platform.zh-CN.md](semantic-acceptance-platform.zh-CN.md) | 验收平台用法；R1～R5 仍以 38 variant 为回归门禁 |

冲突时优先级：

```text
设计总线 → 共享语义契约 → P8 阶段结论 → 本生产 Runtime 计划 → 具体实现细节
```

---

## 8. 当前立即执行的下一步

1. ~~**R1**：编写并跑通 `smoke_langgraph_runtime` 内部脚本（5 次重复）~~ ✅ 已完成
2. ~~**R2**：启动 graph 内 thinking/delegate/feedback 循环（最大块）~~ ✅ 已完成
3. ~~**R3**：Chat API 改持 `RuntimeHost`，按 `runtime_engine` 分发~~ ✅ 已完成
4. **R4（进行中）**：持续运行 migration gate、真实流量观察与 env 回退演练
5. 每日记录 cutover audit，消化 legacy recoverable work
6. 仅在观察期通过且 `legacy_retirement_ready=true` 后进入 **R5**

**不在此阶段做**：删除 ThinkLife loop（属 R5）。

---

## 9. 文件索引（实现落点）

| 路径 | 说明 |
| --- | --- |
| `src/m_agent/runtime/host/` | RuntimeHost 协议与 factory |
| `src/m_agent/runtime/host/composite.py` | 双 engine、按 transaction 归属分发的生产 host |
| `src/m_agent/runtime/langgraph/runtime.py` | LangGraphRuntime 生产 host |
| `src/m_agent/runtime/langgraph/inbox_loop.py` | 生产 inbox drain（turn loop / MVP 二选一） |
| `src/m_agent/runtime/langgraph/turn_graph.py` | R2 turn StateGraph 与 `TransactionTurnEngine` |
| `src/m_agent/runtime/langgraph/turn_ports.py` | planner / delegate executor / effect ledger 出边口 |
| `src/m_agent/runtime/langgraph/config.py` | `LangGraphRuntimeConfig` 与 turn loop 回退开关 |
| `src/m_agent/runtime/routing.py` | 灰度默认路由 |
| `src/m_agent/api/chat_api_runtime.py` | Chat API（持 RuntimeHost；R3 已完成） |
| `src/m_agent/runtime/host/product_views.py` | 双 engine 共享 Scene/TX/flush 产品视图 |
| `tests/runtime/test_chat_api_runtime_host.py` | R3 Chat API host 分发单测 |
| `scripts/smoke_langgraph_runtime.py` | R1 内部 thread 冒烟入口 |
| `scripts/smoke_langgraph_turn_loop.py` | R2 turn loop 冒烟 + ThinkLife 领域对比入口 |
| `scripts/run_runtime_migration_gate.py` | 可重复的双 Runtime R4 发布/观察门禁 |
| `scripts/audit_runtime_cutover.py` | R4/R5 只读 durable-state 审计与 R5 删除门 |
| `tests/runtime/test_runtime_host.py` | Host MVP 单测（含 turn loop 回退） |
| `tests/runtime/test_smoke_langgraph_runtime.py` | R1 冒烟脚本防漂移单测 |
| `tests/runtime/test_langgraph_turn_loop.py` | R2 turn loop 不变量单测 |
| `tests/acceptance/scenarios/` | 38 variant 回归门禁 |
