# M-Agent

**A Stimulus-Native Cognitive Runtime**

> Move agent cognition beyond user-message boundaries, so it can be driven by changes in the world, action outcomes, temporal conditions, and internal expectations.

M-Agent is building a runtime that persists beyond a single LLM call. It admits asynchronous stimuli, maintains transactions and state, compiles context `c` when cognition is needed, governs actions, and feeds effects back into the same cognitive loop.

For a personal assistant, the intended experience is simple:

> **Say it once. It keeps up—and only comes to you when needed.**

[Vision and goals (Chinese)](docs/vision-and-goals.zh-CN.md) · [v0.2.0–v1.0.0 roadmap](docs/roadmap-v0.2.0-v1.0.0.zh-CN.md) · [Target architecture](docs/architecture/cognitive-runtime-architecture.zh-CN.md) · [Roadmap PDF](docs/pdf/M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf)

> **Current stage: v0.2.0 development baseline.** This README deliberately separates implemented behavior from target capabilities; the cognitive-runtime vision is not a claim that every target subsystem already exists.

## Why “stimulus,” not just “message”

A user message is only one kind of stimulus. A persistent agent may also need to reconsider its state because of:

- a message, email, calendar event, web change, or other external observation;
- a successful tool call, execution failure, permission denial, or other action outcome;
- an approaching deadline, a missing expected response, or a stalled task;
- new evidence that conflicts with an existing belief.

The core technical direction is **Stimulus-to-Cognition Compilation**:

```text
Signal
→ Observation
→ Stimulus
→ Admission / Attention
→ Goal / Transaction Attribution
→ Cognitive State Transition
→ Context Snapshot c
→ Action / Silence
→ Effect Feedback
```

The goal is not to collect the largest number of connectors. It is to determine whether an agent should wake after the world changes, which persistent transaction should wake, what cognitive context it should receive, and why it should act or remain silent.

## What v0.2.0 implements today

The repository currently provides the reliability skeleton of the future cognitive runtime:

- one LangGraph-backed `RuntimeHost` (`langgraph_v1`);
- persistent Transaction, TaskState, Activation, and Scene timelines;
- a Stimulus Inbox with priority, admission order, attribution, and source validation;
- a shared runtime path for user messages, due schedules, and execution feedback;
- Thinking, Delegate, Effect, Feedback, and user-reply loops;
- SQLite checkpoints, an effect ledger, a flush journal, and restart-recovery foundations;
- transaction-scoped working memory plus simple RAG exposed as model-invoked tool memory;
- executable semantic acceptance scenarios for Transaction, Stimulus Pool, Attribution, and recovery behavior.

Not implemented yet:

- a general Observation Source SDK and public `runtime.ingest(observation)` API;
- full Attention and `ignored / merged / deferred / activated` dispositions;
- Goal, Expectation, Evidence, Belief, and replayable `Context Snapshot` contracts;
- system memory automatically maintained and injected by the Runtime;
- endogenous stimuli such as an unmet expectation;
- a production Strategy System;
- bounded autonomy built from permissions, budgets, approvals, and a kill switch.

These capabilities belong to v0.3.0–v0.8.0 and are not presented as existing v0.2.0 behavior.

## Roadmap at a glance

| Version | Theme | Primary objective |
| --- | --- | --- |
| v0.2.0 | Runtime baseline | Establish Transaction, Scene, Stimulus, Schedule, Effect/Feedback, and tool-memory foundations |
| v0.2.1 | Trusted baseline | Fix recovery, context and memory correctness, safe defaults, and open-source delivery |
| v0.3.0 | Stimulus Kernel | Publish Observation/Stimulus contracts, `runtime.ingest()`, disposition traces, and adapters |
| v0.4.0 | Attention & Attribution | Filter noise and decide whether to wake and which transaction to wake |
| v0.5.0 | Cognitive State & Context Compiler | Introduce cognitive state and traceable Context Snapshots; run system memory in Shadow mode |
| v0.6.0 | System Memory & Temporal Runtime | Put system memory on the main path; make time and expectations computable; shadow Strategy |
| v0.7.0 | Endogenous Cognition & Strategy | Generate endogenous stimuli and apply constrained, measurable Strategy guidance |
| v0.8.0 | Bounded Autonomy & Cognitive SDK | Add permissions, budgets, approvals, a kill switch, and the cognitive plug-in SDK |
| v0.9.0 | Release Candidate | Freeze contracts and validate migrations, security, and long-running behavior |
| v1.0.0 | Stable Cognitive Runtime | Deliver stable public contracts for a stimulus-native cognitive runtime |

See the [full roadmap](docs/roadmap-v0.2.0-v1.0.0.zh-CN.md) or the [distributable PDF](docs/pdf/M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf) for goals, deliverables, and release gates.

