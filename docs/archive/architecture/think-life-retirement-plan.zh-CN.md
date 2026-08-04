# ThinkLife 完全退役与删除实施计划

> 状态快照：2026-08-02
> 文档状态：代码退役、协议迁移、客户端改造、测试与数据工具已完成；真实数据批次、客户端发布和观察期尚待单独授权
> 范围：后端 Runtime、持久化数据、Chat API、Acceptance、配置、文档以及 M-Agent-UI / M-Agent-Desktop 客户端。

执行说明：本轮已完成 PR 1～PR 5 的仓库实现、`TL-UI-01`～`TL-UI-06`、`TL-DESKTOP-01`～`TL-DESKTOP-02`，并实现了受控备份、审计、Scene 合并和事务决策 dry-run 工具。依照本文 2.1 与数据批次约束，本轮没有停止服务、备份/移动/改写真实 `data/`，没有替用户决定 3 个活动事务，也没有执行 `TL-CLIENT-03` 发布或 7～30 天观察期；这些仍是独立的生产操作批次，不能由代码任务隐式触发。

本文定义如何在不丢失事务、Scene、Schedule、Effect、Dialogue/RAG 和故障恢复能力的前提下，把生产系统从双 Runtime 过渡到只保留 `langgraph_v1`，并最终删除所有活动的 ThinkLife 代码、配置、协议和在线数据。

本计划中的“彻底删除”不是简单删除 `src/m_agent/runtime/think_life/`，而是同时满足：

1. 生产进程只构造和运行 LangGraph Runtime；
2. LangGraph 不再从 ThinkLife 命名空间导入共享实现；
3. 不再接受 `think_life_v1` 配置或回退开关；
4. 活动 API、客户端类型和配置中不再暴露 ThinkLife 名称；
5. 在线持久化目录中不再存在可由 ThinkLife 恢复的工作；
6. 删除双引擎过渡层后，Flush、Dialogue/RAG Outbox 和重启恢复能力不退化；
7. 离线备份只能用于灾难取证，不会被生产进程自动加载。

---

## 1. 当前事实

### 1.1 代码盘点

`src/m_agent/runtime/think_life/` 当前共有 28 个 Python 文件，约 13,614 行：

| 分类 | 规模 | 说明 |
| --- | ---: | --- |
| ThinkLife 专属执行引擎 | 约 2,076 行 | `runtime.py`、`scheduler/loop.py` 及 adapter 入口 |
| 已被 LangGraph 复用的共享内核 | 约 11,538 行 | contracts、transaction、perception、turn support、drainer 等 |

因此不能直接删除整个目录。必须先把约 85% 的共享代码迁到中性命名空间，再删除旧执行环。

包外仍有 54 个文件直接导入 `m_agent.runtime.think_life`：生产源码 25 个、测试 27 个、脚本 2 个。除此之外，还有 43 个后端测试文件、14 个配置文件、4 个脚本和 19 个文档包含 ThinkLife 名称或协议。

### 1.2 旧引擎专属代码

下列实现属于最终应删除的旧执行路径：

- `src/m_agent/runtime/think_life/runtime.py`
- `src/m_agent/runtime/think_life/scheduler/loop.py`
- `src/m_agent/runtime/host/think_life_adapter.py`
- `ThinkLifeRuntime` / `ThinkLifeRuntimeHost` / `ThinkLifeLoop` 导出
- `runtime/host/factory.py` 中的 ThinkLife 构造分支
- ThinkLife acceptance adapter、harness 和双 Runtime 对照 smoke

### 1.3 当前不能删除的共享代码

LangGraph 当前直接复用下列 ThinkLife 命名空间内容：

- `config.py`、`contracts.py`
- `transaction/**`、`transaction_registry.py`
- `perception/**`
- `drainer.py`
- `scheduler/awaiting_user_pause.py`
- `scheduler/cpu_state.py`
- `scheduler/delegate.py`
- `scheduler/execution_feedback.py`
- `scheduler/schedule_lifecycle.py`
- `scheduler/think_context.py`
- `scheduler/tool_runner.py`

