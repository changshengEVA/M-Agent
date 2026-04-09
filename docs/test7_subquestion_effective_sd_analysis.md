# Test7 子问题级 `search_details` 调用分析（正确样本）

## 1. 分析口径

- 数据：`log/test7/locomo10_agent_qa.json`
- 正确判定：`memory_agent_llm_judge_score == 1.0`
- 只看“有子问题”的样本：共 7 题
- 关键指标：  
  - `sd_total`：该题 `search_details` 总调用次数  
  - `subq_count`：该题子问题数量  
  - `sd_per_subq = sd_total / subq_count`  
  - `effective_subq`：至少被一条 `detail` 直接覆盖的子问题

说明：当前日志没有“调用->子问题”的显式字段，下面的子问题分配是基于 `detail` 文本与子问题语义对齐得到的可解释映射。

---

## 2. 先看你的核心判断（是否“子问题多导致总调用高”）

有子问题的 7 题如下（按 `sd_per_subq`）：

| qa_index | subq_count | sd_total | sd_per_subq | 问题 |
|---|---:|---:|---:|---|
| 4 | 3 | 4 | 1.33 | Why did Jon decide to start his dance studio? |
| 17 | 5 | 9 | 1.80 | Why did Gina decide to start her own clothing store? |
| 58 | 2 | 2 | 1.00 | Why did Jon shut down his bank account? |
| 59 | 4 | 9 | 2.25 | Why did Gina combine her clothing business with dance? |
| 56 | 1 | 5 | 5.00 | What did Jon and Gina compare their entrepreneurial journeys to? |
| 63 | 1 | 8 | 8.00 | What kind of professional experience did Gina get accepted for on May 23, 2023? |
| 37 | 1 | 100 | 100.00 | When did Gina mention Shia Labeouf? |

直接结论：

- 多子问题题目（`subq_count > 1`）的 `sd_per_subq` 仅 `1.00~2.25`，平均 `1.596`。  
- 真正极高调用来自单子问题样本（尤其 `qa37` 的 100 次），不是“子问题多”导致。

---

## 3. 子问题级分配明细（你要的“每个子问题调用几次”）

## qa4

- Q: `Why did Jon decide to start his dance studio?`
- `subq_count=3`, `sd_total=4`, `effective_subq=2`

子问题分配：

- SQ1 `What explicit reasons did Jon give ...` -> `0`
- SQ2 `What events or circumstances preceded ...` -> `2`（D1, D2）
- SQ3 `Did anyone influence or encourage ...` -> `2`（D3, D4）

备注：理由类信息主要靠 `search_entity_event + search_content` 补齐，而非额外 `detail`。

## qa17

- Q: `Why did Gina decide to start her own clothing store?`
- `subq_count=5`, `sd_total=9`, `effective_subq=5`

子问题分配：

- SQ1 motivations -> `2`（D1, D2）
- SQ2 preceding events -> `2`（D3, D4）
- SQ3 professional/personal situation -> `3`（D5, D6, D7）
- SQ4 inspirations/influences -> `1`（D8）
- SQ5 goals/aspirations -> `1`（D9）

## qa58

- Q: `Why did Jon shut down his bank account?`
- `subq_count=2`, `sd_total=2`, `effective_subq=2`

子问题分配：

- SQ1 事件定位 -> `1`（D1）
- SQ2 原因抽取 -> `1`（D2）

## qa59

- Q: `Why did Gina combine her clothing business with dance?`
- `subq_count=4`, `sd_total=9`, `effective_subq=4`

子问题分配：

- SQ1 business called/involve -> `3`（D1, D2, D3）
- SQ2 dance background -> `2`（D4, D5）
- SQ3 event/decision/action -> `2`（D6, D7）
- SQ4 motivations -> `2`（D8, D9）

## qa56

- Q: `What did Jon and Gina compare their entrepreneurial journeys to?`
- `subq_count=1`, `sd_total=5`, `effective_subq=1`

子问题分配：

- SQ1 metaphorical comparison -> `5`

## qa63

- Q: `What kind of professional experience did Gina get accepted for on May 23, 2023?`
- `subq_count=1`, `sd_total=8`, `effective_subq=1`

子问题分配：

- SQ1 professional experience accepted on date -> `8`

## qa37

- Q: `When did Gina mention Shia Labeouf?`
- `subq_count=1`, `sd_total=100`, `effective_subq=1`

子问题分配：

- SQ1 mention + timestamp -> `100`

备注：这是单子问题的重试失控案例，不是多子问题摊薄问题。

---

## 4. 这份分析对你当前判断的意义

- 你的判断是对的：不能只看整题 `sd_total`。  
- 更合理的是看 `sd_per_subq` 和 `effective_subq`。  
- 从 test7 正确样本看，多子问题题目整体是“低到中等重试”；异常重试主要出现在单子问题未收敛时。

---

## 5. 建议后续监控字段（评测层）

建议把以下字段加入评测输出：

- `sd_total`
- `subq_count`
- `effective_subq_count`
- `sd_per_subq`
- `max_sd_single_subq`
- `single_subq_over_retry_flag`（例如 `subq_count=1 且 sd_total>12`）

