# M-Agent Runtime 语义验收平台

状态：post-P7；35 个 ThinkLife variant 全部可执行且全绿，当前为 35 `Passed` /
0 `Registered Known Gaps`、0 `Unexpected`

适用计划：[LangGraph Runtime 迁移计划](langgraph-runtime-migration-plan.zh-CN.md)

历史证据：[ThinkLife P1 Known Gap 矩阵](think-life-p1-gap-matrix.zh-CN.md)
（冻结的 2026-07-28 pre-P2 基线，不是当前差距表）

## 1. 为什么不能只看原有 pytest 是否全绿

仓库原有测试能够验证许多局部实现，但测试名称、目录和层级并不能直接回答：

- 迁移不能破坏的 transaction、stimulus、attribution 语义是否都有可执行证据；
- 某个测试到底证明了哪项领域约束；
- API fake 测试是否真的运行了目标 Runtime；
- 恢复、抢占、重放和重复 Feedback 是否会重复产生副作用；
- 当前绿色回归中是否仍存在已知语义缺口。

验收平台仍使用 pytest 作为底层执行器，但在 pytest 之上增加了四项约束：

1. Runtime 无关的 `Scenario → Variant → Runtime binding` 共享合同；
2. `core/robustness/matcher_evaluation/supporting` 四种测试层级；
3. 统一的子进程 Runner、规范化 observation/trace 和结构化报告；
4. 共用底层执行与存档设施的 CLI 和本地 Web UI。

因此，普通 pytest 回归测试和语义验收测试的职责不同：

- 普通测试回答“某个函数或接口有没有退化”；
- 语义验收回答“运行时是否满足迁移合同，以及差距具体落在哪条检查上”。

## 2. 合同与执行基线

### 2.1 完整清单

P1 已建立并接通：

- 28 个顶层场景：`TX-01～10`、`SP-01～08`、`AT-01～10`；
- 35 个可选择 variant：
  - 27 个确定性 Core；
  - 7 个 Robustness；
  - 1 个 `AT-10/matcher_evaluation`；
- `think_life_v1` 与 `langgraph_v1` 两个显式 Runtime binding；
- `executable/not_covered/not_implemented/future` 四种可用性状态；
- `passed/known_gaps/failed/incomplete` 四种聚合结果；
- 每个可执行 case 的 normalized observation、逐项 check 和 semantic trace；
- 规格第 8 节 25 项稳健性要求到 7 个 Robustness variant 的完整映射。

当前 post-P5/M0 catalog 口径为：

| 层级 | 规格清单 | ThinkLife | LangGraph |
|---|---:|---:|---:|
| Core | 27/27 | 27 `Executable` / 27 `Passed` / 0 Registered Known Gaps | 27 `Not Implemented` |
| Robustness | 7/7 | 7 `Executable` / 7 `Passed` / 0 Registered Known Gaps | 7 `Not Implemented` |
| Matcher Evaluation | 1/1 | 1 `Executable` / 1 `Passed` / 0 Registered Known Gap | 1 `Not Implemented` |
| 合计 | 35/35 | 35 `Executable` / 35 `Passed` / 0 Registered Known Gaps | 35 `Not Implemented` |

`think_life_v1` 的 35 个 variant 均会真正进入 Runtime adapter 和 Harness，不再以
`Not Covered` 代替执行。P7 收口后 ThinkLife 合同运行应为 **35 passed、0 xfailed、
0 unexpected**。P7 新增/收口的 Robustness 包括：

- `TX-06/schedule_atomicity_and_control_recovery`（Schedule delivery 幂等、
  故障注入、UI Pause 下的 run 控制恢复）；
- `TX-07/effect_result_feedback_outbox`（effect ledger + Feedback ingress outbox
  原子提交与 terminal acknowledgement）；
- `TX-07/capability_delivery_guarantees`（Capability `delivery_guarantee` 声明与
  可控 sink conformance）。

`run_0e47745ff65c4d989e2c` 与
[ThinkLife P1 Known Gap 矩阵](think-life-p1-gap-matrix.zh-CN.md)只保留为
2026-07-28 pre-P2 历史证据；当前状态必须读取 catalog 并重新执行 Gate，不能从历史表推断。

