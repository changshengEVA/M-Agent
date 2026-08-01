---
name: Remove status projection safely
overview: Transaction 运行时已收敛到 continue / pause / complete / archive；旧 status 只保留兼容投影。失败与 force-stop 按最新决策进入可恢复 pause，只有显式 UI Delete 写 deleted tombstone。
todos:
  - id: semantic-contract
    content: 定稿 PauseReason、四态谓词、抢占边界与 USER_TASK 归因语义
    status: completed
  - id: pr-a-writers
    content: "PR-A/Expand: writer 领域化；fail/force-stop/delete/complete/create 成为单一权威写路径"
    status: completed
  - id: pr-b-preempt
    content: "PR-B/Preempt: 修复 durable requeue；普通抢占限制在 effect dispatch 前"
    status: completed
  - id: deploy-migrate
    content: "Deploy/Migrate: 审计并回填 legacy status-only/skew payload，升级 schema_version"
    status: completed
  - id: pr-c-readers
    content: "PR-C/Switch: loop/attributor/active pointer/LangGraph/API metrics 全部切到领域谓词"
    status: completed
  - id: pr-d-contract
    content: "PR-D/Contract: 删除持久化 status、transition FSM、TransactionStatus 与公共导出"
    status: completed
  - id: release-api
    content: 在至少一个已部署兼容版本后删除 API status
    status: pending
isProject: false
---

> 2026-07-31 实施校正：用户随后明确要求 transaction 只保留四态，因此本文早期草案中
> “failed / force-stop 写 deleted + termination_reason”的设计已作废。当前权威语义为
> `failed -> pause(runtime_error)`、`force-stop -> pause(manual_hold)`、
> `UI Delete -> lifecycle_status=deleted`。正文中仍出现的 termination_reason/delete
> 方案仅是历史讨论，不再是实现依据；contract/remove 与 API status 删除仍需跨真实兼容版本。

# 去除 TransactionStatus 兼容投影方案（安全迁移版）

## 结论与不可违反的顺序

目标不变：运行时控制流只认 `state + lifecycle + activation/delegate + termination_reason`，最终删除：

- `TransactionRecord.status`
- `TransactionStatus`
- `_project_legacy_status`
- `TransactionRegistry.transition`
- `_ALLOWED_TRANSITIONS`
- API 中已废弃的 `"status"`

但不能先切 reader、后改 writer。安全顺序必须是：

```text
Expand writer
  → 修复 durable preempt
  → 审计/迁移历史数据
  → Switch reader
  → 经过回滚与 API 兼容窗口
  → Contract/remove
```

在 reader 切换前，必须同时满足：

1. 新 writer 不再制造 status-only 终态；
2. 旧 payload 已完成回填，或被明确隔离；
3. `FAILED/CANCELLED/COMPLETED` 不再是唯一语义来源；
4. 两个 runtime 的控制流即使看到故意错误的 `status` 也不受影响。

每个 Phase 都必须是可以独立部署和停留的状态；任一门槛失败都不得进入下一阶段。PR 已合并不等于数据迁移已完成，reader cutover 必须由部署后的审计结果显式解锁。

---

## 现状问题

当前存在双写：

1. [`_project_legacy_status`](src/m_agent/runtime/think_life/transaction/domain.py) 根据 `lifecycle + state + active_delegate_id` 写 `status`；
2. Scheduler / LangGraph 仍通过 [`registry.transition`](src/m_agent/runtime/think_life/transaction_registry.py) 直接改 `status`，不改领域字段。

典型 skew：

| 场景 | 兼容 status | 实际领域字段 |
|---|---|---|
| scheduler failure | `failed` | `active + continue` |
| force-stop | `cancelled` | `active + continue` |
| 非 USER_TASK 完成 | `completed` | `active + continue`，activation 未关闭 |
| 抢占 | `suspended` | `active + continue` |
| create | 强制 `pending` | 已经是 `continue` 且已有 active activation |

如果 reader 先改为只看领域字段，上述前三类事务会被重新视为 open/runnable；如果删除字段时只忽略旧 `status`，重启后也会发生同样问题。

---

## 目标领域模型

### 权威字段

