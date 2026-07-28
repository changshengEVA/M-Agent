# M-Agent Runtime 语义验收平台

状态：Phase 0 可用  
适用计划：[LangGraph Runtime 迁移计划](../architecture/langgraph-runtime-migration-plan.zh-CN.md)

## 1. 为什么不能只看原有 pytest 是否全绿

仓库原有测试能够验证许多局部实现，但测试名称、目录和层级并不能直接回答：

- LangGraph 迁移计划中的 14 项运行时不变量是否全部有测试；
- 某个测试到底证明了哪一项领域语义；
- API fake 测试是否真的运行了 `ThinkLifeRuntime`；
- 恢复、抢占和重复 feedback 是否会重复产生副作用；
- 当前绿色结果中是否仍存在已知语义缺口。

验收平台仍使用 pytest 作为底层执行器，但在 pytest 之上增加了四层约束：

1. 固定的 `INV-01` 至 `INV-14` 语义目录；
2. 每项不变量的验收标准和 allowlist 测试证据；
3. 统一的子进程 Runner、规范化 trace 和结构化报告；
4. 共用同一 Runner 的 CLI 与本地 UI。

因此，普通 pytest 回归测试和语义验收测试的职责不同：

- 普通测试回答“某个函数或接口有没有退化”；
- 语义验收回答“运行时是否仍满足迁移不能破坏的领域约束”。

## 2. 当前目录

平台区分“语义不变量”和“pytest 用例”：

- Gate：14 项关键语义，20 个最小阻断用例；
- Semantic Full：仍是同一组 14 项关键语义，但运行 36 个用例，其中包括
  20 个 Gate 用例和 16 个 Supporting 回归用例。

UI 按五个稳定领域组展示这些语义：

| 领域组 | Invariant | Gate 用例 | Semantic Full 用例 |
|---|---|---:|---:|
| 外层入口与调度 | INV-01、INV-02、INV-12 | 4 | 8 |
| 事务归因与状态 | INV-03、INV-04、INV-05 | 4 | 7 |
| 规划、执行与完成 | INV-07 至 INV-11 | 6 | 11 |
| Scene 与记忆生命周期 | INV-06、INV-14 | 5 | 9 |
| 恢复与幂等 | INV-13 | 1 | 1 |

| ID | 关键语义 | Gate 结果 |
|---|---|---|
| INV-01 | 所有刺激先入队 | Known gap |
| INV-02 | 每个 conversation 单 drainer | Known gap |
| INV-03 | feedback 禁止重新语义归因 | Pass |
| INV-04 | feedback 同时匹配 transaction 与 active delegate | Pass |
| INV-05 | transaction 状态隔离 | Known gap |
| INV-06 | conversation 单一 Scene 时间线 | Pass |
| INV-07 | Thinking 只规划 | Pass |
| INV-08 | 每个 delegate 最多一个 capability | Pass |
| INV-09 | 参数不足不误调工具 | Pass |
| INV-10 | reply 走正常 capability 审计路径 | Known gap |
| INV-11 | 回复成功不等于任务完成 | Pass |
| INV-12 | 协作式安全抢占 | Known gap |
| INV-13 | 重复 feedback 与重放幂等 | Known gap |
| INV-14 | flush 只在安全条件下推进 | Pass |

`Known gap` 不是跳过测试。测试会先运行并观察当前行为，在确认缺口仍存在时报告
`xfailed`；平台再将其聚合为非绿色的 `known_gaps`。将来行为修复后，同一测试会自然
转为 `passed`。

当前六项缺口是：

1. 用户消息会先写 Scene 再进入 inbox；Scene 写入失败可能阻止 admission。
2. drainer 在“确认队列为空”和“从 active worker 表移除”之间存在 lost-wakeup 窗口。
3. transaction 虽拥有 `episode_buffer`，flush 仍从 Thinking 的 legacy state registry 读取。
4. `reply_to_user` 能发出回复和写 Scene，但没有调用 `record_tool_use`，因此
   `ExecutionResult.tool_history` 为空。
5. 抢占重入时 envelope 虽保留原 `transaction_id`，attributor 仍会创建新 transaction。
6. feedback 消费后没有清除或持久化 delegate 消费键，相同 feedback 可再次被接受。

这些是 Runtime 修复任务，不应通过放宽验收标准变绿。

## 3. 安装

开发环境安装项目和验收依赖：

```powershell
python -m pip install -e ".[acceptance]"
```

项目已经安装且环境中已有 pytest、FastAPI、Uvicorn 时不需要重复安装。

## 4. CLI

### 查看语义目录

```powershell
python -m m_agent.acceptance list
python -m m_agent.acceptance list --format json
python -m m_agent.acceptance coverage
```

