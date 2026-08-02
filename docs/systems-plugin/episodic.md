# Episodic Subsystem — Plug-in Guide

> 中文版：[episodic.zh-CN.md](./episodic.zh-CN.md) · Index: [README.md](./README.md)

## Role

The episodic subsystem persists dialogue across turns and answers “what was
said before” through recall capabilities. The default implementation is the
local `SimpleRagEpisodicBackend`.

The runtime owns Scene capture, flush ordering, and Dialogue archives. The
episodic backend consumes the resulting round lists, builds a searchable
index, and serves recall. It does not parse Scene storage or schedule flushes.

## Single-host persistence boundary

```text
ChatServiceRuntime
  → RuntimeHost (LangGraphRuntime)
      → RuntimeFlushOrchestrator
          → immutable Scene/transaction snapshot
          → runtime commit
          → Dialogue materialization
          → flush completion

Dialogue payload
  → ThreeLayerChatAgent.persist_dialogue_payload(...)
  → turns_to_rounds(...)
  → EpisodicMemoryBackend.persist_dialogue(...)
  → episodic index
```

The Runtime host exports only user/assistant entries to a Dialogue archive,
in Scene chronological order, using `speaker`, `text`, `turn_id`, and
`timestamp`. Thought, delegate, effect, and scheduler events remain in Scene.

## Ownership

| Layer | Owns | Does not own |
|-------|------|--------------|
| `RuntimeHost` / `RuntimeFlushOrchestrator` | Scene events, immutable flush snapshot, runtime commit, materialization journal, completion | RAG chunks, embeddings, recall ranking |
| Chat memory persistence | Dialogue validation, archive write, `turns_to_rounds` conversion | Runtime transaction state, recall ranking |
| `EpisodicMemoryBackend` | `persist_round`, `persist_dialogue`, shallow/deep recall, episode-note merge on flush | Scene parsing, timestamp repair, flush scheduling |

Scene is the authoritative source for a normal Dialogue materialization. A
Dialogue archive is the system artifact; the episodic index is the subsystem
artifact.

## Mount and configuration

| Item | Value |
|------|-------|
| Chat pointer | `systems.episodic` → `config/systems/episodic/*.yaml` |
| Default | `config/systems/episodic/rag_default.yaml` |
| Code | `src/m_agent/systems/episodic/` |

```yaml
systems:
  episodic: ../../systems/episodic/my_backend.yaml
```

| YAML field | Kind | Default | Role |
|------------|------|---------|------|
| `backend` | `path` → `EpisodicMemoryBackend` | `SimpleRagEpisodicBackend` | Public storage/retrieval implementation |
| `query.enabled` | Boolean | `true` | Controls whether recall capabilities are exposed |
| `query.capability_names` | List | `shallow_recall`, `deep_recall` | Recall capability names |

The product configuration uses `DefaultEpisodeRecorder` to buffer
thinking-layer `episode_note` values until flush.

### Default backend arguments

| Argument | Meaning |
|----------|---------|
| `storage_dir` | RAG parent directory |
| `workflow_id` | Index subdirectory |
| `top_k` | Retrieval result count |
| `embed_model` | `hash`, `alibaba`, or `bge` |

## Backend protocol

```python
def shallow_recall(self, question: str, *, thread_id: str) -> dict: ...
def deep_recall(self, question: str, *, thread_id: str) -> dict: ...
def persist_round(self, *, thread_id: str, user_message: str,
                  assistant_message: str, agent_result: dict | None = None) -> dict: ...
def persist_dialogue(self, *, thread_id: str, rounds: list[dict],
                     reason: str, source: str, progress_callback=None) -> dict: ...
def on_flush(self, *, thread_id: str, conversation_id: str,
             episode_notes: list[dict]) -> None: ...
```

Recall results contain at least `answer`. `persist_*` methods are framework
calls, not LLM tools. A backend treats `rounds` as its index-write input and
does not depend on Scene paths.

## LLM-facing surface

| Surface | Layer | Notes |
|---------|-------|-------|
| `shallow_recall`, `deep_recall` | Execution capabilities | Call the backend through `ControllerCapabilityContext` |
| Capability descriptions | Thinking prompt | `config/systems/tools/capabilities/<tool>.yaml` |
| `episode_note` | Thinking output | Buffered internally and supplied to `on_flush` |
| Recall policy | Runtime prompt | `config/agents/chat/runtime/chat_controller_runtime.yaml` |

Scene files, archive paths, chunks, embeddings, and `persist_*` are not exposed
to the LLM. Keep `query.enabled` aligned with recall names in `tools.enabled`.

## Persistence locations

| Store | Example for user `test` | Writer |
|-------|-------------------------|--------|
| Scene | `data/memory/chat-api/test/scene/*.jsonl` | Runtime host |
| Dialogue archive | `data/memory/chat-api/test/dialogues/` | Chat memory persistence during flush |
| Episodic index | `data/memory/chat-api/test/episodic/` | Episodic backend |

The root is `M_AGENT_MEMORY_ROOT`, `M_AGENT_DATA_DIR/memory`, or the project
`data/memory` directory. Chat startup applies per-user paths with
`chat_user_episodic_rag_paths()`; custom backends may expose details through
`describe_persistence()`.

## Flush lifecycle

1. `RuntimeHost.prepare_flush_segment()` persists an immutable snapshot.
2. `RuntimeHost.stage_flush_materialization()` persists the Dialogue payload.
3. `RuntimeHost.on_flush_segment()` commits the runtime boundary idempotently.
4. Chat memory persistence writes the Dialogue archive and calls
   `backend.persist_dialogue()`.
5. The host records the delivered materialization and completes the segment.

The journal keeps identifiers and payload digests stable so a process restart
can resume an incomplete flush without duplicating the archive or index write.

## Delivery and verification

1. Implement `EpisodicMemoryBackend` without reading Scene directly.
2. Copy `rag_default.yaml` and update `backend.path` / `kwargs`.
3. Point `systems.episodic` at the new file.

```bash
pytest tests/systems/episodic/ tests/systems/test_protocol_shapes.py
pytest tests/chat/test_scene_dialogue_export.py tests/api/test_runtime_memory_capture.py
pytest tests/runtime/test_runtime_flush_orchestrator.py
```