`scheduler/loop.py` 是这一组中唯一应直接删除而不是迁出的主要实现。

### 1.4 持久化数据盘点

截至状态快照，实际持久化根目录中有 5 个 `think_life.sqlite3`，只有 `changshengeva` 的数据库非空：

| 项目 | 当前值 |
| --- | ---: |
| ThinkLife transaction | 4 |
| `continue + active activation` | 3 |
| `complete` | 1 |
| 非终态 Stimulus / Delegate / Effect / Schedule run | 0 |
| 待投递 Feedback / Flush Outbox | 0 |
| Composite Flush blocker | 0 |
| ThinkLife Scene entry | 25 |
| 只存在于旧库的 Scene entry | 19 |
| 与 LangGraph 重复且 digest 一致的 Scene entry | 6 |
| 外部 Schedule 对这 4 个事务的 origin 引用 | 0 |

25 条旧 Scene 都满足 `flush_watermark == scene_seq`，但其中 19 条仍然是旧库独有数据。直接删除旧数据库会导致 Scene API 和审计时间线缺失，因此必须在物理删除前迁移或形成明确的只读归档。

旧 transaction payload 的 schema version 为 2，而当前 Store 打开数据库时会自动升级到 version 4。原始 SQLite 备份必须发生在任何兼容代码重新打开旧库之前。

### 1.5 过渡层仍承担生产可靠性

`CompositeRuntimeHost` 不只负责双引擎路由，还承担：

- 双引擎 Scene 合并；
- per-engine 不可变 Flush snapshot；
- durable Flush Saga；
- Dialogue/RAG materialization Outbox；
- 进程重启后恢复未完成物化；
- 幂等提交和跨引擎完成门。

删除 Composite 前，必须把这些与“多引擎路由”无关的能力迁到单 LangGraph 也会使用的中性 Flush Orchestrator。

### 1.6 客户端范围

后端之外，还有两个独立工作树使用 ThinkLife API 或类型名称：

- `M-Agent-UI`：类型、API service、Thread/Transaction UI、测试和构建产物；
- `M-Agent-Desktop`：API 类型、thread context/reply、UI、测试、README 和构建产物。

后端不能在客户端发布前直接删除旧协议字段。

---

## 2. 设计原则与非目标

### 2.1 必须遵守的原则

1. **共享语义先中性化，旧执行环最后删除。**
2. **数据退役和代码重构分开提交。** 任何活动事务归档或数据库移动都需要单独授权和独立 manifest。
3. **不自动把旧事务标记为完成。** 旧任务只能自然完成、由用户确认归档，或在受控边界迁移。
4. **不伪造 LangGraph checkpoint。** 已消费的 delegate/effect 不得在新图中重放。
5. **先保全 Scene、Dialogue/RAG 和审计历史，再删除数据库。**
6. **旧配置必须 fail-fast。** 完成切换后，发现 `think_life_v1` 时不能静默改用 LangGraph。
7. **不可逆删除永远是最后一步。** 删除代码可通过版本回滚恢复；删除隔离备份才是真正不可逆点。

### 2.2 本计划不做的事情

- 不实现任意状态下的通用 ThinkLife → LangGraph 字节级事务转换器；
- 不在运行中的 transaction 中途改写 engine 归属；
- 不删除共享 Scene JSONL、Dialogue、RAG 或 Schedule 产品数据；
- 不让两个同时具备 Schedule heartbeat/drainer 权限的版本并行消费；
- 不用恢复旧 SQLite 快照的方式覆盖切换后产生的新 LangGraph 数据。

---

## 3. 目标架构

### 3.1 目标代码布局

