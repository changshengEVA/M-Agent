# 可插拔子系统开发指南（`m_agent.systems` + `config/systems`）

> English: [README.md](./README.md)

本文是三个可插拔子系统的总索引：工作记忆（WM）、当前的 episodic 工具记忆槽位和
工具。内容包括通用规则、生产运行时边界、配置、交付与验证。

- 源码：`src/m_agent/systems/`
- 配置：`config/systems/`
- Chat 挂载点：`config/agents/chat/chat_controller.yaml` → `systems:`

完整的 MemoryAgent / MemoryCore / LoCoMo 评测由独立的 WorkspaceMem 仓库维护，
不在本文范围内。

## 当前能力与路线图边界

子系统名称描述的是扩展槽位，不能据此认为完整的认知运行时路线图已经实现。

| 领域 | 当前状态 | 计划接入阶段 |
|------|----------|--------------|
| Episodic 子系统 | `SimpleRagEpisodicBackend` 属于**工具记忆**：模型显式调用 recall capability，检索已物化的对话。它不是由 Runtime 管理的系统记忆。 | v0.5 接入 System Memory SPI 并进行 Shadow 评估；v0.6 在满足放行条件后，由 Context Compiler 自动选择并进入认知主链。 |
| Strategy | 当前子系统 bundle 没有挂载生产可用的 `StrategySystem`。现有设计文档属于路线图材料，不代表当前能力。 | v0.6 仅 Shadow 匹配；v0.7 可选注入；v0.8 只有在公开评测达标后才成为默认候选。 |

v0.2 的重要边界见 Episodic 专题：本地 backend 对 `deep_recall` 显式返回“不支持”；
Runtime 已提交的 `episode_note` 会经过带 Journal 的 Scene→Dialogue Flush 链路持久化，
但它还不是类型化的系统记忆，也不会自动注入 Context。

> **安全提示：**默认 tools bundle 只启用 `read` / `emit` 类能力。日程写入和 Gmail
> 发送必须显式选择 `chat_controller_external_writes.yaml`。这只是能力启用边界，
> 不等于交互式用户审批；受控自主策略仍属于后续路线图。

---

## 1. 规则

| 规则 | 说明 |
|------|------|
| 子系统入口唯一 | 可替换组件通过 `m_agent.systems` 暴露；调用方不进入具体实现包。 |
| Chat 层只放路径 | `chat_controller.yaml` 保存子系统 YAML 路径；实现参数放在对应子系统文件内。 |
| Protocol 与实现分离 | 子系统在根目录声明可运行时检查的 Protocol，实现放在 `default/` 或其他包中。 |
| 内置实现稳定 | 试验实现应使用新包和新 YAML，不直接修改 `default/`。 |
| 鸭子类型 | 实现不必继承 Protocol；加载时通过 `isinstance` 校验所需形状。 |

---

## 2. 子系统专题

子系统在 Chat agent 构造时加载。修改子系统 YAML 后需要重新加载 agent。

| 子系统 | 中文 | English |
|--------|------|---------|
| WM | [wm.zh-CN.md](./wm.zh-CN.md) | [wm.md](./wm.md) |
| Episodic | [episodic.zh-CN.md](./episodic.zh-CN.md) | [episodic.md](./episodic.md) |
| Tools | [tools.zh-CN.md](./tools.zh-CN.md) | [tools.md](./tools.md) |

```yaml
systems:
  wm:       ../../systems/wm/default.yaml
  episodic: ../../systems/episodic/rag_default.yaml
  tools:    ../../systems/tools/default.yaml
```

五个公开 `path` 槽位是 WM `writer` / `reader` / `display`、episodic
`backend` 和 tools `registry`。`EpisodeRecorder` 属于 episodic 集成内部实现。

---

## 3. 生产运行时边界

产品只有一个 runtime host。`create_runtime_host()` 构造 `LangGraphRuntime`；
该实现满足中性的 `RuntimeHost` Protocol，并持久化 engine id `langgraph_v1`。

```text
ChatServiceRuntime
  → create_runtime_host()
  → RuntimeHost (LangGraphRuntime)
      ├─ runtime transaction / perception / dispatch 内核
      ├─ LangGraph transaction graph 与 turn graph
      ├─ RuntimeFlushOrchestrator
      └─ ThreeLayerChatAgent
          ├─ ThinkingAgent ← WM reader
          └─ ExecutionAgent ← tools registry + episodic backend
```

