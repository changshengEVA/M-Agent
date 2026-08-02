# Pluggable Subsystems — Developer Guide (`m_agent.systems` + `config/systems`)

> 中文版：[README.zh-CN.md](./README.zh-CN.md)

This is the index for the three pluggable subsystems: working memory (WM),
episodic memory, and tools. It documents their shared rules, the production
runtime boundary, configuration, delivery, and verification.

- Code: `src/m_agent/systems/`
- Configuration: `config/systems/`
- Chat mount point: `config/agents/chat/chat_controller.yaml` → `systems:`

The full MemoryAgent / MemoryCore / LoCoMo evaluation stack is maintained in
the separate WorkspaceMem repository and is outside this guide.

---

## 1. Rules

| Rule | Detail |
|------|--------|
| One subsystem surface | Replaceable components are exposed through `m_agent.systems`; callers do not reach into an implementation package. |
| Paths at the chat layer | `chat_controller.yaml` contains subsystem YAML paths; implementation parameters live in those subsystem files. |
| Protocol plus implementation | A subsystem declares runtime-checkable protocols at its root and places implementations under `default/` or another package. |
| Stable built-ins | Put an experimental implementation in a new package and YAML file instead of changing `default/`. |
| Duck typing | Implementations need not inherit a protocol; loading validates the required shape with `isinstance`. |

---

## 2. Subsystem guides

Subsystems are loaded when the chat agent is constructed. Reload the agent
after changing a subsystem YAML file.

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

The five public `path` slots are WM `writer` / `reader` / `display`, episodic
`backend`, and tools `registry`. `EpisodeRecorder` is internal to the episodic
integration.

---

## 3. Production runtime boundary

The product has one runtime host. `create_runtime_host()` constructs
`LangGraphRuntime`, which satisfies the neutral `RuntimeHost` protocol and
persists the engine id `langgraph_v1`.

```text
ChatServiceRuntime
  → create_runtime_host()
  → RuntimeHost (LangGraphRuntime)
      ├─ runtime transaction / perception / dispatch kernel
      ├─ LangGraph transaction and turn graphs
      ├─ RuntimeFlushOrchestrator
      └─ ThreeLayerChatAgent
          ├─ ThinkingAgent ← WM reader
          └─ ExecutionAgent ← tools registry + episodic backend
```

| Contract | Current location | Purpose |
|----------|------------------|---------|
| `RuntimeHost` | `m_agent.runtime.host` | Product-facing thread, transaction, Scene, schedule, flush, health, and shutdown operations |
| `LangGraphRuntime` | `m_agent.runtime.langgraph.runtime` | The production implementation of `RuntimeHost` |
| `RuntimeConfig` | `m_agent.runtime.config` | Engine-neutral runtime and scheduler configuration |
| `runtime_hooks` | `ExecutionAgent.invoke_tool_direct` | Per-delegate callbacks and durable identity passed to a capability invocation |

The execution layer copies `runtime_hooks` into
`ControllerCapabilityContext.controller_state["runtime"]`. Capabilities may
consume only the hooks they need. This keeps subsystem code independent of the
graph implementation and gives reply, schedule, Scene, and effect handling a
single neutral protocol.

---

## 4. Configuration

### Subsystem YAML

- Top-level `system` is exactly `wm`, `episodic`, or `tools`.
- A replaceable slot is either a string `path` or `{ path, kwargs }`.
- Paths use `pkg.module:Symbol` or `pkg.module.Symbol`.
- The loader calls `Symbol(**kwargs)` and does not expand `${ENV}` values.

### Chat controller

The chat controller points to the three subsystem files and contains only the
current runtime sections:

```yaml
systems:
  wm:       ../../systems/wm/default.yaml
  episodic: ../../systems/episodic/rag_default.yaml
  tools:    ../../systems/tools/default.yaml

runtime:
  common:
    scene_context_max_entries: 40
    scene_persist_jsonl: true
  langgraph:
    turn_loop: true
    delegate_executor: execution_agent
```

An explicit `systems=SystemsBundle(...)` argument is useful in tests and
embedding applications. Normal product startup loads the `systems:` paths.

---

## 5. Source and configuration layout

```text
src/m_agent/systems/
├── loader.py, bundles.py
├── wm/          protocols.py, system.py, default/
├── episodic/    protocols.py, system.py, query_module.py, default/
└── tools/       base.py, registry.py, manifest.py, system.py, default/

config/systems/
├── wm/default.yaml
├── episodic/rag_default.yaml
└── tools/default.yaml, capabilities/<tool>.yaml
```

---

## 6. Delivering an implementation

1. Choose `wm`, `episodic`, or `tools`.
2. Read its protocol or base types and implement every required method.
3. Export the class or factory from an importable package.
4. Copy the nearest subsystem YAML to a new variant and update `path` / `kwargs`.
5. Point `chat_controller.yaml` at the variant.
6. Add protocol, YAML-loading, behavior, and runtime-boundary tests.
7. Run the verification commands below.

---

## 7. Verification

```bash
pytest tests/systems/
pytest tests/runtime/test_runtime_host.py tests/runtime/test_chat_api_runtime_host.py
pytest tests/runtime/test_langgraph_turn_loop.py
```

Useful focused tests:

| Concern | Test |
|---------|------|
| Protocol shapes | `tests/systems/test_protocol_shapes.py` |
| On-disk YAML loading | `tests/systems/test_system_yaml_loader.py` |
| RAG backend | `tests/systems/episodic/test_rag_backend.py` |
| Runtime systems injection | `tests/systems/test_runtime_systems_override.py` |
| Tool argument routing | `tests/runtime/test_turn_support_tool_args.py` |
| Tool call limits | `tests/test_chat_controller_tool_limits.py` |

---

## 8. Review checklist

- [ ] Every new `path` imports in the target environment.
- [ ] `kwargs` match the constructor.
- [ ] `isinstance(instance, Protocol)` succeeds.
- [ ] The subsystem YAML is present and loadable.
- [ ] Recall capabilities match `episodic.query.enabled` and `tools.enabled`.
- [ ] Capability code uses `ControllerCapabilityContext` dependencies.
- [ ] Runtime-aware capabilities use `runtime_hooks` through `controller_state["runtime"]`.
- [ ] The focused tests and `pytest tests/systems/` pass.

---

## 9. Common mistakes

Incorrect dotted paths; mismatched constructor arguments; enabling recall in
only one subsystem; mutating a process-global registry; bypassing the episodic
backend; putting subsystem parameters in the chat controller; or configuring
an episodic `recorder` as a public slot.

---

## 10. Related documentation

- [Project structure](../development/project-structure.md)
- [Chat API](../chat_api/README.md)

Keep every English/Chinese guide pair synchronized when contracts, paths, or
verification commands change.
