# 当前 Runtime 文档

产品 Runtime 由单个 LangGraph 宿主提供，运行时标识固定为 `langgraph_v1`。共享 Transaction、Perception、Dispatch、Turn Support 与 Flush 能力位于中性命名空间，LangGraph 负责图执行和 checkpoint 恢复。

## 配置与协议

- 通用参数：`runtime.common`
- LangGraph 参数：`runtime.langgraph`
- 能力执行上下文：`runtime_hooks`
- Flush 结果字段：`runtime_flush`
- 事务查询：`get_transactions()`
- Stimulus Kernel（v0.3 / v0.3.1）：见 [stimulus-kernel.md](stimulus-kernel.md)
  - 公开入口：`runtime.ingest(observation)`
  - Chat / Feedback / Schedule：各自 Source Adapter → `ingest`（Adapter 写入 `stimulus_view`）
  - Ingress Freeze：验收 harness / Gateway typed helpers 亦经 Observation → `admit_observation`
  - `submit_user_message` 为 Chat Adapter 兼容薄封装
  - 池状态与处置拆分；Stimulus Trace 可按刺激 / 线程 / 去重键查询

持久化启动要求可用的 SQLite checkpointer 和兼容 schema；不满足条件时启动失败。Scene、Transaction、effect ledger、flush journal、stimulus trace 与 checkpoint 共同支持重试和重启恢复。

Final 版本还会在用户在线持久化目录仍含退役 Runtime 数据库时拒绝启动。必须先按退役计划完成单独授权的停服、SQLite Backup API 备份、审计与隔离；部署脚本不得把该数据库当作普通缓存直接删除。

## 语义验收

[Runtime 语义验收平台](semantic-acceptance-platform.zh-CN.md)维护 38 个 LangGraph 场景，覆盖 TX、SP、AT、robustness、matcher evaluation 与 PoC。

```powershell
python -m m_agent.acceptance contract run --runtime langgraph_v1 --all-layers
python scripts/run_runtime_migration_gate.py --rounds 3
python -m m_agent.lab.stimulus
```

旧实现规格、P1 差距矩阵和迁移过程已经归档，入口见 [`../archive/architecture/`](../archive/architecture/README.md)。归档内容只用于历史追溯。
