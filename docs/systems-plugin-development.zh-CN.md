# 可插拔子系统开发指南（`m_agent.systems` + `config/systems`）

> 英文版：[systems-plugin-development.md](./systems-plugin-development.md)

本文档是 **总索引**（原则、架构、YAML 通用规则、交付与测试）。各子系统可插拔细节见 **[systems-plugin/](./systems-plugin/)** 六篇专题。

源码落点：`src/m_agent/systems/`。配置落点：`config/systems/`。顶层挂载：`config/agents/chat/chat_controller.yaml` 的 `systems:` 三行指针。

**不在本文范围：** 完整 MemoryAgent / MemoryCore / LoCoMo 评测 → [**WorkspaceMem**](F:/AI/WorkspaceMem)。

---

## 1. 边界与原则

| 原则 | 说明 |
|------|------|
| 插拔点唯一 | 可替换组件只通过 `m_agent.systems` 暴露；不要改 `layers/`、`chat/` 内部来“偷偷”接后端。 |
| 配置不内联 | `chat_controller.yaml` **只写** `systems.wm/episodic/tools` 的路径；参数全部在子系统 YAML。 |
| 骨架 vs 整合包 | 子系统根目录放 `protocols.py`、`system.py`、loader；具体实现放在 `<subsystem>/default/` 或你的 `<subsystem>/<包名>/`。 |
| 不动内置 default 做试验 | 私有实验用新子包 + 新 YAML；避免直接改 `default/` 导致合并冲突。 |
| Protocol 鸭子类型 | 实现类不必继承 Protocol；加载时用 `isinstance` + `@runtime_checkable` 校验。 |

---

## 2. 子系统专题（6 篇）

插拔在 **agent 构造时**生效（改 YAML 后重新加载）。各子系统的槽位、LLM 暴露面、交付步骤见下表 — **不要在本文件里翻长文**。

| 子系统 | 中文 | English |
|--------|------|---------|
| WM（工作记忆） | [systems-plugin/wm.zh-CN.md](./systems-plugin/wm.zh-CN.md) | [wm.md](./systems-plugin/wm.md) |
| Episodic（情景记忆 / RAG） | [systems-plugin/episodic.zh-CN.md](./systems-plugin/episodic.zh-CN.md) | [episodic.md](./systems-plugin/episodic.md) |
| Tools（工具套件） | [systems-plugin/tools.zh-CN.md](./systems-plugin/tools.zh-CN.md) | [tools.md](./systems-plugin/tools.md) |

```yaml
# config/agents/chat/chat_controller.yaml — 仅三行指针
systems:
  wm:       ../../systems/wm/default.yaml
  episodic: ../../systems/episodic/rag_default.yaml
  tools:    ../../systems/tools/default.yaml
```

五个对外 `path` 槽位：WM `writer/reader/display`、episodic `backend`、tools `registry`。`EpisodeRecorder` 为系统内置，见 episodic 专题。

---

## 3. 架构一览

### 3.1 概念

| 术语 | 含义 |
|------|------|
| **子系统** | `wm` / `episodic` / `tools` |
| **接入点** | 子系统 dataclass 内字段，如 `episodic.backend` |
| **整合包** | `episodic/default/`、`tools/default/capabilities/` 等一组实现 |
| **系统 YAML** | `config/systems/<子系统>/<变体>.yaml` |
| **Chat 指针** | `chat_controller.yaml` → 相对路径，相对 `config/agents/chat/` 解析 |

### 3.2 加载链路

```text
config/agents/chat/chat_controller.yaml
  systems:
    wm:       ../../systems/wm/default.yaml
    episodic: ../../systems/episodic/rag_default.yaml
    tools:    ../../systems/tools/default.yaml
        │
        ▼
load_systems_bundle_from_config()  →  SystemsBundle
        │
        ▼
ThreeLayerChatAgent
  ├─ ThinkingAgent  ← wm.reader, episode_note → 内置 recorder
  └─ ExecutionAgent ← tools.registry + episodic.backend（经 capability）
        │
        ▼
ThinkLifeRuntime / ThinkLifeLoop
  ├─ 调度 ThinkingAgent 做单步规划
  ├─ 调用 ExecutionAgent 的目标能力
  └─ 能力返回后通过 wm.writer 写入当前事务
```

