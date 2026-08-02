# M-Agent 文档

这里是 `docs/` 的统一入口。活动文档描述当前 LangGraph 单宿主实现；已经完成的旧运行时规格、差距矩阵和迁移过程保存在 [`architecture/archive/`](architecture/archive/README.md)，不作为当前配置或接口依据。

## 快速入口

- 使用项目：[仓库中文 README](../README-zh.md) · [English README](../README.md)
- 调用服务：[Chat API 参考](chat_api/README.md) · [HTTP 请求样例](chat_api/testing.http)
- 理解 Runtime：[当前 Runtime 文档](runtime/README.md)
- 运行语义验收：[Runtime 语义验收平台](runtime/semantic-acceptance-platform.zh-CN.md)
- 查看当前状态：[项目进度与设计](architecture/current-project-progress-and-design-plan.md)
- 理解领域边界：[总体设计架构](architecture/overall-design-architecture.md)
- 开发子系统插件：[中文指南](systems-plugin/README.zh-CN.md) · [English](systems-plugin/README.md)

## 目录

| 目录 | 内容 | 状态 |
| --- | --- | --- |
| [`architecture/`](architecture/README.md) | 当前总体架构、状态和历史归档入口 | 当前设计 |
| [`runtime/`](runtime/README.md) | LangGraph Runtime 与验收平台 | 当前实现 |
| [`chat_api/`](chat_api/README.md) | Chat API、请求样例和浏览器客户端 | 当前接口 |
| [`systems-plugin/`](systems-plugin/README.zh-CN.md) | WM / Episodic / Tools 插件指南（中英） | 当前开发指南 |
| [`development/`](development/project-structure.md) | 项目结构与 Git 工作流 | 开发约定 |
| [`operations/`](operations/deployment-server-zh.md) | 服务器部署与运维 | 运维指南 |
| [`pdf/`](pdf/README.md) | 对外分发的重点标注版 PDF | 生成物 |

## 当前文档约定

- 产品固定启动 `langgraph_v1`。
- 通用运行参数位于 `runtime.common`，图执行参数位于 `runtime.langgraph`。
- 执行层扩展上下文使用 `runtime_hooks`；Flush 响应使用 `runtime_flush`；事务查询使用 `get_transactions()`。
- 活动接口、配置和示例必须使用中性 Runtime 名称。
- `architecture/archive/` 仅用于历史追溯；其中的配置、路径和状态快照不能用于当前部署。
