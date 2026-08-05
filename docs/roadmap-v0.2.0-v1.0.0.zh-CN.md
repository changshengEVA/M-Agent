# M-Agent v0.2.0—v1.0.0 版本规划

> 文档状态：当前权威路线图  
> 基线版本：v0.2.0  
> 确认日期：2026-08-04  
> 发布原则：按放行条件发布，不以日期替代质量门槛

## 1. 总体方向

**愿景：**让 Agent 的认知不再以用户消息为边界，而由世界变化、行动结果、时间条件和内部预期持续驱动。

**技术定位：**M-Agent 是一个 **Stimulus-Native Cognitive Runtime**，核心机制是 **Stimulus-to-Cognition Compilation**。

**产品价值：**

> **说一次，持续接得上；需要你时才出现。**

开发主线固定为：

```text
可信运行时基线
→ Stimulus Kernel
→ Attention 与事务归因
→ Cognitive State 与 Context Compiler
→ 系统记忆与时间
→ 内生刺激与 Strategy
→ 受控自主性与插件生态
→ 稳定契约
```

## 2. 版本总览

| 版本号 | 主题 | 目标 | 核心交付 |
| --- | --- | --- | --- |
| **v0.2.0** | 当前运行时基线 | 固化现有真实能力与限制 | Transaction、Scene、Stimulus Pool、基础归因、Schedule、Effect/Feedback、工具式 RAG、语义验收 |
| **v0.2.1** | 可信基线 | 清除会污染后续认知系统的正确性和开源交付问题 | Single-call Thinking、恢复与 Context 修复、安全默认值、版本统一、CI 与最小安装路径 |
| **v0.3.0** | Stimulus Kernel | 将刺激提升为公开、耐久的一等运行时对象 | `runtime.ingest()`、Observation/Stimulus 协议、确定性处置、Trace、Adapter、Stimulus Lab |
| **v0.4.0** | Attention & Attribution | 判断是否应该醒，以及应唤醒哪件事 | 去重、时序、噪声过滤、Attention Policy、事务归因、StimulusBench v1 |
| **v0.5.0** | Cognitive State & Context Compiler | 从拼接 Prompt 升级为编译认知上下文 | Goal/Commitment/Expectation/Evidence/Belief、Context Snapshot、Compiler SPI、系统记忆 Shadow |
| **v0.6.0** | System Memory & Temporal Runtime | 让过去和时间成为 Runtime 自动维护的认知条件 | 系统记忆主链、WorkspaceMem Adapter、Clock、Expectation 生命周期、Strategy Shadow |
| **v0.7.0** | Endogenous Cognition & Strategy | 让内部状态产生刺激，并让验证后的 Strategy 受限指导行为 | 内生刺激、Strategy 生命周期、受限 Guidance、TaskState Guard、消融评测 |
| **v0.8.0** | Bounded Autonomy & Cognitive SDK | 将持续认知转化为有边界的个人助手和开发者生态 | Standing Goal、权限/预算/审批、Kill Switch、认知插件 SDK、参考适配器 |
| **v0.9.0** | Release Candidate | 冻结契约并验证长期稳定性 | 迁移、备份恢复、故障注入、长时间运行、安全审计、正式发布产物 |
| **v1.0.0** | Stable Cognitive Runtime | 稳定兑现刺激原生认知运行时的公共承诺 | 稳定协议、SemVer、可回放因果链、可替换后端、完整个人助手参考场景 |

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

**主题**

将刺激提升为公开、耐久的一等运行时对象。

**目标**

让开发者能够接入任意事件源，并知道每个被接纳的刺激最终发生了什么。

**核心交付**

- 公开 `runtime.ingest(observation)`；
- 定义 Signal、Observation、Stimulus 三层契约；
- 支持来源、事件时间、接收时间、身份、因果来源、去重键和可信度；
- 定义 `rejected / ignored / merged / deferred / activated` 处置状态；
- 提供持久 Stimulus Trace 和 Source Adapter 模板；
- 提供签名 Webhook 与可注入 Virtual Clock 的最小参考源；
- 发布离线 Stimulus Lab，回放重复、乱序、过期、无关和有效事件。

**放行条件**

- 每个已接纳刺激都有耐久处理中状态或最终处置，不允许无声消失；
- 同一去重键不会重复激活同一事务；
- 任意处理阶段崩溃后可以恢复；
- 开发者无需修改 Runtime 内核即可接入新事件源；
- 以 **Public Alpha** 发布。

### v0.4.0：Attention & Attribution

**主题**

判断是否应该醒，以及应该唤醒哪件事。

**目标**

在缺少显式 `transaction_id` 的开放事件流中，过滤噪声并将刺激归因到正确 Goal 或 Transaction。

**核心交付**

- 来源校验、重复检测、过期判断、顺序和版本检查；
- 可插拔 Attention Policy；
- 相关性、新颖性、紧迫性、可信度和信息价值计算；
- 自动事务归因、候选排序与歧义处理；
- 冲突刺激和迟到刺激处理；
- 每个处置结果的可解释理由；
- StimulusBench v1：多事务归因、噪声、重复、乱序、冲突和正确沉默。

