# WM 子系统——可插拔说明

> English: [wm.md](./wm.md) · 总索引：[README.zh-CN.md](./README.zh-CN.md)

## 职责

WM 把 capability 调用历史投影为当前 runtime transaction 上的紧凑条目。
LangGraph turn graph 随 transaction 持久化 `TransactionRecord.wm_entries`，并在
后续规划轮次中渲染这些条目。

WM 是 transaction 范围的上下文，不提供跨对话检索。可检索的对话历史由
[Episodic 子系统](./episodic.zh-CN.md)提供。

## Runtime 数据流

```text
LangGraph turn graph
  → WMReader.render(transaction.wm_entries)
  → ThinkingAgent decision
  → ExecutionAgent.invoke_tool_direct(..., runtime_hooks=...)
  → WMWriter.write(transaction.wm_entries, tool_history)
  → transaction store commit
```

`runtime_hooks` 为 capability 调用携带 delegate 回调和持久化标识。WM 只接收执行结果
`tool_history`，不保存回调或与 engine 实现绑定的对象。

## 挂载与配置

| 项 | 值 |
|----|-----|
| Chat 指针 | `systems.wm` → `config/systems/wm/*.yaml` |
| 默认 | `config/systems/wm/default.yaml` |
| 源码 | `src/m_agent/systems/wm/` |

```yaml
systems:
  wm: ../../systems/wm/my_variant.yaml
```

## 槽位

| YAML 字段 | Protocol | 默认 | 作用 |
|-----------|----------|------|------|
| `writer` | `WMWriter` | `DefaultWMWriter` | 把投影后的 `tool_history` 追加到当前 transaction |
| `reader` | `WMReader` | `DefaultWMReader` | 为规划渲染 transaction 条目 |
| `display` | `WMDisplay` | `DefaultWMDisplay` | 面向诊断与自定义集成的可选渲染器 |
| `config` | `WorkingMemoryConfig` | 见 `default.yaml` | reader/writer/display 的共享参数；不是 `path` 槽位 |

常用 `config` 字段包括 `enable`、`inject_max_entries`、`max_stored_entries` 和各类
结果截断上限。

## Protocol 要求

- `WMWriter.write(entries, tool_history)` 原地修改 `entries`，并遵守
  `max_stored_entries`。
- `WMReader.render(entries, *, language, task_progress=None)` 返回规划 prompt 文本块。
- `WMDisplay.render(entries, *, language, task_progress=None)` 提供可选视图，不改变
  capability 执行。

WM 代码不得直接调用 episodic backend 或领域 agent。跨子系统操作应通过已声明的
capability 及其 context 完成。

## 交付与验证

1. 按 `wm/protocols.py` 实现 `WMWriter`、`WMReader` 和 `WMDisplay`。
2. 复制 `config/systems/wm/default.yaml` 为新变体并更新路径。
3. 让 `systems.wm` 指向新变体。

```bash
pytest tests/systems/test_protocol_shapes.py tests/systems/test_system_yaml_loader.py
pytest tests/runtime/test_langgraph_turn_loop.py
```
