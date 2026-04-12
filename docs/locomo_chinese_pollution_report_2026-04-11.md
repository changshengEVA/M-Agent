# LoCoMo 测试“中文污染”排查报告（2026-04-11）

## 1. 背景与范围
- 目标配置：`config/eval/memory_agent/locomo/test_env.yaml`
- 目标现象：按上述配置评测时，英文 QA 输出中混入中文。
- 本次核查范围：配置链路、提示词加载、评测调度、历史数据源、实际产出日志/trace。

## 2. 结论（TL;DR）
- 该现象已被确认：本轮 `conv-30` 评测中，`81` 道实际作答题里有 `22` 道出现中文输出（约 `27.2%`）。
- 主要根因不是数据集或 memory 素材本身含中文，而是**模型在 tool-agent 直答链路里发生语言漂移**（English 输入 -> Chinese 输出）。
- 当前链路没有“英文强约束”或“输出语言后验校验”，导致漂移结果被直接落盘，形成“中文污染”。
- 另外存在一个独立风险：runtime prompt 的 `zh` 文本普遍乱码（编码损坏），虽不是本次 English 评测的直接触发点，但会放大后续语言稳定性问题。

## 3. 关键证据

### 3.1 污染已发生且可量化
- 结果文件：`log/rebuild_test_facts_only/locomo10_agent_qa.json`
- Trace 文件：`log/rebuild_test_facts_only/locomo10_agent_qa_qa_trace.jsonl`
- 统计结果：
  - 总作答题数：`81`
  - 含中文题数：`22`
  - `memory_agent_prediction` 含中文：`13/81`
  - `memory_agent_prediction_answer` 含中文：`22/81`
  - `memory_agent_prediction_evidence` 含中文：`22/81`
  - `memory_agent_prediction_plan_summary` 含中文：`22/81`
- 污染样例（trace）：`qa_index=5,6,11,12,...,78` 等题出现中文回答/证据/plan_summary。

### 3.2 数据源与记忆素材不是中文源头
- `data/locomo/data/locomo10.json`：未检测到中文。
- `data/memory/test_facts_only/scene`、`dialogues`、`episodes`：未检测到中文 JSON 文件内容。
- 说明：污染不是从数据集或导入后的记忆素材“带入”。

### 3.3 不是旧结果残留，也不是会话串线
- 评测配置中启用了 `overwrite: true`：`config/eval/memory_agent/locomo/test_env.yaml:35`
- 代码在 `overwrite` 时会以写模式覆盖 trace：`scripts/run_locomo/run_eval_locomo.py:1435`
- 每道题 thread_id 唯一：`scripts/run_locomo/run_eval_locomo.py:1483`
- 说明：不是历史输出残留，也不是不同题之间 thread 混用导致。

### 3.4 English prompt 选择链路正常
- agent 与 memory core 都配置为英文：
  - `config/agents/memory/locomo_eval_memory_agent.yaml:7`
  - `config/memory/core/locomo_eval_memory_core.yaml:5`
- 语言解析只允许 `zh/en` 并按指定语言取值：`src/m_agent/prompt_utils.py:24-30,53-67,100-105`
- MemoryAgent 初始化中将 `prompt_language` 传入并按该语言解析 prompt：`src/m_agent/agents/memory_agent/core.py:105-111,178-187`
- 说明：本轮并不存在“误读成 zh prompt”的直接证据。

### 3.5 污染集中在 tool-agent 直答链路
- 本轮所有污染样本都发生在“非拆解直答”（`sub_questions=[]`）路径。
- 直答路径使用 `agent.invoke`：`src/m_agent/agents/memory_agent/mixins/execution.py:301-310`
- 直答输出未做语言校验，直接进入最终 payload：`src/m_agent/agents/memory_agent/mixins/execution.py:313-318,272-300`
- 对照：分解闸门 `decomposition_gate`（`model.invoke`）产出的 `decomposition_reason` 全为英文，说明漂移主要出现在 tool-agent 生成阶段。

### 3.6 Prompt 本身仅“软约束”语言
- runtime 英文 system prompt写法是“跟随用户语言”：
  - `config/agents/memory/runtime/agent_runtime_facts_only.yaml:102-103`
- 该约束不是“English-only hard constraint”，在多语模型上会出现随机语言漂移。

### 3.7 编码风险（次要但明确存在）
- `agent_runtime_facts_only.yaml` 与 `agent_runtime.yaml` 的 `zh` 区块存在明显乱码（如 `浣犳...`）。
- 同类乱码还出现在部分 Python 文件注释/字符串中。
- 本次 English 评测不直接读取 `zh` 变体，但这属于必须治理的配置卫生问题。

## 4. 根因判断

### 一级根因（本次污染直接原因）
- 模型在 tool-agent 直答阶段出现语言漂移，且链路缺少输出语言硬约束与后验过滤。
- 使用 `deepseek-chat`（多语模型）时，此问题在长链路、多轮工具调用后更容易暴露。

### 二级根因（放大因素）
- prompt 只写“follow user's language”，未对 LoCoMo 英文评测设定强制英文。
- 结果写盘前没有 CJK 检测/重试机制。

### 三级风险（配置质量）
- `zh` 提示词编码损坏，会导致中文模式下进一步异常（乱码污染/行为异常）。

## 5. 修复建议（按优先级）

### P0（建议立即）
- 在 LoCoMo 评测专用 runtime prompt（`agent_runtime_facts_only.yaml` 的 `en`）增加硬约束：
  - “For this evaluation, output must be English only (answer/evidence/plan_summary).”
- 在 `run_eval_locomo.py` 或 MemoryAgent 收口处增加语言守卫：
  - 若问题为英文但输出含 CJK，则触发一次“English-only retry”或标记 `language_contaminated=true`。

### P1（建议尽快）
- 为 `MemoryAgent` 增加可配置 `response_language_lock`（如 `en`）。
- 将直答路径和分解路径统一加输出语言一致性检查。

### P2（治理项）
- 统一修复 runtime/config/code 中乱码文本（至少先修 `config/agents/memory/runtime/*.yaml`）。
- 加入 pre-commit 或 CI 文本编码检查（UTF-8 + no replacement char）。

## 6. 建议验收标准
- 同配置重复跑 3 次（固定 seed），`answer/evidence/plan_summary` 的 CJK 占比为 0。
- 如果启用语言守卫，出现 CJK 时可见明确重试或标记日志。
- 修复后中文模式（`prompt_language=zh`）输出不出现乱码字符。

## 7. 受影响文件（排查中关键）
- `config/eval/memory_agent/locomo/test_env.yaml`
- `config/agents/memory/locomo_eval_memory_agent.yaml`
- `config/memory/core/locomo_eval_memory_core.yaml`
- `config/agents/memory/runtime/agent_runtime_facts_only.yaml`
- `config/agents/memory/runtime/agent_runtime.yaml`
- `scripts/run_locomo/run_eval_locomo.py`
- `src/m_agent/agents/memory_agent/core.py`
- `src/m_agent/agents/memory_agent/mixins/execution.py`
- `src/m_agent/prompt_utils.py`
- `log/rebuild_test_facts_only/locomo10_agent_qa.json`
- `log/rebuild_test_facts_only/locomo10_agent_qa_qa_trace.jsonl`
