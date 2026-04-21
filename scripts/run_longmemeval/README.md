# LongMemEval（M-Agent）

与 LoCoMo 相同：**统一环境配置** [`config/eval/memory_agent/longmemeval/test_env.yaml`](../../config/eval/memory_agent/longmemeval/test_env.yaml)，脚本默认指向该文件。

本仓库的 **eval 只生成** 含 `question_id` 与 `hypothesis` 的 **jsonl**，供上游 LongMemEval 仓库的 `evaluate_qa.py` 打分；不在此实现官方指标。

## 一键流水线（按 question_id 隔离记忆）

目标题目**只从环境配置解析**（见下「`selection`：题目来源优先级」）。解析出几条就跑几条（每条仍是独立 `data/memory/<id>/` 的导入 → 预热 → 评测）。

```bash
python scripts/run_longmemeval/run_longmemeval_pipeline.py --env-config config/eval/memory_agent/longmemeval/test_env.yaml
```

可选 **`--question-id`**：只跑这一条（覆盖配置里解析出的列表）。

```bash
python scripts/run_longmemeval/run_longmemeval_pipeline.py ^
  --env-config config/eval/memory_agent/longmemeval/test_env.yaml ^
  --question-id <question_id>
```

**多条题目时 hypothesis 输出**（默认 `eval.test_id` + `eval.hypothesis_jsonl`）：

- **不加** `--test-id-suffix`：所有题目的 hypothesis 行写入**同一个** `log/<test_id>/<hypothesis_jsonl>`；评测阶段第一题使用 `--overwrite`（仅当 `eval.overwrite: true`），后续题 `--append`。若需覆盖已有文件，请在 YAML 中设 `eval.overwrite: true`。
- **`--test-id-suffix`**：每个 `question_id` 使用 `log/<test_id>__<question_id>/`，各自一份 jsonl，避免同目录覆盖。

```bash
python scripts/run_longmemeval/run_longmemeval_pipeline.py ^
  --env-config config/eval/memory_agent/longmemeval/test_env.yaml ^
  --test-id-suffix
```

若**只**配置了 `selection.sampling`（且 `question_ids` 为空），解析出的每个 `question_id` 同样按上述规则跑流水线。

### `selection`：题目来源优先级

| `selection.question_ids` | `selection.sampling`（`per_question_type` 非空） | 实际跑哪些题 |
|--------------------------|-----------------------------------------------|--------------|
| 非空 | 任意 | **仅** `question_ids`（**忽略** sampling） |
| 空 | 已配置 | 对 `data.file` **全量**按 `question_type` 分层随机抽样 |
| 空 | 未配置 | 报错：需显式 id 或抽样配置 |

**兼容**：若 `selection` 下**没有** `sampling` 键，仍会读取旧配置 **`eval.sampling`**（与「空 question_ids + 抽样」等价）。

### 按题型抽样（`selection.sampling`）

写在 [`test_env.yaml`](../../config/eval/memory_agent/longmemeval/test_env.yaml) 的 **`selection`** 下（与 `question_ids` 并列），字段含义：

| 字段 | 含义 |
|------|------|
| `per_question_type` | **必填**（且非空）才启用抽样。键为数据里的 `question_type` 字符串，值为该类型最多抽取多少条（无放回）。 |
| `seed` | 随机种子，默认 `42`，相同样本可复现。 |
| `default_per_type` | 未出现在 `per_question_type` 里的类型：抽取至多这么条；默认 `0` 表示不抽这些类型。 |

命令行 `eval_longmemeval.py --question-ids a,b,c` 与在 YAML 里写 `question_ids` 一样，视为**显式列表**，不启用抽样。

**仅跑评测写 jsonl**：`eval_longmemeval.py` 在启用抽样且抽到 **多条** 题目时，会多次调用 `run_eval_longmemeval.py`，对同一 `log/<test_id>/<hypothesis_jsonl>` 第一次用 `--overwrite`，之后用 `--append` 追加一行一条。

底层脚本也支持追加：

```bash
python scripts/run_longmemeval/run_eval_longmemeval.py ... --append
```

## 分步命令