`wm.display` 仍是可配置的扩展槽，但 Think-life 能力调用不向执行 prompt 注入它。

### 3.3 源码目录

```text
src/m_agent/systems/
├── loader.py, bundles.py
├── wm/          protocols.py, system.py, default/
├── episodic/    protocols.py, system.py, query_module.py, default/
└── tools/       base.py, registry.py, system.py, default/
```

### 3.4 配置目录（当前仓库）

```text
config/
├── agents/chat/          chat_controller.yaml, chat_model.yaml, runtime/
├── agents/email|schedule/
├── systems/
│   ├── wm/default.yaml
│   ├── episodic/rag_default.yaml
│   └── tools/default.yaml, capabilities/<tool>.yaml
├── prompts/examples/     示例（非对话栈必需）
├── integrations/         如 neo4j.yaml
└── users/                按用户生成；勿在仓库内手改他人目录
```

### 3.5 接入点索引

见 [§2 子系统专题](./systems-plugin/) 六篇文档。

---

## 4. YAML 规范

### 4.1 通用规则

- 顶层必须有 `system: wm | episodic | tools`，与文件用途一致，否则 loader 报 `SystemsConfigError`。
- 插件槽位使用 **字符串 path** 或 **映射 `{ path, kwargs }`**。
- `path` 形式：`pkg.module:Symbol` 或 `pkg.module.Symbol`（最后一段为属性名）。
- Loader 调用：`Symbol(**kwargs)`；无参工厂则 `Symbol()`。
- **环境变量：** loader **不会** 展开 `${VAR}`；请在部署侧注入或写死测试值。

### 4.2 `config/agents/chat/chat_controller.yaml`

**应包含：**

```yaml
model_config_path: "./chat_model.yaml"
runtime_prompt_config_path: "./runtime/chat_controller_runtime.yaml"
email_agent_config_path: "../email/gmail_email_agent.yaml"
schedule_agent_config_path: "../schedule/schedule_agent.yaml"

systems:
  wm:       ../../systems/wm/default.yaml
  episodic: ../../systems/episodic/rag_default.yaml
  tools:    ../../systems/tools/default.yaml
```

**不应包含：** `enabled_tools`、`tool_defaults`、`working_memory`、`plugins` 等与 `systems/*` 重复的块（旧用户副本可保留，loader 仍兼容一个版本）。

**切换子系统：** 只改 `systems.<name>` 指向的另一份 YAML。

### 4.3–4.5 各子系统 YAML 字段

详见专题文档（含字段表、kwargs、持久化路径）：

- WM → [systems-plugin/wm.zh-CN.md](./systems-plugin/wm.zh-CN.md)
- Episodic → [systems-plugin/episodic.zh-CN.md](./systems-plugin/episodic.zh-CN.md)
- Tools → [systems-plugin/tools.zh-CN.md](./systems-plugin/tools.zh-CN.md)

---

## 5. 交付新整合包（强制流程）

1. 选定子系统：`wm` | `episodic` | `tools`（一个 YAML 文件只对应一个）。
2. 阅读对应 `protocols.py` / `base.py`，实现全部必需方法。
3. **代码位置**
   - 仓库内：`src/m_agent/systems/<子系统>/<包名>/`（与 `default` 同级）。
   - 仓库外：独立 pip 包；确保运行环境能 `import` YAML 中的模块。
4. 在包 `__init__.py` 导出 YAML 会引用的类/工厂。
5. 复制最接近的 `config/systems/<子系统>/*.yaml` → `my_variant.yaml`，改 `path`/`kwargs`。
6. 在 `chat_controller.yaml` 的 `systems:` 中指向新 YAML。
7. **测试**（见第 8 节）通过后提 PR。
8. 更新本仓库文档仅当新增**官方**变体（可选）。

---

## 6. 子系统实现要求

详见专题文档（Protocol、数据流、示例路径）：

- [systems-plugin/wm.zh-CN.md](./systems-plugin/wm.zh-CN.md)
- [systems-plugin/episodic.zh-CN.md](./systems-plugin/episodic.zh-CN.md)
- [systems-plugin/tools.zh-CN.md](./systems-plugin/tools.zh-CN.md)

---

## 7. 子系统覆盖与优先级

`ThreeLayerChatAgent` 解析顺序（单槽可独立覆盖）：