```text
src/m_agent/runtime/
  config.py
  domain/
    contracts.py
  transaction/
    domain.py
    effects.py
    flush.py
    predicates.py
    schedule.py
    store.py
    uow.py
    registry.py
  perception/
    attributor.py
    gateway.py
    inbox.py
    matcher_scene_view.py
  turn_support/
    awaiting_user_pause.py
    delegate.py
    execution_feedback.py
    think_context.py
    tool_runner.py
  dispatch/
    cpu_state.py
    drainer.py
    schedule_lifecycle.py
  host/
    protocol.py
    factory.py
    flush_orchestrator.py
    flush_journal.py
  langgraph/
    ...
```

最终不存在 `src/m_agent/runtime/think_life/`。

### 3.2 目标运行路径

```text
Chat API
  → LangGraph RuntimeHost
      → Runtime transaction/perception/dispatch 内核
      → LangGraph transaction + turn graph
      → Runtime Flush Orchestrator
          → SQLite transaction/Scene commit
          → Dialogue/RAG materialization Outbox
```

不存在 engine selector、ThinkLife adapter 或 Composite transaction router。

---

## 4. 分批任务

计划拆成 5 个后端 PR、2 个客户端 PR 和一个独立数据操作批次。桥接版本、LangGraph-only 版本和 Final 版本必须能够分别部署和验证。

### PR 1：共享内核迁出

目标：只移动和重命名共享实现，不改变运行行为，双引擎仍可运行。

- [ ] `TL-R1-01` 将 `contracts.py` 移到 `runtime/domain/contracts.py`。
- [ ] `TL-R1-02` 将 `ThinkLifeConfig` / `ThinkLifeSchedulerConfig` 改为 `RuntimeConfig` / `SchedulerConfig`。
- [ ] `TL-R1-03` 将整个 `transaction/**` 和 registry 迁到 `runtime/transaction/`。
- [ ] `TL-R1-04` 将 `perception/**` 迁到 `runtime/perception/`。
- [ ] `TL-R1-05` 将 turn helper 和 dispatch helper 迁到中性目录。
- [ ] `TL-R1-06` 更新 LangGraph、Host、API、Scene、Tools 和 Acceptance 的 Python import。
- [ ] `TL-R1-07` 更新 11 个 capability YAML 中的 `feedback_projector` 动态导入路径。
- [ ] `TL-R1-08` 将 `runtime.think_life` 有效参数迁到 `runtime.common` 或 `runtime.langgraph`。
- [ ] `TL-R1-09` 为已有用户配置增加 schema migration，移除旧 `runtime.think_life/default_engine`。
- [ ] `TL-R1-10` 暂留有明确删除期限的旧 import re-export shim。
- [ ] `TL-R1-11` 增加 CI 规则：除旧 Runtime/Loop/shim 外，生产代码不得再 import `runtime.think_life`。

退出条件：LangGraph 生产路径不再直接依赖 ThinkLife 命名空间；双引擎门禁仍全绿。

### PR 2：单引擎 RuntimeHost 与可靠 Flush

目标：在删除 Composite 前，把生产可靠性能力迁到单 LangGraph Host。

- [ ] `TL-R2-01` 扩展 `RuntimeHost`，正式纳入 Scene、Schedule、Flush、conversation sequence 和 transaction projection。
- [ ] `TL-R2-02` 将 `CompositeFlushJournal` 泛化为单引擎可用的 `FlushJournal`。
- [ ] `TL-R2-03` 新增 `RuntimeFlushOrchestrator`，保留 snapshot → runtime commit → materialization → complete 顺序。
- [ ] `TL-R2-04` 让 Dialogue/RAG payload 在 runtime commit 前持久 staging。
- [ ] `TL-R2-05` 保留稳定 flush/dialogue ID、幂等冲突检查和重启恢复。
- [ ] `TL-R2-06` 删除 Chat API 中对 `isinstance(CompositeRuntimeHost)` 的可靠性依赖。
- [ ] `TL-R2-07` 增加单引擎故障注入：commit 前、commit 后/物化前、物化后/complete 前、进程重启。
- [ ] `TL-R2-08` 证明删除 Composite 不会使 Scene、Dialogue 或 RAG 重复/丢失。