| 契约 | 当前位置 | 用途 |
|------|----------|------|
| `RuntimeHost` | `m_agent.runtime.host` | 面向产品的 thread、transaction、Scene、schedule、flush、health 与 shutdown 操作 |
| `LangGraphRuntime` | `m_agent.runtime.langgraph.runtime` | `RuntimeHost` 的生产实现 |
| `RuntimeConfig` | `m_agent.runtime.config` | 中性的 runtime 与 scheduler 配置 |
| `runtime_hooks` | `ExecutionAgent.invoke_tool_direct` | 一次 delegate 调用所需的回调与持久化标识 |

执行层把 `runtime_hooks` 复制到
`ControllerCapabilityContext.controller_state["runtime"]`。Capability 只读取自己需要的
hook，使子系统代码不依赖 graph 实现，并让 reply、schedule、Scene 与 effect 处理共享
同一个中性协议。

---

## 4. 配置

### 子系统 YAML

- 顶层 `system` 只能是 `wm`、`episodic` 或 `tools`。
- 可替换槽位使用字符串 `path` 或 `{ path, kwargs }`。
- 路径格式为 `pkg.module:Symbol` 或 `pkg.module.Symbol`。
- Loader 调用 `Symbol(**kwargs)`，且不展开 `${ENV}`。

### Chat controller

Chat controller 指向三个子系统文件，并且只包含当前 runtime 分区：

```yaml
systems:
  wm:       ../../systems/wm/default.yaml
  episodic: ../../systems/episodic/rag_default.yaml
  tools:    ../../systems/tools/default.yaml

runtime:
  common:
    scene_context_max_entries: 40
    scene_persist_jsonl: true
  langgraph:
    turn_loop: true
    delegate_executor: execution_agent
```

测试与嵌入式应用可显式传入 `systems=SystemsBundle(...)`。正常产品启动读取
`systems:` 路径。

---

## 5. 源码与配置布局

```text
src/m_agent/systems/
├── loader.py, bundles.py
├── wm/          protocols.py, system.py, default/
├── episodic/    protocols.py, system.py, query_module.py, default/
└── tools/       base.py, registry.py, manifest.py, system.py, default/

config/systems/
├── wm/default.yaml
├── episodic/rag_default.yaml
└── tools/default.yaml, capabilities/<tool>.yaml
```

---

## 6. 交付实现

1. 选择 `wm`、`episodic` 或 `tools`。
2. 阅读对应 Protocol 或基础类型，实现全部必需方法。
3. 从可导入的包中导出类或工厂。
4. 复制最接近的子系统 YAML 为新变体，更新 `path` / `kwargs`。
5. 让 `chat_controller.yaml` 指向新变体。
6. 增加 Protocol、YAML 加载、行为与 runtime 边界测试。
7. 运行下方验证命令。

---

## 7. 验证

```bash
pytest tests/systems/
pytest tests/runtime/test_runtime_host.py tests/runtime/test_chat_api_runtime_host.py
pytest tests/runtime/test_langgraph_turn_loop.py
```

常用聚焦测试：

| 关注点 | 测试 |
|--------|------|
| Protocol 形状 | `tests/systems/test_protocol_shapes.py` |
| 磁盘 YAML 加载 | `tests/systems/test_system_yaml_loader.py` |
| RAG backend | `tests/systems/episodic/test_rag_backend.py` |
| Runtime systems 注入 | `tests/systems/test_runtime_systems_override.py` |
| 工具参数路由 | `tests/runtime/test_turn_support_tool_args.py` |
| 工具调用上限 | `tests/test_chat_controller_tool_limits.py` |

---

## 8. 评审清单

- [ ] 新 `path` 可在目标环境导入。
- [ ] `kwargs` 与构造函数一致。
- [ ] `isinstance(instance, Protocol)` 通过。
- [ ] 子系统 YAML 存在且可加载。
- [ ] Recall capability 与 `episodic.query.enabled`、`tools.enabled` 一致。
- [ ] Capability 通过 `ControllerCapabilityContext` 获取依赖。
- [ ] 需要 runtime 信息的 capability 通过 `controller_state["runtime"]` 使用 `runtime_hooks`。
- [ ] 聚焦测试与 `pytest tests/systems/` 通过。

---

## 9. 常见错误

常见问题包括：dotted path 拼写错误、构造参数不匹配、只在一个子系统启用 recall、
修改进程级全局 registry、绕过 episodic backend、在 chat controller 内联子系统参数，
或把 episodic `recorder` 当成公开槽位配置。

---

## 10. 相关文档

- [项目结构](../development/project-structure.md)
- [Chat API](../chat_api/README.md)

契约、路径或验证命令变化时，应同步更新每组中英文文档。