## System memory vs. tool memory

M-Agent distinguishes two control models:

- **Tool memory:** the model notices that it needs to remember and explicitly calls RAG, search, or archive tools. The current `SimpleRagEpisodicBackend` belongs here.
- **System memory:** the Runtime projects, consolidates, and retrieves committed Scene, TaskState, Evidence, and Effect data; the Context Compiler evaluates it before a decision is made.

System memory enters Shadow integration in v0.5.0 and the main cognitive path in v0.6.0. Strategy is procedural knowledge: Shadow in v0.6.0, opt-in in v0.7.0, and eligible to become a default candidate only after ablation and safety gates.

## Installation

Python `>=3.10` is required. For the current development repository, install the complete dependency set and then the editable package:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

On Linux or macOS, activate the environment with `source .venv/bin/activate`.

## Minimal configuration

Create `.env` in the repository root. The current chat model uses an OpenAI-compatible API; either key variable is accepted:

```dotenv
API_SECRET_KEY=YOUR_COMPATIBLE_API_KEY
# OPENAI_API_KEY=YOUR_COMPATIBLE_API_KEY
BASE_URL=https://your-provider.example/v1
```

The default simple RAG backend uses local `hash` representations and does not require an embedding-service key. Configure optional credentials only when the corresponding integration is enabled in YAML:

```dotenv
# Alibaba embedding / rerank (optional)
ALIBABA_API_KEY=
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_EMBED_MODEL=text-embedding-v4

# Web search (optional; set the key for the selected provider)
YDC_API_KEY=
TAVILY_API_KEY=
```

## Start the Chat API

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m m_agent.api.chat_api `
  --host 127.0.0.1 `
  --port 8777 `
  --config config/agents/chat/chat_controller.yaml
```

Linux / macOS:

```bash
export PYTHONPATH=src
python -m m_agent.api.chat_api \
  --host 127.0.0.1 \
  --port 8777 \
  --config config/agents/chat/chat_controller.yaml
```

After startup:

- Swagger UI: `http://127.0.0.1:8777/docs`
- OpenAPI JSON: `http://127.0.0.1:8777/openapi.json`
- Health check: `http://127.0.0.1:8777/healthz`

See the [Chat API reference](docs/chat_api/README.md) for authentication, HTTP, SSE, Thread, Transaction, Scene, Memory, and Schedule contracts. The current `/threads/{thread_id}/stimuli` endpoint remains a user-style input surface; the general Observation admission protocol is a v0.3.0 target.

## Current persistence boundaries

- Transaction WM: hot context for one active transaction;
- Scene: `data/memory/chat-api/<user>/scene/<thread_id>.jsonl`;
- Dialogue: archives materialized by Flush;
- Episodic index: `data/memory/chat-api/<user>/episodic/`;
- Runtime/checkpoint/journal: recovery foundations for transactions, stimuli, effects, and flushes.

See the [pluggable-subsystem guide](docs/systems-plugin/README.md) and [current Runtime documentation](docs/runtime/README.md) for implementation details.

## Tests

```bash
pytest
```

Runtime semantic acceptance:

```powershell
python -m m_agent.acceptance contract run --runtime langgraph_v1 --all-layers
python scripts/run_runtime_migration_gate.py --rounds 3
```

See the [Runtime semantic acceptance platform](docs/runtime/semantic-acceptance-platform.zh-CN.md).

## Documentation

| Document | Scope |
| --- | --- |
| [Vision and goals](docs/vision-and-goals.zh-CN.md) | Positioning, user value, memory boundaries, and success criteria |
| [Version roadmap](docs/roadmap-v0.2.0-v1.0.0.zh-CN.md) | Goals, deliverables, and gates from v0.2.0 to v1.0.0 |
| [Roadmap PDF](docs/pdf/M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf) | Distributable version of the roadmap |
| [Cognitive Runtime target architecture](docs/architecture/cognitive-runtime-architecture.zh-CN.md) | Target path from Signal to Effect Feedback |
| [Current Runtime](docs/runtime/README.md) | Current v0.2.0 implementation facts |
| [Chat API](docs/chat_api/README.md) | HTTP and SSE reference |
| [Subsystem plug-ins](docs/systems-plugin/README.md) | Current WM, Episodic, and Tools extension contracts |
| [Documentation index](docs/README.md) | Active docs, target designs, and historical archives |

## Repository layout

Source code lives in `src/m_agent/`, configuration in `config/`, tests in `tests/`, scripts in `scripts/`, and reference clients in `tools/`. See the [project structure guide](docs/development/project-structure.md).

## License

M-Agent is released under the [MIT License](LICENSE).

**中文：** [README-zh.md](README-zh.md)
