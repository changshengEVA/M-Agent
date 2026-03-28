# `locomo10.json` 字段取样分析

## 1. 文件概览
- 文件路径: `data/locomo/data/locomo10.json`
- 文件大小: `2,872,024` bytes (约 `2.74 MB`)
- 顶层类型: `list`
- 顶层记录数: `10`
- `sample_id` 列表: `conv-26, conv-30, conv-41, conv-42, conv-43, conv-44, conv-47, conv-48, conv-49, conv-50`

## 2. 顶层字段（每条记录）
| 字段 | 类型 | 覆盖率 | 取样值 | 备注 |
|---|---|---:|---|---|
| `sample_id` | `str` | 10/10 | `conv-26` | 样本唯一标识 |
| `qa` | `list[object]` | 10/10 | `[{"question": ..., "answer": ..., "evidence": [...], "category": 2}]` | 问答集合 |
| `conversation` | `object` | 10/10 | `{"speaker_a":"Caroline","speaker_b":"Melanie","session_1_date_time":"...","session_1":[...]}` | 多会话对话原文 |
| `event_summary` | `object` | 10/10 | `{"events_session_1":{"Caroline":[...],"Melanie":[],"date":"8 May, 2023"}}` | 会话事件摘要 |
| `observation` | `object` | 10/10 | `{"session_1_observation":{"Caroline":[["...","D1:3"], ...]}}` | 观察/归纳 + 证据 |
| `session_summary` | `object` | 10/10 | `{"session_1_summary":"Caroline and Melanie had a conversation ..."}` | 会话级文本总结 |

## 3. `qa` 字段
### 3.1 结构与统计
- 总 QA 数: `1986`
- 每条记录 QA 数: 最少 `105`，最多 `260`，平均 `198.6`
- `qa[]` 子字段出现次数:
  - `question`: `1986`
  - `evidence`: `1986`
  - `category`: `1986`
  - `answer`: `1542`
  - `adversarial_answer`: `446`

### 3.2 子字段取样
| 子字段 | 类型 | 取样值 |
|---|---|---|
| `qa[].question` | `str` | `When did Caroline go to the LGBTQ support group?` |
| `qa[].answer` | `str / int` | `7 May 2023`（也有 `2022`, `3` 这类整数） |
| `qa[].evidence` | `list[str]` | `["D1:3"]` |
| `qa[].category` | `int` | `2` |
| `qa[].adversarial_answer` | `str` | `self-care is important` |

### 3.3 `category` 分布
- `1`: `282` (`14.20%`)
- `2`: `321` (`16.16%`)
- `3`: `96` (`4.83%`)
- `4`: `841` (`42.35%`)
- `5`: `446` (`22.46%`)

### 3.4 特征与异常
- `category=5` 基本是对抗答案字段：`446` 条里 `444` 条只有 `adversarial_answer`，`2` 条同时含 `answer` 与 `adversarial_answer`。
- `qa[].answer` 缺失 `444` 条（与上面 `category=5` 规律一致）。
- `qa[].evidence` 长度范围 `0~19`，平均 `1.42`。
- `qa[].evidence` 中大多数是标准 `D<session>:<turn>`，但有 `6` 个异常格式（如 `D8:6; D9:17`、`D`、`D:11:26`、`D9:1 D4:4 D4:6`）。

## 4. `conversation` 字段
### 4.1 结构
- 固定字段:
  - `speaker_a` (`str`)
  - `speaker_b` (`str`)
- 动态字段（按会话编号）:
  - `session_n_date_time` (`str`)
  - `session_n` (`list[object]`)

### 4.2 `session_n[]` 对话项结构
- 总对话 turn 数: `5882`
- 每个 `session_n` 的 turn 数: 最少 `10`，最多 `47`，平均 `21.62`
- 核心字段（全部存在，无缺失）:
  - `speaker` (`5882`)
  - `dia_id` (`5882`，格式均为 `D<session>:<turn>`)
  - `text` (`5882`)
- 可选字段:
  - `img_url` (`910`, `15.47%`)
  - `blip_caption` (`1226`, `20.84%`)
  - `query` (`888`, `15.10%`)
  - `re-download` (`206`, `3.50%`)

### 4.3 子字段取样
| 子字段 | 类型 | 取样值 |
|---|---|---|
| `conversation.speaker_a` | `str` | `Caroline` |
| `conversation.speaker_b` | `str` | `Melanie` |
| `conversation.session_n_date_time` | `str` | `1:56 pm on 8 May, 2023` |
| `conversation.session_n[].speaker` | `str` | `Caroline` |
| `conversation.session_n[].dia_id` | `str` | `D1:1` |
| `conversation.session_n[].text` | `str` | `Hey Mel! Good to see you! How have you been?` |
| `conversation.session_n[].img_url` | `list[str]` | `["https://i.redd.it/l7hozpetnhlb1.jpg"]` |
| `conversation.session_n[].blip_caption` | `str` | `a photo of a dog walking past a wall with a painting of a woman` |
| `conversation.session_n[].query` | `str` | `transgender pride flag mural` |
| `conversation.session_n[].re-download` | `bool` | `true` |

