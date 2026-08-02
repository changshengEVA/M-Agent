# Episodic 子系统（情景记忆）——可插拔说明

> English: [episodic.md](./episodic.md) · 总索引：[README.zh-CN.md](./README.zh-CN.md)

## 职责

Episodic 子系统跨轮持久化对话，并通过 recall capability 回答“以前说过什么”。
默认实现是本地 `SimpleRagEpisodicBackend`。

Runtime 负责 Scene 采集、flush 顺序和 Dialogue 归档。Episodic backend 消费由此生成的
round 列表、建立可检索索引并提供 recall；它不解析 Scene 存储，也不决定 flush 时机。

## 单宿主持久化边界

```text
ChatServiceRuntime
  → RuntimeHost (LangGraphRuntime)
      → RuntimeFlushOrchestrator
          → 不可变 Scene/transaction snapshot
          → runtime commit
          → Dialogue materialization
          → flush completion

Dialogue payload
  → ThreeLayerChatAgent.persist_dialogue_payload(...)
  → turns_to_rounds(...)
  → EpisodicMemoryBackend.persist_dialogue(...)
  → episodic index
```

Runtime host 只把 user/assistant entry 导出到 Dialogue 归档，按 Scene 时间排序，并使用
`speaker`、`text`、`turn_id`、`timestamp` 字段。Thought、delegate、effect 和 scheduler
事件继续保留在 Scene 中。

## 所有权

| 层级 | 负责 | 不负责 |
|------|------|--------|
| `RuntimeHost` / `RuntimeFlushOrchestrator` | Scene event、不可变 flush snapshot、runtime commit、materialization journal、completion | RAG chunk、embedding、recall 排序 |
| Chat memory persistence | Dialogue 校验、归档写入、`turns_to_rounds` 转换 | Runtime transaction state、recall 排序 |
| `EpisodicMemoryBackend` | `persist_round`、`persist_dialogue`、浅/深 recall、flush 时合并 episode note | Scene 解析、时间戳修复、flush 调度 |

Scene 是正常 Dialogue materialization 的权威来源。Dialogue 归档属于系统产物，
episodic index 属于子系统产物。

## 挂载与配置

| 项 | 值 |
|----|-----|
| Chat 指针 | `systems.episodic` → `config/systems/episodic/*.yaml` |
| 默认 | `config/systems/episodic/rag_default.yaml` |
| 源码 | `src/m_agent/systems/episodic/` |

```yaml
systems:
  episodic: ../../systems/episodic/my_backend.yaml
```

| YAML 字段 | 类型 | 默认 | 作用 |
|-----------|------|------|------|
| `backend` | `path` → `EpisodicMemoryBackend` | `SimpleRagEpisodicBackend` | 公开的存储/检索实现 |
| `query.enabled` | Boolean | `true` | 控制是否暴露 recall capability |
| `query.capability_names` | List | `shallow_recall`, `deep_recall` | Recall capability 名称 |

产品配置使用 `DefaultEpisodeRecorder`，把思考层 `episode_note` 缓冲到 flush 时处理。

### 默认 backend 参数

| 参数 | 含义 |
|------|------|
| `storage_dir` | RAG 父目录 |
| `workflow_id` | 索引子目录 |
| `top_k` | 检索结果数量 |
| `embed_model` | `hash`、`alibaba` 或 `bge` |

## Backend Protocol

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

Recall 结果至少包含 `answer`。`persist_*` 是框架调用，不是 LLM 工具。Backend 把
`rounds` 作为索引写入输入，不依赖 Scene 路径。

## 暴露给 LLM 的表面

| 表面 | 层级 | 说明 |
|------|------|------|
| `shallow_recall`, `deep_recall` | 执行层 capability | 通过 `ControllerCapabilityContext` 调用 backend |
| Capability 描述 | 思考层 prompt | `config/systems/tools/capabilities/<tool>.yaml` |
| `episode_note` | 思考层输出 | 内部缓冲，并在 flush 时传给 `on_flush` |
| Recall 策略 | Runtime prompt | `config/agents/chat/runtime/chat_controller_runtime.yaml` |

Scene 文件、归档路径、chunk、embedding 与 `persist_*` 不暴露给 LLM。
`query.enabled` 应与 `tools.enabled` 中的 recall 名称一致。

## 持久化位置

| 存储 | 用户 `test` 的示例 | 写入方 |
|------|--------------------|--------|
| Scene | `data/memory/chat-api/test/scene/*.jsonl` | Runtime host |
| Dialogue 归档 | `data/memory/chat-api/test/dialogues/` | Flush 期间的 Chat memory persistence |
| Episodic index | `data/memory/chat-api/test/episodic/` | Episodic backend |

根目录来自 `M_AGENT_MEMORY_ROOT`、`M_AGENT_DATA_DIR/memory` 或项目的 `data/memory`
目录。Chat 启动时通过 `chat_user_episodic_rag_paths()` 应用用户级路径；自定义 backend
可通过 `describe_persistence()` 暴露详情。

## Flush 生命周期

1. `RuntimeHost.prepare_flush_segment()` 持久化不可变 snapshot。
2. `RuntimeHost.stage_flush_materialization()` 持久化 Dialogue payload。
3. `RuntimeHost.on_flush_segment()` 幂等提交 runtime 边界。
4. Chat memory persistence 写 Dialogue 归档并调用 `backend.persist_dialogue()`。
5. Host 记录 materialization 已投递，并完成该 segment。

Journal 保持标识和 payload digest 稳定，使进程重启后可以继续未完成的 flush，且不会
重复写入归档或索引。

## 交付与验证

1. 实现 `EpisodicMemoryBackend`，且不直接读取 Scene。
2. 复制 `rag_default.yaml`，更新 `backend.path` / `kwargs`。
3. 让 `systems.episodic` 指向新文件。

```bash
pytest tests/systems/episodic/ tests/systems/test_protocol_shapes.py
pytest tests/chat/test_scene_dialogue_export.py tests/api/test_runtime_memory_capture.py
pytest tests/runtime/test_runtime_flush_orchestrator.py
```