### 运行 Gate

Gate 是每项语义的最小阻断测试：

```powershell
python -m m_agent.acceptance run phase0
```

只运行一项或几项：

```powershell
python -m m_agent.acceptance run phase0 --invariant INV-04
python -m m_agent.acceptance run phase0 --invariant INV-04 --invariant INV-09
```

### 运行 Semantic Full

Semantic Full 在 Gate 基础上增加经过审计的现有回归证据；它不是全仓 pytest：

```powershell
python -m m_agent.acceptance run phase0 --profile full
```

### 查看历史报告

```powershell
python -m m_agent.acceptance show <run_id>
python -m m_agent.acceptance show <run_id> --format json
```

CLI 退出码：

- `0`：所选不变量全部通过；
- `1`：存在失败、已知缺口或不完整结果；
- `2`：命令、目录或运行器错误。

## 5. UI

启动只监听本机的验收仪表盘：

```powershell
python -m m_agent.acceptance ui --host 127.0.0.1 --port 8788
```

命令启动时会直接打印可点击地址和 `Ctrl+C` 停止提示，例如：

```text
M-Agent 语义验收 UI 正在启动 / Acceptance UI is starting:
  http://127.0.0.1:8788/
按 Ctrl+C 停止服务 / Press Ctrl+C to stop.
```

UI 支持：

- 按“领域组 → 语义不变量 → Gate/Supporting 用例 → Trace”分层查看证据；
- 明确显示 Gate 的 14/20 和 Semantic Full 的 14/36；
- 全选、清空、组级三态选择，以及运行单项或单个领域组；
- Gate/Semantic Full 切换，并按当前 Profile 重新计算用例数量；
- 启动和取消运行；
- 分别使用绿色、琥珀色、红色展示通过、Known Gap 和新增失败；
- 按问题、状态和领域筛选结果；
- 查看验收原则、验收条件、风险、Known Gap 原因和规范化 Trace；
- 下载 Summary JSON、pytest JSON、JUnit、stdout 和 stderr；
- 恢复当前浏览器会话中的最近一次 run。

UI 是独立本地进程，不挂载到生产 Chat API。

## 6. 报告与 trace

每次运行会在仓库的 `.acceptance-runs/<run_id>/` 生成：

```text
summary.json        # 平台聚合结果
pytest-report.json  # 每个 allowlist node 的结构化结果和语义 trace
junit.xml           # CI 可读取的 JUnit 报告
stdout.log
stderr.log
tmp/                # 本次 pytest 独立 basetemp
```

新建的语义场景使用 `SemanticTrace` 记录稳定事件。Trace 会：

- 使用递增 `seq` 表达逻辑顺序；
- 将随机 transaction、stimulus、delegate ID 映射为 `tx#1`、`stim#1`、`dlg#1`；
- 移除时间戳、绝对路径和耗时等不稳定字段；
- 随 pytest case 结果写入 `pytest-report.json`。

这使后续 `think_life_v1` 和 `langgraph_v1` 可以比较同一套规范化证据，而不是比较随机
UUID、日志字符串或最终回复文本。

## 7. 执行安全边界

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

## 8. 添加或收紧测试

1. 先在 `src/m_agent/acceptance/catalog.py` 中明确：
   - 不变量；
   - 验收标准；
   - 风险；
   - Gate test；
   - Supporting tests。
2. 为新增不变量指定稳定 `group_id`、顺序和所属领域；缺失的行为场景放到：
   `tests/acceptance/invariants/test_runtime_semantics.py`。
3. 场景应使用 deterministic fake model/tool/store，不访问真实邮件、日程、搜索或 LLM。
4. 对顺序、归因和副作用次数使用 `SemanticTrace`，不要断言完整 prompt 或日志文本。
5. 当前实现不满足语义时，应让平台显示 `Known gap`；不要删除断言或用普通 skip 隐藏。
6. 执行：

```powershell
python -m pytest -q --maxfail=0 tests/acceptance
python -m m_agent.acceptance run phase0 --profile full
```

## 9. 下一阶段

当前平台完成了 Phase 0 的目录、Runner、CLI、UI 和首批确定性场景。进入 LangGraph PoC
前，还应继续补充：

- 修复当前六项 Known gap，并让现有确定性场景自然转绿；
- tool 前后、feedback 入队前、reply 后的故障注入；
- checkpoint restart、schema version 和 effect ledger；
- 同一场景在 `think_life_v1` 与 `langgraph_v1` 之间的 normalized trace 对比。

M0 的通过标准应是 Gate 全绿，而不是 pytest 进程退出码为零或最终回复文本相似。