### 4.4 会话数量
- 每条记录 `session_n` 数量: 最少 `19`，最多 `32`，平均 `27.2`
- 唯一会话编号范围: `1~32`
- 特殊情况: `conv-26` 有 `session_20_date_time` 到 `session_35_date_time`，但没有对应 `session_20~35` 对话内容（`date_time` 比实际会话多 16 个）。

## 5. `event_summary` 字段
### 5.1 结构
- 动态字段: `events_session_n`
- `events_session_n` 内部结构:
  - `date` (`str`)
  - `<participant_name>` (`list[str]`，该参与者该会话的事件列表)

### 5.2 统计
- 总 `events_session_n` 数: `272`
- 每条记录会话数: 最少 `19`，最多 `32`，平均 `27.2`
- 所有 `events_session_n` 都有 `date`
- 参与者事件字段类型统一为 `list`

### 5.3 取样
| 子字段 | 类型 | 取样值 |
|---|---|---|
| `event_summary.events_session_n.date` | `str` | `8 May, 2023` |
| `event_summary.events_session_n.<participant>` | `list[str]` | `["Caroline attends an LGBTQ support group for the first time."]` |

## 6. `observation` 字段
### 6.1 结构
- 动态字段: `session_n_observation`
- `session_n_observation` 内部:
  - `<participant_name>`: `list[[observation_text, evidence], ...]`

### 6.2 统计
- 总 `session_n_observation` 数: `272`
- 观察对（`[text, evidence]`）总数: `2541`
- `evidence` 多数为标准 `D<session>:<turn>`，但有 `15` 条为组合/列表格式（如 `D26:14, D26:34, D26:42` 或 `["D27:7","D27:9",...]`）

### 6.3 取样
| 子字段 | 类型 | 取样值 |
|---|---|---|
| `observation.session_n_observation.<participant>` | `list[list]` | `[["Caroline attended an LGBTQ support group...", "D1:3"], ...]` |
| `observation.session_n_observation.<participant>[].text` | `str` | `Caroline attended an LGBTQ support group recently and found the transgender stories inspiring.` |
| `observation.session_n_observation.<participant>[].evidence` | `str` | `D1:3` |

## 7. `session_summary` 字段
### 7.1 结构
- 动态字段: `session_n_summary`
- 值类型: `str`

### 7.2 统计与取样
- 总 `session_n_summary` 数: `272`
- 每条记录会话数: 最少 `19`，最多 `32`，平均 `27.2`
- 取样值: `Caroline and Melanie had a conversation on 8 May 2023 at 1:56 pm...`

## 8. 按样本汇总（关键计数）
| sample_id | qa_count | conv_sessions | conv_date_times | event_sessions | observation_sessions | summary_sessions | turns_total |
|---|---:|---:|---:|---:|---:|---:|---:|
| conv-26 | 199 | 19 | 35 | 19 | 19 | 19 | 419 |
| conv-30 | 105 | 19 | 19 | 19 | 19 | 19 | 369 |
| conv-41 | 193 | 32 | 32 | 32 | 32 | 32 | 663 |
| conv-42 | 260 | 29 | 29 | 29 | 29 | 29 | 629 |
| conv-43 | 242 | 29 | 29 | 29 | 29 | 29 | 680 |
| conv-44 | 158 | 28 | 28 | 28 | 28 | 28 | 675 |
| conv-47 | 190 | 31 | 31 | 31 | 31 | 31 | 689 |
| conv-48 | 239 | 30 | 30 | 30 | 30 | 30 | 681 |
| conv-49 | 196 | 25 | 25 | 25 | 25 | 25 | 509 |
| conv-50 | 204 | 30 | 30 | 30 | 30 | 30 | 568 |

## 9. 结论（简版）
- 该文件是一个结构较稳定的多会话对话数据集，核心字段齐全，`conversation/event_summary/observation/session_summary` 的会话编号大体一致。
- 主要不一致点集中在证据字段格式（`qa.evidence` 和 `observation` 的少量组合引用）以及 `conv-26` 存在额外 `session_x_date_time` 占位键。
- 如果后续要做严格 schema 校验，建议对 `evidence` 做标准化（拆分多引用）并单独处理 `conv-26` 的多余日期键。