退出条件：只构造 LangGraph Host 时，Flush、Dialogue/RAG Outbox 和重启恢复与当前 Composite 等价。

### PR 3：协议中性化与 Acceptance 去耦

目标：让产品协议和验收平台不再以 ThinkLife 命名或实现为基础。

- [ ] `TL-R3-01` `think_life_hooks` → `runtime_hooks`，`controller_state["think_life"]` → `controller_state["runtime"]`。
- [ ] `TL-R3-02` `_think_life_pending_users` → `_pending_user_turns`。
- [ ] `TL-R3-03` `get_think_life_transactions()` → `get_transactions()`。
- [ ] `TL-R3-04` `think_life_flush` / `think_life_segment` → `runtime_flush` / `runtime_segment`。
- [ ] `TL-R3-05` `ChatServiceRuntime.think_life` → `runtime_host`。
- [ ] `TL-R3-06` `_THINK_LIFE_PLANNING_EVENTS` → `_RUNTIME_PLANNING_EVENTS`。
- [ ] `TL-R3-07` `schedule_heartbeat.py` 只依赖 RuntimeHost，不再调用 `runtime.think_life`。
- [ ] `TL-R3-08` 将 acceptance 的 TX/SP/AT runner 和 harness 提取为 shared 实现。
- [ ] `TL-R3-09` LangGraph adapter 不再继承或导入 ThinkLife adapter 文件。
- [ ] `TL-R3-10` 保留 LangGraph 38 个场景，删除双 Runtime 选择和 ThinkLife binding。
- [ ] `TL-R3-11` 更新 CLI、Acceptance Web UI、catalog 硬编码 nodeid 和测试 fixture。
- [ ] `TL-R3-12` 清理 prompt、日志、注释中的当前 ThinkLife 品牌描述。

若存在外部客户端依赖，Bridge 版本可以同时输出新旧字段一个发布周期；两个客户端完成迁移后，Final 版本必须删除旧字段。

### 数据批次：旧事务和历史退场

目标：在不自动完成任务、不重放副作用的前提下，使旧数据库不再包含可恢复工作。

- [ ] `TL-DATA-01` 禁止创建新的 ThinkLife transaction，旧 Host 进入 drain-only。
- [ ] `TL-DATA-02` 停服，并在任何 schema upgrade 前使用 SQLite Backup API 备份所有运行库。
- [ ] `TL-DATA-03` 备份 LangGraph checkpoint、Flush journal、Scene 和 Schedule 产品库。
- [ ] `TL-DATA-04` 生成包含路径、大小、row count、schema version、SHA256 的 manifest。
- [ ] `TL-DATA-05` 对已 complete transaction 执行 action-only flush 并归档。
- [ ] `TL-DATA-06` 逐条处理 3 个 active transaction，禁止批量自动 complete。
- [ ] `TL-DATA-07` 若用户选择自然完成，保持旧引擎仅消费该 transaction，直到进入终态。
- [ ] `TL-DATA-08` 若用户选择弃置，使用明确原因 `legacy_engine_retirement` 归档/删除并保存审计记录。
- [ ] `TL-DATA-09` 若任务必须保留，作废旧 activation，只迁移 task state/WM，在 LangGraph 新开 restore activation；不迁移 checkpoint，不重放 consumed delegate/effect。
- [ ] `TL-DATA-10` 按 `append_id + payload digest` 合并 19 条旧库独有 Scene，并保持其 flushed 语义。
- [ ] `TL-DATA-11` 重复条目的 digest 不一致时立即停止迁移并人工处理。
- [ ] `TL-DATA-12` 扫描外部 Schedule origin；不得留下指向旧 transaction 的 active schedule。
- [ ] `TL-DATA-13` 扩展 cutover audit，覆盖 Scene 完整性、外部 Schedule、可恢复 archive 和新 legacy 写入。
- [ ] `TL-DATA-14` 在跨重启的多次审计中确认所有 legacy/composite blocker 为 0。

