# Config Layout

`config/` is organized by ownership:

- `agents/`: top-level runnable agent configs
- `memory/core/`: MemoryCore parameter configs
- `prompts/`: reusable prompt templates
- `integrations/`: external service configs such as Neo4j
- `users/`: per-user generated chat/memory config bundles for auth mode

Recommended hierarchy:

- `ChatController config -> MemoryAgent config -> MemoryCore config`
- `MemoryAgent config -> MemoryCore config`

Config map:

- `config/agents/chat/chat_controller.yaml`
  Used by: `python -m m_agent.api.chat_api`
  Role: top-level chat controller config and tool wiring
- `config/prompts/runtime/chat_controller_runtime.yaml`
  Used by: `chat_controller.yaml`
  Role: top-level chat controller prompt source, including tool policy and tool descriptions
- `config/agents/memory/chat_memory_agent.yaml`
  Used by: `chat_controller.yaml`
  Role: recall-layer MemoryAgent under chat
- `config/memory/core/chat_memory_core.yaml`
  Used by: `chat_memory_agent.yaml`
  Role: MemoryCore backend for chat memory
- `config/agents/memory/locomo_eval_memory_agent.yaml`
  Used by: `python scripts/run_eval_locomo.py`
  Role: default MemoryAgent config for LoCoMo evaluation
- `config/memory/core/locomo_eval_memory_core.yaml`
  Used by: `locomo_eval_memory_agent.yaml`
  Role: MemoryCore backend for LoCoMo evaluation
- `config/agents/eval/realtalk_eval_memory_agent.yaml`
  Used by: ReaLTalk evaluation/manual QA
  Role: MemoryAgent config for ReaLTalk
- `config/memory/core/realtalk_eval_memory_core.yaml`
  Used by: `realtalk_eval_memory_agent.yaml`
  Role: MemoryCore backend for ReaLTalk
- `config/memory/core/dev_openai_memory_core.yaml`
  Used by: `tests/test_core_sys.py` and local smoke tests
  Role: developer-only MemoryCore config with explicit model choices

Prompt and integration files are shared building blocks rather than entry configs.
