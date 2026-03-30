# Config Layout

`config/` now uses a role-based structure:

- `agents/`: top-level agent runtime configs
- `memory/core/`: MemoryCore parameter configs
- `prompts/`: prompt templates used by build and retrieval workflows
- `integrations/`: external service configs such as Neo4j

Canonical defaults:

- `config/agents/memory/agent_sys.yaml`
- `config/agents/chat/test_agent_chat.yaml`
- `config/memory/core/agent_sys_memory.yaml`

Only the new paths above are supported.
