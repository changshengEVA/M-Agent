# Config Layout

Runtime configuration for the **M-Agent chat stack**. MemoryAgent / LoCoMo eval configs are in [**WorkspaceMem**](F:/AI/WorkspaceMem).

## Developer guide

**Subsystem plug-ins:**

- [docs/systems-plugin/README.zh-CN.md](../docs/systems-plugin/README.zh-CN.md) — 总索引（中文）
- [docs/systems-plugin/](../docs/systems-plugin/) — WM / episodic / tools 专题（各中英一篇，共 6 篇）
- [docs/systems-plugin/README.md](../docs/systems-plugin/README.md) — index (English)

## Directories

| Path | Purpose |
|------|---------|
| `agents/chat/` | Chat controller, model, runtime prompts |
| `agents/email/`, `agents/schedule/` | Domain agents |
| `systems/` | WM / episodic / tools YAML variants |
| `prompts/examples/` | Demo only |
| `integrations/` | e.g. Neo4j |
| `users/` | Per-user generated configs (do not hand-edit others' trees) |

## Chat chain (default)

```text
agents/chat/chat_controller.yaml
  → systems/wm/default.yaml
  → systems/episodic/rag_default.yaml
  → systems/tools/default.yaml (+ one manifest per tool under capabilities/)
  → chat_model.yaml, runtime/chat_controller_runtime.yaml
```

Swap a subsystem: change **one** path under `systems:` in `chat_controller.yaml`.