`langgraph_v1` 在 P6 范围已实现 5 个 binding（`TX-01/core`、
`TX-01/uow_replay_and_result` 与 3 个 PoC slice）；其余 binding 仍明确返回
`Not Implemented`；不会启动空测试，
也不会把缺实现聚合为通过。

### 2.2 P1 退出条件的含义

catalog 校验、35 个 ThinkLife variant 可执行、35 个 LangGraph binding 显式
`Not Implemented` 三项同时满足后，`p1_exit_ready=true`。

这表示“共享合同、执行入口和真实差距表已经齐备”，不表示旧 Runtime 已满足新语义。
M0 要求 ThinkLife Core Gate 全绿；P7 完成后 Robustness 亦全绿，合同运行退出码应为 `0`。

## 3. Runtime Harness 边界

共享测试只调用领域命令与查询，不引用 Runtime 私有类、节点或方法。
`RuntimeHarness` 的完整边界如下：

| 领域 | Harness 操作 | 类型 |
|---|---|---|
| Conversation 与 fixture | `create_conversation`、`seed_transaction`、`seed_delegate` | 命令 |
| Stimulus admission | `submit_stimulus` | 命令 |
| Inbox 读取 | `list_ready_stimuli`、`load_stimulus`、`load_partition_state` | 查询 |
| Consumer 与 claim | `acquire_consumer`、`takeover_consumer`、`claim_next_stimulus`、`finalize_stimulus`、`process_next_stimulus` | 命令/查询 |
| Transaction | `load_transaction`、`list_match_candidates`、`control_transaction`、`restore_transaction` | 命令/查询 |
| Scene、Schedule 与 Effect | `read_scene`、`load_schedule`、`load_effects` | 查询 |
| Flush 与故障 | `trigger_flush`、`inject_fault` | 命令 |
| 并发控制 | `set_worker_latch`、`wait_for_disposition`、`control_effect_sink` | 命令/查询 |
| 恢复与 Matcher | `restart_runtime`、`evaluate_matcher` | 命令/查询 |

每次调用返回统一的 `HarnessResult`：

- `operation`：稳定操作名；
- `outcome`：`ok/rejected/not_found/empty/unsupported` 等领域结果；
- `supported`：当前 Runtime 是否原生支持目标语义；
- `data`：规范化领域观察值；
- `reason`：拒绝或缺能力的明确原因。

post-P2 Harness 组合真实的 `SQLiteRuntimeStore`、`TransactionRegistry`、
`RuntimeUnitOfWork`、`SceneLogStore` 与 `FlushCoordinator`，并让 Transaction、
activation/delegate、transition ledger、Scene、watermark、Flush journal/outbox 使用同一
SQLite Store。当前已具备：

- `continue/pause/complete/archive` 四态以及独立的 Deleted lifecycle tombstone；
- activation/delegate 创建、完成、失效与 Restore 后的新 activation；
- revision、CAS fencing、transition ID + command digest ledger 和完整结果重放；
- transaction 级 Pause、Delete、Complete、Restore，且 Archive 只能由 Flush 产生；
- Scene append idempotency、稳定 Flush ID、watermark、eligible archive 与 Flush
  commit 后物化恢复。

这些能力已经进入 P2 reference Store 和 Harness；生产 Chat Runtime 的完整跨 store
接线不应由测试替代。尚未实现的 admission/worker、routing/matcher、effect 等操作仍返回
结构化 `unsupported` 或 Registered Known Gap 证据，不会伪造目标状态，也不会改成 skip。

## 4. Known Gap 的动态判定

Known Gap 不是给整个测试套一个无条件 xfail。每条 observation check 都包含：

- `actual` 与 `expected`；
- `passed`；
- 支撑判断的规范化 `evidence`；
- 失败时使用的 `known_gap_key`。

Runtime binding 同时登记允许出现的 Known Gap key。运行时按以下规则判定：

1. check 全部通过且 binding 没有遗留登记时，case 才是 `Passed`；
2. 只有当**每条失败 check** 的 key 都已在该 binding 登记时，case 才动态
   `xfail`，并聚合为 `Known Gap`；
