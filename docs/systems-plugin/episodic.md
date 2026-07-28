# Episodic Subsystem — Plug-in Guide

> 中文版：[episodic.zh-CN.md](./episodic.zh-CN.md) · Index: [README.md](./README.md)

## Role

**Persist dialogue across turns** and answer “what was said before” via recall tools on the execution layer. Default: local RAG (`SimpleRagEpisodicBackend`).

Episodic does **not** capture runtime events or write Dialogue JSON from Scene—that is **Chat system** responsibility. The plug-in consumes **Dialogue archives** already written by the system and **round lists** passed to `persist_*` to build searchable episodic memory.

## System vs plug-in boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **Chat system** (`chat_api_runtime`, `ThinkLifeRuntime`, Scene store) | Scene logs all runtime events; on flush, extract **user/reply** in real time order into **v1-shaped Dialogue**; write `dialogues/` | RAG chunks, embeddings, recall ranking |
| **Episodic plug-in** (`EpisodicMemoryBackend`) | Index from round lists (`persist_round` / `persist_dialogue`); `shallow_recall` / `deep_recall`; merge `episode_note` on flush via `on_flush` | Turn pairing fixes, timestamp repair, reading Scene JSONL, flush scheduling |

```text
Scene (jsonl, system-internal)
    │  flush: build_dialogue_payload_from_scene_entries
    ▼
Dialogue JSON (v1 shape)  →  dialogues/<user>/YYYY-MM/*.json   ← system artifact (upload/validate)
    │  turns_to_rounds → persist_dialogue(rounds)
    ▼
Episodic RAG  →  episodic/<user>/…                     ← plug-in artifact
```

**Runtime path:** flush exports user/assistant turns from Scene using the v1 fields `speaker` / `text` / `turn_id` / `timestamp`, with real chronological order and timestamps—no thought/action in the archive. `BufferedRound` remains an in-process history and flush-bookkeeping structure; it is not the canonical archive source.

## Dialogue input contract (plug-in view)

The plug-in **never parses Scene**. It only receives archive files and runtime round lists derived by the Chat system.

| Aspect | Runtime contract |
|--------|------------------|
| Turn order | Chronological from Scene; multiple user turns before one reply are allowed |
| Timestamps | Scene `occurred_at` |
| Turn fields | `speaker`, `text`, `turn_id`, `timestamp` |

Upload validation: `dialogue_validation.py`.

## Mount & swap

| Item | Value |
|------|-------|
| Chat pointer | `systems.episodic` → `config/systems/episodic/*.yaml` |
| Default | `config/systems/episodic/rag_default.yaml` |
| Code | `src/m_agent/systems/episodic/` |

```yaml
systems:
  episodic: ../../systems/episodic/my_backend.yaml
```

## Swappable slots

| YAML field | Kind | Default | Role |
|------------|------|---------|------|
| `backend` | `path` → `EpisodicMemoryBackend` | `SimpleRagEpisodicBackend` | **Only public plug-in target** |
| `query.enabled` | switch | `true` | `false` removes recall from prompt + whitelist |
| `query.capability_names` | list | shallow/deep recall | optional |

**Do not set `recorder:`** — fixed `DefaultEpisodeRecorder` (buffers thinking-layer `episode_note` → flush → `on_flush`). Recorder is **not** a public plug-in slot.

### RAG backend kwargs

| kwarg | Meaning |
|-------|---------|
| `storage_dir` | RAG parent directory |
| `workflow_id` | Subdirectory slug |
| `top_k` | Retrieval top-k |
| `embed_model` | `hash` / `alibaba` / `bge` |

## Custom backend protocol

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

Recall payloads need at least `answer`. Framework calls `persist_*`; they are not LLM tools. Treat `rounds` as the sole index-write input; do not depend on Scene paths.

## LLM-facing surfaces (with [tools.md](./tools.md))

| Surface | Layer | Notes |
|---------|-------|-------|
| `shallow_recall` / `deep_recall` | Execution tools | `question: str` → `backend.*_recall` |
| Tool descriptions | Execution prompt | `tools/capabilities/<tool>.yaml` |
| `episode_note` | Thinking output | Internal recorder; LLM does not call recorder |
| Recall rules | Execution prompt | `chat_controller_runtime.yaml` |

**Not visible to LLM:** Scene jsonl, RAG paths, chunk files, embeddings, `persist_*`, `dialogues/` layout.

Keep `query.enabled` aligned with recall entries in `tools.enabled`.

## Chat persistence

| Store | Path (user `test`) | When written | Owner |
|-------|-------------------|--------------|-------|
| Scene log | `data/memory/chat-api/test/scene/*.jsonl` | Runtime append | **System** (not plug-in API) |
| Dialogue archive | `data/memory/chat-api/test/dialogues/` | Successful flush | **System** writes file; **plug-in** indexes rounds |
| Episodic RAG | `data/memory/chat-api/test/episodic/` | Each `persist_round` + flush `persist_dialogue` | **Plug-in** |

Root: `M_AGENT_MEMORY_ROOT`, or `M_AGENT_DATA_DIR/memory`, or `<project>/data/memory`.

Runtime `_rebind_episodic_for_chat_user()` overrides YAML paths. Custom backends should use `chat_user_episodic_rag_paths()` or implement `describe_persistence()`.

**Debug:** `GET /v1/chat/threads/{id}/memory/state` → `episodic_persistence`; normal flush events report `flush_mode: scene`. If Scene export is unexpectedly unavailable, the data-loss recovery path reports `flush_mode: buffered_rounds`.

**Note:** Writing `dialogues/*.json` alone does not build embeddings; indexing requires `persist_dialogue` (upload import or normal flush).

## Flush lifecycle (short)

1. User flush or idle timeout.
2. `ThinkLifeRuntime.build_dialogue_flush_payload` reads Scene `entries_since_flush`.
3. **Agent:** `persist_dialogue_payload` writes v1 Dialogue → `turns_to_rounds` → `backend.persist_dialogue`.
4. On success: `mark_scene_flushed(through_seq)`, drain `episode_note`, `on_flush_segment`, bump `conversation_seq`.
5. Recovery only: if no Scene payload can be built, persist the in-process buffered rounds through `persist_dialogue`.

## Delivery

1. Implement `EpisodicMemoryBackend` (rounds / recall only; no Scene).
2. Copy `rag_default.yaml`, edit `backend.path` / `kwargs`.
3. Point `systems.episodic`.

```bash
pytest tests/systems/episodic/ tests/systems/test_protocol_shapes.py tests/chat/test_scene_dialogue_export.py
```
