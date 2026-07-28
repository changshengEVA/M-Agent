# Episodic 子系统（情景记忆）— 可插拔说明

> English: [episodic.md](./episodic.md) · 总索引：[README.zh-CN.md](./README.zh-CN.md)

## 职责

**跨轮持久化**对话片段，经执行层 recall 工具回答「以前说过什么」。默认实现为本地 RAG（`SimpleRagEpisodicBackend`）。

Episodic **不负责**采集运行时事件、也不负责把 Scene 写成 Dialogue JSON——那是 **Chat 系统层** 的职责。插件只消费系统已经写好的 **Dialogue 归档** 与 `persist_*` 传入的 **round 列表**，构建可检索的情景记忆。

## 系统层 vs 插件边界

| 层级 | 负责 | 不负责 |
|------|------|--------|
| **Chat 系统**（`chat_api_runtime`、`ThinkLifeRuntime`、Scene store） | Scene 记录完整运行时事件；flush 时从 Scene 提取 **user/reply**，按真实时间序写入 **v1 形 Dialogue**；写入 `dialogues/` | RAG chunk、embedding、recall 检索逻辑 |
| **Episodic 插件**（`EpisodicMemoryBackend`） | 从 round 列表建索引（`persist_round` / `persist_dialogue`）；`shallow_recall` / `deep_recall`；flush 时 `on_flush` 合并 `episode_note` | 修正 turn 配对、补时间戳、读 Scene JSONL、决定 flush 时机 |

```text
Scene (jsonl, 系统内部)
    │  flush：build_dialogue_payload_from_scene_entries
    ▼
Dialogue JSON（v1 结构）  →  dialogues/<user>/YYYY-MM/*.json   ← 系统产物（可上传/校验）
    │  turns_to_rounds → persist_dialogue(rounds)
    ▼
Episodic RAG  →  episodic/<user>/…                     ← 插件产物
```

**运行时路径：** flush 从 Scene 导出 user/assistant turns（使用 v1 字段：`speaker` / `text` / `turn_id` / `timestamp`），语序与时间为 Scene 真实值，不含 thought/action。`BufferedRound` 仍用于进程内 history 与 flush 记账，但不是归档内容的权威来源。

## Dialogue 输入契约（插件视角）

插件 **不解析** Scene；只接收：

1. **归档文件**：`data/memory/chat-api/<user>/dialogues/**/*.json`
2. **运行时 round 列表**：`persist_dialogue(..., rounds=[...])`

运行时从 Scene 生成统一的 Dialogue JSON：

| 项目 | 运行时契约 |
|------|------------|
| `turns[]` 语序 | 按 Scene 发生时间，可连续多条 user 再 reply |
| 时间戳 | Scene `occurred_at` |
| turn 字段 | `speaker`, `text`, `turn_id`, `timestamp` |

上传校验见 `dialogue_validation.py`。

## 挂载与切换

| 项 | 值 |
|----|-----|
| Chat 指针 | `systems.episodic` → `config/systems/episodic/*.yaml` |
| 默认 | `config/systems/episodic/rag_default.yaml` |
| 源码 | `src/m_agent/systems/episodic/` |

```yaml
systems:
  episodic: ../../systems/episodic/my_backend.yaml
```

## 可插拔槽位

| YAML 字段 | 类型 | 默认 | 作用 |
|-----------|------|------|------|
| `backend` | `path` → `EpisodicMemoryBackend` | `SimpleRagEpisodicBackend` | **唯一对外插拔点** |
| `query.enabled` | 开关 | `true` | `false` 时从 prompt/白名单移除 recall |
| `query.capability_names` | 列表 | `shallow_recall`, `deep_recall` | 可选 |

**勿配置 `recorder:`** — 系统固定 `DefaultEpisodeRecorder`（缓冲思考层 `episode_note` → flush → `on_flush`）。Recorder **不是**公开插件槽位。