3. 任意未登记失败都必须是 `Failure`，已有 Known Gap 不能吞掉新回归；
4. 如果已登记 gap 不再复现，测试同样失败，要求删除过时登记，避免“永久 xfail”；
5. Runtime 整体尚未实现时才使用 `Not Implemented`，而不是用 Known Gap 假装执行。

当前 3 个 Registered Known Gaps 各自登记一个与 variant 稳定对应的 key；32 个
`Passed` variant 不再登记 gap。例如：

```text
TX-06/schedule_atomicity_and_control_recovery
                        → tx_06.schedule_atomicity_and_control_recovery
TX-07/effect_result_feedback_outbox
                        → tx_07.effect_result_feedback_outbox
TX-07/capability_delivery_guarantees
                        → tx_07.capability_delivery_guarantees
```

这样，报告能够区分“目标差距仍按预期存在”和“出现了未登记的新失败”。

### 4.1 当前 AT-01～AT-04 剩余差距

2026-07-28 历史矩阵中关于“缺 activation/Restore”的描述只反映 pre-P2 状态。
post-P2 已具备 activation/delegate、Pause/Complete/Archive Restore、旧 activation
失效和新 activation 创建；post-P5/M0 后，`AT-01～AT-10` 与全部 Core 已转绿：

| Variant | 当前状态 |
|---|---|
| `AT-01/core` | Passed：Feedback 三元校验、Schedule/显式来源绕过 matcher、已归因重入锁定。 |
| `AT-02/core` | Passed：候选精确为 pause + 未 flush complete。 |
| `AT-03/core` | Passed：matcher 选择 pause 后 restore 并开启新 activation。 |
| `AT-04/core` | Passed：matcher 选择未 flush complete 后重新激活。 |
| `AT-08/core` / `AT-09/core` | Passed：归因稳定与 fake matcher 安全新建契约。 |
| `AT-10/matcher_evaluation` | Passed：`offline_policy_v1` 达到 ≥90% / ≥85% 冻结阈值。 |

这里列的是当前责任边界，不回写
[2026-07-28 pre-P2 历史矩阵](think-life-p1-gap-matrix.zh-CN.md)中的冻结表格。

## 5. AT-10 Matcher Evaluation

`AT-10/matcher_evaluation` 使用冻结数据集
`tests/acceptance/data/at_10_matcher_v1.zh-CN.jsonl`。数据集遵循
`matcher.v1` schema，共 30 条：

| 类别 | 数量 |
|---|---:|
| `pause_continuation` | 8 |
| `complete_reactivation` | 6 |
| `unrelated_create` | 6 |
| `ambiguous_create` | 5 |
| `multi_candidate_unique` | 5 |
| 合计 | 30 |

每条样本冻结 stimulus、conversation tail、候选 transaction 和期望动作。评测入口通过
Harness 的 `evaluate_matcher` 返回结构化输出有效率、总准确率、reuse recall、
wrong-candidate 与 false-reuse 等证据。ThinkLife 已接入确定性
`offline_policy_v1`，可对冻结数据重复运行三次并报告指标；P5 后已达到冻结阈值，
该 variant 为 Passed。真实 LLM matcher 仍独立于确定性 Core Gate。

## 6. CLI

### 6.1 查看合同与覆盖

```powershell
python -m m_agent.acceptance contract list
python -m m_agent.acceptance contract list --format json
python -m m_agent.acceptance contract coverage
```

### 6.2 运行共享场景

运行单个场景：

```powershell
python -m m_agent.acceptance contract run --scenario TX-01 --runtime think_life_v1
python -m m_agent.acceptance contract run --scenario TX-01 --runtime think_life_v1 --format json
```

按 canonical 层级选择：

```powershell
python -m m_agent.acceptance contract run --runtime think_life_v1 --layer core
python -m m_agent.acceptance contract run --runtime think_life_v1 --layer robustness
python -m m_agent.acceptance contract run --runtime think_life_v1 --layer matcher_evaluation
```

一次运行 P1 的三个目标层级：

