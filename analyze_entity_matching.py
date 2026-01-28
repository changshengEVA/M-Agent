#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析实体匹配情况
"""

import json
import numpy as np
from pathlib import Path

def analyze_entity_library():
    """分析实体库中的实体匹配情况"""
    library_path = "data/memory/test3/kg_data/entity_library.json"
    
    if not Path(library_path).exists():
        print(f"实体库文件不存在: {library_path}")
        return
    
    print(f"加载实体库: {library_path}")
    
    with open(library_path, 'r', encoding='utf-8') as f:
        library_data = json.load(f)
    
    print(f"实体库中有 {len(library_data)} 个实体")
    
    # 提取实体信息
    entities = []
    for item in library_data:
        entity_id = item.get("ID", "")
        aliases = item.get("alias_names", [])
        embedding = item.get("embedding")
        
        if entity_id and embedding:
            entities.append({
                "id": entity_id,
                "aliases": aliases,
                "embedding": embedding,
                "embedding_len": len(embedding)
            })
    
    print(f"\n有嵌入向量的实体: {len(entities)} 个")
    
    # 计算相似度矩阵
    n = len(entities)
    similarity_matrix = np.zeros((n, n))
    entity_ids = [e["id"] for e in entities]
    
    for i in range(n):
        for j in range(n):
            if i == j:
                similarity_matrix[i, j] = 1.0
            else:
                vec_i = np.array(entities[i]["embedding"])
                vec_j = np.array(entities[j]["embedding"])
                
                dot_product = np.dot(vec_i, vec_j)
                norm_i = np.linalg.norm(vec_i)
                norm_j = np.linalg.norm(vec_j)
                
                if norm_i == 0 or norm_j == 0:
                    similarity = 0.0
                else:
                    similarity = dot_product / (norm_i * norm_j)
                
                similarity_matrix[i, j] = similarity
    
    # 找出"北大"和"Peking_University"
    peking_idx = -1
    beida_idx = -1
    
    for i, entity in enumerate(entities):
        if entity["id"] == "Peking_University":
            peking_idx = i
            print(f"找到 Peking_University (索引: {i})")
            print(f"  别名: {entity['aliases']}")
            print(f"  嵌入向量长度: {entity['embedding_len']}")
        
        if entity["id"] == "北大":
            beida_idx = i
            print(f"找到 北大 (索引: {i})")
            print(f"  别名: {entity['aliases']}")
            print(f"  嵌入向量长度: {entity['embedding_len']}")
    
    if peking_idx >= 0 and beida_idx >= 0:
        similarity = similarity_matrix[peking_idx, beida_idx]
        print(f"\nPeking_University 和 北大 的相似度: {similarity:.4f}")
        
        # 检查阈值
        threshold = 0.8
        print(f"阈值: {threshold}")
        print(f"是否超过阈值: {similarity >= threshold}")
        
        # 分析其他相似实体
        print(f"\nPeking_University 与其他实体的相似度:")
        for i, entity in enumerate(entities):
            if i != peking_idx:
                sim = similarity_matrix[peking_idx, i]
                print(f"  - {entity['id']}: {sim:.4f} {'(超过阈值)' if sim >= threshold else ''}")
        
        print(f"\n北大 与其他实体的相似度:")
        for i, entity in enumerate(entities):
            if i != beida_idx:
                sim = similarity_matrix[beida_idx, i]
                print(f"  - {entity['id']}: {sim:.4f} {'(超过阈值)' if sim >= threshold else ''}")
    
    # 找出所有超过阈值的实体对
    threshold = 0.8
    above_threshold = []
    
    for i in range(n):
        for j in range(i + 1, n):
            similarity = similarity_matrix[i, j]
            if similarity >= threshold:
                above_threshold.append((similarity, i, j))
    
    print(f"\n所有相似度 >= {threshold} 的实体对 (共 {len(above_threshold)} 对):")
    for similarity, i, j in above_threshold:
        entity1 = entities[i]["id"]
        entity2 = entities[j]["id"]
        print(f"  {similarity:.4f}: {entity1} <-> {entity2}")
    
    # 分析LLM可能判定的原因
    print("\n分析LLM判定为NO_MATCH的可能原因:")
    print("1. 虽然相似度0.8超过阈值，但LLM可能认为:")
    print("   - 'Peking_University'是英文名称")
    print("   - '北大'是中文简称")
    print("   - 在上下文中可能表示不同的事物")
    print("2. LLM的prompt设计可能过于严格")
    print("3. 可能需要调整阈值或改进prompt")
    
    # 检查实体库中的别名
    print("\n实体库中的别名关系:")
    for entity in entities:
        if entity["aliases"]:
            print(f"  {entity['id']} 的别名: {entity['aliases']}")
        else:
            print(f"  {entity['id']} 没有别名")

def check_kg_manager_logic():
    """检查KGManager的后处理逻辑"""
    print("\n检查KGManager后处理逻辑:")
    
    # 模拟后处理逻辑
    threshold = 0.8
    
    # 假设的相似度
    similarities = {
        ("Peking_University", "北大"): 0.7997,  # 从之前的输出看是0.7997
        ("Peking_University", "changshengEVA"): 0.8204,
        ("Peking_University", "启元实验室"): 0.8073,
    }
    
    for (entity1, entity2), similarity in similarities.items():
        print(f"{entity1} <-> {entity2}: {similarity:.4f}")
        if similarity >= threshold:
            print(f"  ✓ 超过阈值 {threshold}, 应该进入LLM判别")
            # LLM可能会判定为NO_MATCH的原因:
            if (entity1, entity2) == ("Peking_University", "北大"):
                print("  → LLM可能认为: 英文全称 vs 中文简称，可能不是同一实体")
        else:
            print(f"  ✗ 未达到阈值 {threshold}")

def main():
    """主函数"""
    print("分析实体匹配情况")
    print("=" * 60)
    
    analyze_entity_library()
    check_kg_manager_logic()
    
    print("\n建议:")
    print("1. 考虑调整相似度阈值 (当前: 0.8)")
    print("2. 改进LLM的prompt，提供更多上下文信息")
    print("3. 考虑添加规则: 如果实体是明显的翻译关系，自动匹配")
    print("4. 可以添加人工审核机制处理边界情况")

if __name__ == "__main__":
    main()