**1）编辑环境配置**

文件：`config/eval/memory_agent/longmemeval/test_env.yaml`（字段含义与 `locomo/test_env.yaml` 对齐；`selection` 使用 `question_ids` 而非 `conv_ids`。）

**2）导入（单题一次）**

```bash
python scripts/run_longmemeval/import_longmemeval_one.py --env-config config/eval/memory_agent/longmemeval/test_env.yaml
```

或纯 CLI（不写 YAML）：

```bash
python scripts/run_longmemeval/import_longmemeval_one.py ^
  --data-json data/LongMemEval/data/longmemeval_s_cleaned.json ^
  --question-id <question_id> ^
  --clean-output
```

记忆根目录：`data/memory/longmemeval/<json_stem>/<question_id>/`（可用 `--process-id` 覆盖）。

**3）预热**

不传 `--workflow-id` 时，与导入规则一致：先用 `import.process_id`；若为空且 `selection.question_ids` **只有一条**，则自动使用 `longmemeval/<数据文件 stem>/<question_id>`（与 `import_longmemeval_one` 默认相同）。

```bash
python scripts/run_longmemeval/warmup_longmemeval.py --env-config config/eval/memory_agent/longmemeval/test_env.yaml
```

默认会**抑制**第三方 HTTP 客户端与逐条「Fact extract …」的 INFO（底层为 `warmup_locomo.py`）。需要完整调试输出时：命令行加 **`--debug`**，或在 YAML 的 **`warmup.debug: true`**；也可设环境变量 **`M_AGENT_LOG_DEBUG=1`**。

并行：`warmup.scene_max_workers`（`M_AGENT_SCENE_MAX_WORKERS`）控制多个 episode 文件并行**生成 scene**；`warmup.scene_fact_max_workers`（`M_AGENT_SCENE_FACT_MAX_WORKERS`）控制并行**抽 scene 事实**。默认均为 1。

**4）生成 hypothesis jsonl（供官方评测）**

`eval_longmemeval.py` 使用同样的 `workflow_id` 解析逻辑；一般只需：

```bash
python scripts/run_longmemeval/eval_longmemeval.py --env-config config/eval/memory_agent/longmemeval/test_env.yaml
```

若配置里有多条 `question_ids` 且未设 `import.process_id`，须显式传入 `--workflow-id`（与当前要评的那条记忆根一致）。若仅用 **`selection.sampling`**（或兼容的 `eval.sampling`）且抽到多条题，`eval_longmemeval.py` 会按题切换 `workflow_id`（`longmemeval/<json_stem>/<question_id>`），无需手写。

输出默认：`log/<eval.test_id>/longmemeval_hypothesis.jsonl`（文件名由配置项 `eval.hypothesis_jsonl` 决定）。

同时（新增）：会额外按题写 **每题一个工整 JSON 文件**（推荐阅读这个，而不是 jsonl 压缩行）：

- 默认目录：`log/<eval.test_id>/recall/`
- 默认文件：`log/<eval.test_id>/recall/<question_id>.json`
- 可用 `run_eval_longmemeval.py --recall-dir <folder>` 自定义目录名

**5）证据追踪日志（gold 证据 → segment → facts）**

`eval_longmemeval.py` / `run_eval_longmemeval.py` 的 recall JSON 主要面向“模型检索到了什么”。如果你还需要追踪：
- **正确证据材料**是什么（oracle turn-level `has_answer: true`）
- 这些证据材料分布在哪些 **segment**（映射到 `dialogue_id:episode_id:segment_id`）
- 这些 segments 产出了哪些 **facts**（来自 `data/memory/.../scene/*.json` 的 `facts[].evidence`）

可以在评测跑完后执行：

```bash
python scripts/run_longmemeval/trace_longmemeval_evidence.py --test-id longmemeval_run_test_0100672e --oracle-json data/LongMemEval/data/longmemeval_oracle.json
```

输出：
- `log/<test_id>/recall_trace/<question_id>.json`：每题一份 enriched trace
- `log/<test_id>/recall_trace/summary.jsonl`：每题一行汇总（gold/retrieved/hit 计数）

