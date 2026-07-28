# Tools Subsystem — Plug-in Guide

> Chinese: [tools.zh-CN.md](./tools.zh-CN.md) · Index: [README.md](./README.md)

## Role

The tools subsystem exposes LangChain capabilities to the execution layer. The
default suite uses **one YAML manifest per tool** so registration, prompting,
defaults, parameter routing, and integration metadata are discoverable in one
place.

## Layout

```text
config/systems/tools/
├── default.yaml                 # suite composition and cross-tool policy
└── capabilities/
    ├── web_search.yaml          # one complete tool descriptor
    ├── schedule_create.yaml
    └── ...
```

The chat controller still mounts one suite:

```yaml
systems:
  tools: ../../systems/tools/default.yaml
```

`default.yaml` points to the directory and controls the enabled whitelist:

```yaml
system: tools
capabilities_dir: ./capabilities
enabled: [reply_to_user, get_current_time, web_search]
defaults:
  __controller__:
    max_calls_per_turn: 12
```

If `enabled` is omitted, every manifest in `capabilities_dir` is enabled. The
legacy `registry` and `runtime_descriptions_path` fields remain supported for
older third-party suites.

## Capability manifest

```yaml
name: web_search
version: 1
category: information
builder: m_agent.systems.tools.default.capabilities.web_search_ops:_build_web_search_tool

descriptions:
  en: Search the public web or inspect a known URL.
  zh: 搜索公开网络或读取指定 URL。

input:
  mode: param_llm
  schema: inferred_from_tool

output:
  schema: builtins:dict
  feedback_projector: m_agent.runtime.think_life.scheduler.execution_feedback:feedback_summary_from_tool_history
  memory_projector: m_agent.chat.working_memory:project_tool_call_to_entry

policy:
  side_effect: read
  max_calls_per_turn: 3

dependencies: [web_search_client, api_credentials]

defaults:
  provider: auto
  max_results: 5
```

| Field | Runtime meaning |
|---|---|
| `name` | Stable tool name used by planning, invocation, logs, and memory |
| `version` | Positive contract version |
| `category` | Category exposed by capability descriptors |
| `builder` | Importable callable used to build the LangChain tool; loading fails early if invalid |
| `descriptions` | Localized descriptions injected into the thinking-layer capability block |
| `input.mode` | `param_llm`, `instruction_arg`, `no_args`, or `reply` |
| `input.instruction_arg` | Target kwarg for direct instruction mapping |
| `input.schema` | Schema origin/documentation; `inferred_from_tool` uses the LangChain args schema |
| `output.*` | Result-contract and projection metadata retained on `ControllerCapabilitySpec` |
| `policy.max_calls_per_turn` | Enforced through the tool defaults/limit mechanism |
| `policy.side_effect` | Review and safety metadata (`read`, `write`, `send`, etc.) |
| `dependencies` | Declared backend/service requirements |
| `defaults` | Per-tool runtime defaults |

`side_effect`, `dependencies`, and output projector paths are currently
descriptive metadata. Tool code must still obtain dependencies from
`ControllerCapabilityContext`, and non-trivial result projections must be
routed in `src/m_agent/chat/working_memory.py`.

## Input modes

| Mode | Behavior |
|---|---|
| `param_llm` | Build the tool schema and let the parameter LLM fill structured arguments |
| `instruction_arg` | Map the thinking-layer instruction directly to `instruction_arg` |
| `no_args` | Invoke with `{}` |
| `reply` | Build the `reply_to_user` payload without parameter filling |

The manifest is now authoritative for suites loaded from `capabilities_dir`.
The old hard-coded skip-parameter table remains only as a compatibility
fallback for programmatically constructed legacy registries.

## Implementation requirements

The builder must return a LangChain tool and should use:

- `start_tool_call` and `finish_tool_call` for observability;
- `check_tool_call_limits` before external work;
- `record_tool_use` with a structured result;
- `context.get_episodic_backend()` or another explicit context dependency.

If the result contains evidence needed later, also add a projection route and
formatter in `src/m_agent/chat/working_memory.py`, plus tests in
`tests/chat/test_working_memory.py`.

## Adding a tool

1. Implement its builder in `src/m_agent/systems/tools/default/capabilities/` or another importable package.
2. Add exactly one manifest under `config/systems/tools/capabilities/`.
3. Add its name to the suite `enabled` list when the suite uses an explicit whitelist.
4. Add dependency wiring, memory projection, and specialized feedback behavior when required.
5. Test manifest loading, argument routing, invocation, limits, and projection.

```bash
pytest tests/systems/ tests/runtime/test_think_life_tool_args.py tests/test_chat_controller_tool_limits.py
```
