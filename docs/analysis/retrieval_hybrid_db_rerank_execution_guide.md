# MemoryAgent 检索改造执行手册（BM25 + 多向量 + Rerank）

> 面向你的当前问题：`search_details` 从纯向量升级为可解释、可扩展、可面试表达的混合检索体系。  
> 目标：明确“先做什么、用什么库、参数怎么起、每一步怎么验收”。

---

## 1. 先回答你的核心问题

## 1.1 先算法还是先数据库？

结论：**“检索协议先行 + 数据库原生优先落地”**。

- 不是先盲选库，也不是先写一套全自研再迁移。
- 先定义一套与数据库无关的检索协议（召回、融合、重排、输出格式、评测口径）。
- 再选“原生能力最贴近协议”的数据库，优先用数据库原生能力实现，缺口再在应用层补。

这样做的好处：
- 你不会被某个数据库 API 反向绑架算法。
- 你又不会重复造轮子（比如 DB 已有 Hybrid/RRF/Rerank 就直接用）。

---

## 2. 目标检索协议（你可以直接当 PRD）

## 2.1 检索输入输出约定

输入：
- `query_text`
- 可选过滤：`entity_id`, `speaker`, `time_range`, `episode_ids`, `scene_ids`
- `topk_final`

输出（统一结构）：
- `results`: 按最终分数排序
  - `fact_id`
  - `episode_id`
  - `atomic_fact`
  - `evidence`（dialogue_id / turn_span）
  - `score_sparse`
  - `score_dense`
  - `score_fused`
  - `score_rerank`
  - `trace`（命中来源：sparse/dense/multivector）

## 2.2 三阶段检索流程

1. **召回（Recall）**
   - Sparse/BM25 召回（关键词、实体词、时间词）
   - Dense 召回（语义近邻）
   - 可选：多向量/标签向量召回（“一段 Episode 对应多个 Atomic facts 向量”）
2. **融合（Fusion）**
   - RRF 或加权线性融合
3. **重排（Rerank）**
   - Cross-encoder 或 late-interaction（如 ColBERT）重排候选

---

## 3. 数据粒度设计（非常关键）

你提到“一个 Episode 对应多个 Atomic facts 编码向量”，建议不要只存 episode 粒度。

推荐“双粒度”：

1. **主检索粒度：Atomic fact**
   - 每条 fact 一行/一个 point（最利于命中与解释）
2. **聚合粒度：Episode**
   - 保存 episode 聚合信息与多向量表示（可选，用于补召回与摘要）

为什么：
- 用户问题常常命中某个具体细节，而不是整段 episode。
- 只做 episode 粒度会引入大量噪声，且 rerank 成本更高。

---

## 4. 参数默认值（第一版可直接用）

## 4.1 召回参数

- `top_sparse = 80`
- `top_dense = 80`
- `top_multivector = 40`（启用多向量时）
- `candidate_pool_max = 160`（去重后）

## 4.2 融合参数

优先用 RRF：
- `rrf_k = 60`
- 若支持加权 RRF：`w_sparse=0.45, w_dense=0.40, w_multi=0.15`

若只能线性融合：
- 对各路分数做 min-max 归一化
- `fused = 0.45*sparse + 0.40*dense + 0.15*multi`

## 4.3 重排参数

- `rerank_top_n = 50`
- `final_top_k = 8`（给生成器/答复模块）
- `min_rerank_score` 先不硬阈值（前两轮评测后再加）

## 4.4 性能保护

- 查询超时时间：`1200~1800ms`
- fallback：当 rerank 超时 -> 直接返回 fusion top-k

---

## 5. 数据库能力对照（聚焦你的场景）

## 5.1 Qdrant（最贴合你现在需求）

匹配度：**高**

原生能力：
- 稀疏 + 稠密混合查询（Query API + prefetch + fusion）
- 原生支持 BM25 稀疏表示（`Qdrant/bm25`）
- 支持 named vectors（同一点多向量空间）
- 支持 multivector + `max_sim`（适合 late interaction）
- 官方给出“Hybrid + Rerank”完整实践路径

适合你的原因：
- 你要“Episode -> 多个 Atomic facts 向量”的形态，Qdrant 的 named vector / multivector 很顺手。

## 5.2 Weaviate（集成体验好，工程成熟）

匹配度：**中高**

原生能力：
- Hybrid（BM25 + vector）并行融合
- `alpha` 可调权重，支持 BM25 参数
- 支持 named vectors
- 支持 multi-vector（v1.30+）
- 内置 rerank 流程（与 vector/bm25/hybrid 都可组合）

适合你的原因：
- 想要尽量“单系统内完成多阶段检索+重排”。

## 5.3 OpenSearch（检索工程能力最强之一）

匹配度：**中高**

原生能力：
- `hybrid` query
- search pipeline 支持 normalization / score-ranker(RRF)
- 支持 rerank processor（含 cross-encoder 方式）
- 对规则检索、过滤、可观测性、线上运维友好

注意：
- 对“单文档多向量晚交互”的表达，不如 Qdrant/Weaviate 直接（通常用多字段或多阶段改写实现）。

## 5.4 PostgreSQL + pgvector（低运维、事务友好）

匹配度：**中**

