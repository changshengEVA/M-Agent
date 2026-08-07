# M-Agent 文档

这里是 `docs/` 的统一入口。活动文档严格区分项目愿景、目标架构、版本路线和当前实现；废弃规格与历史迁移材料保存在 [`archive/`](archive/README.md)，不作为当前配置或接口依据。

## 快速入口

- 使用项目：[仓库中文 README](../README-zh.md) · [English README](../README.md)
- 查看版本变更：[CHANGELOG](../CHANGELOG.md)
- 理解哲学动机：[哲学动机（中英）](philosophy-motivation.md)
- 理解愿景：[愿景与项目目标](vision-and-goals.zh-CN.md)
- 查看路线：[v0.2.0—v1.0.0 版本规划](roadmap-v0.2.0-v1.0.0.zh-CN.md) · [PDF](pdf/M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf)
- 理解目标架构：[认知运行时目标架构](architecture/cognitive-runtime-architecture.zh-CN.md)
- 调用服务：[Chat API 参考](chat_api/README.md) · [HTTP 请求样例](chat_api/testing.http)
- 理解 Runtime：[当前 Runtime 文档](runtime/README.md)
- 运行语义验收：[Runtime 语义验收平台](runtime/semantic-acceptance-platform.zh-CN.md)
- 设计思考层优化：[Thinking 单次 LLM 调用设计](architecture/thinking-single-call-design.zh-CN.md)
- 设计策略系统：[Strategy 系统级接入方案](architecture/strategy-single-llm-integration.zh-CN.md)
- 开发子系统插件：[中文指南](systems-plugin/README.zh-CN.md) · [English](systems-plugin/README.md)

## 目录

| 目录 | 内容 | 状态 |
| --- | --- | --- |
| [`architecture/`](architecture/README.md) | 目标认知架构与在途战术设计 | 当前目标/设计 |
| [`runtime/`](runtime/README.md) | LangGraph Runtime 与验收平台 | 当前实现 |
| [`chat_api/`](chat_api/README.md) | Chat API、请求样例和浏览器客户端 | 当前接口 |
| [`systems-plugin/`](systems-plugin/README.zh-CN.md) | WM / Episodic / Tools 插件指南（中英） | 当前开发指南 |
| [`development/`](development/project-structure.md) | 项目结构与常用命令 | 开发约定 |
| [`operations/`](operations/README.md) | 当前运维支持范围 | 当前边界 |
| [`pdf/`](pdf/README.md) | 对外分发的当前 PDF | 生成物 |
| [`archive/`](archive/README.md) | 废弃规格、迁移快照、旧部署和旧 PDF | 历史归档 |

## 当前文档约定

- 产品固定启动 `langgraph_v1`。
- 通用运行参数位于 `runtime.common`，图执行参数位于 `runtime.langgraph`。
- 执行层扩展上下文使用 `runtime_hooks`；Flush 响应使用 `runtime_flush`；事务查询使用 `get_transactions()`。
- 活动接口、配置和示例必须使用中性 Runtime 名称。
- 目标能力必须标注计划版本，不能写成当前事实。
- `archive/` 仅用于历史追溯；其中的配置、路径和状态快照不能用于当前部署。
