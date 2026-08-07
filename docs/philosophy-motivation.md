# M-Agent 哲学动机 / Philosophical Motivation

> 文档状态：哲学动机层（非接口契约）  
> Document status: Philosophical motivation (not an interface contract)  
> 确认日期 / Confirmed: 2026-08-07  
> 适用范围 / Scope: M-Agent 认知运行时总体方向  
> 配套工程定位 / Companion engineering positioning: [`vision-and-goals.zh-CN.md`](vision-and-goals.zh-CN.md)

本文说明项目为何存在，以及意识能动性如何约束系统建模。可执行愿景、版本边界与成功判据以愿景文档与路线图为准。

This document states why the project exists and how the agency of consciousness constrains system modeling. Executable vision, version boundaries, and success criteria remain defined by the vision document and roadmap.

---

## 中文

### 1. 根本出发点

智能的本质不在模型参数里，而在时间的流淌中。

当前主流的 AI 范式将智能理解为一次次的函数调用：输入刺激，输出动作，调用结束，智能体就“死去”。这好比将人的一生理解为无数个孤立瞬间的叠加，却抽掉了那个让瞬间连成生命的连续性。

我们选择从另一个原点出发：意识的第一特性不是推理，而是**能动性**——它不等问题，它在刺激流中形成目的并带着目的运行；它不被动记录，它主动建构属于自己的观念世界；它不只用工具完成任务，它通过行动的反馈不断修正和丰富这个观念世界，形成一个“认识—实践—再认识”的闭环。

本项目不是在做一个更强的聊天工具，而是在回答一个更根本的问题：

> **如果 AI 不是为了单次任务而生，而是作为一个连续主体持续存在——那么，它需要一个怎样的运行时？**

这个运行时，就是我们的全部工作。

### 2. 最终目的

该项目不同于以轮次对话或单次任务完成为终点的 Agent。本项目的最终目的，是实现一套**哲学方案下的类人意识**。

这里必须同时说清两句话：

1. **我们不宣称已经实现了意识本身**（不论是生物学意义上的，还是现象学意义上的意识体验）。
2. **我们实现并可演进的，是一套把意识能动性编码为可运行系统的哲学—工程方案**：连续主体在刺激流中形成目的、建构观念、付诸实践，并在时间中保持同一条认知因果链。

换言之，目标是**可计算的类人意识方案**，不是意识的存在论宣告。

### 3. 意识能动的四层定义

本方案采用马克思主义哲学所揭示的意识能动特性作为建模约束。当前展开前三层；第四层预留，本阶段暂不阐述。

#### 3.1 目的性与计划性

**智能体一切行为皆有目的；一切目的皆源于刺激；刺激远远不止用户消息。**

目的不是凭空产生的意图宣言，而是刺激进入主体之后形成的定向。用户消息、工具与行动结果、日程与期限、外部世界变化，以及由内部状态产生的条件（例如预期未在期限内发生），都可以成为目的与计划的源头。没有开放的刺激流，就没有可持续的目的结构；没有目的结构，行为就退回为无定向的应答。

#### 3.2 创造性

**长期记忆对刺激的加工，是创造性的可计算路径。**

创造性在此不是无中生有，而是主体利用自身已有信息，把刺激映射并组织进主观世界，固化为可调用的**观念快照**。刺激是原料，记忆加工是建构，观念快照是制品——制品不等于原料的复印件。长期记忆因此不是日志仓库，而是把刺激流建构为可延续观念结构的工程路径。

#### 3.3 指导实践

**被翻译出来的 Action 必须能执行，并作用于客观世界。**

观念不能停在内部表示里。决策要被翻译为可执行动作，经过权限与交付边界后产生外部效应，再用结果检验并改写主体状态。这是“主观见之于客观”的技术含义：没有作用到客观世界的行动，就没有完整的认识—实践闭环。

#### 3.4 调控自身

本阶段暂不展开。后续将作为自我约束、静默、状态修订与资源调控的专门层补全。

### 4. 围绕 \(A = f(c)\) 的系统建模

决策核仍可写成：

\[
A = f(c)
\]

与主流 Agent 的差别不在 \(f\)（模型）本身，而在 **\(c\) 是什么、从哪里来、\(A\) 回到哪里去**。