能力边界：
- pgvector 官方明确可与 Postgres FTS 组合做 hybrid
- 可做 RRF / cross-encoder（二次重排）
- 但 Postgres 内置 FTS 默认是 `ts_rank/ts_rank_cd` 体系，不是严格 BM25
- 若你必须“严格 BM25”，可额外引入 BM25 扩展（例如 `pg_textsearch`）

适合你的原因：
- 如果你强依赖事务一致性、团队 DB 运维偏 Postgres。

---

## 6. 推荐落地路线（一步一步做）

## Step 0：锁评测集与口径（1-2 天）

- 抽 LOCOMO 100 题作为固定回归集（覆盖实体、时间、细节、跨轮对话）。
- 定义指标：
  - `Recall@20`
  - `MRR@10`
  - `Answer EM/F1`
  - `Latency P95`
- 产出：`baseline_report_v0.md`

## Step 1：定义“检索协议接口”（1 天）

在代码中先定义统一接口（不绑库）：
- `retrieve_candidates(query, filters) -> candidates`
- `fuse(candidates) -> ranked_candidates`
- `rerank(query, ranked_candidates) -> final`

先做“协议层”，后续换库只换适配器。

## Step 2：先做算法基线（2-3 天）

基于现有 JSON/内存，快速做一个可跑的混合检索基线：
- sparse（先用 BM25 库）
- dense（现有 embedding）
- RRF 融合
- rerank（先接一个可用 cross-encoder）

目的不是线上部署，而是先拿到“算法增益曲线”。

## Step 3：选库并做最小可用适配器（3-5 天）

推荐顺序：
1. **Qdrant 适配器（首选）**
2. Weaviate / OpenSearch（按你团队运维偏好二选一）

验收标准：
- 在相同评测集上，DB版与基线版指标差距不超过 3%
- P95 延迟明显下降或可控

## Step 4：接入多向量策略（2-4 天）

两种方式二选一：

1. Atomic fact 为主（推荐）
   - 每个 fact 一条记录
   - episode_id 作为聚合字段
2. Episode multivector（补充）
   - 每个 episode 一个点，内部为多向量矩阵
   - 用于补召回或 rerank 前候选扩展

## Step 5：上线前保护（1-2 天）

- 超时降级：rerank timeout -> fusion 直出
- 结果可解释：返回 sparse/dense/fused/rerank 子分数
- 监控看板：Recall、MRR、P95、空结果率、错误率

---

## 7. 你当前项目的建议决策

结合你现状（已有 Neo4j、`search_details` 纯向量、LOCOMO 测试中）：

1. 先把检索协议定下来（不改主干业务逻辑）。
2. 第一落地库优先 **Qdrant**（BM25 + named vectors + multivector + hybrid/rerank 一条链闭环）。
3. Neo4j 继续做 KG，不和细节检索主链耦死。
4. 等检索稳定后，再考虑是否把存储统一到“Neo4j + 另一个检索库”的双存储生产架构。

---

## 8. 你可以直接抄的“第一版参数”

- chunk 粒度：Atomic fact（平均 1~2 句）
- `top_sparse=80`
- `top_dense=80`
- `top_multi=40`
- 融合：`RRF(k=60)`
- rerank：`top_n=50`
- 最终给回答器：`top_k=8`
- 观测窗口：连续 3 次评测集提升才调参落库

---

## 9. 官方参考（用于你做方案答辩/面试）

## Qdrant
- Hybrid Queries: https://qdrant.tech/documentation/search/hybrid-queries/
- Text Search（含 BM25 说明）: https://qdrant.tech/documentation/guides/text-search/
- Vectors（Named vectors / Multivectors）: https://qdrant.tech/documentation/manage-data/vectors/
- Hybrid Search with Reranking: https://qdrant.tech/documentation/search-precision/reranking-hybrid-search/

## Weaviate
- Hybrid search（BM25 + vector）: https://docs.weaviate.io/weaviate/search/hybrid
- Hybrid search 概念与参数: https://docs.weaviate.io/weaviate/concepts/search/hybrid-search
- Vector config（Named vectors / Multi-vectors）: https://docs.weaviate.io/weaviate/manage-collections/vector-config
- Rerank: https://docs.weaviate.io/weaviate/search/rerank

## OpenSearch
- Hybrid query: https://docs.opensearch.org/latest/query-dsl/compound/hybrid/
- Normalization processor: https://docs.opensearch.org/latest/search-plugins/search-pipelines/normalization-processor/
- Score ranker processor (RRF): https://docs.opensearch.org/latest/search-plugins/search-pipelines/score-ranker-processor/
- Rerank processor: https://docs.opensearch.org/latest/search-plugins/search-pipelines/rerank-processor/

## PostgreSQL / pgvector
- pgvector（Hybrid Search 章节）: https://github.com/pgvector/pgvector
- PostgreSQL Full Text Search（ranking）: https://www.postgresql.org/docs/18/textsearch-controls.html
- PostgreSQL text search functions: https://www.postgresql.org/docs/18/functions-textsearch.html

## Neo4j（你当前 KG 继续保留）
- Full-text indexes: https://neo4j.com/docs/cypher-manual/current/indexes/semantic-indexes/full-text-indexes/
- Vector indexes: https://neo4j.com/docs/cypher-manual/current/indexes/semantic-indexes/vector-indexes

