# 架构与迁移文档

本目录描述目标语义和迁移路线，不代表当前 `think_life_v1` 已经实现全部内容。

建议按以下顺序阅读：

1. [总体设计架构](overall-design-architecture.md)  
   最稳定的领域设计总线，定义 Transaction、Scene、刺激池和归因的职责边界。
2. [Runtime 迁移语义测试规格](runtime-migration-semantic-test-spec.zh-CN.md)  
   把目标语义展开成可执行验收场景和详细约束。
3. [当前项目进度与设计计划](current-project-progress-and-design-plan.md)  
   记录当前完成度、缺口、P0～P8 顺序和下一步；属于时间敏感文档。
4. [LangGraph Runtime 迁移计划](langgraph-runtime-migration-plan.zh-CN.md)  
   候选实现路径；是否采用 LangGraph 仍由 PoC 结果决定。
5. [生产 Runtime 层实施计划](production-runtime-layer-plan.zh-CN.md)  
   P8 之后：RuntimeHost、LangGraphRuntime 生产接线、Chat API 灰度与旧 loop 收缩（时间敏感）。

当前实现行为见 [`../runtime/`](../runtime/README.md)。

状态提示：截至 2026-07-30，P0～P8 与 acceptance 双 Runtime 矩阵已完成；
生产 Runtime 接线见第 5 份文档；完整阶段 Gate 以第 3 份文档为准。
