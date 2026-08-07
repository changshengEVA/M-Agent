# M-Agent v0.2.0—v1.0.0 版本规划

> 文档状态：当前权威路线图  
> 基线版本：v0.2.0  
> 确认日期：2026-08-06  
> 修订说明：自 v0.4.0 起重排；v0.2.0—v0.3.x 历史交付保持不变  
> 发布原则：按放行条件发布，不以日期替代质量门槛

## 1. 总体方向

**愿景：**让 Agent 的认知不再以用户消息为边界，而由世界变化、行动结果、时间条件和内部预期持续驱动。

**技术定位：**M-Agent 是一个 **Stimulus-Native Cognitive Runtime**，核心机制是 **Stimulus-to-Cognition Compilation**。

**产品价值：**

> **说一次，持续接得上；需要你时才出现。**

**1.0 前阶段目标：**先建成初步完整的认知运行时（短时 WM 已具备，补齐长时记忆并与主系统稳定拼接），再正式开源。开源后随社区累积刺激种类与内容，再引入去重、噪声过滤等 Attention 能力。

开发主线固定为：

```text
可信运行时基线
→ Stimulus Kernel
→ 情景记忆子系统（自洽）
→ 经验子系统（自洽）
→ 长时记忆接入主链与主系统稳定
→ Cognitive State 与 Context Compiler
→ 时间、内生刺激与轻量 Strategy
→ 受控自主性与发布硬化
→ 稳定契约（v1.0 正式开源）
→ [开源后] Attention 与开放事件流归因
```

## 2. 版本总览

| 版本号 | 主题 | 目标 | 核心交付 |
| --- | --- | --- | --- |
| **v0.2.0** | 当前运行时基线 | 固化现有真实能力与限制 | Transaction、Scene、Stimulus Pool、基础归因、Schedule、Effect/Feedback、工具式 RAG、语义验收 |
| **v0.2.1** | 可信基线 | 清除会污染后续认知系统的正确性和开源交付问题 | Single-call Thinking、恢复与 Context 修复、安全默认值、版本统一、CI 与最小安装路径 |
| **v0.3.0** | Stimulus Kernel | 将刺激提升为公开、耐久的一等运行时对象 | `runtime.ingest()`、Observation/Stimulus 公共协议、进程式池状态、确定性处置、Trace、Source Adapter、Stimulus Lab |
| **v0.3.1** | Chat Source Adapter | 产品 Chat 经显式 Adapter 统一走 `ingest` | `ChatSourceAdapter`、Chat Signal→Observation、幂等键、`submit_user_message` 兼容薄封装 |
| **v0.4.0** | 情景记忆子系统 | 建成自洽的情景记忆能力闭环 | Flush 录入、系统浅召回（高限制防污染）、工具深召回（高准确） |
| **v0.5.0** | 经验子系统 | 建成自洽的经验能力闭环 | Flush 录入、仅系统召回；与情景记忆解耦 |
| **v0.6.0** | 长时记忆接入与主系统稳定 | 两子系统拼进主链，主系统可长期稳定运行 | Flush 只触发不内嵌算法、决策前评估注入、隔离/导出/删除、故障降级 |
| **v0.7.0** | Cognitive State & Context Compiler | 从拼接 Prompt 升级为编译认知上下文 | Goal/Commitment/Expectation/Evidence/Belief、Context Snapshot、WM+浅情景+经验的编译与预算 |
| **v0.8.0** | Temporal, Endogenous & Strategy | 让时间与内部状态驱动继续，并受限启用经验指导 | Clock/Expectation、内生刺激与冷却配额、经验→受限 Guidance（Opt-in）、TaskState Guard |
| **v0.9.0** | Bounded Autonomy & Release Candidate | 有边界助手体验 + 契约冻结与长期稳定性 | 权限/预算/审批/Kill Switch、迁移备份、故障注入、长时间运行、安全审计 |
| **v1.0.0** | 初步完整认知运行时 | 正式开源并稳定兑现公共承诺 | Stimulus Kernel、conv 级事务归因、WM、情景/经验记忆、Context 编译、基础自主边界 |
| **v1.x+** | Attention & Attribution（生态期） | 刺激种类变多后的过滤与开放归因 | `ignored`、去重/噪声、开放事件流归因、StimulusBench |

