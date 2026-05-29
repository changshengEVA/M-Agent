# Project Structure

设计原则：

1. 源码收敛在 `src/m_agent/`。
2. 可执行入口在 `scripts/`（chat API、工具脚本）；**LoCoMo / 记忆评测** 在 [**WorkspaceMem**](F:/AI/WorkspaceMem)。
3. 测试、示例、实验目录与正式源码分离。
4. 路径统一由 `m_agent.paths` / `m_agent.config_paths` 管理。

```text
M-Agent/
├─ src/m_agent/
│  ├─ agents/              # 领域 Agent：Email / Schedule
│  ├─ layers/              # perception / thinking / execution 三层
│  ├─ api/                 # FastAPI、ChatServiceRuntime、SSE
│  ├─ chat/                # ThreeLayerChatAgent（组装 layers + systems）
│  ├─ systems/             # wm / episodic / tools（协议 + loader）
│  │  ├─ wm/               # protocols.py, system.py, default/
│  │  ├─ episodic/         # protocols.py, system.py, default/（RAG 等）
│  │  └─ tools/            # base.py, registry.py, default/（capabilities）
│  ├─ load_model/          # Chat / RAG 用 embedding、LLM 调用
│  └─ utils/
├─ scripts/                # 非 eval 的运维/工具脚本
├─ tests/
├─ config/
│  ├─ agents/chat/         # chat_controller.yaml、chat_model.yaml
│  └─ systems/             # wm / episodic / tools 子系统 YAML
├─ data/
└─ docs/
```

约定：

- **三层栈**：`layers/perception`（`PerceptionInput` 组装）、`layers/thinking`、`layers/execution`。
- **子系统插件开发**（详细）：[`docs/systems-plugin-development.zh-CN.md`](systems-plugin-development.zh-CN.md)
- **工具能力** 只在 `src/m_agent/systems/tools/capabilities/` 实现；`chat/capabilities` 等旧路径已移除。
- **Episodic 默认**：`config/systems/episodic/rag_default.yaml` → `SimpleRagEpisodicBackend`；Chat 运行时按用户写入 `data/memory/chat-api/<user>/episodic/`（RAG）与 `dialogues/`（flush 归档），详见 [`systems-plugin/episodic.zh-CN.md`](systems-plugin/episodic.zh-CN.md)；各子系统插拔见 [`systems-plugin/`](systems-plugin/)。
- **完整 MemoryAgent**：见 `F:/AI/WorkspaceMem`（`workspace_mem` 包）。

常用命令：

```bash
python -m m_agent.api.chat_api
pytest tests/
```
