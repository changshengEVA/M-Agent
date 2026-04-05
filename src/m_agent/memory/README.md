Memory_sys 接口设计文档（V1）

一、系统概述
Memory_sys 是系统的 结构化记忆访问模块。
其职责为：
向 Agent 提供可用于推理的结构化记忆证据。
Memory_sys 不负责：
● 问题理解
● 推理
● 自然语言生成
Agent 基于 Memory_sys 返回的信息完成推理与回答。

二、接口总体结构
Memory_sys 对外暴露两类接口：
Memory_sys
├── 外部接口（Agent 可调用）
└── 内部接口（系统内部使用）

2.1 外部接口（External API）
外部接口用于向 Agent 提供：
● 实体定位能力
● 实体信息访问能力
● 事件检索能力
● 记忆证据获取能力
外部接口返回内容必须为：
● 结构化数据
● 可推理证据
● 非自然语言结论

2.2 内部接口（Internal API）
内部接口用于 Memory_sys 内部执行：
● 索引访问
● 匹配计算
● 检索流程控制
● 合法性校验
内部接口不允许 Agent 调用。

三、外部接口说明

3.1 实体解析接口（Entity Grounding）
功能
将实体名称解析为系统内部唯一实体标识。
接口
resolve_entity(name: str)
返回
{
  "hit": true,
  "entity_uid": "E123",
  "canonical_name": "Kate",
  "aliases": [...]
}

3.2 实体属性 / 特征查询
功能
查询实体相关属性或抽象特征及其证据。
接口
query_entity_property(
    entity_uid: str,
    query_text: str
)

3.3 实体信息获取
功能
返回实体当前已知的结构化信息。
接口
get_entity_profile(entity_uid: str)

3.4 事件搜索接口
功能
根据条件检索结构化事件集合。
接口
search_events(query: dict)

3.5 实体事件枚举
功能
获取指定实体关联事件集合。
接口
get_entity_events(
    entity_uid: str,
    limit: int = 50,
    order_by: str = "time_desc"
)

四、内部接口说明

4.1 实体合法性校验
entity_exists(entity_uid: str)
用于内部 UID 校验与查询保护。

4.2 实体索引检索
_entity_index_lookup(name_embedding)
用于实体召回与别名匹配。

4.3 特征匹配模块
_feature_match(entity_uid, feature_embedding)
执行特征语义匹配。

4.4 事件检索模块
_event_retriever(filters)
生成事件候选集合。

五、设计原则
1. Memory_sys 提供证据，不提供答案
2. 外部接口面向记忆访问能力
3. 返回结果必须支持推理
4. Agent 可自由组合调用流程