## 3. 分版本规划

### v0.2.0：当前运行时基线

**主题**

从单次 Agent Run 迈向持久运行时。

**目标**

确认当前实现的真实能力和边界，为后续版本提供不可回退的事实基线。

**核心交付**

- Transaction、TaskState 与 Scene 时间线；
- 持久 Stimulus Pool 和基本事务归因；
- 用户消息、计划事件、执行反馈和观察类型契约；
- Heartbeat 与一次性计划；
- Thinking、Delegate、Effect、Feedback 执行闭环；
- 工作记忆和工具式简单 RAG；
- Runtime 语义验收平台。

**放行条件**

v0.2.0 只作为开发基线，不宣传为已经完成的认知运行时或生产稳定版。

### v0.2.1：可信基线

**状态：** 已于 2026-08-05 完成实现与放行验证。

**主题**

先保证现有闭环可信。

**目标**

修复会污染后续 Attention、Memory 和 Strategy 的底层问题，不引入新的大型认知概念。

**核心交付**

- 统一包版本、API 展示版本和文档版本；
- 完成 Single-call Thinking 主链与 TaskState 基础保护；
- 修复运行时记忆写入与 Flush 生命周期；
- 修复简单向量表示的跨进程稳定性、错误命中和浅/深召回同义问题；
- 消除 Context 中的当前刺激重复，保证最新证据不会被截断；
- 修复 Schedule 领取后、进入 Runtime 前崩溃导致的任务滞留；
- 明确工具副作用等级，默认关闭发送和外部写入能力；
- 建立 Windows/Linux CI、最小安装、环境变量示例和离线测试路径；
- 补齐开源治理、变更记录、安全策略和贡献说明。

**放行条件**

- 当前语义验收场景全部通过；
- 核心测试套件稳定通过，不依赖超时规避；
- 在刺激接纳、模型调用、Effect 和 Feedback 边界故障后能够恢复；
- 默认配置不会产生未经授权的外部写操作；
- 干净环境能够完成安装、测试和最小示例运行。

### v0.3.0：Stimulus Kernel

**状态：** 已于 2026-08-06 完成实现与放行验证（Public Alpha）。

**主题**

将刺激提升为公开、耐久的一等运行时对象。

**目标**

让外部开发者能够按文档接入 Source Adapter，并知道每个被接纳的刺激最终发生了什么。

**范围边界**

- Public Alpha 面向**外部开发者接 Source Adapter**，不是完整认知插件 SDK；
- 公共稳定契约只包含 `Observation` 与 `Stimulus`；`Signal` 留在 Adapter 内部，不进稳定公共契约；
- **刺激池状态**与**处置结果**拆成两套词表：前者管调度与恢复，后者管结案解释与审计；
- v0.3 只做确定性内核结案；“该不该理”的 `ignored` 语义不在 1.0 前主线，留给 **v1.x+ Attention**（刺激生态成熟后）；
- **v0.3.0** 不要求 Chat 经 Source Adapter 接入；Chat 可在 **v0.3.x** 迁入；
- **离开 v0.3 线之前**，Chat 与内部入口必须统一走 `runtime.ingest(observation)`，不允许长期旁路。

**核心交付**

- 公开 `runtime.ingest(observation)`；
- 定义 Observation、Stimulus 公共契约，并在文档中说明 Adapter 内部可保留原始 Signal；
- 支持来源、事件时间、接收时间、身份、因果来源、去重键和可信度；
- 定义进程式**刺激池状态**（如 `new / ready / running / waiting / terminated`），调度与恢复只依赖池状态及少量控制位（如 `terminal` / `retryable` / `reenterable`）；
- 定义确定性**处置结果**：`rejected / merged / discarded / completed / aborted / failed`，并附带 `reason_code` / `reason`；
  - `deferred` 归入池状态 `waiting`，不是处置词；
  - `activated` 表示进入 `running` 的过程/事件，不是结案处置；
  - `ignored` 不进入 v0.3 公共处置枚举；
