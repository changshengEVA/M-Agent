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
│  ├─ api/                 # FastAPI、ChatServiceRuntime、SSE
│  ├─ chat/                # ThreeLayerChatAgent（组装 layers + systems）
│  ├─ runtime/             # Think-life 产品 Runtime
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
│  └─ systems/             # wm / episodic / tools 子系统 YAML
├─ data/
└─ docs/
   ├─ architecture/        # 目标架构、语义基线、迁移与阶段计划
   ├─ runtime/             # 当前 Runtime 规格与验收平台
   ├─ chat_api/            # Chat API 参考与请求样例
   ├─ systems-plugin/      # 子系统插件指南（中英）
   ├─ development/         # 项目结构与开发流程
   ├─ operations/          # 部署与运维
   └─ pdf/                 # 对外分发的 PDF
```

约定：

- **三层栈**：`layers/perception`（`PerceptionInput` 组装）、`layers/thinking`、`layers/execution`。
- **当前产品 Runtime**：[`runtime/think-life-runtime-spec.zh-CN.md`](../runtime/think-life-runtime-spec.zh-CN.md)。
- **目标架构与迁移**：[`architecture/README.md`](../architecture/README.md)。
- **子系统插件开发**：[`systems-plugin/README.zh-CN.md`](../systems-plugin/README.zh-CN.md)。
- **工具能力** 只在 `src/m_agent/systems/tools/capabilities/` 实现；`chat/capabilities` 等旧路径已移除。
- **Episodic 默认实现**：见 [`systems-plugin/episodic.zh-CN.md`](../systems-plugin/episodic.zh-CN.md)。
- **完整 MemoryAgent 与 LoCoMo 评测**：由独立的 WorkspaceMem 仓库维护，不属于本仓库文档范围。

常用命令：

```bash
python -m m_agent.api.chat_api
pytest tests/
```