- `state`: `continue | pause | complete | archive`
- `lifecycle_status`: `active | deleted`
- `current_activation_id` + `ActivationRecord.status`
- `active_delegate_id` + `DelegateRecord.status`
- `termination_reason`: `failed | force_stopped | user_deleted | legacy_cancelled | null`
- `last_error`: 可选诊断文本，不承担终态类型职责

`termination_reason` 与 `last_error` 分工：

- `termination_reason` 是稳定、可查询、可投影的领域事实；
- `last_error` 只保存错误说明；
- `lifecycle=deleted` 且 `termination_reason=failed` 表示失败 tombstone；
- `lifecycle=deleted` 且 reason 为 `force_stopped/user_deleted/legacy_cancelled` 表示取消或删除；
- 正常完成不写 termination reason，走 `state=complete|archive`。

目标不变量：

- live transaction 的 `termination_reason` 必须为空；
- deleted transaction 的 `termination_reason` 必须非空；
- `termination_reason=failed` 时 `last_error` 必须非空；
- `last_error` 的有无不能用于推断 termination 类型。

若产品明确决定不再区分 failed 与 cancelled，可以删除 `termination_reason=failed` 的兼容投影，但必须把它记录为 API breaking change；不能继续称为“兼容层”。

### 旧 status 到新语义

| 旧语义 | 新权威语义 |
|---|---|
| `pending` | 取消；create 后立即是带 active activation 的 `continue` |
| `running` | live + `continue` + 无 pending delegate |
| `waiting_execution` | live + `continue` + 有 pending delegate |
| `suspended`（协作暂停） | live + `pause` |
| `suspended`（抢占） | TX 保持 live + `continue`；刺激 durable requeue |
| `completed` | `state in {complete, archive}` |
| `failed` | `lifecycle=deleted + termination_reason=failed + last_error` |
| `cancelled`（force-stop） | `lifecycle=deleted + termination_reason=force_stopped` |
| `cancelled`（UI Delete） | `lifecycle=deleted + termination_reason=user_deleted` |

### 兼容 status 只允许计算

扩展期仍保留字段/API 时，由一个纯函数 `project_compat_status(record)` 计算：

```text
deleted + termination_reason=failed                  -> failed
deleted                                               -> cancelled
active + continue + pending delegate                  -> waiting_execution
active + continue + no pending delegate               -> running
active + pause                                        -> suspended
active + complete/archive                             -> completed
```

Scheduler、LangGraph、Store 和测试夹具不得直接赋值兼容 status。

---

## 谓词边界

新建 `transaction/predicates.py`，只放无 Store I/O 的纯函数；不要用一个含糊的 `terminal` 覆盖所有上下文。

- `is_live_record(r)`  
  `lifecycle_status==active AND deleted_at is None`
- `is_open_continue(r)`  
  live + `state==continue`
- `is_waiting_delegate(r)`  
  open continue + `active_delegate_id`
- `is_runnable_record(r)`  
  open continue + 无 active delegate + 有 `current_activation_id`
- `is_paused(r)`  
  live + `state==pause`
- `is_execution_closed(r)`  
  `lifecycle==deleted OR state in {complete, archive}`
- `is_permanently_closed(r)`  
  `lifecycle==deleted`
- `is_match_candidate(r)`  
  live + `state in {pause, complete}`；保持当前 matcher 契约
- `can_begin_delegate(r, activation)`  
  runnable + activation 属于该 transaction + 是 current + `activation.status==active`

`can_begin_delegate` 必须接收 `ActivationRecord`；仅凭 `TransactionRecord` 无法判断 activation 的真实状态。Registry 仍负责加载 bundle 和 CAS，domain command 仍是最终门禁。

以下概念不能偷用同一个谓词：

- active pointer eligibility
- matcher eligibility
- flush-blocking active segment
- execution runnable
- permanent terminal

例如 `complete` 对 scheduler 已关闭，但 Flush 前仍可进入 matcher；`pause` 不 runnable，却可能继续阻止对话段 Flush。

---

## 本次重构不顺带改变的产品语义

当前 sourceless USER_MESSAGE 调用 `_resolve_user(..., reuse_active=False)`，核心测试也要求新消息默认创建新 transaction；运行时文档与此存在冲突。

本方案默认：

