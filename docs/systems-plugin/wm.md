# WM Subsystem — Plug-in Guide

> 中文版：[wm.zh-CN.md](./wm.zh-CN.md) · Index: [systems-plugin-development.md](../systems-plugin-development.md)

## Role

Project **in-process** tool-call history into text for thinking and execution layers. WM is **not persisted across restarts** and has **no recall tools** — use [episodic.md](./episodic.md) for cross-turn retrieval.

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
| `writer` | `WMWriter` | `DefaultWMWriter` | Append `tool_history` to `wm_entries` after execution |
| `reader` | `WMReader` | `DefaultWMReader` | WM text → **thinking** plan / summarize |
| `display` | `WMDisplay` | `DefaultWMDisplay` | WM text → **execution** system prompt |
| `config` | `WorkingMemoryConfig` | see default.yaml | Not a `path`; injected into writer/reader/display |

## LLM-facing surfaces

| Surface | Layer |
|---------|-------|
| WM text block | Thinking (`WMReader.render`) |
| WM text block | Execution (`WMDisplay.render`) |
| No tools | LLM only reads rendered prompt text |

## Implementation

- `WMWriter.write` — mutate `entries`, respect `max_stored_entries`
- `WMReader.render` / `WMDisplay.render` — prompt injection

**Do not** call episodic backends or domain agents from WM code.

## Delivery

1. Implement protocols in `wm/protocols.py`
2. Copy `config/systems/wm/default.yaml` → `my_variant.yaml`
3. Point `systems.wm` in `chat_controller.yaml`

```bash
pytest tests/systems/test_protocol_shapes.py tests/systems/test_system_yaml_loader.py
```
