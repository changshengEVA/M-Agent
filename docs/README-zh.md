# M-Agent

### **M-Agent: A Multi-Dimensional Memory Agent for Long-Term Dialogue Question Answering**

M-Agent 是一个面向 **长期对话记忆问答（Long-Term Dialogue QA）** 的 Agent-Memory 系统，用于解决传统 RAG 在记忆检索场景中的语义匹配问题。

在长期对话记忆中，用户的问题往往具有 **抽象性、跨时间性和推理性**，而原始证据通常是 **局部、具体的文本片段**。  
这种 **query 与 evidence 之间的语义层级偏差** 会导致传统基于 embedding similarity 的检索方法难以正确召回证据。

M-Agent 通过引入 **Retrieval Target Decomposition** 和 **Multi-Dimensional Memory Retrieval**，构建了一个可扩展的记忆检索系统，使 Agent 能够根据不同类型的问题调用对应的检索工具，从而提高记忆问答的准确性。

![pipeline_img](pipeline_img.png)
图 1. 直接Embedding检索与M-Agent检索框架对比，左侧：直接对问题进行Embedding检索，在涉及多实体或抽象关系的问题时容易出现错误召回或无法找到有效证据。
右侧：M-Agent首先将问题拆解为多个子问题，并通过六类语义标签（Entity、Feature、Action、Time、Reason–Result、Theme）进行检索，最终对召回的Episode进行分析得到答案。

---
### **TODO**


---
### **Project Layout**

当前结构中，正式源码统一放在 `src/m_agent/`，CLI 入口集中在 `scripts/`，测试集中在 `tests/`，示例放在 `examples/`，实验性目录放在 `experiments/`。

如果要看完整目录设计说明，可以直接打开 `docs/project-structure.md`。

部署相关可参考：`docs/deployment-server-zh.md`
--- 
### **Quick_start**

以下步骤只覆盖从 0 到运行 `run_eval_locomo.py`。

1. 进入项目根目录并创建虚拟环境

```bash
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\activate

# macOS / Linux
# python3 -m venv .venv
# source .venv/bin/activate
```

2. 安装依赖

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

3. 在项目根目录创建 `.env`，并填写下面字段

```dotenv
# MemoryCore LLM: src/m_agent/load_model/OpenAIcall.py
# Fill one of API_SECRET_KEY or OPENAI_API_KEY
API_SECRET_KEY=YOUR_OPENAI_COMPATIBLE_KEY
OPENAI_API_KEY=
BASE_URL=https://api.openai.com/v1

# Agent model key: model_name=deepseek-chat (config/agents/memory/locomo_eval_memory_agent.yaml)
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_KEY

# Embedding key: embed_provider（在 config/memory/core/*.yaml 中配置）
ALIBABA_API_KEY=YOUR_ALIBABA_KEY
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_EMBED_MODEL=text-embedding-v4

# Optional switches (keep consistent with current repo defaults)
LANGUAGE=zh
EMBED_PROVIDER=aliyun
LLM_PROVIDER=deepseek
```

4. 先做 LoCoMo 预处理（配置驱动 + conv_id 对齐）

先编辑 `config/eval/memory_agent/locomo/test_env.yaml`：
- `selection.conv_ids`（要构造/评测的会话）
- `import.process_id`（workflow_id）
- `eval.test_id`

然后执行：

```bash
python scripts/run_locomo/import_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
```

预处理完成后会在 `data/memory/<process_id>/` 生成（或更新）：

- `dialogues/`
- `episodes/`
- `scene/`（由 MemoryCore 在导入 `episodes/` 时内部生成）

5. 运行 LoCoMo 评测脚本（同一配置文件）

```bash
python scripts/run_locomo/eval_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
```

6. 查看输出结果

- `log/<test-id>/locomo10_agent_qa.json`
- `log/<test-id>/locomo10_agent_qa_stats.json`
- `log/<test-id>/locomo10_agent_qa_run.log`
- `log/<test-id>/locomo10_agent_qa_qa_trace.jsonl`

每条 QA 结果可能还会保存内部规划元数据字段 `memory_agent_prediction_plan`，
与预测与证据等字段一并写入。