该批次是用户数据操作，必须单独授权，不能隐藏在代码 PR 或测试命令中执行。

### PR 4：切换 LangGraph-only 并删除旧执行环

目标：生产构造路径只剩 LangGraph。

- [ ] `TL-R4-01` `ChatServiceRuntime` 直接创建 LangGraph Host，移除 `multiplex=True`。
- [ ] `TL-R4-02` `create_runtime_host()` 只构造 LangGraph。
- [ ] `TL-R4-03` 删除 `ThinkLifeRuntime`、`ThinkLifeLoop`、`ThinkLifeRuntimeHost`。
- [ ] `TL-R4-04` 删除 `host/think_life_adapter.py`。
- [ ] `TL-R4-05` 删除 Composite 的 transaction routing 和双 Scene 聚合；保留已中性化的 Flush Orchestrator。
- [ ] `TL-R4-06` 删除 `think_life_v1` engine 选项和旧 fallback。
- [ ] `TL-R4-07` 发现旧环境变量或配置时 fail-fast，并给出迁移提示。
- [ ] `TL-R4-08` 删除 LangGraph smoke 中的 ThinkLife 对照 sample，保留 LangGraph 领域断言。
- [ ] `TL-R4-09` 删除 ThinkLife acceptance adapter/harness。
- [ ] `TL-R4-10` 删除只验证旧 Loop 私有行为的测试；共享语义测试迁移/改名而不是丢弃。

退出条件：生产启动和测试均不会导入、构造或写入 ThinkLife Runtime。

### PR 5：最终代码、配置和文档清理

目标：活动后端仓库中不存在 ThinkLife 命名空间或兼容协议。

- [ ] `TL-R5-01` 删除整个 `src/m_agent/runtime/think_life/`。
- [ ] `TL-R5-02` 删除所有 import shim、旧 API 字段和兼容 property。
- [ ] `TL-R5-03` 删除 `runtime.default_engine` 和旧 rollback 文档。
- [ ] `TL-R5-04` 将 LangGraph 依赖从 optional extra 提升为基础依赖。
- [ ] `TL-R5-05` 重新锁定依赖并完成 clean-install gate。
- [ ] `TL-R5-06` 重新生成或移除跟踪的 `m_agent.egg-info/PKG-INFO`、`SOURCES.txt`。
- [ ] `TL-R5-07` 重写 README、Runtime、Chat API、systems-plugin 和部署文档。
- [ ] `TL-R5-08` 将 ThinkLife 当前规格和差距表移入明确的历史归档或从活动文档索引删除。
- [ ] `TL-R5-09` 增加生产源码/config/scripts/tests 的零引用 CI 门禁；历史 ADR/退役文档使用显式 allowlist。
- [ ] `TL-R5-10` 构建 wheel 并验证其中没有 `runtime/think_life` 或陈旧元数据。

### 客户端 PR：M-Agent-UI 与 M-Agent-Desktop

- [ ] `TL-UI-01` 将 `ThinkLifeTransaction*` 改为 `RuntimeTransaction*`。
- [ ] `TL-UI-02` 将 `ThreadThinkLifeState` 改为 `ThreadRuntimeState`。
- [ ] `TL-UI-03` 将 `isThinkLifeThread` 改为 `isProductRuntimeThread` 或删除不必要判断。
- [ ] `TL-UI-04` 消费 `runtime_flush`、`get_transactions` 和中性 health/thread state。
- [ ] `TL-UI-05` 更新 UI 测试、lint、类型检查和 build。
- [ ] `TL-UI-06` 重新生成构建产物，禁止手工修改 dist bundle。
- [ ] `TL-DESKTOP-01` 完成 Desktop API 类型、thread context/reply 和组件改名。
- [ ] `TL-DESKTOP-02` 更新 Desktop 测试、README、架构文档和 build。
- [ ] `TL-CLIENT-03` 客户端发布并确认不再读取旧协议后，允许后端 Final 删除兼容字段。