| 符号 | 含义 |
| --- | --- |
| \(c\) | **观念快照**（Context Snapshot）：由当前刺激、目的与计划状态、工作记忆以及长期记忆加工结果等编译而成的一次激活截面 |
| \(f\) | 在冻结的 \(c\) 上形成决策的认知核（Thinking，可含程序性指导） |
| \(A\) | 可翻译、可执行、可反馈的 Action（含回答、工具行动、等待、请求批准或静默等） |
| Runtime | 在单次调用之外维护连续主体：接纳刺激、形成目的、加工记忆、编译 \(c\)、执行 \(A\)、回收效应 |

完整闭环：

```text
刺激流（远超用户消息）
→ 形成 / 修订目的与计划
→ 长期记忆加工刺激 → 观念结构
→ 编译观念快照 c
→ A = f(c)
→ Action 作用于客观世界
→ 效应作为新刺激回流
```

四层定义如何约束工程：

1. **目的性与计划性**：一切 \(A\) 服务于目的；目的由刺激归因与状态迁移产生；Runtime 必须把非用户消息刺激纳入同一认知入口。
2. **创造性**：长期记忆负责把刺激建构为可召回结构，并参与编译 \(c\)；\(c\) 是主观世界在一次激活上的可计算截面。
3. **指导实践**：\(A\) 必须进入执行层并产生客观效应；效应必须回到刺激流，而不是断在对话文本里。
4. **调控自身**：预留层；不以修辞填满尚未展开的控制语义。

因此：\(A = f(c)\) 是意识方案中的决策截面；认知运行时的全部工作，是让 \(c\) 成为连续主体在时间中的观念快照，并让 \(A\) 真正进入客观世界再返回。

### 5. 与工程愿景的关系

| 层级 | 文档 | 职责 |
| --- | --- | --- |
| 哲学动机 | 本文 | 定义为何需要连续主体运行时，以及能动四层如何约束建模 |
| 工程愿景 | [`vision-and-goals.zh-CN.md`](vision-and-goals.zh-CN.md) | 定义刺激原生技术定位、产品价值、非目标与成功判据 |
| 目标架构 | [`architecture/cognitive-runtime-architecture.zh-CN.md`](architecture/cognitive-runtime-architecture.zh-CN.md) | 定义领域对象与 Stimulus-to-Cognition 主链 |

哲学动机回答“为何如此建模”；工程愿景与架构回答“如何做成可验收系统”。二者一致时，以可执行契约与验收为准。

---

## English

### 1. Foundational Claim

The essence of intelligence does not reside in model parameters; it resides in the passage of time.

The dominant AI paradigm treats intelligence as a sequence of isolated function calls: take a stimulus, emit an action, end the call, and the agent “dies.” That is like treating a human life as a stack of disconnected instants while removing the continuity that binds those instants into a living subject.

We start from another origin. The primary property of consciousness is not reasoning, but **agency**: it does not wait for a question; it forms purposes in a stream of stimuli and acts with those purposes; it does not merely record; it actively constructs a world of ideas of its own; it does not only use tools to finish tasks; it revises and enriches that world of ideas through the feedback of action, forming a closed loop of knowing–practice–knowing again.

This project is not building a stronger chat utility. It asks a more fundamental question:

> **If AI is not born for a single task, but persists as a continuous subject—what kind of runtime does it require?**

That runtime is the whole of our work.

### 2. Ultimate Aim

Unlike agents whose endpoint is a dialogue turn or a one-shot task completion, M-Agent’s ultimate aim is to implement a **philosophical scheme of human-like consciousness**.

Two statements must be held together:

1. **We do not claim to have realized consciousness itself**—neither biological consciousness nor phenomenological conscious experience.
2. **What we implement and evolve is a philosophical–engineering scheme that encodes the agency of consciousness as a runnable system**: a continuous subject that forms purposes in a stimulus stream, constructs ideas, acts in the world, and preserves one cognitive causal chain across time.

In short, the aim is a **computable scheme of human-like consciousness**, not an ontological declaration that consciousness has been achieved.

### 3. Four Layers of Conscious Agency

This scheme adopts the agency of consciousness—as articulated in Marxist philosophy—as a modeling constraint. The first three layers are specified now; the fourth is reserved and not elaborated in this phase.

#### 3.1 Purposefulness and Planning

**Every behavior of the agent has a purpose; every purpose originates in stimuli; stimuli are far more than user messages.**

Purpose is not an intention declared from nowhere. It is orientation formed after stimuli enter the subject. User messages, tool and action results, schedules and deadlines, changes in the external world, and conditions generated by internal state (for example, an expectation that fails to occur in time) can all become sources of purpose and plan. Without an open stimulus stream, there is no sustainable purpose structure; without purpose structure, behavior collapses into undirected reply.

