# 根本方案：统一「记忆证据可答性契约」（非按题型归类）

## 为什么不按题型归类

按「开业 / 实习录取 / 时长 / …」逐类加规则会无限膨胀，且模型仍会在**新题型**上复现同一错误。  
真正的问题是：**系统没有声明一套统一的「对话记忆里什么算能答」**，默认沿用了 LLM 的先验——要像新闻一样有**字面日历日、明确起点、唯一真值**——与 **segment 记录时间 + 多段叙事** 的证据形态不匹配。

## 根因（一条）

**缺一条跨 judge、跨 final 的显式认识论（epistemology）**：  
以 **证据块中的陈述 + 该块的记录时间** 为第一等事实；在缺失「文书式精确日」时，用 **时间锚与区间** 作答并口头限定，而不是整体弃权或长期 `INSUFFICIENT`。

## 根本做法：单一「Memory Evidence Contract」

在 **[config/agents/memory/runtime/agent_runtime_facts_only.yaml](config/agents/memory/runtime/agent_runtime_facts_only.yaml)** 与 **[config/agents/memory/runtime/agent_runtime.yaml](config/agents/memory/runtime/agent_runtime.yaml)** 中，**不要**为每类问题写分支。

改为在两处 **复用同一段原则**（中英各一份，文字可完全一致）：

1. **真值来源**  
   可依据的事实 = 证据里的 **对话陈述** + 证据头里的 **记录时间 / 会话时间范围**（以及事实列表）。不要求话里再重复一遍 ISO 日期。

2. **禁止的否决模式**  
   不得仅因「没有 named calendar date / 没有 explicit START / 不能唯一确定真实世界发生日」就判不可答或 `INSUFFICIENT`。  
   若多段证据给出多个时间锚，应 **综合为区间或多锚回答**，而非继续检索「魔法句子」。

3. **时间类问题（统一覆盖 when / how long / since when）**  
   - **When**：优先给出 **支持该陈述的片段记录日期**；若与别段冲突，说明区间或「最早可核对 … 至 …」。  
   - **How long**：若无单一开工句，用 **与问题相关的最早证据时间** 到 **事件完成（如开业）时间** 估计时长，并一句说明假设（起点取最早明确筹备/相关行动）。  
   不要求叙事里出现「第零天」。

4. **充分性判定（judge）**  
   `SUFFICIENT` = 工作区里已有证据 **足以按上述契约** 写出有依据的答案（含限定语）。  
   `INSUFFICIENT` 仅用于：**确实缺关键实体/主题**、或 **零相关陈述**，而不是「不够精确」。

5. **最终回答（final）**  
   与 judge 对齐：在契约下能答则 **不要用「完全无法确定」收尾**；`gold_answer` 仅在 **无任何支持陈述** 时置 null。  
   不确定性用 **简短 caveat** 表达，而不是替代正文。

## 实现要点（仍是一次性改配置）

- 在 `workspace_judge_prompt` 与 `final_answer_from_workspace_prompt` 的 **Instructions** 末尾各增加 **同一小节**（标题可叫 `Memory evidence contract`），五要点压缩成一段也可，关键是 **原则复用、不列题型**。  
- **不**新增 `when.yaml` / `duration.yaml` 等分文件，除非日后抽成 `includes`（当前 YAML 若不支持 include，则两处手动保持同步）。

## 可选后续（非必须）

- 若契约仍被模型忽略：再往 final 传入 `workspace.status` 仅作 **debug**，并在契约中写明「即使多轮检索结束，仍按契约从已有证据作答」——**仍不是按题型路由**。

## 评测预期

- 与官方 LoCoMo **字面 gold** 可能仍不一致；用 **LLM judge** 或 **人工** 对齐「契约下的正确性」。  
- 目标：**减少**你日志里那种「明明有锚却弃权」的系统性失败，而不是刷某一题的 F1。

## Todos

- [ ] 起草中英「Memory evidence contract」单段正文（无题型枚举）。  
- [ ] 并入 `agent_runtime_facts_only.yaml` 与 `agent_runtime.yaml` 的 judge + final。  
- [ ] 用既有失败样例（开业 / 录取日 / studio duration）各抽一条冒烟，确认不再「无理由 INSUFFICIENT + 全文弃权」。
