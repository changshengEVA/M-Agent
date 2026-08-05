# 架构文档

本目录只保留当前目标架构和仍在推进的战术设计。当前实现事实由 Runtime 文档与可执行验收定义；未来目标不能被描述成 v0.2 已有能力。

建议按以下顺序阅读：

1. [愿景与项目目标](../vision-and-goals.zh-CN.md)：定义项目为何存在、技术定位与产品价值。
2. [认知运行时目标架构](cognitive-runtime-architecture.zh-CN.md)：定义 Stimulus-to-Cognition 主链、系统边界和目标领域对象。
3. [v0.2.0—v1.0.0 路线图](../roadmap-v0.2.0-v1.0.0.zh-CN.md)：定义每个版本的主题、目标、核心交付和放行条件。
4. [当前 Runtime 文档](../runtime/README.md)：说明 v0.2 当前 LangGraph 单宿主、配置和运行 Gate。
5. [Runtime 语义验收平台](../runtime/semantic-acceptance-platform.zh-CN.md)：说明当前可执行语义场景。

## 已实现的战术设计

- [Thinking 单次 LLM 调用设计](thinking-single-call-design.zh-CN.md)：已在 v0.2.1 实现并验收。

## 未来设计

- [Strategy 系统级接入方案](strategy-single-llm-integration.zh-CN.md)：未来设计；v0.6 Shadow、v0.7 Opt-in、v0.8 评测达标后才可成为默认候选。

## 历史材料

旧 ThinkLife 规格、LangGraph 迁移过程、旧总体架构、时间点状态快照和旧 PDF 已统一移入 [`../archive/`](../archive/README.md)。归档内容不定义当前行为。

当前产品只构造 `langgraph_v1`。通用配置位于 `runtime.common`，图配置位于 `runtime.langgraph`；能力上下文、Flush 和事务查询分别使用 `runtime_hooks`、`runtime_flush` 与 `get_transactions()`。