常用参数：
- `--question-ids a,b,c`：只追踪部分题
- `--recall-dir <folder>`：当你的 recall 不在默认 `recall/` 下时
- `--memory-root <dir>`：当你的 memory 根不在默认 `data/memory/longmemeval/*/<question_id>/` 结构下时
- `--overwrite`：覆盖已有 trace 输出

**5.5）逐轮 Markdown 表（execute / rerank / Judge / 金段 hit·miss）**

在仓库根目录执行，**仅需 `test_id`**（与 `log/<test_id>/` 目录名一致，通常来自环境配置里的 `eval.test_id`）：

```bash
python scripts/run_longmemeval/gen_round_tables_md.py longmemeval_run_try_each_4_without_time_recall
```

（PowerShell 同理，路径用反斜杠即可。）

**输入**（均在 `log/<test_id>/` 下）：

- `recall_trace/<question_id>.json`
- `longmemeval_hypothesis.jsonl.eval-results-gpt-4o`（题目顺序、`hypothesis`、gpt-4o `autoeval_label.label`）

**LOCOMO：** 同一脚本可用 `--eval-file log/<test_id>/locomo10_agent_qa.eval-results-<model>.jsonl` 与 `--title-prefix LOCOMO`；eval jsonl 由 `scripts/run_locomo/evaluate_agent_qa_llm_judge.py --export-eval-jsonl` 生成，`recall_trace` 由 `scripts/run_locomo/trace_locomo_evidence.py` 生成。详见 `scripts/run_locomo/README.md`。

**输出**（写入同一 `log/<test_id>/`）：

| 文件 | 内容 |
|------|------|
| `phase_60_questions_round_tables.md` | **混合**：eval 行顺序下的全部题目；文首有 **绿/红/灰图例**，每题标题为 **绿色条（正确）** / **红色条（错误）** + 浅色底（HTML 预览） |
| `phase_60_questions_round_tables_correct.md` | **仅正确**：`autoeval_label.label == true` |
| `phase_60_questions_round_tables_wrong.md` | **仅错误**：`label == false` |

无 gpt-4o 标签的题目只出现在混合文件中；正确/错误拆分时若一侧为空，仍会生成带说明的短文件。

也可直接调用底层脚本：

```bash
python scripts/run_longmemeval/run_eval_longmemeval.py ^
  --data-file data/LongMemEval/data/longmemeval_s_cleaned.json ^
  --config config/agents/memory/longmemeval_eval_memory_agent.yaml ^
  --test-id longmemeval_try ^
  --workflow-id longmemeval/<json_stem>/<question_id> ^
  --question-ids <question_id> ^
  --overwrite
```

## 官方 QA 打分

