# Config Layout

`config/` is organized by ownership:

- `agents/`: top-level runnable agent configs
- `eval/`: evaluation-only selection configs and test-set definitions
- `memory/core/`: MemoryCore parameter configs
- `prompts/`: reusable non-runtime prompt templates (memory build/filter/eval/examples)
- `integrations/`: external service configs such as Neo4j
- `users/`: per-user generated chat/memory config bundles for auth mode

Recommended hierarchy:

- `ChatController config -> MemoryAgent config -> MemoryCore config`
- `MemoryAgent config -> MemoryCore config`

Config map:

- `config/agents/chat/chat_controller.yaml`
  Used by: `python -m m_agent.api.chat_api`
  Role: top-level chat controller config and tool wiring
- `config/agents/chat/runtime/chat_controller_runtime.yaml`
  Used by: `chat_controller.yaml`
  Role: top-level chat controller prompt source, including tool policy and tool descriptions
- `config/agents/memory/chat_memory_agent.yaml`
  Used by: `chat_controller.yaml`
  Role: recall-layer MemoryAgent under chat
- `config/agents/memory/runtime/agent_runtime.yaml`
  Used by: `chat_memory_agent.yaml` / `locomo_eval_memory_agent.yaml`
  Role: MemoryAgent runtime prompt source, including unified planner/system prompts plus decompose/sub-question/synthesis/direct-path templates
- `config/memory/core/chat_memory_core.yaml`
  Used by: `chat_memory_agent.yaml`
  Role: MemoryCore backend for chat memory
- `config/memory/core/runtime/memory_core_runtime.yaml`
  Used by: `chat_memory_core.yaml` / `locomo_eval_memory_core.yaml`
  Role: MemoryCore runtime prompt templates used by extraction/merge/resolution services
- `config/agents/memory/locomo_eval_memory_agent.yaml`
  Used by: `python scripts/run_locomo/run_eval_locomo.py`
  Role: default MemoryAgent config for LoCoMo evaluation
- `config/memory/core/locomo_eval_memory_core.yaml`
  Used by: `locomo_eval_memory_agent.yaml`
  Role: MemoryCore backend for LoCoMo evaluation
- `config/agents/email/gmail_email_agent.yaml`
  Used by: `m_agent.agents.email_agent.EmailAgent` and `m_agent.agents.chat_controller_agent.ChatControllerAgent`
  Role: Gmail EmailAgent config used by standalone email flows and top-level chat email tools
- `config/memory/core/dev_openai_memory_core.yaml`
  Used by: `tests/test_core_sys.py` and local smoke tests
  Role: developer-only MemoryCore config with explicit model choices

Prompt and integration files are shared building blocks rather than entry configs; runtime prompts are colocated with their owning module configs.

MemoryCore retrieval tuning:

- `config/memory/core/*.yaml` supports `detail_search_hybrid` for `search_details`:
  - `dense_recall_topn`, `sparse_recall_topn`
  - `rrf_k`, `dense_weight`, `sparse_weight`
  - `bm25_k1`, `bm25_b`
- `config/memory/core/*.yaml` supports `detail_search_multi_route` for multi-route recall:
  - `enable`, `route_count`, `query_generator`, `route_types`
  - `per_route_topk`, `fusion`, `max_workers`, `rrf_k`

MemoryAgent runtime knobs:

- `config/agents/memory/*.yaml` supports `workspace`:
  - `enable_state_machine`, `max_rounds`, `max_actions_per_round`
  - `max_episode_candidates`, `max_keep`, `min_evidence_to_answer`
  - `remedy_recall_max_times`, `enable_detail_search_multi_route`

Memory build LLM selection:

- `config/memory/core/*.yaml` can specify which OpenAI-compatible chat model id is used during:
  - Dialogue → Episodes segmentation (memory pre pipeline)
  - MemoryCore import/build (episodes → scene/facts/KG)

Supported keys (connection still from `.env`: `BASE_URL`, `API_SECRET_KEY`, `OPENAI_MODEL`):

- `llm_provider`: currently supports `openai` (OpenAI-compatible).
- `llm_model_name`: model id for MemoryCore build-time LLM usage. If empty, defaults to `OPENAI_MODEL`.
- `episode_llm_provider`: optional override for episode segmentation stage (defaults to `llm_provider`).
- `episode_llm_model_name`: optional override for episode segmentation stage (defaults to `llm_model_name` / `OPENAI_MODEL`).
