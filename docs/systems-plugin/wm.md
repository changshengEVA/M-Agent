# WM Subsystem — Plug-in Guide

> 中文版：[wm.zh-CN.md](./wm.zh-CN.md) · Index: [README.md](./README.md)

## Role

WM projects capability-call history into compact entries on the active runtime
transaction. The LangGraph turn graph persists `TransactionRecord.wm_entries`
with the transaction and renders those entries into later planning turns.

WM is transaction-scoped context, not cross-conversation recall. Use the
[episodic subsystem](./episodic.md) for searchable dialogue history.

## Runtime flow

```text
LangGraph turn graph
  → WMReader.render(transaction.wm_entries)
  → ThinkingAgent decision
  → ExecutionAgent.invoke_tool_direct(..., runtime_hooks=...)
  → WMWriter.write(transaction.wm_entries, tool_history)
  → transaction store commit
```

`runtime_hooks` carries delegate callbacks and durable identifiers for the
capability invocation. WM receives the resulting `tool_history`; it does not
store callbacks or engine-specific objects.

## Mount and configuration

| Item | Value |
|------|-------|
| Chat pointer | `systems.wm` → `config/systems/wm/*.yaml` |
| Default | `config/systems/wm/default.yaml` |
| Code | `src/m_agent/systems/wm/` |

```yaml
systems:
  wm: ../../systems/wm/my_variant.yaml
```

## Slots

| YAML field | Protocol | Default | Role |
|------------|----------|---------|------|
| `writer` | `WMWriter` | `DefaultWMWriter` | Append projected `tool_history` to the active transaction |
| `reader` | `WMReader` | `DefaultWMReader` | Render the transaction's entries for planning |
| `display` | `WMDisplay` | `DefaultWMDisplay` | Optional renderer for diagnostics and custom integrations |
| `config` | `WorkingMemoryConfig` | See `default.yaml` | Shared reader/writer/display parameters; not a `path` slot |

Common `config` fields include `enable`, `inject_max_entries`,
`max_stored_entries`, and per-result truncation limits.

## Protocol requirements

- `WMWriter.write(entries, tool_history)` mutates `entries` in place and
  enforces `max_stored_entries`.
- `WMReader.render(entries, *, language, task_progress=None)` returns the
  planning prompt block.
- `WMDisplay.render(entries, *, language, task_progress=None)` provides an
  optional view without changing capability execution.

WM code must not call the episodic backend or domain agents directly. Cross-
subsystem work goes through declared capabilities and their context.

## Delivery and verification

1. Implement `WMWriter`, `WMReader`, and `WMDisplay` from `wm/protocols.py`.
2. Copy `config/systems/wm/default.yaml` to a variant and update its paths.
3. Point `systems.wm` at the variant.

```bash
pytest tests/systems/test_protocol_shapes.py tests/systems/test_system_yaml_loader.py
pytest tests/runtime/test_langgraph_turn_loop.py
```