---

## 5. 数据处置细则

### 5.1 T0 备份顺序

维护窗口必须按以下顺序执行：

1. 停止 Chat API、drainer 和 Schedule heartbeat；
2. 确认不存在运行中的进程或 SQLite writer；
3. 使用 SQLite Backup API 生成一致性备份，不直接复制活动 WAL 文件；
4. 备份 ThinkLife/ LangGraph Runtime DB、LangGraph checkpoints、Flush journal、Scene 和 Schedule；
5. 对备份执行 `PRAGMA integrity_check`；
6. 生成 manifest 和 SHA256；
7. 只有 manifest 校验通过后，才允许启动 drain/migration 代码。

### 5.2 三个活动事务的决策

每个事务必须有独立决策记录：

| 选择 | 处理 | 约束 |
| --- | --- | --- |
| 自然完成 | Bridge 版本继续用 ThinkLife 承接该事务 | drain-only，不得新建 ThinkLife 事务 |
| 用户弃置 | 在维护锁下归档/删除 | 记录 revision、原因、操作者和 idempotency key |
| 保留任务 | 迁移 task state/WM，由 LangGraph 新开 activation | 作废旧 activation；不重放 delegate/effect；必须测试首次 resume |

默认不选择“保留任务迁移”，除非任务确实需要继续。当前只有 3 条且没有 pending 副作用时，自然完成或确认归档的风险更低。

### 5.3 Scene 合并规则

1. `append_id` 是稳定去重键；
2. 相同 `append_id` 必须具有相同 canonical payload digest；
3. digest 冲突必须 fail-fast，禁止覆盖；
4. 旧库独有 entry 保留 `occurred_at`、transaction/delegate/tool correlation；
5. 目标库可以重新分配本地 `seq`，但需记录 source engine/seq 供审计；
6. 已 flush 的旧 entry 迁入后不能重新进入 Dialogue/RAG materialization；
7. 合并后分别比较 entry count、append-id set、digest set 和 dialogue/RAG replay count。

### 5.4 在线库隔离与最终删除

旧数据库不能从在线目录直接永久删除：

1. 审计门通过后停服；
2. 将 `think_life.sqlite3` 移入生产进程不可扫描的只读隔离目录；
3. 写入 retirement manifest/tombstone；
4. 部署 LangGraph-only 版本并冷启动；
5. 保留 Bridge 镜像和隔离备份一个约定观察周期，建议 30 天；
6. 观察期无回滚需求后，再单独审批不可逆删除。

Final 版本若在在线持久化根发现未登记的 `think_life.sqlite3`，应 fail-fast，而不是忽略可能仍有工作的数据库。

---

## 6. 发布与回滚

### 6.1 三个可部署版本

| 版本 | 作用 | 回滚能力 |
| --- | --- | --- |
| Bridge | 共享内核中性化；保留旧 Loop、双 Host 和 import/API shim | 可继续恢复旧事务 |
| LangGraph-only | 删除旧执行路径；暂留必要协议别名 | 可回到 Bridge 代码，但继续使用 LangGraph 数据 |
| Final | 删除 shim、旧字段、旧配置和在线旧数据库 | 只允许回到仍以 LangGraph 为主的上一版本 |

### 6.2 回滚规则

1. LangGraph-only 产生新写入后，不允许恢复 T0 数据库快照覆盖在线数据；
2. 代码回滚到 Bridge 时，默认 engine 仍应保持 LangGraph；
3. Bridge 的旧 Host 只能为明确的 legacy transaction 服务，不能重新开放新事务创建；
4. 回滚实例不得与新实例同时消费 Schedule heartbeat 或 drainer；
5. 数据隔离后需要回滚时，先停服，再恢复 Bridge 镜像和对应隔离库；
6. 隔离备份被不可逆删除后，不再承诺 ThinkLife 恢复能力。