1. 构造参数 `systems=SystemsBundle(...)`
2. 待迁移的 `plugins=`（`DeprecationWarning`）
3. YAML `systems:`
4. 待迁移 YAML 中的 `plugins:` / 扁平字段
5. 代码内置 default

**测试 / API 注入：**

```python
from m_agent.systems import SystemsBundle, load_episodic_system

bundle = SystemsBundle(
    episodic=load_episodic_system({"system": "episodic", "backend": {"path": "..."}}),
)
```

---

## 8. 测试与验收标准

### 8.1 必跑

```bash
pytest tests/systems/
pytest tests/chat/test_three_layer_plugins.py
```

### 8.2 推荐用例

| 目标 | 文件 |
|------|------|
| Protocol 形状 | `tests/systems/test_protocol_shapes.py` |
| 磁盘 YAML 可加载 | `tests/systems/test_system_yaml_loader.py` |
| RAG backend | `tests/systems/episodic/test_rag_backend.py` |
| `systems_override` | `tests/systems/test_runtime_systems_override.py` |
| 工具调用上限 | `tests/test_chat_controller_tool_limits.py` |

### 8.3 新 backend 最小单测

```python
from m_agent.systems.episodic.protocols import EpisodicMemoryBackend

def test_my_backend_is_protocol():
    assert isinstance(MyBackend(storage_dir=":memory:"), EpisodicMemoryBackend)
```

### 8.4 PR 自检清单

- [ ] 新 `path` 在目标 venv 可 `import`
- [ ] `kwargs` 与构造函数一致
- [ ] `isinstance(..., Protocol)` 通过
- [ ] 已添加/更新 `config/systems/.../my_variant.yaml`
- [ ] `query.enabled` 与 `tools.enabled` 中的 recall 工具一致
- [ ] 未修改 `default/` 除非明确要改官方行为
- [ ] `pytest tests/systems/` 通过

---

## 9. 对话栈消费关系

| 模块 | 使用 |
|------|------|
| `ThreeLayerChatAgent` | 组装 `SystemsBundle`、`ThinkingAgent` 与 `ExecutionAgent` |
| `ThinkLifeLoop` | 组织规划、目标能力调用、WM 写入与反馈刺激 |
| `ThinkingAgent` | 通过 `wm.reader` 读当前事务 WM；`episode_note` → 内置 recorder；**不**直接 recall |
| `ExecutionAgent` | 直接调用目标 capability；recall 仅经 capability → backend |

领域 Agent（`EmailAgent`、`ScheduleAgent`）仍在 `m_agent.agents`；仅通过 tools capability 适配器接入。

---

## 10. 常见错误

1. **path 拼写错误** — 启动即 `SystemsConfigError`，而非首条聊天失败。
2. **kwargs 不匹配 `__init__`** — 同上。
3. **关闭 query 却启用 shallow_recall** — 配置不一致。
4. **全局 `register_capability`** — 多租户/多配置互相污染。
5. **Capability 绕过 backend 读记忆** — 无法通过换 episodic YAML 切换实现。
6. **在 chat_controller 内联子系统参数** — 违反单一配置源，难维护。
7. **在 episodic YAML 配置 `recorder:`** — 非对外插拔点；应使用默认 `DefaultEpisodeRecorder`。

---

## 11. 旧配置结构迁移（一个版本）

本节仅描述 YAML 结构迁移，与运行模式无关。以下字段若仍出现在已有的 `config/users/*/chat.yaml` 中，loader 会翻译为虚拟 `SystemsBundle`（可能伴随 `DeprecationWarning`）：

- `plugins:`
- `enabled_tools` / `tool_defaults`
- `working_memory`
- `episode_query_enabled`

新模板 `config/agents/chat/chat_controller.yaml` 已仅使用 `systems:`。新用户脚手架会复制该模板。

---

## 12. 相关文档

- [项目结构](./project-structure.md)
- [对话流水线与 SSE](./m_agent_pipeline.md)
- [Chat API](./chat_api/README.md)
- [WorkspaceMem](F:/AI/WorkspaceMem) — MemoryAgent 与评测

维护说明：子系统插拔细节写在 `docs/systems-plugin/`（6 篇）；本文件与英文版同步维护通用章节；`src/m_agent/systems/README*.md` 与 `config/**/README.md` 仅短索引。