- 提供持久 Stimulus Trace 和 Source Adapter 模板；
- 发布离线 Stimulus Lab，回放重复、乱序、过期、无关和有效事件（**放行必带**）；
- 提供签名 Webhook 与可注入 Virtual Clock 的**最小参考源模板**（有即可，不单独阻塞放行）。

**放行条件**

- 每个已接纳刺激都有耐久池状态，并在终止时具备明确处置结果，不允许无声消失；
- 同一去重键不会重复激活同一事务；
- ingest→admit→claim→activate/finalize 任一阶段崩溃后可以恢复；
- 外部开发者无需修改 Runtime 内核即可接入新事件源；
- Stimulus Lab 覆盖重复、乱序、过期、无关和有效事件的可回放验收；
- 以 **Public Alpha** 发布。

**v0.3.x 收敛**

- **v0.3.1 Chat Source Adapter：**将 Chat / 用户消息经显式 `ChatSourceAdapter`（私有 Signal → Observation → `runtime.ingest`）接纳；`submit_user_message` 降为兼容薄封装；补齐 `idempotency_key`（`chat:{thread_id}:{message_id}`）与同步路径 `user_turn` payload。
- **后续 0.3.x（Ingress Freeze）：**在退出 v0.3 线进入 v0.4 之前，消除 schedule / feedback / gateway 等剩余旁路入口，统一刺激接纳路径。

### v0.4.0：情景记忆子系统

**主题**

建成自洽的情景记忆（Episodic）子系统，补齐长时记忆的第一块。

**目标**

在不影响主系统运行的前提下，提供可独立演进的情景记忆能力：Flush 触发录入，系统浅召回与工具深召回职责分离；复杂索引与检索逻辑不塞进 Flush 编排。

**范围边界**

- Working Memory（短时对话连续性）已在既有运行时具备，本版本不重做 WM；
- 事务归因继续以 **conv 内归因** 为准，不扩展开放事件流 Attention；
- 现有 `episodic` 工具 RAG 槽位应演进/收敛到本子系统，避免并行两套语义；
- Runtime 拥有 Scene / Flush 边界与 Dialogue 权威产物；情景子系统拥有索引、浅/深召回策略与存储。

**核心交付**

- **录入：**Flush 过程中触发子系统 API，消费不可变 flush 材料写入情景索引（幂等、可重试）；
- **浅召回（系统召回）：**决策前由 Runtime 调用；必须具备高召回限制（top-k、分数阈值、注入预算），防止污染当前认知；
- **深召回（工具召回）：**模型显式调用，面向“回忆准确场景”，以高准确为放行标准；
- 子系统可单独测试与替换，FlushOrchestrator 只负责边界与触发，不内嵌检索算法；
- 用户/线程隔离与基础可观测性（来源、时间、命中理由）。

**放行条件**

- Flush 崩溃/重试不丢、不重复污染情景索引；
- 浅召回有明确上限与隔离，可证明不会无界注入 Context；
- 深召回准确路径可测，且与浅召回语义不再混用；
- 子系统可在脱离完整产品路径时单独验收。

### v0.5.0：经验子系统

**主题**

建成自洽的经验（Experience）子系统，与情景记忆解耦。

**目标**

让“做过什么、什么有效”可在 Flush 后沉淀，并在决策前由系统召回；经验链路自洽，避免把提炼与检索复杂度堆进 Flush。

**范围边界**

- 经验召回 **仅系统召回**，不以模型主动“经验工具搜索”为主路径；
- 经验系统先于 Strategy 生命周期自立；Strategy 的候选/验证/晋级在后续版本从经验能力上生长；
- 录入可异步，但不得破坏主 flush 成功语义（失败形成可重试 backlog，fail-open）。

**核心交付**

- **录入：**Flush 触发，从已提交结果/事务材料提炼经验条目并持久化；
- **召回：**仅系统召回；提供候选、来源、适用条件与置信度；
- 与情景记忆清晰分界：情景回答“当时发生了什么”，经验回答“这类情况通常怎么做/什么有效”；
- 用户隔离、导出/删除钩子（可与 v0.6 拼接时一并硬化）。