本仓库 **只生成** `question_id` + `hypothesis` 的 **jsonl**；**F1 等官方指标**在上游 [LongMemEval](https://github.com/xiaowu0162/LongMemEval) 的 `evaluate_qa.py` 里跑。下面按「先装环境 → 填配置文件 → 一条命令」说明。

---

### 怎么用（按顺序做）

**1）准备两个 conda 环境（不要混用）**

| 环境 | 用途 |
|------|------|
| **MAG**（你平时跑 M-Agent 的） | 导入、预热、`eval_longmemeval.py` 写 jsonl |
| **longmemeval-eval**（新建） | 只装 LongMemEval 的 `requirements-lite.txt`，只跑官方打分 |

在 **longmemeval-eval** 里安装上游 lite 依赖（**不要**在 MAG 里执行，否则会降级 `openai` 与 MAG 冲突）：

```bash
conda create -n longmemeval-eval python=3.9 -y
conda activate longmemeval-eval
cd /path/to/LongMemEval
pip install -r requirements-lite.txt
```

另需：克隆 LongMemEval，并在 `data/` 下准备好 **`longmemeval_oracle.json`**（与上游 README 一致）。

**2）复制配置文件并填写**

在 **M-Agent 仓库根目录**：

```text
copy config\eval\memory_agent\longmemeval\official_eval.example.yaml config\eval\memory_agent\longmemeval\official_eval.yaml
```

（Linux/mac：`cp ...`）`official_eval.yaml` 已在 `.gitignore` 中，**不要提交**。

打开 [`official_eval.yaml`](../../config/eval/memory_agent/longmemeval/official_eval.example.yaml)（你刚复制的文件），至少填这三类：

1. **`longmemeval_root`**：LongMemEval 克隆根目录（含 `src/evaluation/evaluate_qa.py`）。
2. **`python`**：`longmemeval-eval` 里的 `python.exe` 绝对路径。
3. **`api.openai_api_key`**（以及可选的 **`api.openai_base_url`**、**`api.openai_organization`**）：裁判模型走官方或兼容网关时填写；与 OpenAI SDK 环境变量同名含义。

可选字段见下表。

**3）生成 hypothesis jsonl（在 MAG 里）**

```bash
python scripts/run_longmemeval/eval_longmemeval.py --env-config config/eval/memory_agent/longmemeval/test_env.yaml
```

**4）一键跑官方打分（在 M-Agent 根目录）**

```bash
python scripts/run_longmemeval/run_official_evaluate_qa.py
```

先看解析结果再真跑：

```bash
python scripts/run_longmemeval/run_official_evaluate_qa.py --dry-run
```

等价：`.\scripts\run_longmemeval\run_official_evaluate_qa.ps1`（把参数传给 Python）。

命令行参数会 **覆盖** 配置文件里的同名字段；完整列表：`python scripts/run_longmemeval/run_official_evaluate_qa.py --help`。

---

### `official_eval.yaml` 字段说明

| 字段 | 含义 |
|------|------|
| `longmemeval_root` | LongMemEval 仓库根目录（必填，除非用 `--longmemeval-root` / `LONGMEMEVAL_ROOT`） |
| `python` | 运行 `evaluate_qa.py` 的解释器，**必须**是装了 `requirements-lite` 的环境（如 longmemeval-eval） |
| `judge_model` | 传给上游的第一个参数，裁判模型名，如 `gpt-4o` |
| `m_agent_env_config` | M-Agent 的 YAML，用于读 `eval.test_id`、`eval.hypothesis_jsonl`，拼出 `log/<test_id>/<文件>` |
| `hypothesis_jsonl` | 可选；填则覆盖上面的自动拼接（相对项目根的路径） |
| `oracle_json` | 可选；默认 `data/LongMemEval/data/longmemeval_oracle.json` |
| `api.openai_api_key` | 写入子进程环境 `OPENAI_API_KEY`；留空则沿用当前终端/系统已有环境变量 |
| `api.openai_base_url` | 写入 `OPENAI_BASE_URL`（兼容网关）；留空则继承环境 |
| `api.openai_organization` | 写入 `OPENAI_ORGANIZATION`；可选 |

**优先级**：命令行参数 > `official_eval.yaml` > 环境变量（`LONGMEMEVAL_ROOT` 等）。

---

### 与 MAG 的环境冲突（必读）

`requirements-lite.txt` 会固定 **`openai==1.35.1`**。MAG 里常见 **`openai>=2.x`**。若在 MAG 里装 lite，会降级并报错。

若已误装，可尝试：`pip install "openai>=2.20.0,<3"`（在 MAG 中），并检查 `pip check`。

---

### 手动上游命令（可选）

若不用本仓库脚本，可在 `LongMemEval/src/evaluation` 下执行：

```bash
python evaluate_qa.py gpt-4o <hypothesis.jsonl> ../../data/longmemeval_oracle.json
```

hypothesis 建议用 M-Agent 绝对路径。日志与 `print_qa_metrics.py` 用法见 [LongMemEval 上游 README](https://github.com/xiaowu0162/LongMemEval) 的 *Testing Your System*。

**Azure**：若上游脚本写死官方 endpoint，需改上游代码或查其 issue；不能仅靠环境变量。

## 并行（可选）

- Episode 构建：环境变量 `M_AGENT_EPISODE_MAX_WORKERS`，或在 YAML 的 `import.episode_max_workers` 中配置（导入前生效）。
- 预热场景事实：`M_AGENT_SCENE_FACT_MAX_WORKERS`，或 `warmup.scene_fact_max_workers`。