### 6.3 观察周期

建议：

- Bridge 全量切到新中性内核后观察至少一个发布周期；
- LangGraph-only 真实流量观察 7～14 天；
- cutover audit 至少跨进程重启多次通过；
- 在线旧库隔离后建议保留 30 天再物理删除。

---

## 7. 验收矩阵

| 门禁 | 完成条件 |
| --- | --- |
| 静态依赖 | LangGraph、Host、API、Tools 无 `m_agent.runtime.think_life` import |
| 目录 | `src/m_agent/runtime/think_life` 不存在 |
| Engine | 只存在 `langgraph_v1`；旧配置明确启动失败 |
| Runtime | turn、checkpoint、restart、effect、schedule、flush 全绿 |
| Acceptance | LangGraph 38/38，无 ThinkLife 选择项和 mandatory xfail |
| Flush | 四个故障窗口和重启恢复通过，Dialogue/RAG 不丢失、不重复 |
| API | health、thread state、Stimulus、Transaction CRUD、SSE、Schedule 只使用中性字段 |
| 数据 | legacy transaction/stimulus/effect/schedule/outbox/composite blocker 全为 0 |
| Scene | append-id/digest 集合完整；19 条旧库独有记录已处理 |
| Schedule | 外部 active schedule 不引用 legacy transaction |
| 包安装 | 全新环境安装基础包即可启动 LangGraph，无需 optional extra |
| 全量测试 | 后端 pytest 0 failure；当前基线为 495 passed |
| 客户端 | UI test/lint/build 与 Desktop test/check/build 全绿 |
| 构建物 | Wheel 和两个前端 dist 中无活动 ThinkLife 模块/协议 |
| 冷启动 | 隔离旧数据库后可冷启动、重启并恢复 checkpoint |
| 负向门禁 | 在线目录重新出现未登记 legacy DB 时启动失败 |

建议增加以下仓库门禁：

```powershell
rg -n -i "think[_ -]?life" src config scripts tests
```

Final 阶段该命令应无活动引用；历史 ADR、退役文档和离线备份必须位于明确 allowlist，不能被生产打包或加载。

---

## 8. 完成定义

只有同时满足下列条件，ThinkLife 才算彻底删除：

- [ ] `legacy_retirement_ready=true`，扩展审计中的 Scene/Schedule/Archive 门也通过；
- [ ] 三个活动旧事务都有经确认的终态或受控迁移记录；
- [ ] 旧库独有 Scene 已合并或进入明确、可验证的只读归档；
- [ ] 单 LangGraph Host 保留原有 Flush/Dialogue/RAG 重启恢复能力；
- [ ] 生产代码不再构造 ThinkLife 或 Composite transaction router；
- [ ] `runtime/think_life`、旧 engine 选项和旧 fallback 已删除；
- [ ] 后端、两个客户端和构建产物不再依赖旧协议；
- [ ] 在线持久化目录不存在可被 ThinkLife 恢复的数据；
- [ ] Bridge 镜像、隔离备份和 manifest 已按保留策略处置；
- [ ] 后端、客户端、部署和负向配置门禁全部通过。

预计工作量为 5 个后端 PR、2 个客户端 PR、一个单独授权的数据批次，约 6～10 个工程日；生产观察和隔离保留周期不计入工程日。

---

## 9. 第一实施批次

开始执行时，应先完成 **PR 1：共享内核迁出**。该批次只做代码搬迁、命名中性化和配置 schema 兼容，不触碰真实事务或数据库，不删除旧执行环。PR 1 全量门禁通过后，再进入单引擎 Flush Orchestrator 改造。