```powershell
python -m m_agent.acceptance contract run --runtime think_life_v1 --all-layers
python -m m_agent.acceptance contract run --runtime think_life_v1 --all-layers --format json
```

`--all-layers` 指 `core + robustness + matcher_evaluation`；旧 INV Supporting 套件仍由
旧入口独立运行：

```powershell
python -m m_agent.acceptance run phase0
python -m m_agent.acceptance run phase0 --profile full
```

查看历史报告：

```powershell
python -m m_agent.acceptance show <run_id>
python -m m_agent.acceptance show <run_id> --format json
```

### 6.3 P2 Foundation 与当前已实现合同

P2 Foundation Gate 使用 marker 选择，不在文档中绑定固定测试条数：

```powershell
python -m pytest -q -m p2_foundation
```

重新验证当前 9 个 `Passed` variant（6 个 TX，以及因 P2 新建 transaction 语义同步
转绿的 `AT-05/06/07`）：

```powershell
python -m m_agent.acceptance contract run --runtime think_life_v1 --scenario TX-01 --scenario TX-04 --scenario TX-09 --scenario TX-10 --scenario AT-05 --scenario AT-06 --scenario AT-07 --all-layers
```

命令每次都读取当前 catalog。若实现退化，已移除 gap 的 variant 会成为 Failure，而不会被
2026-07-28 的历史 xfail 掩盖。

CLI 退出码：

- `0`：目录结构完整，或所选执行项全部通过；
- `1`：存在失败、Known Gap 或不完整结果；
- `2`：命令、目录或运行器错误。

## 7. Web UI

启动只监听本机的验收仪表盘：

```powershell
python -m m_agent.acceptance ui --host 127.0.0.1 --port 8788
```

启动后打开 <http://127.0.0.1:8788/>。默认页只展示 P1 共享合同，不再把旧
`INV-01～INV-14` 混排在结果下方。页面按“总览 → 索引 → 单项证据”的顺序组织：

```text
当前 Runtime 与运行结论
├── Passed / Known Gap / Unexpected / checks 汇总
├── TX / SP / AT 领域统计与筛选
└── Variant 索引
    └── 当前选中的一个 Variant
        ├── 未满足检查：Expected / Actual / Evidence / Gap key
        ├── 已通过检查
        └── normalized facts / semantic trace / 技术详情
```

UI 支持：

- 明确区分“平台可执行覆盖”和“Runtime 是否满足目标合同”，不会把 35/35 可执行显示成
  35/35 通过；
- 选择 Runtime 后启动或取消 Core、Robustness、Matcher Evaluation 全矩阵；
- 按领域、层级、状态和关键字筛选 Variant，并且一次只展开当前选中项的证据；
- 在失败检查中并排展示 `expected`、`actual`、`evidence` 和 `known_gap_key`；
- 分别使用绿色、琥珀色、红色展示 Passed、Registered Known Gap 和未登记
  Unexpected Failure；
- 按 Runtime 隔离结果；切换到 LangGraph 时不会继续显示 ThinkLife 的运行证据；
- 刷新页面后优先恢复该 Runtime 上次记录的 run；本地记录不存在时读取服务端最近一次
  P1 run；
- 下载 Summary JSON、pytest JSON、JUnit、stdout 和 stderr；
- 从页面右上角进入 <http://127.0.0.1:8788/legacy> 查看旧 INV Supporting
  历史兼容视图。默认 P1 页不会加载旧 catalog 或旧 run。

UI 是独立本地进程，不挂载到生产 Chat API。

## 8. 报告与 trace

每次运行会在仓库的 `.acceptance-runs/<run_id>/` 生成：

```text
summary.json        # 平台聚合结果与场景树
pytest-report.json  # allowlist node、observation、checks 和 semantic trace
junit.xml           # CI 可读取的 JUnit 报告
stdout.log
stderr.log
tmp/                # 本次 pytest 独立 basetemp
```

P1 报告包括：

- `result_kind=scenario_contract`；
- `runtime_id`、`scenario_ids` 和 layer 选择；
- `scenario_id`、`variant_id`、`scenario_layer`；
- `scenarios[].variants[]` 分层结果；
- 每个 case 的 normalized `observation.facts/checks` 与 trace。

