# M-Agent

### **M-Agent: A Multi-Dimensional Memory Agent for Long-Term Dialogue Question Answering**

M-Agent is an **Agent-Memory** system for **Long-Term Dialogue QA**, designed to address semantic mismatch issues that standard RAG pipelines often face in memory retrieval.

In long-horizon dialogue memory, user questions are often **abstract, cross-temporal, and reasoning-heavy**, while raw evidence is usually **local and concrete text fragments**.
This semantic-level gap between **query** and **evidence** makes pure embedding-similarity retrieval unreliable.

M-Agent introduces **Retrieval Target Decomposition** and **Multi-Dimensional Memory Retrieval**, so the agent can pick retrieval tools based on question type and improve answer accuracy.

![pipeline_img](docs/pipeline_img.png)
Figure 1. Comparison between direct embedding retrieval and the M-Agent retrieval framework.
Left: directly embedding the question may fail on multi-entity or abstract-relation questions.
Right: M-Agent decomposes the question into sub-questions and retrieves evidence using six semantic dimensions (Entity, Feature, Action, Time, Reason-Result, Theme), then synthesizes answers from recalled episodes.

---
### **TODO**


---
### **Quick_start**

The following steps only cover the shortest path from 0 to running `run_eval_locomo.py`.

1. Create and activate a virtual environment

```bash
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\activate

# macOS / Linux
# python3 -m venv .venv
# source .venv/bin/activate
```

2. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

3. Create `.env` in the project root and fill the fields below

```dotenv
# MemoryCore LLM: load_model/OpenAIcall.py
# Fill one of API_SECRET_KEY or OPENAI_API_KEY
API_SECRET_KEY=YOUR_OPENAI_COMPATIBLE_KEY
OPENAI_API_KEY=
BASE_URL=https://api.openai.com/v1

# Agent model key: model_name=deepseek-chat (config/prompt/agent_sys.yaml)
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_KEY

# Embedding key: embed_provider=aliyun (config/prompt/agent_sys.yaml)
ALIBABA_API_KEY=YOUR_ALIBABA_KEY
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_EMBED_MODEL=text-embedding-v4

# Optional switches (keep consistent with current repo defaults)
LANGUAGE=zh
EMBED_PROVIDER=aliyun
LLM_PROVIDER=deepseek
```

4. Run LoCoMo preprocessing first (`memory_pre`)

> `run_eval_locomo.py` uses `config/prompt/agent_sys.yaml` by default, where `workflow_id` is `testlocomo`.
> Keep preprocessing `--id` the same (`testlocomo`), or change both to the same value.

```bash
python pipeline/memory_pre.py --id testlocomo --data-source data/locomo/data/locomo10.json --loader-type locomo --embed-provider aliyun
```

After preprocessing, these folders will be generated/updated under `data/memory/testlocomo/`:

- `dialogues/`
- `episodes/`
- `kg_candidates/`
- `scene/`

5. Run LoCoMo evaluation

```bash
# Quick check: sampled run
python run_eval_locomo.py --test-id quickstart --sample-fraction 0.1

# Full run: 10/10 samples
# python run_eval_locomo.py --test-id quickstart-full --sample-fraction 1.0
```

6. Check outputs

- `log/<test-id>/locomo10_agent_qa.json`
- `log/<test-id>/locomo10_agent_qa_stats.json`
- `log/<test-id>/locomo10_agent_qa_run.log`
- `log/<test-id>/locomo10_agent_qa_qa_trace.jsonl`