**放行条件**

- 经验录入故障不阻断主 flush 成功；
- 召回路径 fail-open：故障时不注入、不阻断 Thinking；
- 经验与情景存储/索引可独立演进，无强制耦合；
- 子系统可单独验收。

### v0.6.0：长时记忆接入与主系统稳定

**主题**

把情景与经验两个自洽子系统拼进主系统，并保证主路径长期稳定运行。

**目标**

完成“缺的就是这两个子系统的搭建与和主系统的拼接，以及主系统的稳定运行”：Flush 只触发，决策前评估注入，主链可长时间跑。

**核心交付**

- Flush 编排：`snapshot → runtime commit → 触发情景录入 / 经验录入`（幂等、可重试、可降级）；
- 决策热路径：WM + 情景浅召回 + 经验召回 → 进入后续 Context 编译所需的候选集（本版本至少完成主链可注入或等价 Shadow→主链切换开关）；
- 记忆故障 fail-open：不阻断 Thinking / 刺激处置；
- 用户隔离、导出、删除、重建与 Schema 迁移路径；
- 主系统稳定：长时间运行、崩溃恢复、无刺激静默丢失、无可控范围外的资源增长；
- 后端可通过 Adapter 替换，不与 Runtime 内核绑定。

**放行条件**

- 两子系统与主链拼接完成，主路径可重复、可恢复；
- 无跨用户/跨线程污染；
- 记忆可完整导出、删除并从权威事件重建；
- 长时间运行验收通过（无静默丢刺激、无状态损坏）。

### v0.7.0：Cognitive State & Context Compiler

**主题**

从“拼 Prompt”升级为编译认知上下文。

**目标**

建立可持久、可追踪的认知状态，并将刺激、WM、情景浅召回与经验召回编译成最小充分上下文 `c`。

**核心交付**

- 明确 Goal、Commitment、Expectation、Evidence、Belief 与 TaskState 边界（按需渐进，不阻塞记忆主链已具备的能力）；
- 定义 `ContextItem`、`ContextSnapshot`、Context Provider 和 Context Compiler SPI；
- 每个 Context Item 携带来源、时间、置信度、敏感级别和 token 成本；
- Context 去重、预算分配和冲突标注；
- 保证当前刺激、活动目标和最新有效证据不会被摘要或截断；
- 浅召回与经验注入受预算与污染约束；
- 保存、检查和回放 Context Snapshot。

**放行条件**

- 每次模型调用都有对应 Context Snapshot；
- 所有进入 `c` 的内容都能追溯到来源；
- Snapshot 可持久化并用于决策回放；
- 无证据内容不能直接成为 Belief 或 Completed；
- 浅召回/经验注入不会突破预算或无界污染。

### v0.8.0：Temporal, Endogenous & Strategy

**主题**

让时间与内部状态也能驱动继续，并让经验以受限 Strategy Guidance 指导行为。

**目标**

在长时记忆与 Context 编译已可用的基础上，建立时间/预期语义与内生刺激，并将经验系统上的 Strategy 能力以 Opt-in 方式接入。

**核心交付**

- 支持事件时间、接收时间、截止时间、持续时间和可注入 Clock；
- 建立 Expectation 生命周期与超时检测；
- 支持预期未满足、任务停滞、Belief 冲突和 Commitment 到期等内生刺激；
- 内生刺激去重、冷却时间和资源配额，避免自激循环；
- 定义 Strategy 的 `candidate / validated / active / deprecated` 生命周期；
- 允许经过验证的 Strategy 以受限 Guidance 进入 Context（默认 Opt-in）；
- TaskState Guard：Strategy 不能创造事实、替代证据或自行完成任务；
- Strategy 开关、消融评测和影响 Trace。

**放行条件**

- Fake Clock 下的时间与预期测试可确定性复现；
- 内生刺激不会形成无限自唤醒循环；
- Strategy 只对显式开启的 Agent 或事务生效；
- Strategy 不提高错误完成率和未经授权行动率；
- 每次使用均能说明来源、适用条件和决策影响。

