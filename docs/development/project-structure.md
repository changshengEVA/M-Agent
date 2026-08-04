# Project Structure

设计原则：

1. 产品源码收敛在 `src/m_agent/`。
2. 运维和一次性工具入口放在 `scripts/`，桌面端、Web 端和移动端放在 `tools/`。
3. 测试、示例、实验和正式源码分离。
4. 运行路径统一由 `m_agent.paths` / `m_agent.config_paths` 管理。
5. `docs/` 按主题组织；当前实现与未来目标设计分开维护。

```text
M-Agent/
├─ src/m_agent/
│  ├─ acceptance/          # Runtime 语义验收平台
│  ├─ agents/              # 领域 Agent
│  ├─ layers/              # perception / thinking / execution 三层
│  ├─ api/                 # FastAPI、ChatServiceRuntime、SSE、图片上传/Caption
│  ├─ chat/                # ThreeLayerChatAgent（组装 layers + systems）
│  ├─ runtime/             # 共享运行内核、LangGraph 与 RuntimeHost
│  ├─ schedule/            # 日程领域与服务
│  ├─ integrations/        # 外部服务集成
│  ├─ systems/             # wm / episodic / tools（协议 + loader）
│  │  ├─ wm/               # protocols.py, system.py, default/
│  │  ├─ episodic/         # protocols.py, system.py, default/（RAG 等）
│  │  └─ tools/            # base.py, registry.py, default/（capabilities）
│  ├─ load_model/          # Chat / RAG 用 embedding、LLM 调用
│  └─ utils/
├─ scripts/                # 非 eval 的运维/工具脚本
├─ tests/
├─ tools/                  # M-Agent UI / Desktop / App
├─ config/
│  ├─ agents/chat/         # chat_controller.yaml、chat_model.yaml
│  ├─ integrations/        # 外部集成配置
│  ├─ systems/             # wm / episodic / tools 子系统 YAML
│  └─ users/               # 用户配置说明；运行数据不提交
├─ data/
└─ docs/
   ├─ README.md            # 文档统一入口
   ├─ vision-and-goals.zh-CN.md
   │                        # 项目愿景、定位与边界
   ├─ roadmap-v0.2.0-v1.0.0.zh-CN.md
   │                        # 当前版本路线与放行条件
   ├─ architecture/        # 目标认知架构与仍在推进的战术设计
   │  ├─ cognitive-runtime-architecture.zh-CN.md
   │  │                    # Stimulus-to-Cognition 目标架构（不是 v0.2 现状）
   │  ├─ thinking-single-call-design.zh-CN.md
   │  └─ strategy-single-llm-integration.zh-CN.md
   ├─ runtime/             # LangGraph Runtime 与验收平台
   ├─ chat_api/            # Chat API 参考与请求样例
   ├─ systems-plugin/      # 子系统插件指南（中英）
   ├─ development/         # 当前项目结构与开发约定
   ├─ operations/          # 当前运维支持范围；尚不承诺正式生产部署
   ├─ pdf/                 # 由活动 Markdown 生成的当前分发 PDF
   └─ archive/             # 不再定义当前行为的历史材料
      ├─ architecture/     # ThinkLife、迁移规格、旧领域架构和状态快照
      ├─ operations/       # 旧个人服务器部署快照
      ├─ development/      # 已退出公共约定的个人工作流
      ├─ assets/           # 不能代表当前架构的旧图像
      └─ pdf/              # 已被当前路线图取代的旧 PDF
```

约定：

- **三层栈**：`layers/perception`（`PerceptionInput` 组装）、`layers/thinking`、`layers/execution`。
- **愿景与产品边界**：[`vision-and-goals.zh-CN.md`](../vision-and-goals.zh-CN.md)。
- **版本路线**：[`roadmap-v0.2.0-v1.0.0.zh-CN.md`](../roadmap-v0.2.0-v1.0.0.zh-CN.md)。
- **目标认知架构**：[`architecture/cognitive-runtime-architecture.zh-CN.md`](../architecture/cognitive-runtime-architecture.zh-CN.md)，描述未来目标，不得写成当前已实现能力。
- **当前产品 Runtime**：[`runtime/README.md`](../runtime/README.md)。
- **活动架构设计**：[`architecture/README.md`](../architecture/README.md)。
- **历史归档**：[`archive/README.md`](../archive/README.md)；归档内容不得作为当前接口、配置或部署依据。
- **运维边界**：[`operations/README.md`](../operations/README.md)；旧部署快照位于 `archive/operations/`。
- **PDF**：`pdf/` 只放当前可分发生成物，过时 PDF 放在 `archive/pdf/`；Markdown 始终是权威源。
- **子系统插件开发**：[`systems-plugin/README.zh-CN.md`](../systems-plugin/README.zh-CN.md)。
- **工具能力** 只在 `src/m_agent/systems/tools/default/capabilities/` 实现；`chat/capabilities` 等旧路径已移除。
- **Episodic 默认实现**：见 [`systems-plugin/episodic.zh-CN.md`](../systems-plugin/episodic.zh-CN.md)。
- **完整 MemoryAgent 与 LoCoMo 评测**：由独立的 WorkspaceMem 仓库维护，不属于本仓库文档范围。

常用命令：

```bash
python -m m_agent.api.chat_api
pytest tests/
```
