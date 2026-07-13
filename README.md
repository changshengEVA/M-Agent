# M-Agent

**M-Agent** is a **memory-centric** agent framework for **personal information life**.
![pipeline_img](docs/M-Agent.png)




## How to Run

### Server side (Chat API)

Run the agent as a **long-lived HTTP / SSE service** (`python -m m_agent.api.chat_api`). You create a run per user message, subscribe to the event stream, and read the final result while the server keeps **thread-level** session state.

**Think-life is the sole product runtime.** Stimuli go through a **perception bus**, WM is isolated by transaction, the **Scene log** records a chronological timeline, and user-visible text is emitted only through the **`reply_to_user`** tool. Schedule heartbeat and execution feedback use the same runtime path. See **[docs/think-life-runtime-spec.zh-CN.md](docs/think-life-runtime-spec.zh-CN.md)**.

### Client side

- **M-Agent-UI**: the simplest information-interaction surface **[available now]**
- **M-Agent-desktop**: a desktop-form personal assistant

---

## Repository Layout

Core source code lives under `src/m_agent/`, runnable entry scripts under `scripts/`, automated tests under `tests/`, examples under `examples/`, and experimental integrations under `experiments/`. Configuration lives under `config/`, while runtime artifacts mostly land in `data/` and `log/`.

For a fuller directory layout and design conventions, see: **[docs/project-structure.md](docs/project-structure.md)**.

---

## Requirements

- **Python**: `>= 3.10` (see `pyproject.toml`)
- **Neo4j (optional)**: required when graph storage / entity-relation features are enabled (install separately and ensure connection settings match the project)
- **LLM / Embedding / Rerank**: configured via `.env` and YAML files under `config/`, supporting OpenAI-compatible providers, Alibaba Cloud (DashScope), etc. (see below)

---

## Installation

From the project root:

```powershell
# Windows PowerShell example
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Editable install (recommended for local development):

```bash
pip install -e .
```

---

## Environment Variables (`.env`)

Create a `.env` file in the project root and fill in keys / base URLs as needed. Common entries are listed below (defer to in-repo config comments for the source of truth):

```dotenv
# Chat / RAG LLM (e.g. src/m_agent/load_model/OpenAIcall.py)
# Fill in either API_SECRET_KEY or OPENAI_API_KEY
API_SECRET_KEY=YOUR_OPENAI_COMPATIBLE_KEY
OPENAI_API_KEY=
BASE_URL=https://api.openai.com/v1

# Chat model (config/agents/chat/chat_model.yaml)
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_KEY

# RAG embedding (config/systems/episodic/rag_default.yaml)
ALIBABA_API_KEY=YOUR_ALIBABA_KEY
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_EMBED_MODEL=text-embedding-v4
# Rerank: compatible-API example; for the Singapore region switch to dashscope-intl

# Optional switches (keep aligned with current repo defaults)
LANGUAGE=zh
EMBED_PROVIDER=aliyun
LLM_PROVIDER=deepseek

# Web search (Tavily / You.com — optional, for the web_search tool)
YDC_API_KEY=YOUR_YOU_COM_KEY
TAVILY_API_KEY=YOUR_TAVILY_KEY
```

---

## Usage 1: Chat API as a Long-Running Backend (FastAPI)

The repo ships an HTTP / SSE chat service built on **fixed startup-time config + thread-level session state** (i.e. *not* the "send full config with every request" pattern). The service always runs the Think-life runtime.

### Start the service

```powershell
$env:PYTHONPATH = "src"
python -m m_agent.api.chat_api `
  --host 127.0.0.1 `
  --port 8777 `
  --config config/agents/chat/chat_controller.yaml `
  --idle-flush-seconds 1800 `
  --history-max-rounds 12 `
  --schedule-beat-seconds 10 `
  --users-db config/users/users.json `
  --session-ttl-seconds 43200
```

Think-life tuning (delegates per transaction, Scene context window, preemption) lives under `runtime.think_life` in `chat_controller.yaml`.

### After startup

- Swagger UI: `http://127.0.0.1:8777/docs`
- OpenAPI JSON: `http://127.0.0.1:8777/openapi.json`

Full API reference, authentication, thread events and schedule details: **[docs/chat_api/README.md](docs/chat_api/README.md)**.

### Bash (Linux / macOS)

```bash
export PYTHONPATH=src
python -m m_agent.api.chat_api \
  --host 127.0.0.1 \
  --port 8777 \
  --config config/agents/chat/chat_controller.yaml
```



## Episodic memory in this repo (simple RAG)

The **chat stack** uses a **RAG episodic backend** (`SimpleRagEpisodicBackend`) by default, configured via `config/systems/episodic/rag_default.yaml`. It writes to the per-user vector index after each turn and flush, and serves `shallow_recall` / `deep_recall` queries.

- **Pluggable subsystem guides:** [docs/systems-plugin/](docs/systems-plugin/) (one Chinese and one English guide for each of WM, episodic memory, and tools)
- **RAG index:** `data/memory/chat-api/<user>/episodic/` (`chunks.jsonl`, `embeddings.npy`)
- **Dialogue archive** (flush): `data/memory/chat-api/<user>/dialogues/`
- **Scene log**: `data/memory/chat-api/<user>/scene/<thread_id>.jsonl` — a chronological, cross-transaction record of what was said and done
- **How this differs from WM:** WM is isolated by **transaction** and provides hot in-process context; episodic memory and Scene logs can be retrieved across turns or read chronologically

For persistence details, see **[docs/systems-plugin/episodic.md](docs/systems-plugin/episodic.md)**. `GET .../memory/state` returns `thread_state.episodic_persistence`.

## WorkspaceMem (full memory stack + benchmarks)

The evidence-driven **MemoryAgent / MemoryCore** implementation and **LoCoMo / LongMemEval / REALTALK** evaluation pipelines have moved to a standalone repository:

**[F:/AI/WorkspaceMem](F:/AI/WorkspaceMem)** (`workspace_mem` Python package)

- **[docs/project-structure.md](docs/project-structure.md)** — M-Agent layout
- **[src/m_agent/systems/README.md](src/m_agent/systems/README.md)** — plug-in contracts

---


## Tests

```bash
pytest
```

For markers and policy, see `[tool.pytest.ini_options]` in `pyproject.toml`.

---

## Documentation Index

| Document | Content |
| --- | --- |
| [docs/project-structure.md](docs/project-structure.md) | Directory conventions and common commands |
| [scripts/run_locomo/README.md](scripts/run_locomo/README.md) | LoCoMo configuration and per-script reference |
| [docs/chat_api/README.md](docs/chat_api/README.md) | Full Chat API reference |
| [docs/think-life-runtime-spec.zh-CN.md](docs/think-life-runtime-spec.zh-CN.md) | Think-life runtime spec (Chinese) |
| [tools/M-Agent-UI/API.md](tools/M-Agent-UI/API.md) | Frontend integration API |

---

## License

This project is released under the **MIT License**. See the root [LICENSE](LICENSE) file for details.

---

**中文 README:** [README-zh.md](README-zh.md)