- 保持当前实现和核心测试：普通 sourceless 新消息不自动并入 open continue transaction；
- `pause/complete` 仍通过 matcher 决定 restore 或 create；
- 显式 `transaction_id`、Feedback、schedule delivery 继续锁定原事务线；
- 删除 `_resolve_user` 中不可达的 status 判断，或改为领域谓词但不启用 `reuse_active=True`。

如需“新 user 消息自动复用 active transaction”，另开语义变更；必须先定义 waiting delegate、用户高优先级输入和并发工具执行策略。

---

## Phase 0 — 语义冻结与基线

本阶段不改变运行时行为。

1. 定稿并记录：
   - `termination_reason` 枚举及 API 表达；
   - 普通抢占允许发生的边界；
   - USER_TASK sourceless 归因维持现状；
   - API status 的废弃版本与删除版本。
2. 建立全仓引用清单：
   - `TransactionStatus`
   - `record.status`
   - `registry.transition`
   - `_project_legacy_status`
3. 增加 characterization tests：
   - legacy status 与 domain 字段一致及故意 skew 两组；
   - ThinkLife 与 LangGraph 非 USER_TASK 完成；
   - fail、force-stop、pause、delegate feedback；
   - Registry 冷重启 active index；
   - 非空 `/transactions` item schema。
4. 保存真实 SQLite v2 fixture，至少包含：
   - PENDING、RUNNING、WAITING、SUSPENDED；
   - status-only FAILED、CANCELLED、COMPLETED；
   - terminal status + active activation/pending delegate；
   - 已经正确写出 pause/complete/archive/deleted 的记录。

完成门槛：基线测试通过，且 fixture 能稳定复现旧 skew。

---

## Phase 1 / PR-A — Expand：先领域化所有 writer

Reader 暂时继续兼容 `status`，但新 writer 必须先写完整领域事实，并通过 `project_compat_status` 保持旧 reader 可工作。

### 1.1 失败与终止

新增原子领域命令：

```text
fail_transaction(record, activation, delegates, *, error, now)
terminate_transaction(record, activation, delegates, *, reason, now)
```

Registry façade：

```text
registry.fail(transaction_id, error, expected_revision=None)
registry.delete(transaction_id, reason=user_deleted, expected_revision=None)
```

要求：

- 同一个 Store UoW 内写 `termination_reason + last_error + lifecycle=deleted`；
- 同时失效 current activation 和所有 pending delegates；
- revision 只增加一次；
- active pointer 同步清除；
- 晚到 Feedback 永久 expected-discard；
- 明确定义重复 fail/delete、force-stop 与 failure 竞争时的幂等结果和 reason 优先级；
- 事件和 schedule lifecycle 回调至多发送一次。

替换 ThinkLife/LangGraph 的所有 `transition(FAILED)` 调用，错误文本必须作为命令参数传入，不再先后分两次写。

### 1.2 正常完成

- USER_TASK：按当前设计保持 open，直到明确 complete/flush；仅删除无意义的 RUNNING 归一化。
- 非 USER_TASK：ThinkLife `loop._complete_transaction_after_turn` 和 LangGraph `_complete_after_turn` 必须调用 `registry.complete()`。
- `registry.complete()` 负责关闭 activation、校验无 pending delegate、写 `state=complete`。
- Store Flush 只负责 eligible `complete → archive`，不得直接写兼容 status。

### 1.3 create 与 delegate

- `create()` 经过 `open_activation()` 后立即 runnable；
- 删除强制 `status=PENDING` 与开跑时 `PENDING→RUNNING`，二者必须在同一 PR 完成；
- `begin_delegate` 删除 legacy status gate，改用 `(record, activation)` 领域校验；
- compatibility field 如仍存在，只由 `project_compat_status` 写出。

### 1.4 force-stop

- ThinkLife 与 LangGraph 的 thread force-stop 都走 `registry.delete(..., reason=force_stopped)`；
- 删除 active activation/pending delegate 时保持单 UoW；
- scheduled transaction 还必须同步其 schedule run/delivery 生命周期，不能只改 transaction；
- in-flight loop 与 endpoint 重复终止同一 transaction 时必须幂等。

完成门槛：

- 生产 runtime 不再调用 `registry.transition(FAILED|CANCELLED|COMPLETED)`；
- 新产生的终态全部具有领域事实；
- legacy reader 和 API 仍可通过计算投影工作；
- 还不能切换 active index/reader。