`SemanticTrace` 使用递增 `seq` 表达逻辑顺序，并把随机 transaction、stimulus、delegate
ID 映射为 `tx#1`、`stim#1`、`dlg#1`。时间戳、绝对路径和耗时等不稳定字段会被移除。
因此，后续 `think_life_v1` 和 `langgraph_v1` 可以比较同一套领域证据，而不是比较 UUID、
日志字符串或最终回复文本。

## 9. 旧 INV Supporting 目录

`INV-01～INV-14` 继续作为 Supporting Tests 和历史映射保留，不再代表 P1 Core Gate：

- Gate：14 项关键语义，20 个最小阻断用例；
- Semantic Full：同一组 14 项语义的 36 个用例，其中包含 20 个 Gate 和 16 个
  Supporting 回归用例。

旧 CLI 入口仍可用于持续回归，Web 历史视图位于 `/legacy`。P1 进度、Runtime 对比和
迁移差距以默认页的 TX/SP/AT 共享合同为准；旧结果不参与 P1 总览指标。

## 10. 执行安全边界

验收 Runner 具有以下限制：

- 只运行 catalog 中登记的 pytest node ID；
- 不接受任意 pytest 参数、文件路径、shell 命令或环境变量；
- 使用 `subprocess` 且 `shell=False`；
- 同时只允许一个验收子进程；
- 显式使用 `--maxfail=0` 收集完整矩阵；
- 禁止 pytest 第三方插件自动加载；
- 不向子进程传递 API key、OAuth token 等凭据；
- 每次运行使用独立临时目录、超时和可取消进程；
- CLI 只允许 UI 绑定 `127.0.0.1`、`localhost` 或 `::1`。

不要在生产 Chat API 进程内调用 `pytest.main()`。现有 `tests/conftest.py` 会重置
Chat API 的若干进程内全局状态，进程内执行会干扰真实会话。

## 11. 添加或收紧测试

新增或收紧 P1 场景时：

1. 在 `scenario_catalog.py` 声明 `ScenarioSpec/ScenarioVariantSpec/RuntimeBindingSpec`；
2. 由对应 Runtime adapter 通过共享 Harness 提供领域操作和观察值；
3. 共享断言不得引用 Runtime 私有类、节点或方法；
4. 使用 deterministic fake model/tool/store，不访问真实邮件、日程、搜索或 LLM；
5. 对顺序、归因和副作用次数使用 normalized checks 与 `SemanticTrace`；
6. 当前实现不满足语义时，给具体失败 check 登记稳定 key；不要删除断言或普通 skip；
7. 修复实现后，同时删除已不再复现的 Known Gap 登记。

常用验证命令：

```powershell
python -m pytest -q -m p2_foundation
python -m m_agent.acceptance contract run --runtime think_life_v1 --scenario TX-01 --scenario TX-04 --scenario TX-09 --scenario TX-10 --scenario AT-05 --scenario AT-06 --scenario AT-07 --all-layers
python -m pytest -q --maxfail=0 tests/acceptance
python -m m_agent.acceptance contract coverage
python -m m_agent.acceptance contract run --runtime think_life_v1 --all-layers
python -m m_agent.acceptance run phase0 --profile full
```

## 12. post-P7 状态与后续阶段

P8 灰度与 LangGraph 完整矩阵已完成：

- `think_life_v1` 与 `langgraph_v1` 均为 38/38 variant 可执行；
- 共享验收矩阵在 `langgraph_v1` 选择下 56/56 全绿；
- 持久 SQLite checkpointer、`runtime_engine` 灰度路由已落地。

下一步进入稳定观察期：逐步把新建 transaction 默认路由切到 LangGraph，观察无
串扰后再收缩旧 transaction 循环。保持未登记失败为红色 Failure，防止 Runtime
改造引入新回归。

P1 退出的意义是“所有合同都能运行并给出真实证据”；M0/P7 的意义才是“ThinkLife
Core + Robustness 全部通过并独立达到 Matcher 阈值”。两者不可互换。
