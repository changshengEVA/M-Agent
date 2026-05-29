# WM 子系统 — 可插拔说明

> English: [wm.md](./wm.md) · 总索引：[systems-plugin-development.zh-CN.md](../systems-plugin-development.zh-CN.md)

## 职责

把本轮工具调用历史投影成 **进程内** 文本块，供思考层与执行层读取。WM **不跨轮持久化**，**无 recall 工具**；跨轮检索见 [episodic.zh-CN.md](./episodic.zh-CN.md)。

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
| `writer` | `WMWriter` | `DefaultWMWriter` | 执行后把 `tool_history` 写入 `wm_entries` |
| `reader` | `WMReader` | `DefaultWMReader` | WM 文本 → **思考层** plan / summarize |
| `display` | `WMDisplay` | `DefaultWMDisplay` | WM 文本 → **执行层** system prompt |
| `config` | `WorkingMemoryConfig` | 见 default.yaml | 非 `path`；loader 注入 writer/reader/display |

`config` 常用项：`enable`、`inject_max_entries`、`max_stored_entries`、`max_question_chars` 等。

## 暴露给 LLM

| 暴露面 | 层级 |
|--------|------|
| WM 文本块 | 思考层（`WMReader.render`） |
| WM 文本块 | 执行层（`WMDisplay.render`） |
| 无工具 | LLM 不能主动查 WM，只读 prompt 内已渲染片段 |

## 实现要点

- `WMWriter.write(entries, tool_history)` — 原地追加，遵守 `max_stored_entries`
- `WMReader.render(entries, *, language)` — 思考层注入
- `WMDisplay.render(entries, *, language)` — 执行层注入（默认同 reader tail-N）

**禁止：** 在 WM 包内直接调 episodic 或 Email/Schedule；跨层走 tools capability。

## 交付

1. 实现 `WMWriter` / `WMReader` / `WMDisplay`（见 `wm/protocols.py`）
2. 复制 `config/systems/wm/default.yaml` → `my_variant.yaml`
3. 改 `chat_controller.yaml` 的 `systems.wm`

```bash
pytest tests/systems/test_protocol_shapes.py tests/systems/test_system_yaml_loader.py
```
