# Tools Subsystem — Plug-in Guide

> 中文版：[tools.zh-CN.md](./tools.zh-CN.md) · Index: [systems-plugin-development.md](../systems-plugin-development.md)

## Role

Register **LangChain tools** (capabilities) for the execution layer: whitelist, defaults, descriptions. Recall tools **must** use `context.get_episodic_backend()` — see [episodic.md](./episodic.md).

## Mount & swap

| Item | Value |
|------|-------|
| Chat pointer | `systems.tools` → `config/systems/tools/*.yaml` |
| Default | `default.yaml` + `runtime_descriptions.yaml` |
| Code | `src/m_agent/systems/tools/default/capabilities/` |

```yaml
systems:
  tools: ../../systems/tools/my_toolset.yaml
```

## Swappable slots & config

| YAML field | Kind | Role |
|------------|------|------|
| `registry` | `path` → `ControllerCapabilityRegistry` | **Primary path slot** |
| `enabled` | string list | Tool whitelist for the LLM |
| `defaults` | map | Per-tool kwargs; `memory_recall.max_calls_per_turn` |
| `runtime_descriptions` / `_path` | inline or path | Tool descriptions (zh/en) |

## LLM-facing surfaces

Tools in `enabled`; descriptions in execution prompt; limits via `defaults`.

## Implementation

- `ControllerCapabilitySpec` + `build_tool(context, description)`
- Use `start_tool_call` / `finish_tool_call` / `check_tool_call_limits`
- Prefer isolated `build_my_registry()` over global `register_capability()`

## Delivery

1. Implement capabilities + registry factory
2. Copy `config/systems/tools/default.yaml`
3. Point `systems.tools`

```bash
pytest tests/systems/ tests/test_chat_controller_tool_limits.py
```
