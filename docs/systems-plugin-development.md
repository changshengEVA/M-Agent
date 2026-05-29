# Pluggable Subsystems — Developer Guide (`m_agent.systems` + `config/systems`)

> 中文版：[systems-plugin-development.zh-CN.md](./systems-plugin-development.zh-CN.md)

This is the **index** (rules, architecture, shared YAML, delivery, testing). Per-subsystem plug-in guides live in **[systems-plugin/](./systems-plugin/)** (6 files).

- **Code:** `src/m_agent/systems/`
- **Config:** `config/systems/`
- **Mount point:** `config/agents/chat/chat_controller.yaml` → `systems:` (three paths only)

**Out of scope here:** full MemoryAgent / MemoryCore / LoCoMo eval → [**WorkspaceMem**](F:/AI/WorkspaceMem).

---

## 1. Rules of the road

| Rule | Detail |
|------|--------|
| Single plug-in surface | Replaceable pieces go through `m_agent.systems`; do not patch `layers/` or `chat/` to reach a backend. |
| No inline subsystem params | `chat_controller.yaml` holds **paths only**; all knobs live in per-system YAML files. |
| Skeleton vs integration package | `protocols.py` + `system.py` at subsystem root; implementations under `default/` or your own package directory. |
| Do not experiment in `default/` | Fork a new subpackage + YAML for private variants. |
| Duck-typed protocols | No inheritance required; loader validates with `@runtime_checkable`. |

---

## 2. Subsystem guides (6 files)

Plug-in applies at **agent construction** (reload after YAML change). Slot details, LLM surfaces, and delivery steps:

| Subsystem | 中文 | English |
|-----------|------|---------|
| WM | [wm.zh-CN.md](./systems-plugin/wm.zh-CN.md) | [wm.md](./systems-plugin/wm.md) |
| Episodic | [episodic.zh-CN.md](./systems-plugin/episodic.zh-CN.md) | [episodic.md](./systems-plugin/episodic.md) |
| Tools | [tools.zh-CN.md](./systems-plugin/tools.zh-CN.md) | [tools.md](./systems-plugin/tools.md) |

```yaml
systems:
  wm:       ../../systems/wm/default.yaml
  episodic: ../../systems/episodic/rag_default.yaml
  tools:    ../../systems/tools/default.yaml
```

Five public `path` slots: WM writer/reader/display; episodic `backend`; tools `registry`. `EpisodeRecorder` is system-internal — see episodic guide.

---

## 3. Architecture

### Config + load chain

```text
chat_controller.yaml  →  systems.{wm,episodic,tools}  →  load_*_system()
  →  SystemsBundle  →  ThreeLayerChatAgent  →  ThinkingAgent / ExecutionAgent
```

### Source layout

See [systems-plugin-development.zh-CN.md §3.3](./systems-plugin-development.zh-CN.md) (same tree).

### Plug-in access points

See [systems-plugin/](./systems-plugin/) (6 guides).

---

## 4. YAML reference (summary)

Shared rules: [zh-CN guide §4](./systems-plugin-development.zh-CN.md). Per-subsystem YAML fields: [systems-plugin/](./systems-plugin/).

**Common**

- Top-level `system: wm | episodic | tools`
- Slots: string `path` or `{ path, kwargs }`
- Loader does **not** expand `${ENV}` placeholders

**Chat controller** (`config/agents/chat/chat_controller.yaml`)

- Must have `systems.wm`, `systems.episodic`, `systems.tools` paths
- Must **not** duplicate `enabled_tools`, `working_memory`, etc. (legacy still accepted in old user files)

**Swap implementation:** change one line under `systems:`.

**On-disk defaults**

| File | Role |
|------|------|
| `config/systems/wm/default.yaml` | WM reader/writer/display + `config:` block |
| `config/systems/episodic/rag_default.yaml` | RAG backend kwargs |
| `config/systems/tools/default.yaml` | registry, enabled, defaults |
| `config/systems/tools/runtime_descriptions.yaml` | execution-layer tool descriptions |

---

## 5. Delivery checklist

1. Pick subsystem (`wm` / `episodic` / `tools`).
2. Implement protocol in-repo (`systems/<name>/<package>/`) or external pip package.
3. Export symbols referenced by YAML `path`.
4. Copy nearest variant YAML → `my_variant.yaml`, edit `path`/`kwargs`.
5. Point `chat_controller.yaml` `systems.<name>` at it.
6. Run `pytest tests/systems/` (+ checklist in §8 of zh-CN doc).
7. Do not patch `default/` for one-off trials.

---

## 6. Implementation notes

See [systems-plugin/wm.md](./systems-plugin/wm.md), [episodic.md](./systems-plugin/episodic.md), [tools.md](./systems-plugin/tools.md).

---

## 7. Override precedence

`systems=` arg → legacy `plugins=` → YAML `systems:` → legacy flat fields → built-in defaults.

```python
from m_agent.systems import SystemsBundle, load_episodic_system
bundle = SystemsBundle(episodic=load_episodic_system({...}))
```

---

## 8. Testing

```bash
pytest tests/systems/
```

See zh-CN §8 for file-level map and PR checklist.

---

## 9. Pitfalls

Wrong dotted path · kwargs mismatch · `query.enabled` vs recall tools mismatch · global registry mutation · bypassing episodic backend · inlining params in chat_controller · configuring episodic `recorder:` (not a public plug-in slot).

---

## 10. Related docs

- [Project structure](./project-structure.md)
- [Pipeline / SSE](./m_agent_pipeline.md)
- [Chat API](./chat_api/README.md)
- [WorkspaceMem](F:/AI/WorkspaceMem)

**Maintenance:** plug-in detail in `docs/systems-plugin/` (6 files); keep this index + zh-CN in sync; short README indexes elsewhere.
