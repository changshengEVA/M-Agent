#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查看实体库中的相似度矩阵
"""

import json
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd

def load_entity_library(library_path: str) -> Tuple[Dict[str, List[float]], Dict[str, List[str]]]:
    """
    加载实体库，返回嵌入向量和别名映射
    
    Returns:
        embeddings: 实体ID -> 嵌入向量
        aliases: 实体ID -> 别名列表
    """
    with open(library_path, 'r', encoding='utf-8') as f:
        library_data = json.load(f)
    
    embeddings = {}
    aliases = {}
    
    for item in library_data:
        entity_id = item.get("ID")
        if not entity_id:
            continue
        
        # 获取嵌入向量
        embedding = item.get("embedding")
        if embedding:
            embeddings[entity_id] = embedding
        
        # 获取别名
        alias_list = item.get("alias_names", [])
        aliases[entity_id] = alias_list
    
    return embeddings, aliases

def compute_similarity_matrix(embeddings: Dict[str, List[float]]) -> Tuple[np.ndarray, List[str]]:
    """
    计算相似度矩阵
    
    Returns:
        similarity_matrix: n x n 相似度矩阵
        entity_ids: 实体ID列表
    """
    entity_ids = list(embeddings.keys())
    n = len(entity_ids)
    
    if n == 0:
        return np.array([]), []
    
    # 将嵌入向量转换为numpy数组
    embedding_vectors = []
    for entity_id in entity_ids:
        embedding = embeddings[entity_id]
        embedding_vectors.append(np.array(embedding))
    
    # 计算相似度矩阵
    similarity_matrix = np.zeros((n, n))
    
    for i in range(n):
        for j in range(n):
            if i == j:
                similarity_matrix[i, j] = 1.0
            else:
                vec_i = embedding_vectors[i]
                vec_j = embedding_vectors[j]
                
                # 计算余弦相似度
                dot_product = np.dot(vec_i, vec_j)
                norm_i = np.linalg.norm(vec_i)
                norm_j = np.linalg.norm(vec_j)
                
                if norm_i == 0 or norm_j == 0:
                    similarity = 0.0
                else:
                    similarity = dot_product / (norm_i * norm_j)
                
                similarity_matrix[i, j] = similarity
    
    return similarity_matrix, entity_ids

def print_similarity_matrix(similarity_matrix: np.ndarray, entity_ids: List[str], aliases: Dict[str, List[str]]):
    """打印相似度矩阵"""
    n = len(entity_ids)
    
    if n == 0:
        print("实体库为空")
        return
    
    print(f"实体库中共有 {n} 个实体")
    print("\n实体列表:")
    for i, entity_id in enumerate(entity_ids):
        alias_str = ", ".join(aliases.get(entity_id, []))
        if alias_str:
            print(f"  {i:2d}. {entity_id} (别名: {alias_str})")
        else:
            print(f"  {i:2d}. {entity_id}")
    
    print("\n相似度矩阵:")
    print(" " * 15, end="")
    for j in range(min(10, n)):  # 只显示前10列
        print(f"{j:8d}", end="")
    print()
    
    for i in range(min(20, n)):  # 只显示前20行
        # 显示行标签（实体ID前10个字符）
        label = entity_ids[i][:12]
        if len(entity_ids[i]) > 12:
            label += "..."
        print(f"{label:15s}", end="")
        
        for j in range(min(10, n)):  # 只显示前10列
            similarity = similarity_matrix[i, j]
            if i == j:
                print(f"{'1.000':>8s}", end="")
            else:
                print(f"{similarity:8.3f}", end="")
        print()
    
    if n > 10:
        print(f"\n(只显示前10列，共{n}列)")
    if n > 20:
        print(f"(只显示前20行，共{n}行)")

def find_top_similarities(similarity_matrix: np.ndarray, entity_ids: List[str], top_k: int = 10):
    """找出最相似的实体对"""
    n = len(entity_ids)
    
    if n < 2:
        print("实体数量不足，无法计算相似度")
        return
    
    similarities = []
    
    for i in range(n):
        for j in range(i + 1, n):
            similarity = similarity_matrix[i, j]
            similarities.append((similarity, i, j))
    
    # 按相似度降序排序
    similarities.sort(reverse=True, key=lambda x: x[0])
    
    print(f"\n最相似的前 {top_k} 对实体:")
    print("-" * 60)
    print(f"{'相似度':<10s} {'实体1':<30s} {'实体2':<30s}")
    print("-" * 60)
    
    for idx, (similarity, i, j) in enumerate(similarities[:top_k]):
        entity1 = entity_ids[i]
        entity2 = entity_ids[j]
        print(f"{similarity:.4f}    {entity1:<30s} {entity2:<30s}")

def analyze_threshold_similarities(similarity_matrix: np.ndarray, entity_ids: List[str], threshold: float = 0.8):
    """分析超过阈值的相似度"""
    n = len(entity_ids)
    
    if n < 2:
        return
    
    above_threshold = []
    
    for i in range(n):
        for j in range(i + 1, n):
            similarity = similarity_matrix[i, j]
            if similarity >= threshold:
                above_threshold.append((similarity, i, j))
    
    print(f"\n相似度 >= {threshold} 的实体对 (共 {len(above_threshold)} 对):")
    print("-" * 80)
    
    for similarity, i, j in above_threshold[:20]:  # 只显示前20对
        entity1 = entity_ids[i]
        entity2 = entity_ids[j]
        print(f"相似度: {similarity:.4f} - {entity1} <-> {entity2}")
    
    if len(above_threshold) > 20:
        print(f"... 还有 {len(above_threshold) - 20} 对未显示")

def export_to_csv(similarity_matrix: np.ndarray, entity_ids: List[str], output_path: str):
    """导出相似度矩阵到CSV文件"""
    df = pd.DataFrame(similarity_matrix, index=entity_ids, columns=entity_ids)
    df.to_csv(output_path)
    print(f"\n相似度矩阵已导出到: {output_path}")

def main():
    """主函数"""
    # 实体库路径
    library_path = "data/memory/test3/kg_data/entity_library.json"
    
    if not Path(library_path).exists():
        print(f"实体库文件不存在: {library_path}")
        print("请先运行KGManager处理一些数据以生成实体库")
        return
    
    print(f"加载实体库: {library_path}")
    
    try:
        # 加载实体库
        embeddings, aliases = load_entity_library(library_path)
        
        if not embeddings:
            print("实体库中没有嵌入向量数据")
            print("实体列表:")
            for entity_id, alias_list in aliases.items():
                alias_str = ", ".join(alias_list)
                if alias_str:
                    print(f"  - {entity_id} (别名: {alias_str})")
                else:
                    print(f"  - {entity_id}")
            return
        
        print(f"找到 {len(embeddings)} 个有嵌入向量的实体")
        
        # 计算相似度矩阵
        similarity_matrix, entity_ids = compute_similarity_matrix(embeddings)
        
        if len(entity_ids) == 0:
            print("没有可计算相似度的实体")
            return
        
        # 打印基本信息
        print_similarity_matrix(similarity_matrix, entity_ids, aliases)
        
        # 找出最相似的实体对
        find_top_similarities(similarity_matrix, entity_ids, top_k=15)
        
        # 分析阈值以上的相似度
        analyze_threshold_similarities(similarity_matrix, entity_ids, threshold=0.7)
        analyze_threshold_similarities(similarity_matrix, entity_ids, threshold=0.8)
        analyze_threshold_similarities(similarity_matrix, entity_ids, threshold=0.9)
        
        # 导出到CSV
        export_to_csv(similarity_matrix, entity_ids, "entity_similarity_matrix.csv")
        
        # 统计信息
        print("\n统计信息:")
        print(f"实体总数: {len(entity_ids)}")
        
        # 计算平均相似度（排除对角线）
        mask = ~np.eye(len(entity_ids), dtype=bool)
        non_diagonal = similarity_matrix[mask]
        
        if len(non_diagonal) > 0:
            avg_similarity = np.mean(non_diagonal)
            max_similarity = np.max(non_diagonal)
            min_similarity = np.min(non_diagonal)
            
            print(f"平均相似度 (排除自身): {avg_similarity:.4f}")
            print(f"最大相似度: {max_similarity:.4f}")
            print(f"最小相似度: {min_similarity:.4f}")
            
            # 相似度分布
            bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
            hist, _ = np.histogram(non_diagonal, bins=bins)
            
            print("\n相似度分布:")
            for i in range(len(bins)-1):
                count = hist[i]
                percentage = count / len(non_diagonal) * 100
                print(f"  {bins[i]:.1f}-{bins[i+1]:.1f}: {count:4d} 对 ({percentage:5.1f}%)")
        
    except Exception as e:
        print(f"处理实体库时出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()