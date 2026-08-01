# M-Agent 文档

这里是 `docs/` 的唯一入口。文档按用途分组，明确区分“当前已经实现的行为”和
“后续迁移的目标语义”，避免把设计计划误读成现状。

## 快速入口

- 使用项目：[仓库中文 README](../README-zh.md) · [English README](../README.md)
- 调用服务：[Chat API 参考](chat_api/README.md) · [HTTP 请求样例](chat_api/testing.http)
- 理解当前 Runtime：[Think-life 当前实现规格](runtime/think-life-runtime-spec.zh-CN.md)
- 查看未来设计：[架构与迁移文档索引](architecture/README.md)
- 运行语义验收：[Runtime 语义验收平台](runtime/semantic-acceptance-platform.zh-CN.md)
  （当前 ThinkLife：35 Executable / 9 Passed / 26 Registered Known Gaps）
- 查看当前进度：[项目进度与设计计划](architecture/current-project-progress-and-design-plan.md)
- 查看生产接线计划：[生产 Runtime 层实施计划](architecture/production-runtime-layer-plan.zh-CN.md)（P8 后 R1～R5）
- 验证 P2 TX Foundation：`python -m pytest -q -m p2_foundation`
- 追溯 pre-P2 基线：[ThinkLife P1 Known Gap 历史矩阵](runtime/think-life-p1-gap-matrix.zh-CN.md)
- 开发子系统插件：[中文指南](systems-plugin/README.zh-CN.md) · [English](systems-plugin/README.md)

## 目录

| 目录 | 内容 | 状态 |
| --- | --- | --- |
| [`architecture/`](architecture/README.md) | 目标架构、语义测试基线、进度计划、LangGraph 迁移方案 | 设计与规划 |
| [`runtime/`](runtime/README.md) | 当前 Think-life 行为和验收平台使用方法 | 当前实现 |
| [`chat_api/`](chat_api/README.md) | Chat API 参考、请求集合、浏览器客户端示例 | 当前接口 |
| [`systems-plugin/`](systems-plugin/README.zh-CN.md) | WM / Episodic / Tools 插件指南（中英） | 当前开发指南 |
| [`development/`](development/project-structure.md) | 项目结构与 Git 工作流 | 开发约定 |
| [`operations/`](operations/deployment-server-zh.md) | 服务器部署与运维 | 运维指南 |
| [`pdf/`](pdf/README.md) | 对外分发的重点标注版 PDF | 生成物 |

## 阅读约定

- 调试现有代码时，以 `runtime/` 和 `chat_api/` 为准。
- 评审迁移目标时，以 `architecture/overall-design-architecture.md` 和
  `architecture/runtime-migration-semantic-test-spec.zh-CN.md` 为准。
- `architecture/current-project-progress-and-design-plan.md` 是时间敏感的状态快照，
  阶段变化后需要同步更新。
- P1 差距矩阵是 2026-07-28 的历史基线；当前 gap 状态以 scenario catalog、最新结构化
  运行报告和 P2 Foundation Gate 为准。
- 一次性分析、旧草案和无引用截图不再保存在 `docs/`；需要追溯时使用 Git 历史。

