# Test7 `search_details` 深挖分析（按正确样本族群）

## 1) 范围与判定口径

- 数据文件：`log/test7/locomo10_agent_qa.json`
- 样本集合：`memory_agent_llm_judge_score == 1.0`（正确样本）
- 总正确样本数：74
- 分群依据：每个问题在工具调用中 `tool_name == "search_details"` 的调用次数（`sd_count`）

---

## 2) 正确样本族群分布

| 族群 | `search_details` 次数 | 样本数 | 占比 |
|---|---:|---:|---:|
| A | 0~3 | 64 | 86.5% |
| B | 4 | 4 | 5.4% |
| C | 5~10 | 5 | 6.8% |
| D | >10 | 1 | 1.4% |

补充统计（正确样本）：

- `sd_count` 分布：`0:1, 1:48, 2:8, 3:7, 4:4, 5:2, 8:1, 9:2, 100:1`
- 中位数 `median=1`，`P75=2`，`P90=4`，`P95≈6.05`
- `<=3` 次占 86.5%，`<=5` 次占 94.6%

---

## 3) 每个族群的真实样例（看 `detail` 如何填写）

## A 族群（0~3 次）：主流、短链路、问题导向

### 样例 A1（`qa_index=0`, `sd=1`）

- 问题：`When Jon has lost his job as a banker?`
- 子问题：无
- 调用序列：`SD -> SC`
- `search_details.detail`：
  - `Jon lost his job as a banker`

说明：一次直达式 detail，然后 `search_content` 拉原文取时间。

### 样例 A2（`qa_index=2`, `sd=3`）

- 问题：`How do Jon and Gina both like to destress?`
- 子问题：无
- 调用序列：`SD -> SC -> SD -> SD -> SC`
- `search_details.detail`：
  - `Jon and Gina both like to destress`
  - `Jon likes to destress by dancing`
  - `Gina likes to destress`

说明：先“合并问法”，再拆成“按人分解”的两条 detail。

### 样例 A3（`qa_index=58`, `sd=2`，含子问题）

- 问题：`Why did Jon shut down his bank account?`
- 子问题（2条）：
  - `Find events or statements where Jon mentions closing or planning to close his bank account.`
  - `Extract the reason(s) Jon gives for this action in those events/statements.`
- 调用序列：`RID -> SD -> SC -> SD -> SC`
- `search_details.detail`：
  - `Jon shut down bank account closing bank account`
  - `Jon shut down bank account reason why`

说明：detail 与子问题是“一问一条”对应关系，先定位事件，再抽原因。

---

## B 族群（4 次）：轻度扩写、同义改写增强召回

### 样例 B1（`qa_index=18`, `sd=4`）

- 问题：`Do Jon and Gina start businesses out of what they love?`
- 子问题：无
- 调用序列：`SD -> SD -> SD -> SD -> SC`
- `search_details.detail`：
  - `Jon start business out of what he loves`
  - `Gina start business out of what she loves`
  - `Jon business love passion`
  - `Gina business love passion`

说明：4 次都在做“同义与主体拆分”，无明显漂移。

### 样例 B2（`qa_index=4`, `sd=4`，含子问题）

- 问题：`Why did Jon decide to start his dance studio?`
- 子问题（3条）：
  - `What explicit reasons did Jon give for starting the dance studio?`
  - `What events or circumstances preceded Jon's decision to start the studio?`
  - `Did anyone influence or encourage Jon to start the dance studio?`
- 调用序列（简化）：`RID -> SEV -> ... -> SD -> SC -> SD -> SC -> ... -> SD -> SD -> SEV -> ...`
- `search_details.detail`：
  - `Jon starting dance studio circumstances events before decision`
  - `Jon lost job banker before starting dance studio`
  - `Jon influenced encouraged to start dance studio`
  - `Jon encouraged by someone to start dance studio`

说明：detail 主要覆盖“前置事件 + 影响因素”两个子问题，reason 子问题更依赖事件检索与原文读取补齐。

---

## C 族群（5~10 次）：多子问题驱动，循环检索更明显

### 样例 C1（`qa_index=17`, `sd=9`，5个子问题）