### `SimpleRagEpisodicBackend` kwargs

| kwargs | 含义 |
|--------|------|
| `storage_dir` | RAG 父目录 |
| `workflow_id` | 子目录 slug |
| `top_k` | 检索条数 |
| `embed_model` | `hash` / `alibaba` / `bge` |

## 自定义 backend 须实现

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

- **Recall** 返回值至少含 `answer`。
- **`persist_*`** 由框架在每轮同步路径或 flush 时自动调用，**不是** LLM 工具。
- 自定义 backend 应把 `rounds` 当作唯一写索引输入；若需读历史，读 `dialogues/` 或通过 `describe_persistence()` 暴露的路径，**不要**依赖 Scene 路径。

## 暴露给 LLM（与 [tools.zh-CN.md](./tools.zh-CN.md) 协作）

| 暴露面 | 层级 | 说明 |
|--------|------|------|
| `shallow_recall` / `deep_recall` | 执行层工具 | `question: str` → `backend.*_recall` |
| 工具描述 | 执行层 prompt | `tools/capabilities/<tool>.yaml` |
| `episode_note` | 思考层字段 | 内置 recorder，LLM 不直接调 recorder |
| recall 规则 | 执行层 prompt | `chat_controller_runtime.yaml` |

**LLM 不可见：** Scene jsonl、RAG 路径、chunk 文件、embedding、`persist_*`、`dialogues/` 布局。

`query.enabled` 须与 `tools.enabled` 中的 recall 工具一致。

## Chat 用户级持久化

| 存储 | 路径（用户 `test`） | 写入时机 | 所有者 |
|------|---------------------|----------|--------|
| Scene 日志 | `data/memory/chat-api/test/scene/*.jsonl` | 运行时 append | **系统**（非插件 API） |
| 对话归档 | `data/memory/chat-api/test/dialogues/` | flush 成功 | **系统**写文件；**插件**读 round 建 RAG |
| 情景 RAG | `data/memory/chat-api/test/episodic/` | 每轮 `persist_round` + flush `persist_dialogue` | **插件** |

根目录：`M_AGENT_MEMORY_ROOT` 或 `M_AGENT_DATA_DIR/memory` 或 `<项目>/data/memory`。

Chat API 运行时 `_rebind_episodic_for_chat_user()` 会覆盖 YAML 里的 `storage_dir`/`workflow_id`。自定义 backend 请读 `chat_user_episodic_rag_paths()` 或实现 `describe_persistence()`。

**调试：**

- `GET /v1/chat/threads/{id}/memory/state` → `episodic_persistence`
- 正常 flush 事件 payload 含 `flush_mode: scene`；若 Scene 导出意外不可用，防丢数据恢复路径报告 `flush_mode: buffered_rounds`

**注意：** 仅手工写入 `dialogues/*.json` **不会**自动建 embedding；须经 `persist_dialogue`（上传导入或正常 flush）触发 backend。

## Flush 生命周期（简要）

1. 用户触发 flush 或 idle 超时。
2. `ThinkLifeRuntime.build_dialogue_flush_payload` ← Scene `entries_since_flush`。
3. **Agent：** `persist_dialogue_payload` 写 v1 Dialogue → `turns_to_rounds` → `backend.persist_dialogue`。
4. 成功：`mark_scene_flushed(through_seq)`、drain `episode_note`、`on_flush_segment`、递增 `conversation_seq`。
5. 仅恢复路径：无法构建 Scene payload 时，通过 `persist_dialogue` 持久化进程内 buffered rounds。

## 交付

1. 实现 `EpisodicMemoryBackend`（只处理 rounds / recall，不碰 Scene）。
2. 复制 `rag_default.yaml` → 改 `backend.path` / `kwargs`。
3. 改 `systems.episodic` 指针。

```bash
pytest tests/systems/episodic/ tests/systems/test_protocol_shapes.py tests/chat/test_scene_dialogue_export.py
```
