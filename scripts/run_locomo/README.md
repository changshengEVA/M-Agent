# run_locomo scripts

Config-driven LoCoMo workflow.

## 1) Edit env config

File: `config/eval/memory_agent/locomo/test_env.yaml`

Set:
- `selection.conv_ids`
- optional `selection.questions` (`sample_id + qa_indices`)
- `import.process_id`
- `eval.test_id`
- `eval.memory_agent_config` (if needed)

## 2) Build dialogues + episodes (only selected conv_ids)

```bash
python scripts/run_locomo/import_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
```

## 3) Warmup: generate scenes + atomic facts from episodes

```bash
python scripts/run_locomo/warmup_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
```

Reads the same `eval.memory_agent_config` as `eval_locomo.py`, initializes MemoryCore
with identical parameters, and calls `load_from_episode_path` to build
scene JSON files (with atomic facts + embeddings) into `data/memory/<workflow_id>/scene/`.

Use `--force` to delete existing scenes and regenerate from scratch.
Use `--dry-run` to print resolved config without executing.

If this step is skipped, `eval_locomo.py` will run the same warmup implicitly
on first agent creation (when the scene directory is empty).

## 4) Run evaluation (only selected conv_ids)

```bash
python scripts/run_locomo/eval_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
```

When `selection.questions` is configured, `eval_locomo.py` auto-generates a temporary
question selection file under `log/<test_id>/_env_question_selection.yaml` and runs only those questions.

## Optional tools

```bash
# Sweep hybrid retrieval params
python scripts/run_locomo/sweep_locomo_hybrid_params.py --help

# Draw score charts from log/<test-id>/locomo10_agent_qa_stats.json
python scripts/run_locomo/plot_locomo_scores.py --help

# LLM-judge evaluation for locomo10_agent_qa.json
python scripts/run_locomo/evaluate_agent_qa_llm_judge.py --help
```