**放行条件**

- 确定性重复、过期和时序用例全部正确；
- 无显式事务 ID 的归因达到公开基线；
- 公开无关刺激误激活率；
- 每次归因都能展示候选、采用依据和拒绝依据。

### v0.5.0：Cognitive State & Context Compiler

**主题**

从“拼 Prompt”升级为编译认知上下文。

**目标**

建立可持久、可追踪的认知状态，并将刺激、状态、目标和证据编译成最小充分上下文 `c`。

**核心交付**

- 明确 Goal、Commitment、Expectation、Evidence、Belief 与 TaskState 边界；
- 定义 `ContextItem`、`ContextSnapshot`、Context Provider 和 Context Compiler SPI；
- 每个 Context Item 携带来源、时间、置信度、敏感级别和 token 成本；
- Context 去重、预算分配和冲突标注；
- 保证当前刺激、活动目标和最新有效证据不会被摘要或截断；
- 保存、检查和回放 Context Snapshot；
- **以 Shadow 模式接入 System Memory SPI**：投影已提交状态并执行候选召回，但暂不影响决策。

**放行条件**

- 每次模型调用都有对应 Context Snapshot；
- 所有进入 `c` 的内容都能追溯到来源；
- Snapshot 可持久化并用于决策回放；
- 无证据内容不能直接成为 Belief 或 Completed；
- System Memory Shadow 开关不改变实际决策。

### v0.6.0：System Memory & Temporal Runtime

**主题**

让过去和时间成为 Runtime 自动维护的认知条件。

**目标**

将系统记忆正式接入认知主链，并建立时间、预期和期限的统一运行语义。

**核心交付**

- 系统记忆自动写入、整合、召回和注入；
- 支持情景记忆、事务状态、可靠事实和未完成事项；
- 提供 Memory Projector、SystemMemoryProvider 和 MemoryEvidence；
- WorkspaceMem 等后端通过 Adapter 接入，不与 Runtime 内核绑定；
- 支持事件时间、接收时间、截止时间、持续时间和可注入 Clock；
- 建立 Expectation 生命周期与超时检测；
- 支持记忆的用户隔离、导出、删除、重建和 Schema 迁移；
- 保留工具记忆，作为模型主动调用的扩展查询能力；
- **StrategySystem 以 Shadow 模式接入**：构造 Current Situation、检索候选并记录影响，但不注入 Context。

**放行条件**

- 系统记忆无跨用户和跨事务污染；
- 每条注入记忆都有来源、时间和置信度；
- 记忆可完整导出、删除并从权威事件重建；
- Fake Clock 下的时间与预期测试可确定性复现；
- Strategy Shadow 不改变实际决策。

### v0.7.0：Endogenous Cognition & Strategy

**主题**

从被动接收外部事件，迈向由内部状态产生认知刺激。

**目标**

让未满足预期、任务停滞和证据冲突形成内生刺激，并让经过验证的 Strategy 受限地指导行为。

**核心交付**

- 支持预期未满足、任务停滞、Belief 冲突和 Commitment 到期等内生刺激；
- 提供内生刺激去重、冷却时间和资源配额，避免自激循环；
- 定义 Strategy 的 `candidate / validated / active / deprecated` 生命周期；
- 只从具有真实结果证据的事务中提取 Strategy Candidate；
- 允许经过验证的 Strategy 以受限 Guidance 进入 Context；
- 提供 TaskState Guard：Strategy 不能创造事实、替代证据或自行完成任务；
- 提供 Strategy 开关、消融评测和影响 Trace。

**放行条件**

- 内生刺激不会形成无限自唤醒循环；
- Strategy 只对显式开启的 Agent 或事务生效；
- 在预注册评测集上相对无 Strategy 基线取得可复现净收益；
- Strategy 不提高错误完成率和未经授权行动率；
- 每次使用均能说明来源、适用条件和决策影响。

### v0.8.0：Bounded Autonomy & Cognitive SDK

**主题**

将持续认知转化为有边界的个人助手与开发者生态。

**目标**

允许 Agent 在用户授权范围内持续观察和行动，同时开放稳定候选的认知扩展接口。

**核心交付**

- Standing Goal 与长期 Subscription；
- 能力白名单、预算、有效期、安静时段和频率限制；
- 确定性 Approval Policy、全局暂停、事务暂停和 Kill Switch；
- 只读能力与外部写入能力分级；
- Stimulus Source、Attention、Attribution、Context Provider、System Memory、Strategy 和 Capability 插件 SDK；
- 通用签名 Webhook 及至少一个只读个人信息源参考适配器；
- 插件兼容性测试和版本声明；
- Strategy 达标时成为默认候选，未达标则保持 Opt-in。

**放行条件**

- 所有副作用在执行前都通过确定性策略检查；
- 模型输出不能绕过审批策略；
- 至少两个独立示例插件只依赖公开 SDK；
- 用户能够查看、暂停和撤销长期观察与自主行为；
- 招牌场景完成“说一次，后续变化自动接回原事务”；
- 以 **Public Beta** 发布。

