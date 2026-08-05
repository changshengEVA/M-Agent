# Episodic Subsystem — Plug-in Guide

> 中文版：[episodic.zh-CN.md](./episodic.zh-CN.md) · Index: [README.md](./README.md)

## Role

The `episodic` name currently identifies a compatibility and extension slot.
In v0.2, it persists dialogue across turns and answers “what was said before”
through recall capabilities. The default implementation is the local
`SimpleRagEpisodicBackend`.

This is **tool memory**: the model must explicitly invoke a recall capability.
It is not the planned **System Memory**, which will be owned by the Runtime and
evaluated automatically while the Context Compiler builds a cognitive context.
The roadmap introduces the System Memory SPI in v0.5 in shadow mode and moves
eligible memory into the main context path in v0.6.

Strategy is a separate planned subsystem, not a current episodic feature or a
production capability. Its roadmap status is v0.6 shadow-only matching, v0.7
opt-in guidance, and v0.8 default-candidate consideration only after published
evaluation gates pass.

The runtime owns Scene capture, flush ordering, and Dialogue archives. The
episodic backend consumes the resulting round lists, builds a searchable
index, and serves recall. It does not parse Scene storage or schedule flushes.

## Current v0.2 limitations

- The local backend implements only `shallow_recall`. It retains the
  `deep_recall` protocol method for compatibility, but returns
  `supported: false` / `reason: deep_recall_not_supported` instead of
  presenting shallow retrieval as a semantic deep recall.
- On the transaction-bound runtime path, a successful thinking step commits
  `episode_note` as a stable Scene entry. The immutable flush snapshot carries
  it into Dialogue `meta.trace_summary.episode_notes`, and the default backend
  indexes it with that exact thread/dialogue. Failed speculative steps do not
  publish notes. The standalone/direct compatibility path still uses an
  in-process recorder and does not have the same durability guarantee.
- A committed note is durable annotation metadata, not a typed immutable
  Episode or a Runtime-managed cognitive memory item.
- The dialogue RAG index does not maintain Goals, Beliefs, Expectations,
  provenance-aware state transitions, or automatic Context injection. Those
  belong to the future System Memory and Cognitive State roadmap.

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
| `EpisodicMemoryBackend` | `persist_round`, `persist_dialogue`, thread-scoped shallow recall, an explicitly unsupported compatibility response for deep recall, and optional episode-note merge when notes are delivered | Scene parsing, timestamp repair, flush scheduling |

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

`DefaultEpisodeRecorder` remains for standalone/direct compatibility calls.
The transaction-bound product runtime instead uses committed Scene entries and
the immutable flush snapshot as the durability boundary.

### Default backend arguments

| Argument | Meaning |
|----------|---------|
| `storage_dir` | RAG parent directory |
| `workflow_id` | Index subdirectory |
| `top_k` | Retrieval result count |
| `min_score` | Minimum cosine score for a hit (`0.2` by default); lower scores produce an explicit no-hit |
| `embed_model` | `hash`, `alibaba`, or `bge` |

The offline `hash` embedder uses a cross-process-stable, versioned BLAKE2b
feature hash. The index publishes `chunks.jsonl`, `embeddings.npy`, and
`index.meta.json` with checksums via atomic file replacement. Missing,
corrupt, or incompatible metadata/embeddings are rebuilt from the chunk log
at startup. Recall candidates are filtered to the exact `thread_id` before
ranking, so one thread cannot contribute evidence to another.

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

Recall results contain at least `answer`. A shallow no-hit returns an empty
answer/evidence list with `hit: false` and a reason. The default deep method
returns the explicit unsupported payload described above. `persist_*` methods
are framework calls, not LLM tools. A backend treats `rounds` as its
index-write input and does not depend on Scene paths.

## LLM-facing surface

| Surface | Layer | Notes |
|---------|-------|-------|
| `shallow_recall` | Default execution capability | Calls the backend through `ControllerCapabilityContext` |
| `deep_recall` | Compatibility capability, disabled by the default tool suite | The local backend returns an explicit unsupported result |
| Capability descriptions | Thinking prompt | `config/systems/tools/capabilities/<tool>.yaml` |
| `episode_note` | Thinking output | Committed to Scene on successful transaction-bound steps, then copied into frozen Dialogue metadata; direct compatibility calls remain in-memory |
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

## Dialogue flush lifecycle

1. `RuntimeHost.prepare_flush_segment()` persists an immutable snapshot.
2. `RuntimeHost.stage_flush_materialization()` persists the Dialogue payload.
3. `RuntimeHost.on_flush_segment()` commits the runtime boundary idempotently.
4. Chat memory persistence writes the Dialogue archive and calls
   `backend.persist_dialogue()`.
5. The host records the delivered materialization and completes the segment.

The journal keeps identifiers and payload digests stable so a process restart
can resume an incomplete Dialogue materialization without duplicating an
acknowledged archive or index write. Transaction-bound notes are part of the
same frozen payload and are indexed only for the matching thread/dialogue.
The legacy `on_flush()` merge remains a scoped compatibility path; it cannot
attach notes to an unrelated thread or dialogue.

## Delivery and verification

1. Implement `EpisodicMemoryBackend` without reading Scene directly.
2. Copy `rag_default.yaml` and update `backend.path` / `kwargs`.
3. Point `systems.episodic` at the new file.

```bash
pytest tests/systems/episodic/ tests/systems/test_protocol_shapes.py
pytest tests/chat/test_scene_dialogue_export.py tests/api/test_runtime_memory_capture.py
pytest tests/runtime/test_runtime_flush_orchestrator.py
```
