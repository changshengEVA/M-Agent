# Episodic 子系统（情景记忆）——可插拔说明

> English: [episodic.md](./episodic.md) · 总索引：[README.zh-CN.md](./README.zh-CN.md)

## 职责

当前的 `episodic` 名称首先表示兼容与扩展槽位。v0.2 中，它负责跨轮持久化对话，
并通过 recall capability 回答“以前说过什么”。默认实现是本地
`SimpleRagEpisodicBackend`。

它属于**工具记忆**：模型必须显式调用 recall capability。它不是计划中的
**系统记忆（System Memory）**；后者由 Runtime 管理，并在 Context Compiler 构造
认知上下文时自动评估。路线图安排为 v0.5 以 Shadow 模式接入 System Memory SPI，
v0.6 再把符合条件的记忆接入 Context 主链。

Strategy 是独立的规划中子系统，不是当前 Episodic 功能，也不是现有生产能力。
路线图状态为：v0.6 仅 Shadow 匹配，v0.7 可选注入，v0.8 只有在公开评测达标后
才考虑成为默认候选。

Runtime 负责 Scene 采集、flush 顺序和 Dialogue 归档。Episodic backend 消费由此生成的
round 列表、建立可检索索引并提供 recall；它不解析 Scene 存储，也不决定 flush 时机。

## 当前 v0.2 限制

- 本地 backend 只实现 `shallow_recall`。它为协议兼容保留 `deep_recall` 方法，但会返回
  `supported: false` / `reason: deep_recall_not_supported`，不再把浅层检索包装成具有
  深层语义的召回。
- 在 transaction-bound Runtime 路径中，成功的 Thinking step 会把 `episode_note` 作为
  稳定 Scene entry 提交。不可变 Flush snapshot 会把它写入 Dialogue 的
  `meta.trace_summary.episode_notes`，默认 backend 再按精确 thread/dialogue 建索引；
  失败的推测步骤不会发布 note。standalone/direct 兼容路径仍使用进程内 recorder，
  不具备同等持久性保证。
- 已提交 note 是持久注释 metadata，但还不是类型化、不可变的 Episode，也不是 Runtime
  管理的认知记忆对象。
- 当前 Dialogue RAG 索引不维护 Goal、Belief、Expectation、带来源的状态迁移，也不会
  自动注入 Context。这些能力属于未来的系统记忆与 Cognitive State 路线图。

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
| `EpisodicMemoryBackend` | `persist_round`、`persist_dialogue`、按 thread 隔离的浅层 recall、显式返回“不支持”的深层 recall 兼容接口，以及 note 已送达时的可选合并 | Scene 解析、时间戳修复、flush 调度 |

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

`DefaultEpisodeRecorder` 保留给 standalone/direct 兼容调用。transaction-bound 产品
Runtime 以已提交 Scene entry 和不可变 Flush snapshot 作为持久化边界。

### 默认 backend 参数

| 参数 | 含义 |
|------|------|
| `storage_dir` | RAG 父目录 |
| `workflow_id` | 索引子目录 |
| `top_k` | 检索结果数量 |
| `min_score` | 命中的最低余弦分数（默认 `0.2`）；低于阈值时显式返回 no-hit |
| `embed_model` | `hash`、`alibaba` 或 `bge` |

离线 `hash` embedder 使用跨进程稳定且有版本号的 BLAKE2b feature hash。索引通过
原子文件替换发布带校验和的 `chunks.jsonl`、`embeddings.npy` 和
`index.meta.json`；启动时会从 chunk 日志重建缺失、损坏或不兼容的 metadata / embedding。
召回在排序前按精确 `thread_id` 过滤，因此一个 thread 的内容不会进入另一个 thread
的 evidence。

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

Recall 结果至少包含 `answer`。浅层 no-hit 返回空 answer / evidence、`hit: false` 和
具体 reason；默认 deep 方法返回上文所述的显式“不支持”结果。`persist_*` 是框架调用，
不是 LLM 工具。Backend 把 `rounds` 作为索引写入输入，不依赖 Scene 路径。

## 暴露给 LLM 的表面

| 表面 | 层级 | 说明 |
|------|------|------|
| `shallow_recall` | 默认执行层 capability | 通过 `ControllerCapabilityContext` 调用 backend |
| `deep_recall` | 默认 tools suite 不启用的兼容 capability | 本地 backend 显式返回“不支持” |
| Capability 描述 | 思考层 prompt | `config/systems/tools/capabilities/<tool>.yaml` |
| `episode_note` | 思考层输出 | transaction-bound 成功步骤提交到 Scene，再进入冻结的 Dialogue metadata；direct 兼容调用仍为进程内缓冲 |
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

## Dialogue Flush 生命周期

1. `RuntimeHost.prepare_flush_segment()` 持久化不可变 snapshot。
2. `RuntimeHost.stage_flush_materialization()` 持久化 Dialogue payload。
3. `RuntimeHost.on_flush_segment()` 幂等提交 runtime 边界。
4. Chat memory persistence 写 Dialogue 归档并调用 `backend.persist_dialogue()`。
5. Host 记录 materialization 已投递，并完成该 segment。

Journal 保持标识和 payload digest 稳定，使进程重启后可以继续未完成的 Dialogue
materialization，并避免重复已确认的归档或索引写入。transaction-bound note 属于同一份
冻结 payload，并且只会按匹配的 thread/dialogue 建索引。旧 `on_flush()` 合并仅作为
受 thread/dialogue 约束的兼容路径，不会把 note 附加到无关对话。

## 交付与验证

1. 实现 `EpisodicMemoryBackend`，且不直接读取 Scene。
2. 复制 `rag_default.yaml`，更新 `backend.path` / `kwargs`。
3. 让 `systems.episodic` 指向新文件。

```bash
pytest tests/systems/episodic/ tests/systems/test_protocol_shapes.py
pytest tests/chat/test_scene_dialogue_export.py tests/api/test_runtime_memory_capture.py
pytest tests/runtime/test_runtime_flush_orchestrator.py
```
