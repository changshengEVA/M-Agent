# WM Subsystem — Plug-in Guide

> 中文版：[wm.zh-CN.md](./wm.zh-CN.md) · Index: [systems-plugin-development.md](../systems-plugin-development.md)

## Role

Project **in-process** capability-call history into text for Think-life planning. WM is **not persisted across restarts** and has **no recall tools** — use [episodic.md](./episodic.md) for persisted retrieval.

## Mount & swap

| Item | Value |
|------|-------|
| Chat pointer | `systems.wm` → `config/systems/wm/*.yaml` |
| Default | `config/systems/wm/default.yaml` |
| Code | `src/m_agent/systems/wm/` |

```yaml
systems:
  wm: ../../systems/wm/my_variant.yaml
```

## Swappable slots

| YAML field | Protocol | Default | Role |
|------------|----------|---------|------|
| `writer` | `WMWriter` | `DefaultWMWriter` | Append `tool_history` to the active transaction's `wm_entries` after a capability call |
| `reader` | `WMReader` | `DefaultWMReader` | Render `wm_entries` for the next **Think-life planning** turn |
| `display` | `WMDisplay` | `DefaultWMDisplay` | Optional renderer for integrations; the Think-life execution path does not inject a WM system prompt |
| `config` | `WorkingMemoryConfig` | see default.yaml | Not a `path`; injected into writer/reader/display |

## LLM-facing surfaces

| Surface | Layer |
|---------|-------|
| WM text block | Think-life planning (`WMReader.render`) |
| Capability result | Scheduler writes `tool_history` through `WMWriter.write` |
| `WMDisplay` | Available to custom integrations; not consumed by Think-life capability invocation |
| No tools | LLM only reads rendered prompt text |

## Implementation

- `WMWriter.write` — mutate the active transaction's entries and respect `max_stored_entries`
- `WMReader.render` — inject the transaction's recent entries into planning
- `WMDisplay.render` — provide an optional text rendering without changing Think-life execution

**Do not** call episodic backends or domain agents from WM code.

## Delivery

1. Implement protocols in `wm/protocols.py`
2. Copy `config/systems/wm/default.yaml` → `my_variant.yaml`
3. Point `systems.wm` in `chat_controller.yaml`

```bash
pytest tests/systems/test_protocol_shapes.py tests/systems/test_system_yaml_loader.py
```
