# 架构文档

活动架构文档描述当前 LangGraph 单宿主实现。已经完成阶段的规格、语义基线、差距矩阵和迁移计划保存在 [`archive/`](archive/README.md)，仅用于历史追溯。

建议按以下顺序阅读：

1. [总体设计架构](overall-design-architecture.md)
   定义 Transaction、Scene、刺激池、归因和三层 Agent 的稳定职责边界。
2. [当前项目进度与设计](current-project-progress-and-design-plan.md)
   说明当前 RuntimeHost、LangGraph、checkpoint、flush journal 和验收状态。
3. [当前 Runtime 文档](../runtime/README.md)
   说明配置、协议名称与运行 Gate。
4. [Runtime 语义验收平台](../runtime/semantic-acceptance-platform.zh-CN.md)
   说明 38 个 LangGraph 场景、CLI 和 Web UI。
5. [历史归档](archive/README.md)
   保存已完成阶段的原始规格和计划，不作为当前部署依据。

当前产品只构造 `langgraph_v1`。通用配置位于 `runtime.common`，图配置位于 `runtime.langgraph`；能力上下文、Flush 和事务查询分别使用 `runtime_hooks`、`runtime_flush` 与 `get_transactions()`。