### v0.9.0：Release Candidate

**主题**

冻结契约，验证长期稳定性。

**目标**

停止增加核心概念，完成稳定版所需的工程、安全、迁移和文档工作。

**核心交付**

- 冻结公共 API、配置、数据 Schema 和插件接口；
- 提供从 v0.2.x 起的迁移工具、Dry Run 和自动备份；
- 验证备份、恢复、重建和数据删除；
- 支持声明范围内的单机多进程并发；
- 执行崩溃、乱序、重复投递、磁盘异常和外部超时故障注入；
- 完成长时间运行、资源上限、安全审计和威胁模型；
- 发布 PyPI 包、容器镜像、示例项目和运维文档；
- 明确每项 Capability 的幂等、至多一次或不确定执行语义。

**放行条件**

- 声明支持的旧数据升级路径全部通过；
- 长时间运行无刺激静默丢失、状态损坏和不可控资源增长；
- 无已知 P0/P1 缺陷；
- 两个 RC 周期内稳定契约不再发生破坏性变更；
- 所有公开示例可从干净环境复现。

### v1.0.0：Stable Stimulus-Native Cognitive Runtime

**主题**

稳定、可扩展、可审计的认知运行时。

**目标**

兑现“世界变化能够在正确时刻、因正确原因，回到正确事务，并形成正确认知上下文”的稳定公开承诺。

**核心交付**

稳定以下公共契约：

- Observation 与 Stimulus；
- Attention 与 Disposition；
- Goal / Transaction Attribution；
- Cognitive State；
- Context Snapshot 与 Context Compiler；
- System Memory；
- Expectation 与 Endogenous Stimulus；
- Strategy；
- Policy、Approval 与 Effect；
- Cognitive Runtime Plugin SDK。

同时提供 SemVer、弃用策略、迁移、备份、恢复、导出、删除、离线 Fake Runtime、模型后端替换以及可回放认知因果链。

**放行条件**

- 新安装、旧版升级、崩溃恢复和数据删除全部通过；
- 所有被接纳刺激都有可追踪的最终处置；
- 每次行动都能追溯到刺激、事务、Context、Strategy 和权限依据；
- 外部效果的执行保证被明确记录，不宣传无法实现的通用 Exactly Once；
- 插件兼容测试和公开基准稳定通过；
- 文档、示例与实现一致；
- 无已知 P0/P1 问题。

## 4. 系统记忆与工具记忆接入计划

| 能力 | 定义 | 接入版本 |
| --- | --- | --- |
| 工具记忆 | 模型主动调用的搜索、RAG 或档案查询 | v0.2.0 已存在，后续作为扩展查询保留 |
| System Memory SPI | Runtime 自动投影并评估记忆候选 | v0.5.0 Shadow |
| 系统记忆主链 | Context Compiler 自动选择并注入必要记忆 | v0.6.0 正式生效 |
| 系统记忆 SDK 候选 | 后端可替换、可迁移、可导出删除 | v0.8.0 |
| 系统记忆稳定契约 | 稳定公共协议与升级策略 | v1.0.0 |

系统记忆与工具记忆以控制权区分：

- **工具记忆：模型想起后主动查询。**
- **系统记忆：Runtime 保证必要记忆在决策前得到评估和编译。**

## 5. StrategySystem 接入计划

| 版本 | 状态 | 行为 |
| --- | --- | --- |
| **v0.6.0** | Shadow | 构造 Situation、检索和记录候选 Strategy，但不影响决策 |
| **v0.7.0** | Opt-in | 验证后的 Strategy 可受限进入 Context，并受 TaskState Guard 约束 |
| **v0.8.0** | Default Candidate | 通过公开消融评测后才可成为默认候选，未达标则继续 Opt-in |
| **v1.0.0** | Stable Contract | 稳定接口、生命周期和安全边界，不承诺所有 Agent 默认启用 |

Strategy 上线顺序固定为：

> **Shadow → Opt-in → 消融评测达标 → 默认候选。**

## 6. 开源发布节点

- **v0.3.0 Public Alpha：**第一次向开发者兑现 Stimulus Kernel；
- **v0.8.0 Public Beta：**第一次兑现受控个人助手体验与认知插件 SDK；
- **v0.9.0 Release Candidate：**冻结契约，只处理稳定性和发布问题；
- **v1.0.0 Stable Release：**正式承担长期兼容承诺。

## 7. 路线图治理规则

- 新认知能力必须经历 `Shadow → Opt-in → Default Candidate`；
- 新 Schema 必须同时提供版本、迁移、Dry Run、备份和重建路径；
- 文档中的能力声明必须有可执行测试或明确标注“目标设计”；
- 核心 Runtime 不绑定特定模型、记忆算法或连接器供应商；
- 版本接近预算或发布日期不是降低放行标准的理由；
- 未达到评测门槛的 Strategy、系统记忆或自主能力可以保持实验状态，不阻塞稳定 Runtime 发布。
