# REALTALK（M-Agent）

与 `locomo`、`longmemeval` 同样使用统一环境配置：
`config/eval/memory_agent/realtalk/test_env.yaml`。

## 一键流水线（按 chat 级 sample_id 隔离记忆）

批量运行 `selection` 解析出的所有 sample：

```bash
python scripts/run_realtalk/run_realtalk_pipeline.py --env-config config/eval/memory_agent/realtalk/test_env.yaml --batch
```

单个 sample：

```bash
python scripts/run_realtalk/run_realtalk_pipeline.py --env-config config/eval/memory_agent/realtalk/test_env.yaml --sample-id realtalk-chat-1
```

## 分步命令

1) 导入（dialogues + episodes）：

```bash
python scripts/run_realtalk/import_realtalk.py --env-config config/eval/memory_agent/realtalk/test_env.yaml
```

2) 预热（scene + atomic facts）：

```bash
python scripts/run_realtalk/warmup_realtalk.py --env-config config/eval/memory_agent/realtalk/test_env.yaml
```

3) 评测：

```bash
python scripts/run_realtalk/eval_realtalk.py --env-config config/eval/memory_agent/realtalk/test_env.yaml
```

## 选择规则（selection）

- `sample_ids`：显式指定 chat 级 sample（优先级最高），格式 `realtalk-chat-{chatNo}`。
- `chat_ids`：自动展开为 `realtalk-chat-{chatNo}`。

建议在批量场景使用 `run_realtalk_pipeline.py --batch --test-id-suffix`，避免不同 sample 覆盖同一个 `log/<test_id>/`。