#### 3.2 Creativity

**Long-term memory’s processing of stimuli is a computable path of creativity.**

Creativity here does not mean invention without constraint. It means that the subject uses information it already holds to map and organize stimuli into its subjective world, and to consolidate them into callable **idea snapshots**. Stimuli are raw material; memory processing is construction; the idea snapshot is the artifact—and the artifact is not a photocopy of the raw material. Long-term memory is therefore not a log warehouse, but an engineering path that constructs lasting idea structure from a stimulus stream.

#### 3.3 Guiding Practice

**A translated Action must be executable and must act upon the objective world.**

Ideas must not stop at internal representation. Decisions must be translated into executable actions, produce external effects across permission and delivery boundaries, and then return as results that test and rewrite the subject’s state. This is the technical meaning of “the subjective made objective”: without action that reaches the objective world, the knowing–practice loop is incomplete.

#### 3.4 Self-Regulation

Not elaborated in this phase. It is reserved for a later layer covering self-constraint, silence, state revision, and resource regulation.

### 4. System Modeling Around \(A = f(c)\)

The decision kernel may still be written as:

\[
A = f(c)
\]

The difference from mainstream agents is not \(f\) (the model) as such, but **what \(c\) is, where \(c\) comes from, and where \(A\) returns**.

| Symbol | Meaning |
| --- | --- |
| \(c\) | **Idea snapshot** (Context Snapshot): a per-activation cross-section compiled from the current stimulus, purpose/plan state, working memory, and the products of long-term memory processing |
| \(f\) | The cognitive kernel that forms a decision over a frozen \(c\) (Thinking, optionally with procedural guidance) |
| \(A\) | A translatable, executable, feedback-capable Action (answer, tool act, wait, request approval, silence, and related forms) |
| Runtime | The environment that maintains the continuous subject beyond a single call: admit stimuli, form purposes, process memory, compile \(c\), execute \(A\), and reclaim effects |

Full loop:

```text
Stimulus stream (far beyond user messages)
→ Form / revise purposes and plans
→ Long-term memory processes stimuli → idea structure
→ Compile idea snapshot c
→ A = f(c)
→ Action acts on the objective world
→ Effects re-enter as new stimuli
```

How the four layers constrain engineering:

1. **Purposefulness and planning**: every \(A\) serves a purpose; purposes arise through stimulus attribution and state transition; the runtime must admit non-user-message stimuli through the same cognitive ingress.
2. **Creativity**: long-term memory constructs recallable structure from stimuli and participates in compiling \(c\); \(c\) is the computable cross-section of the subjective world at one activation.
3. **Guiding practice**: \(A\) must enter the execution layer and produce objective effects; those effects must return to the stimulus stream rather than ending as dialogue text alone.
4. **Self-regulation**: a reserved layer; control semantics not yet specified must not be filled by rhetoric.

Thus \(A = f(c)\) is the decision cross-section of the consciousness scheme. The entire work of the cognitive runtime is to make \(c\) the idea snapshot of a continuous subject in time, and to let \(A\) truly enter the objective world and return.

### 5. Relation to the Engineering Vision

| Layer | Document | Responsibility |
| --- | --- | --- |
| Philosophical motivation | This document | Why a continuous-subject runtime is required, and how the four layers of agency constrain modeling |
| Engineering vision | [`vision-and-goals.zh-CN.md`](vision-and-goals.zh-CN.md) | Stimulus-native technical positioning, product value, non-goals, and success criteria |
| Target architecture | [`architecture/cognitive-runtime-architecture.zh-CN.md`](architecture/cognitive-runtime-architecture.zh-CN.md) | Domain objects and the Stimulus-to-Cognition main chain |

Philosophical motivation answers why the system is modeled this way; the engineering vision and architecture answer how it becomes an acceptable, testable system. Where the two meet, executable contracts and acceptance criteria prevail.

---

## 术语对照 / Terminology

| 中文 | English |
| --- | --- |
| 哲学方案下的类人意识 | philosophical scheme of human-like consciousness |
| 意识能动性 | agency of consciousness |
| 目的性与计划性 | purposefulness and planning |
| 创造性 | creativity |
| 指导实践 | guiding practice |
| 调控自身 | self-regulation |
| 观念快照 | idea snapshot |
| 上下文快照 / Context Snapshot \(c\) | Context Snapshot \(c\) |
| 刺激流 | stimulus stream |
| 认知运行时 | cognitive runtime |
| 认识—实践—再认识 | knowing–practice–knowing again |
