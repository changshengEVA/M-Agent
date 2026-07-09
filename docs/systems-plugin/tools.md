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

## Working-memory evidence projection

Tool registration only exposes the capability to the execution layer. If a
tool result contains facts the thinking layer needs on later turns, add a
matching WM projection in `src/m_agent/chat/working_memory.py`:

1. Add a `_project_<tool_name>(params, result, config)` helper that converts
   the raw tool return into a compact evidence dict.
2. Route `tool_name` in `project_tool_call_to_entry(...)`.
3. Add a `kind == "<tool_name>"` branch in `_format_entry_line(...)` so
   `WMReader.render(...)` exposes the useful fields to the thinking layer.
4. Add tests in `tests/chat/test_working_memory.py`.

Do this during capability registration/review for every tool whose result
cannot be safely reduced to `{tool}: {summary}`. Search, mail, schedule, file,
browser, and multi-step workflow tools normally need first-class projection.

For example, `web_search` should expose query/url, provider, mode, result
count, content length, answer excerpt, and top source title/url/snippet fields;
otherwise the thinking layer only sees the fallback answer and cannot inspect
what led to it.

## Think-life skip-param declaration (`THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG`)

Some tools skip the param LLM; the thinking layer’s `instruction` is mapped directly to invoke kwargs via `build_tool_input` in `src/m_agent/runtime/think_life/scheduler/tool_runner.py`.

When adding a capability that should skip param fill, register it in **`THINK_LIFE_SKIP_PARAM_INSTRUCTION_ARG`**:

| Key | Value |
|-----|--------|
| Tool name | Kwarg name filled from `instruction`, or `None` for no args (e.g. `get_current_time`) |

Current entries: `get_current_time` -> none; `shallow_recall` / `deep_recall` -> `question`. `reply_to_user` is handled separately. Structured tools such as `web_search`, schedule, and mail tools (`schedule_create`, `schedule_query`, `schedule_delete`, `email_send`, etc.) use the param LLM.

## Delivery

1. Implement capabilities + registry factory
2. Copy `config/systems/tools/default.yaml`
3. Point `systems.tools`

```bash
pytest tests/systems/ tests/test_chat_controller_tool_limits.py
```