### v0.9.0：Bounded Autonomy & Release Candidate

**主题**

将持续认知转化为有边界的个人助手，并冻结契约、验证长期稳定性。

**目标**

允许 Agent 在用户授权范围内持续观察和行动；停止增加核心概念，完成稳定版所需的工程、安全、迁移和文档工作。

**核心交付**

- Standing Goal 与长期 Subscription；
- 能力白名单、预算、有效期、安静时段和频率限制；
- 确定性 Approval Policy、全局暂停、事务暂停和 Kill Switch；
- 只读能力与外部写入能力分级；
- Stimulus Source、Context Provider、情景/经验记忆、Strategy 和 Capability 插件 SDK（Attention 插件留待 v1.x+）；
- 通用签名 Webhook 及至少一个只读个人信息源参考适配器；
- 冻结公共 API、配置、数据 Schema 和插件接口；
- 从 v0.2.x 起的迁移工具、Dry Run 和自动备份；
- 崩溃、乱序、重复投递、磁盘异常和外部超时故障注入；
- 长时间运行、资源上限、安全审计和威胁模型；
- 发布 PyPI 包、容器镜像、示例项目和运维文档。

**放行条件**

- 所有副作用在执行前都通过确定性策略检查；
- 模型输出不能绕过审批策略；
- 用户能够查看、暂停和撤销长期观察与自主行为；
- 声明支持的旧数据升级路径全部通过；
- 长时间运行无刺激静默丢失、状态损坏和不可控资源增长；
- 无已知 P0/P1 缺陷；
- 两个 RC 周期内稳定契约不再发生破坏性变更。

### v1.0.0：初步完整的 Stable Cognitive Runtime

**主题**

稳定、可扩展、可审计的**初步完整**认知运行时；正式开源。

**目标**

兑现“短时接得上（WM），长时靠情景/经验接得上；刺激可追踪处置；上下文可编译可回放”的稳定公开承诺。  
**不**将完整 Attention / 开放事件流噪声治理作为 1.0 阻断项。

**核心交付**

稳定以下公共契约：

- Observation 与 Stimulus（含确定性处置）；
- Conversation 级 Transaction Attribution（既有能力的稳定承诺）；
- Working Memory；
- 情景记忆（录入 / 系统浅召回 / 工具深召回）；
- 经验记忆（录入 / 系统召回）；
- Cognitive State（已落地子集）；
- Context Snapshot 与 Context Compiler；
- Expectation 与 Endogenous Stimulus（已落地子集）；
- Strategy（Opt-in 稳定接口，不承诺默认启用）；
- Policy、Approval 与 Effect；
- Cognitive Runtime Plugin SDK（不含强制 Attention 完备）。

同时提供 SemVer、弃用策略、迁移、备份、恢复、导出、删除、离线 Fake Runtime、模型后端替换以及可回放认知因果链。

**放行条件**

- 新安装、旧版升级、崩溃恢复和数据删除全部通过；
- 所有被接纳刺激都有可追踪的最终处置；
- 招牌场景：短时连续 + 跨 Flush 后仍能靠情景/经验接上；
- 每次行动都能追溯到刺激、事务、Context 与权限依据；
- 外部效果的执行保证被明确记录，不宣传无法实现的通用 Exactly Once；
- 文档、示例与实现一致；
- 无已知 P0/P1 问题。

### v1.x+：Attention & Attribution（开源后生态期）

**主题**

在刺激种类与内容随开源社区累积之后，再做去重、噪声过滤与开放事件流归因。

**为何后置**

- 1.0 前刺激种类少，去重/噪声过滤收益低、机会成本高；
- Conv 内事务归因已够支撑当前产品闭环；
- 现有刺激处理在不影响系统运行的前提下已够用；
- Attention 属于刺激生态成熟后的治理层，不是初步完整认知运行时的主线。

**核心交付**

