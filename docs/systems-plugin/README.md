# Pluggable Subsystems — Developer Guide (`m_agent.systems` + `config/systems`)

> 中文版：[README.zh-CN.md](./README.zh-CN.md)

This is the **index** (rules, architecture, shared YAML, delivery, testing). The six
per-subsystem guides live in this directory.

- **Code:** `src/m_agent/systems/`
- **Config:** `config/systems/`
- **Mount point:** `config/agents/chat/chat_controller.yaml` → `systems:` (three paths only)

**Out of scope here:** the full MemoryAgent / MemoryCore / LoCoMo evaluation stack,
which is maintained in the separate WorkspaceMem repository.

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
| WM | [wm.zh-CN.md](./wm.zh-CN.md) | [wm.md](./wm.md) |
| Episodic | [episodic.zh-CN.md](./episodic.zh-CN.md) | [episodic.md](./episodic.md) |
| Tools | [tools.zh-CN.md](./tools.zh-CN.md) | [tools.md](./tools.md) |

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

See [README.zh-CN.md §3.3](./README.zh-CN.md) (same tree).

### Plug-in access points

See the six subsystem guides listed in [§2](#2-subsystem-guides-6-files).

---

## 4. YAML reference (summary)

Shared rules: [zh-CN guide §4](./README.zh-CN.md). Per-subsystem YAML fields are
documented in the six guides in this directory.

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
| `config/systems/tools/capabilities/<tool>.yaml` | one executable descriptor per tool |

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

See [wm.md](./wm.md), [episodic.md](./episodic.md), and [tools.md](./tools.md).

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

- [Project structure](../development/project-structure.md)
- [Chat API](../chat_api/README.md)

**Maintenance:** keep the six subsystem guides and both index languages in sync.
