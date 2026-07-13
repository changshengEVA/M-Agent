# Tools 子系统 — 可插拔说明

> English: [tools.md](./tools.md) · 总索引：[systems-plugin-development.zh-CN.md](../systems-plugin-development.zh-CN.md)

## 职责

注册执行层 **LangChain 工具**（capability），管理白名单、默认参数与工具描述。Recall 类工具 **必须** 用 `context.get_episodic_backend()`，见 [episodic.zh-CN.md](./episodic.zh-CN.md)。

## 挂载与切换

| 项 | 值 |
|----|-----|
| Chat 指针 | `systems.tools` → `config/systems/tools/*.yaml` |
| 默认 | `default.yaml` + `runtime_descriptions.yaml` |
| 源码 | `src/m_agent/systems/tools/default/capabilities/` |

```yaml
systems:
  tools: ../../systems/tools/my_toolset.yaml
```

## 可插拔与配置

| YAML 字段 | 类型 | 作用 |
|-----------|------|------|
| `registry` | `path` → `ControllerCapabilityRegistry` | 注册 capability 工厂（**主要 path 槽位**） |
| `enabled` | 字符串列表 | 暴露给 LLM 的工具白名单 |
| `defaults` | 映射 | 每工具 kwargs；`__controller__.max_calls_per_turn`；`memory_recall.max_calls_per_turn` |
| `runtime_descriptions` / `_path` | 内联或路径 | 工具描述（zh/en 按 `prompt_language`） |

描述优先级：`runtime_descriptions` > legacy `chat_controller_runtime.yaml` 内 `tools.*.description`。

## 暴露给 LLM

| 暴露面 | 说明 |
|--------|------|
| `enabled` 内各工具 | `@tool`：recall、reply、email、schedule 等 |
| `runtime_descriptions` | capability 区块中的自然语言说明 |
| `defaults` 调用上限 | 单工具及 `memory_recall` 分组 |

## 实现要点

- 每个 capability = `ControllerCapabilitySpec(name=..., build_tool=...)`
- `build_tool(context, description)` 返回 LangChain `@tool`
- 必须走 `start_tool_call` / `finish_tool_call` / `check_tool_call_limits`

**扩展方式：**

| 方式 | 适用 |
|------|------|
| 改 `tools/default/` + `enabled` | 扩展官方工具集 |
| 新包 + `build_my_registry()` | 第三方（**推荐**） |

**禁止：** 全局 `register_capability()` 污染 default registry；recall 绕过 `get_episodic_backend()`。

## Think-life 参数化跳过声明（`THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG`）

Think-life 对部分工具**不走 param LLM**，而是把思考层本轮的 `instruction` 直接映射为 invoke 参数（见 `src/m_agent/runtime/think_life/scheduler/tool_runner.py`）。

新增或调整「单字符串 / 无参」类能力时，若应跳过 param LLM，必须在 **`THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG`** 中登记：

| 字段含义 | 说明 |
|----------|------|
| 键 | capability 名（与 `ControllerCapabilitySpec.name` 一致） |
| 值 | 思考层 `instruction` 写入的工具参数字段名；`None` 表示无参（如 `get_current_time`） |

当前登记示例：

| 工具 | `instruction` 映射字段 |
|------|------------------------|
| `get_current_time` | （无，`{}`） |
| `shallow_recall` | `question` |
| `deep_recall` | `question` |

`reply_to_user` 单独处理，不在此表中。其余工具（如 `email_send`、`schedule_create`、`schedule_query`、`schedule_delete`）仍走 param LLM 结构化填参。

## 交付

1. 实现 capability + registry 工厂
2. 复制 `config/systems/tools/default.yaml`
3. 改 `systems.tools` 指针

```bash
pytest tests/systems/ tests/test_chat_controller_tool_limits.py
```