- 来源校验、重复检测、过期判断、顺序和版本检查的可插拔强化；
- 可插拔 Attention Policy，正式引入“正确沉默 / `ignored`”语义（作为 Attention 决策，而非 v0.3 内核处置枚举的扩展）；
- 相关性、新颖性、紧迫性、可信度和信息价值计算；
- 开放事件流下的自动事务归因、候选排序与歧义处理；
- 冲突刺激和迟到刺激处理；
- 每次 Attention / Attribution 决策的可解释理由；
- StimulusBench：多事务归因、噪声、重复、乱序、冲突和正确沉默。

**放行条件**

- 确定性重复、过期和时序用例全部正确；
- 无显式事务 ID 的归因达到公开基线；
- 公开无关刺激误激活率；
- 每次归因都能展示候选、采用依据和拒绝依据。

## 4. 长时记忆接入计划

| 能力 | 定义 | 接入版本 |
| --- | --- | --- |
| Working Memory | 事务内短时连续 | v0.2 已具备 |
| 情景记忆·录入 | Flush 触发子系统写入 | v0.4.0 |
| 情景记忆·浅召回 | 系统召回，高限制防污染 | v0.4.0 实现，v0.6.0 主链拼接 |
| 情景记忆·深召回 | 工具召回，高准确 | v0.4.0 |
| 经验·录入 | Flush 触发子系统写入 | v0.5.0 |
| 经验·召回 | 仅系统召回 | v0.5.0 实现，v0.6.0 主链拼接 |
| Context 编译注入 | Runtime 编译最小充分上下文 | v0.7.0 |
| Attention / 噪声治理 | 多源刺激过滤与开放归因 | v1.x+（开源后） |

控制权原则：

- **深召回（工具）：**模型想起后主动查询。
- **浅召回 / 经验召回（系统）：**Runtime 在决策前评估是否注入。
- **Flush：**只负责边界与触发，不拥有记忆算法细节。
- 两个子系统保持自洽，避免把复杂记忆构建链路全塞进 flush。

## 5. StrategySystem 接入计划

| 版本 | 状态 | 行为 |
| --- | --- | --- |
| **v0.5.0** | 经验子系统自立 | 经验录入与系统召回可用；尚未以 Strategy Guidance 注入 |
| **v0.8.0** | Opt-in | 验证后的 Strategy 可受限进入 Context，并受 TaskState Guard 约束 |
| **v0.9.0—v1.0.0** | Default Candidate（可选） | 通过公开消融评测后才可成为默认候选，未达标则继续 Opt-in |
| **v1.0.0** | Stable Contract | 稳定接口、生命周期和安全边界，不承诺所有 Agent 默认启用 |

Strategy 上线顺序固定为：

> **经验子系统自立 → Opt-in Guidance → 消融评测达标 → 默认候选。**

## 6. 开源发布节点

- **v0.3.0 Public Alpha：**第一次向外部开发者兑现 Source Adapter 与 Stimulus Kernel；
- **v0.3.1：**Chat Source Adapter 收敛（产品 Chat 经 Adapter → `ingest`）；
- **后续 v0.3.x：**消除剩余内部旁路后，才可离开 v0.3 线进入 v0.4（情景记忆）；
- **v0.6—v0.7：**长时记忆拼通 + Context 编译可用，形成可演示的认知运行时雏形；
- **v0.9.0 Release Candidate：**受控自主 + 契约冻结与稳定性验证；
- **v1.0.0 Stable / 正式开源：**初步完整认知运行时，承担长期兼容承诺；
- **v1.x+：**Attention、多源噪声治理与开放事件流归因（生态期）。

## 7. 路线图治理规则

- 新认知能力必须经历明确的接入阶段（记忆子系统：自立 → 主链拼接 → Context 编译；Strategy：自立 → Opt-in → Default Candidate）；
- 新 Schema 必须同时提供版本、迁移、Dry Run、备份和重建路径；
- 文档中的能力声明必须有可执行测试或明确标注“目标设计”；
- 核心 Runtime 不绑定特定模型、记忆算法或连接器供应商；
- 版本接近预算或发布日期不是降低放行标准的理由；
- 未达到评测门槛的 Strategy、高级 Attention 或自主能力可以保持实验状态，不阻塞稳定 Runtime 发布；
- **Attention / 噪声过滤不得挤占 1.0 前长时记忆与主系统稳定主线。**