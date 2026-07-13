# WM 子系统 — 可插拔说明

> English: [wm.md](./wm.md) · 总索引：[systems-plugin-development.zh-CN.md](../systems-plugin-development.zh-CN.md)

## 职责

把能力调用历史投影成 **进程内** 文本块，供 Think-life 规划读取。WM **不在进程重启后保留**，**无 recall 工具**；持久化检索见 [episodic.zh-CN.md](./episodic.zh-CN.md)。

## 挂载与切换

| 项 | 值 |
|----|-----|
| Chat 指针 | `systems.wm` → `config/systems/wm/*.yaml` |
| 默认 | `config/systems/wm/default.yaml` |
| 源码 | `src/m_agent/systems/wm/` |

```yaml
# config/agents/chat/chat_controller.yaml
systems:
  wm: ../../systems/wm/my_variant.yaml
```

## 可插拔槽位

| YAML 字段 | Protocol | 默认 | 作用 |
|-----------|----------|------|------|
| `writer` | `WMWriter` | `DefaultWMWriter` | 能力调用后把 `tool_history` 写入当前事务的 `wm_entries` |
| `reader` | `WMReader` | `DefaultWMReader` | 为下一次 **Think-life 规划**渲染 `wm_entries` |
| `display` | `WMDisplay` | `DefaultWMDisplay` | 供扩展集成使用的可选渲染器；Think-life 执行路径不注入 WM system prompt |
| `config` | `WorkingMemoryConfig` | 见 default.yaml | 非 `path`；loader 注入 writer/reader/display |

`config` 常用项：`enable`、`inject_max_entries`、`max_stored_entries`、`max_question_chars` 等。

## 暴露给 LLM

| 暴露面 | 层级 |
|--------|------|
| WM 文本块 | Think-life 规划（`WMReader.render`） |
| 能力调用结果 | 调度器通过 `WMWriter.write` 写入 `tool_history` |
| `WMDisplay` | 可供自定义集成使用；Think-life 能力调用不消费它 |
| 无工具 | LLM 不能主动查 WM，只读 prompt 内已渲染片段 |

## 实现要点

- `WMWriter.write(entries, tool_history)` — 原地追加到当前事务，遵守 `max_stored_entries`
- `WMReader.render(entries, *, language)` — 把事务的近期条目注入规划
- `WMDisplay.render(entries, *, language)` — 提供可选文本渲染，不改变 Think-life 执行路径

**禁止：** 在 WM 包内直接调 episodic 或 Email/Schedule；跨层走 tools capability。

## 交付

1. 实现 `WMWriter` / `WMReader` / `WMDisplay`（见 `wm/protocols.py`）
2. 复制 `config/systems/wm/default.yaml` → `my_variant.yaml`
3. 改 `chat_controller.yaml` 的 `systems.wm`

```bash
pytest tests/systems/test_protocol_shapes.py tests/systems/test_system_yaml_loader.py
```