---

## Phase 2 / PR-B — 修复抢占与 durable requeue

### 2.1 抢占边界

默认采用保守策略：

- think 阶段或 effect dispatch 前：允许普通优先级抢占；TX 不变，原刺激 durable requeue；
- `begin_delegate` / 外部 effect dispatch 后：普通优先级输入不再取消并自动重跑该 effect，先让当前 delegate 产出 Feedback；
- force-stop 是独立终止命令，可以取消执行并 tombstone transaction；
- 如果 capability 明确支持稳定 idempotency key，可另行允许 dispatch 后恢复，但必须有专门测试。

这样避免“已创建 pending delegate、执行被取消、Feedback 永远不来、重排刺激又不可 runnable”的死锁，也避免未知副作用被重复执行。

### 2.2 Store 原子 requeue

不得用普通 `admit_stimulus` 重排同一个 claimed stimulus。新增类似：

```text
requeue_claimed_stimulus(stimulus, claim_token, *, payload, priority)
```

要求：

- CAS 校验 claim token/epoch；
- 保留同一 `stimulus_id`、`transaction_id`、`accepted_seq`；
- 持久化新的 `_preempt_count` 与 `_checkpoint`；
- 清空 claimed fields 并回到 ready；
- 重启后仍可继续；
- 达到 max_preempt 后只终止一次，不形成无限 requeue。

完成门槛：真实 SQLite Inbox + 重启测试通过；不能只用内存 Inbox。

---

## Phase 3 / Deploy-Migrate — 审计与回填 legacy payload

Reader 切换前执行。迁移必须有稳定 migration ID、dry-run 报告和可重复执行语义。

### 3.1 迁移优先级

1. 已经具有正确 domain 事实的记录以 domain 为准；
2. 对 `active + continue` 的 status-only 记录使用 legacy status 修复；
3. 无法安全推断的记录进入 quarantine/report，不能静默变成 runnable。

### 3.2 回填矩阵

| legacy payload | 迁移动作 |
|---|---|
| `PENDING + active/continue` | 确认或补建 active activation；投影为 running |
| `RUNNING + active/continue` | 校验 active activation；保持 continue |
| `WAITING + active_delegate_id` | 校验 pending Delegate 与 current active Activation |
| `WAITING` 但无 pending delegate | 修复为 runnable；记录审计原因 |
| `SUSPENDED + state=pause` | 保留协作 pause |
| `SUSPENDED + state=continue` | 按旧抢占处理；校验 durable ready/claimed stimulus，否则报告 |
| `COMPLETED + active/continue` | 失效遗留 pending delegate，合法关闭 activation，写 state=complete |
| `FAILED + active/continue` | 原子写 deleted + reason=failed；缺失 error 时写 migration sentinel |
| `CANCELLED + active/continue` | 原子写 deleted + reason=legacy_cancelled |
| 已 `archive/deleted` | 保持 domain 事实，仅规范化兼容投影 |

### 3.3 Schema 与回滚

- `TransactionRecord.schema_version` 升级到 v3；
- v3 reader 能读 v2 payload；
- migration 使用专门 Store 路径更新 immutable schema version；
- 回滚窗口内继续序列化兼容 status，避免旧代码把无 status payload读成 PENDING；
- 在回滚窗口结束前不得执行内部字段删除。

### 3.4 切换前审计门槛

必须为零：

- `active + continue + legacy status in {failed,cancelled,completed}`
- deleted record 仍有 active activation/pending delegate
- complete/archive record 仍有 current activation/pending delegate
- active delegate 与 Delegate/Activation 子表不一致
- 无法解释且未 quarantine 的 SUSPENDED/WAITING

迁移后关闭并重新打开 Registry，重新构建 active index，再跑相同审计。

---

## Phase 4 / PR-C — Switch：读路径切到领域谓词

只有 Phase 3 审计通过后才能部署。

### Scheduler / LangGraph

- 开跑前使用 `is_runnable_record/is_waiting_delegate/is_paused`；
- paused 只能通过明确 restore 重入，不能用 `transition(RUNNING)` 伪恢复；
- waiting delegate 不得再次规划新 delegate；等待 Feedback 或执行已定义的显式控制命令；
- normal completion/failure/force-stop 已在 Phase 1 使用领域命令；
- 给 `registry.transition` 加 spy/guard，热路径一旦调用即测试失败。

