# run_locomo 脚本

由配置文件驱动的 LoCoMo 工作流。

## 一键流水线（按会话隔离记忆）

为避免多个会话混在同一目录 `data/memory/<id>/` 下，请对每个会话单独执行 **导入 → 预热 → 评测**，并使用相同的 `process_id` / `workflow_id`（默认形如 `locomo/<conv_id>`）：

```bash
python scripts/run_locomo/run_locomo_pipeline.py --env-config config/eval/memory_agent/locomo/test_env.yaml --conv-id conv-30
```

对环境配置文件中列出的每个 `selection.conv_ids` 批量执行：

```bash
python scripts/run_locomo/run_locomo_pipeline.py --env-config config/eval/memory_agent/locomo/test_env.yaml --batch --test-id-suffix
```

**日志：** 流水线启动时会完整打印一次 `data` / `selection` / `import` / `warmup` / `eval` 以及 `pipeline_cli`。子步骤会设置 `LOCOMO_SKIP_ENV_CONFIG_LOG=1`，因此 import / warmup / eval 不会重复整段 YAML；它们仍会打印简短标题、解析后的 `footer` 行以及相关环境变量。若单独运行各脚本，则会记录完整配置块。

以下 CLI 参数也可在各子脚本中单独使用：

- `import_locomo.py`：`--process-id`、`--conv-ids`
- `warmup_locomo.py`：`--workflow-id`
- `eval_locomo.py`：`--workflow-id`、`--test-id`、`--conv-ids`
- `run_eval_locomo.py`：`--workflow-id`

**并行度** 可在环境 YAML 中配置（推荐），或通过环境变量设置（若 YAML 中写了同名项，则以 YAML 为准）：

- `import.episode_max_workers` → 在构建 episode 前设置 `M_AGENT_EPISODE_MAX_WORKERS`（配置中默认 `1`）。
- `warmup.scene_fact_max_workers` → 在场景事实抽取前设置 `M_AGENT_SCENE_FACT_MAX_WORKERS`（配置中默认 `1`）。

## 1）编辑环境配置

文件：`config/eval/memory_agent/locomo/test_env.yaml`

结构说明：

| 区块 | 作用 |
|------|------|
| `data` | LoCoMo JSON 路径与 `loader_type`（导入与评测共用）。 |
| `selection` | `conv_ids` 以及可选的 `questions`（问答子集）。 |
| `import` | `process_id`、`clean_output`、**`episode_max_workers`**。 |
| `warmup` | **`scene_fact_max_workers`**，可选 **`force`**（与 CLI `--force` 相同）。 |
| `eval` | `test_id`、`memory_agent_config`、采样、`overwrite`、`recall_dir`、`llm_judge_exclude_category_5` 等。 |

常见修改项：

- `selection.conv_ids`
- 可选 `selection.questions`（`sample_id` + `qa_indices`）
- `import.process_id`
- 调并行时修改 `import.episode_max_workers` / `warmup.scene_fact_max_workers`
- `eval.test_id`、`eval.memory_agent_config`

## 2）构建对话与 episode（仅所选 conv_ids）

```bash
python scripts/run_locomo/import_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
```

## 3）预热：由 episode 生成场景与原子事实

```bash
python scripts/run_locomo/warmup_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
```

与 `eval_locomo.py` 读取相同的 `eval.memory_agent_config`，以相同参数初始化 MemoryCore，并调用 `load_from_episode_path`，将场景 JSON（含原子事实与向量）写入 `data/memory/<workflow_id>/scene/`。

使用 `--force` 或 `warmup.force: true` 可删除已有场景并从头生成。使用 `--dry-run` 仅打印解析后的配置，不执行。

默认会抑制第三方 HTTP 与逐条 Fact extract 的 INFO；需要完整调试输出时加 **`--debug`**、在 YAML 中设 **`warmup.debug: true`**，或设置 **`M_AGENT_LOG_DEBUG=1`**。

并行度：`warmup.scene_max_workers` → 环境变量 `M_AGENT_SCENE_MAX_WORKERS`（多个 `by_dialogue/.../episodes_v1.json` 并行生成 scene）；`warmup.scene_fact_max_workers` → `M_AGENT_SCENE_FACT_MAX_WORKERS`（多个 scene 文件并行抽原子事实）。未设置时默认为 1。

若跳过此步，`eval_locomo.py` 在首次创建智能体且场景目录为空时，会隐式执行相同的预热。

## 4）运行评测（仅所选 conv_ids）

```bash
python scripts/run_locomo/eval_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
```

当配置了 `selection.questions` 时，`eval_locomo.py` 会在 `log/<test_id>/_env_question_selection.yaml` 下自动生成临时选题文件，并只跑这些题目。

**评测产物（与 LongMemEval 对齐的链路）：**

- `log/<test_id>/locomo10_agent_qa.json`：聚合预测与指标（与此前相同）。
- `log/<test_id>/locomo10_agent_qa_qa_trace.jsonl`：逐题断点恢复用 trace（每行含 `trace_id`、`recall_json` 等）。
- `log/<test_id>/<recall_dir>/`：默认 `recall/`，每题 `sample_id__q<源下标>.json` + 可选 `.../<stem>/Workspace/round_XXX.json`（与 `run_eval_longmemeval.py` 同构）。

在 **完成一次 eval** 且已有 `data/memory/<workflow_id>/` 后，可生成 **recall_trace** 与 **逐轮 Markdown**（需先跑 LLM judge 并导出 eval jsonl，以便与 `gen_round_tables_md.py` 对接）：

```bash
# 1) 可选：LLM 裁判并导出 LongMemEval 风格的 eval jsonl（与下方 gen_round_tables 的 --eval-file 一致即可）
python scripts/run_locomo/evaluate_agent_qa_llm_judge.py \
  --env-config config/eval/memory_agent/locomo/test_env.yaml \
  --input log/<test_id>/locomo10_agent_qa.json \
  --export-eval-jsonl log/<test_id>/locomo10_agent_qa.eval-results-gpt-4o.jsonl

# 2) 由 recall + 标注 + memory 生成 recall_trace/（--workflow-id 须与 import 的 process_id 一致）
python scripts/run_locomo/trace_locomo_evidence.py \
  --test-id <test_id> \
  --workflow-id locomo/conv-50 \
  --data-file data/locomo/data/locomo10.json \
  --overwrite

# 3) 生成与 LongMemEval 相同版式的 phase_60_questions_round_tables*.md
python scripts/run_longmemeval/gen_round_tables_md.py <test_id> \
  --eval-file log/<test_id>/locomo10_agent_qa.eval-results-gpt-4o.jsonl \
  --title-prefix LOCOMO
```

LongMemEval 侧说明见 [`scripts/run_longmemeval/README.md`](../run_longmemeval/README.md)（`recall_trace`、`gen_round_tables_md.py`）。

## 可选工具

```bash
# 扫描混合检索参数
python scripts/run_locomo/sweep_locomo_hybrid_params.py --help

# 根据 log/<test-id>/locomo10_agent_qa_stats.json 绘制分数图
python scripts/run_locomo/plot_locomo_scores.py --help

# 对 locomo10_agent_qa.json 做 LLM 裁判评测（建议加 --env-config 以读取 eval.llm_judge_exclude_category_5，与 run_eval 一致跳过 category 5）
python scripts/run_locomo/evaluate_agent_qa_llm_judge.py --help
```