- 问题：`Why did Gina decide to start her own clothing store?`
- 子问题数量：5
- 调用序列（简化）：`RID/SEV/SD/SC` 的子问题循环
- `search_details.detail`（节选）：
  - `Gina reasons motivations starting her own clothing store`
  - `Gina why start clothing store motivation reason`
  - `Gina starting clothing store circumstances events before decision`
  - `Gina lost her job before starting clothing store`
  - `Gina's professional situation before starting clothing store`
  - `Gina inspiration role model influence starting clothing store business`
  - `Gina clothing store goals aspirations vision`

说明：detail 基本按子问题主题分块填写，属于“结构化多轮补证”。

### 样例 C2（`qa_index=56`, `sd=5`，1个子问题）

- 问题：`What did Jon and Gina compare their entrepreneurial journeys to?`
- 子问题（1条）：找比喻
- 调用序列：`SD -> SD -> SD -> SD -> SC -> SC -> SD`
- `search_details.detail`：
  - `Jon Gina compare entrepreneurial journeys metaphor analogy`
  - `entrepreneurial journey comparison metaphor like`
  - `Jon Gina entrepreneurial journey compared to`
  - `"entrepreneurial journey" "like" "metaphor" Jon Gina`
  - `"like having a partner to dance with" entrepreneurial journey`

说明：先抽象词扩检，再贴近原句/短语锚定。

### 样例 C3（`qa_index=63`, `sd=8`）

- 问题：`What kind of professional experience did Gina get accepted for on May 23, 2023?`
- 子问题（1条）：找“May 23 + accepted for”
- `search_details.detail`（有重复）：
  - `Gina accepted professional experience on May 23 2023`
  - `May 23 2023 Gina accepted internship`
  - `May 23 2023 Gina accepted`
  - `fashion internship accepted Gina`
  - `Gina accepted professional experience May 23 2023`
  - `May 23 2023 Gina accepted`
  - `May 23 2023 fashion internship`
  - `accepted internship May 23`

说明：该类出现“同义重试 + 轻度重复”，但仍可在 10 次内收敛。

---

## D 族群（>10 次）：极端重试与 query 漂移

### 样例 D1（`qa_index=37`, `sd=100`, `sc=50`）

- 问题：`When did Gina mention Shia Labeouf?`
- 子问题（1条）：找 Gina 提及 Shia Labeouf 的时间
- 调用序列：大量 `SD` 与 `SC` 交替，未及时收敛
- `search_details.detail` 特征：
  - 前期：`Gina mention Shia Labeouf` / `Gina said Shia Labeouf` / `Shia Labeouf actor`
  - 中期：开始“无限扩词链”  
    `... in conversation with Jon about ... film or movie or show or TV ...`
  - 后期：再切 `topk=10` 继续同义重试

定量特征：

- 100 条 detail 里仅 76 条唯一，唯一率 0.76（重复显著）
- 这是正确样本里的唯一重尾离群点

---

## 4) “怎么看调用方式”可操作方法（你后续可直接复查）

你要看的不是最终答案文本，而是这三层：

1. `memory_agent_prediction_sub_questions`  
2. `memory_agent_prediction_tool_calls[*].tool_name` 的序列  
3. 每次 `search_details` 的 `params.detail` 文本

对应结论就是：

- 该问题是否被拆子问题
- 每个子问题阶段有没有写入新的 detail
- detail 是“有信息增量”还是“同义重复/漂移”

---

## 5) 从样例提炼出的调用模式（按可解释性）

- 模式 M1（直达型）：`SD -> SC`，1~2 次即可定位
- 模式 M2（主体拆分型）：先联合问法，再按实体分两条 detail
- 模式 M3（子问题循环型）：对子问题逐个做 `SEV/SD/SC` 补证
- 模式 M4（重试漂移型）：detail 词面不断扩张、重复增加、收益下降

---

## 6) 对你当前担心点的直接回答

- “重复执行 `search_details` 有必要吗？”  
  有必要，但只在 M2/M3 这类“每次 detail 有新增语义”的重试有价值。  
  D 族群这类高频重试大多是低收益重复。

- “该看什么判断重试是否合理？”  
  看 detail 增量而不是只看调用次数：  
  新实体/新关系/新时间约束进入 detail，才是有效重试。