### Registry / active pointer

- `_rebuild_active_index`、`get_active_user_transaction`、`_sync_terminal_active_pointer`、`complete_active_user_transaction` 全部改用上下文正确的领域谓词；
- 冷重启覆盖多条 open transaction、pause、complete、archive、deleted、waiting delegate；
- active pointer 仍是明确索引，不用“任意 open continue”替代指针归属。

### Attributor / Thinking

- `is_match_candidate` 继续只接受 live pause/complete；
- matcher view 删除 legacy status，使用 `state/lifecycle/task_state`；
- `layers/thinking/core.py` 的 TransactionRecord fallback 不再读取 `item.status`；
- 保持 sourceless USER_MESSAGE 不自动复用 active continue 的当前语义。

### Product/API 内部判断

- `product_views.py` 与 ThinkLife runtime 中的：
  - active transaction
  - active user segment
  - flush metrics
  - deleted/terminal 判断
  分别改用合适谓词；
- `TransactionRecord.can_accept_wm_write()` 去掉 status 依赖；
- API `"status"` 此时仍返回，但只调用 `project_compat_status`。

完成门槛：

- 对 record.status 注入任意错误值，控制流结果不变；
- runtime/layer 生产代码没有 `registry.transition(...)` 调用；
- 两个 runtime 的完整 acceptance 通过。

---

## Phase 5 / PR-D — Contract：删除内部字段与 FSM

部署 Phase 4 并度过明确的回滚观察窗口后执行。

1. 删除 `TransactionRecord.status` 的序列化与反序列化参数；
2. v3 reader 继续容忍旧 payload 中多余的 `"status"` 键；
3. 删除 `_project_legacy_status` 及 `transaction/schedule.py` 等所有调用；
4. 删除 `registry.transition`、`_ALLOWED_TRANSITIONS`；
5. 删除 `TransactionStatus` 与 `think_life.__init__` 公共导出；
6. Store Flush、schedule coordinator、smoke、acceptance seed 全部只构造领域状态；
7. API compatibility adapter 保留独立的字符串投影，不重新引入内部 FSM。

静态门槛：

```text
runtime/layer 中 TransactionStatus 引用数 == 0
runtime/layer 中 TransactionRecord.status 控制流/序列化引用数 == 0
registry.transition 调用数 == 0
_project_legacy_status 引用数 == 0
```

ActivationStatus、DelegateStatus、ScheduleRunStatus 等独立子实体状态不在删除范围内。

---

## Phase 6 / Release N+1 — 删除 API status

Phase 5 与本阶段必须跨越至少一个真实部署版本，而不是只分成两个未发布 PR。

- 文档明确 deprecated version、removal version；
- 非空 `/transactions` 合同测试覆盖兼容版本和移除版本；
- 客户端只使用：
  - `state`
  - `lifecycle_status`
  - `active_delegate_id`
  - `termination_reason`
  - `last_error`
- 确认内部 UI、外部调用方或版本化 endpoint 已迁移后，删除 `"status"`。

如果没有兼容承诺且决定同版本 breaking change，必须升级 API version，并从方案中删除“短兼容窗口”的表述。

---

## 主要改动范围

### Domain / Store

- `runtime/think_life/contracts.py`
- `runtime/think_life/transaction/domain.py`
- 新建 `runtime/think_life/transaction/predicates.py`
- `runtime/think_life/transaction/store.py`
- `runtime/think_life/transaction/schedule.py`
- `runtime/think_life/transaction_registry.py`
- `runtime/think_life/__init__.py`

### Runtime readers/writers

- `runtime/think_life/scheduler/loop.py`
- `runtime/think_life/perception/attributor.py`
- `runtime/think_life/runtime.py`
- `runtime/langgraph/inbox_loop.py`
- `runtime/langgraph/turn_graph.py`
- `runtime/langgraph/runtime.py`
- `runtime/host/product_views.py`
- `layers/thinking/core.py`

### Acceptance / tests / docs

