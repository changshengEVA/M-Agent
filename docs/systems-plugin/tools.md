# Tools Subsystem — Plug-in Guide

> 中文版：[tools.zh-CN.md](./tools.zh-CN.md) · Index: [README.md](./README.md)

## Role

The tools subsystem exposes LangChain capabilities to the execution layer.
The current suite uses one YAML manifest per capability so registration,
prompt descriptions, argument routing, result projection, limits, and
dependency metadata have one source of truth.

## Layout and suite configuration

```text
config/systems/tools/
├── default.yaml
└── capabilities/
    ├── web_search.yaml
    ├── schedule_create.yaml
    └── ...
```

```yaml
# config/agents/chat/chat_controller.yaml
systems:
  tools: ../../systems/tools/default.yaml
```

```yaml
# config/systems/tools/default.yaml
system: tools
capabilities_dir: ./capabilities
enabled:
  - shallow_recall
  - deep_recall
  - reply_to_user
  - get_current_time
  - web_search
  - schedule_create
  - schedule_query
  - schedule_delete
  - email_ask
  - email_read
  - email_send
defaults:
  __controller__:
    max_calls_per_turn: 12
```

If `enabled` is omitted, every valid manifest in `capabilities_dir` is enabled.

> **Current safety boundary:** the checked-in default suite includes capabilities
> that can create or delete schedules and send email. `policy.side_effect` is
> descriptive review metadata in v0.2; it is not a deterministic authorization
> or approval gate. Review and reduce `enabled` before using untrusted input or
> real external accounts. Runtime-enforced approval policy is a later roadmap item.

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
  feedback_projector: m_agent.runtime.turn_support.execution_feedback:feedback_summary_from_tool_history
  memory_projector: m_agent.chat.working_memory:project_tool_call_to_entry

policy:
  side_effect: read
  max_calls_per_turn: 3

dependencies: [web_search_client, api_credentials]

defaults:
  provider: auto
  max_results: 5
```

| Field | Meaning |
|-------|---------|
| `name` | Stable capability name used by planning, invocation, logs, and memory |
| `version` | Positive contract version |
| `category` | Descriptor category |
| `builder` | Importable callable that returns a LangChain tool |
| `descriptions` | Localized descriptions shown to the thinking layer |
| `input.mode` | `param_llm`, `instruction_arg`, `no_args`, or `reply` |
| `input.instruction_arg` | Target key for direct instruction mapping |
| `input.schema` | Schema reference; `inferred_from_tool` reads the LangChain args schema |
| `output.*` | Result schema and validated feedback/WM projector paths |
| `policy.max_calls_per_turn` | Per-capability invocation limit |
| `policy.side_effect` | Descriptive review metadata such as `read`, `write`, `send`, or `emit`; v0.2 does not enforce authorization from this field |
| `dependencies` | Required services supplied through capability context |
| `defaults` | Per-capability runtime defaults |

All dotted executable references are resolved while loading the suite, so an
invalid manifest fails during startup.

## Input modes

| Mode | Behavior |
|------|----------|
| `param_llm` | Fill structured arguments from the tool schema |
| `instruction_arg` | Map the thinking-layer instruction to the declared key |
| `no_args` | Invoke with `{}` |
| `reply` | Build a `reply_to_user` payload without parameter filling |

The manifest controls argument routing for every file-backed capability.

## Runtime and `runtime_hooks` contract

The LangGraph turn port invokes exactly one capability through
`ExecutionAgent.invoke_tool_direct(..., runtime_hooks=...)`. The execution
layer copies that mapping into
`ControllerCapabilityContext.controller_state["runtime"]`.

Current hooks may include:

| Hook | Purpose |
|------|---------|
| `transaction_id`, `conversation_id`, `delegate_id`, `effect_id` | Durable attribution |
| `idempotency_key`, `delivery_guarantee` | Side-effect identity and delivery policy |
| `on_reply` | Deliver a user-visible reply |
| `on_schedule_created` | Attach a created schedule to runtime state |
| `scene_writer` | Append a transaction-fenced Scene entry |

Hooks are per invocation and optional unless a capability's contract requires
one. Capability code reads them from `controller_state["runtime"]`, obtains
services from `ControllerCapabilityContext`, and does not import graph internals.

Builders should call `start_tool_call`, `check_tool_call_limits`,
`record_tool_use`, and `finish_tool_call` around external work. Episodic access
goes through `context.get_episodic_backend()`.

## Adding a capability

1. Implement a builder under
   `src/m_agent/systems/tools/default/capabilities/` or another importable package.
2. Add exactly one manifest under `config/systems/tools/capabilities/`.
3. Add the name to `enabled` when the suite uses an explicit list.
4. Wire context dependencies and add feedback/WM projection when required.
5. Test loading, argument routing, invocation, limits, projection, and hooks.

```bash
pytest tests/systems/
pytest tests/runtime/test_turn_support_tool_args.py tests/test_chat_controller_tool_limits.py
pytest tests/chat/test_working_memory.py tests/runtime/test_langgraph_turn_loop.py
```
