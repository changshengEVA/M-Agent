# Subsystem configs (`config/systems/`)

One YAML file = one loaded subsystem. The chat controller holds **three pointers** (`systems.wm` / `episodic` / `tools`).

**Index:** [docs/systems-plugin-development.zh-CN.md](../../docs/systems-plugin-development.zh-CN.md)

**Plug-in guides (6 files):** [docs/systems-plugin/](../../docs/systems-plugin/)

| Subsystem | 中文 | English |
|-----------|------|---------|
| WM | [wm.zh-CN.md](../../docs/systems-plugin/wm.zh-CN.md) | [wm.md](../../docs/systems-plugin/wm.md) |
| Episodic | [episodic.zh-CN.md](../../docs/systems-plugin/episodic.zh-CN.md) | [episodic.md](../../docs/systems-plugin/episodic.md) |
| Tools | [tools.zh-CN.md](../../docs/systems-plugin/tools.zh-CN.md) | [tools.md](../../docs/systems-plugin/tools.md) |

## Shipped files

```
wm/default.yaml
episodic/rag_default.yaml
tools/default.yaml
tools/capabilities/<tool>.yaml
```

## Quick swap

```yaml
# config/agents/chat/chat_controller.yaml
systems:
  episodic: ../../systems/episodic/my_variant.yaml
```

Do not inline subsystem parameters in `chat_controller.yaml`.