- `acceptance/adapters/think_life_*.py`
- `acceptance/adapters/langgraph_*.py`
- `acceptance/catalog.py`
- `tests/runtime/test_think_life_*.py`
- `tests/runtime/test_langgraph_turn_loop.py`
- `tests/acceptance/invariants/test_runtime_semantics.py`
- Chat API 非空 transaction contract tests
- 两个 LangGraph smoke scripts
- `docs/runtime/think-life-runtime-spec.zh-CN.md`
- `docs/chat_api/README.md`

Acceptance seed 必须随对应 Phase 更新，不能全部推迟到最终删字段阶段；否则 reader 切换后，status-only seed 会构造出错误的领域状态。

---

## 每阶段验证门槛

统一使用 `MAG` conda 环境：

```powershell
conda run -n MAG python -m pytest -q -m p2_foundation
conda run -n MAG python -m pytest -q --maxfail=0 tests/runtime tests/acceptance
conda run -n MAG python -m m_agent.acceptance contract coverage
conda run -n MAG python -m m_agent.acceptance contract run --runtime think_life_v1 --all-layers
conda run -n MAG python -m m_agent.acceptance contract run --runtime langgraph_v1 --all-layers
conda run -n MAG python scripts/smoke_langgraph_runtime.py
conda run -n MAG python scripts/smoke_langgraph_turn_loop.py
```

重点新增测试：

1. predicates 全真值表与故意 skew；
2. v2 SQLite fixture → v3 migration → restart；
3. migration 重放、CAS revision、activation/delegate 清理；
4. ThinkLife/LangGraph 非 USER_TASK 真实 complete；
5. fail/force-stop 单 UoW、幂等与 late Feedback；
6. schedule transaction 终止时 run/delivery 一致；
7. persistent preempt/requeue、restart、max_preempt；
8. waiting delegate 收到高优先级 USER_MESSAGE；
9. active index 冷重启；
10. 非空 `/transactions` API 投影矩阵；
11. compatibility status 故意错误时控制流不变；
12. Phase 5 全仓零引用检查。

---

## 主要风险与防护

1. **reader 过早切换**  
   防护：writer-first + migration audit gate；PR-C 依赖部署完成，而不只依赖 PR-B 合并。
2. **legacy terminal 复活**  
   防护：v2 fixture、迁移矩阵、重启 active-index 审计。
3. **失败与删除无法区分**  
   防护：显式 `termination_reason`，`last_error` 仅作诊断。
4. **effect dispatch 后重复执行**  
   防护：普通抢占限制在 dispatch 前；dispatch 后除非 capability 有稳定幂等键，否则先完成 Feedback。
5. **durable requeue 丢 checkpoint/count**  
   防护：claim-token CAS 的专用 requeue Store API 与重启测试。
6. **Attributor 顺带改变产品语义**  
   防护：本重构保持 `reuse_active=False`；另开语义变更。
7. **API 兼容窗口名存实亡**  
   防护：Phase 5/6 跨真实部署版本，或明确升级 API version。
8. **回滚旧代码把无 status 读成 PENDING**  
   防护：观察窗口内继续写兼容字段；窗口结束后才执行内部 contract。

---

## Done Definition

只有同时满足以下条件才算完成：

- 所有终态、暂停、delegate waiting 都能由领域字段唯一解释；
- runtime 控制流不读取兼容 status；
- 历史 payload 已迁移或隔离，不会在重启后复活；
- fail/force-stop/delete 能原子清理 activation/delegate；
- 非 USER_TASK 通过真实 domain complete 关闭；
- durable preempt 在 SQLite + restart 下不丢刺激、不无限重排、不重复 effect；
- API status 已按版本承诺完成废弃；
- `TransactionStatus / registry.transition / _project_legacy_status / TransactionRecord.status` 在内部运行时代码中零引用；
- ThinkLife、LangGraph、foundation、acceptance、smoke 全部通过。

不接受以下中间态：

- reader 已切 domain，但 writer 仍制造 status-only 终态；
- 只删 `_project_legacy_status`，保留 `transition` FSM；
- 忽略旧 payload status，却不迁移 FAILED/CANCELLED/COMPLETED；
- 把 `last_error` 当作 failed/cancelled 的唯一类型标记；
- 只用内存 Inbox 证明 durable preempt 正确。
