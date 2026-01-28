#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可视化实体相似度矩阵
"""

import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from typing import Dict, List
import warnings
warnings.filterwarnings('ignore')

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

def load_entity_data(library_path: str):
    """加载实体库数据"""
    with open(library_path, 'r', encoding='utf-8') as f:
        library_data = json.load(f)
    
    embeddings = {}
    aliases = {}
    
    for item in library_data:
        entity_id = item.get("ID")
        if not entity_id:
            continue
        
        embedding = item.get("embedding")
        if embedding:
            embeddings[entity_id] = embedding
        
        alias_list = item.get("alias_names", [])
        aliases[entity_id] = alias_list
    
    return embeddings, aliases

def compute_similarity_matrix(embeddings: Dict[str, List[float]]):
    """计算相似度矩阵"""
    entity_ids = list(embeddings.keys())
    n = len(entity_ids)
    
    if n == 0:
        return np.array([]), []
    
    # 转换为numpy数组
    embedding_vectors = np.array([embeddings[eid] for eid in entity_ids])
    
    # 计算余弦相似度矩阵
    norms = np.linalg.norm(embedding_vectors, axis=1, keepdims=True)
    normalized = embedding_vectors / norms
    similarity_matrix = np.dot(normalized, normalized.T)
    
    return similarity_matrix, entity_ids

def create_heatmap(similarity_matrix: np.ndarray, entity_ids: List[str], output_path: str = "similarity_heatmap.png"):
    """创建相似度热力图"""
    if len(entity_ids) == 0:
        print("没有实体数据可可视化")
        return
    
    plt.figure(figsize=(12, 10))
    
    # 创建热力图
    mask = np.triu(np.ones_like(similarity_matrix, dtype=bool), k=1)
    data_to_plot = similarity_matrix.copy()
    
    # 使用seaborn绘制热力图
    ax = sns.heatmap(
        data_to_plot,
        mask=mask,
        cmap="RdYlBu_r",
        vmin=0.6,
        vmax=1.0,
        center=0.8,
        square=True,
        linewidths=0.5,
        cbar_kws={"shrink": 0.8, "label": "相似度"},
        annot=True,
        fmt=".3f",
        annot_kws={"size": 8}
    )
    
    # 设置坐标轴标签
    ax.set_xticks(np.arange(len(entity_ids)) + 0.5)
    ax.set_yticks(np.arange(len(entity_ids)) + 0.5)
    
    # 缩短长标签
    short_labels = []
    for label in entity_ids:
        if len(label) > 10:
            short_labels.append(label[:8] + "...")
        else:
            short_labels.append(label)
    
    ax.set_xticklabels(short_labels, rotation=45, ha="right", fontsize=10)
    ax.set_yticklabels(short_labels, rotation=0, fontsize=10)
    
    plt.title("实体相似度矩阵热力图", fontsize=16, pad=20)
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"热力图已保存到: {output_path}")
    plt.show()

def create_similarity_network(similarity_matrix: np.ndarray, entity_ids: List[str], threshold: float = 0.8, output_path: str = "similarity_network.png"):
    """创建相似度网络图"""
    if len(entity_ids) < 2:
        print("实体数量不足，无法创建网络图")
        return
    
    try:
        import networkx as nx
        
        # 创建图
        G = nx.Graph()
        
        # 添加节点
        for i, entity_id in enumerate(entity_ids):
            G.add_node(entity_id, size=similarity_matrix[i, i] * 100)
        
        # 添加边（只添加相似度高于阈值的边）
        edges = []
        for i in range(len(entity_ids)):
            for j in range(i + 1, len(entity_ids)):
                similarity = similarity_matrix[i, j]
                if similarity >= threshold:
                    edges.append((entity_ids[i], entity_ids[j], similarity))
        
        for u, v, weight in edges:
            G.add_edge(u, v, weight=weight, width=weight * 3)
        
        if len(edges) == 0:
            print(f"没有相似度 >= {threshold} 的边，无法创建网络图")
            return
        
        plt.figure(figsize=(14, 10))
        
        # 计算节点位置
        pos = nx.spring_layout(G, k=2, iterations=50)
        
        # 绘制节点
        node_sizes = [G.nodes[n].get('size', 300) for n in G.nodes()]
        nx.draw_networkx_nodes(G, pos, node_size=node_sizes, node_color='lightblue', alpha=0.8)
        
        # 绘制边
        edge_widths = [G[u][v]['width'] for u, v in G.edges()]
        nx.draw_networkx_edges(G, pos, width=edge_widths, alpha=0.5, edge_color='gray')
        
        # 绘制标签
        nx.draw_networkx_labels(G, pos, font_size=10, font_family='sans-serif')
        
        # 添加边权重标签
        edge_labels = {(u, v): f"{G[u][v]['weight']:.3f}" for u, v in G.edges()}
        nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_size=8)
        
        plt.title(f"实体相似度网络图 (阈值: {threshold})", fontsize=16)
        plt.axis('off')
        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        print(f"网络图已保存到: {output_path}")
        plt.show()
        
    except ImportError:
        print("需要安装networkx库来创建网络图: pip install networkx")

def create_similarity_distribution(similarity_matrix: np.ndarray, output_path: str = "similarity_distribution.png"):
    """创建相似度分布直方图"""
    # 获取非对角线元素
    mask = ~np.eye(len(similarity_matrix), dtype=bool)
    similarities = similarity_matrix[mask]
    
    if len(similarities) == 0:
        print("没有相似度数据可分析")
        return
    
    plt.figure(figsize=(12, 5))
    
    # 创建子图
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    
    # 直方图
    ax1.hist(similarities, bins=20, edgecolor='black', alpha=0.7, color='skyblue')
    ax1.set_xlabel('相似度', fontsize=12)
    ax1.set_ylabel('频数', fontsize=12)
    ax1.set_title('相似度分布直方图', fontsize=14)
    ax1.grid(True, alpha=0.3)
    
    # 添加统计信息
    stats_text = f"""
    统计信息:
    数量: {len(similarities)}
    均值: {np.mean(similarities):.4f}
    中位数: {np.median(similarities):.4f}
    标准差: {np.std(similarities):.4f}
    最小值: {np.min(similarities):.4f}
    最大值: {np.max(similarities):.4f}
    """
    ax1.text(0.02, 0.98, stats_text, transform=ax1.transAxes, 
             fontsize=9, verticalalignment='top',
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    # 箱线图
    ax2.boxplot(similarities, vert=True, patch_artist=True,
                boxprops=dict(facecolor='lightgreen'))
    ax2.set_ylabel('相似度', fontsize=12)
    ax2.set_title('相似度箱线图', fontsize=14)
    ax2.grid(True, alpha=0.3)
    
    # 添加阈值线
    thresholds = [0.7, 0.8, 0.9]
    colors = ['orange', 'red', 'purple']
    for threshold, color in zip(thresholds, colors):
        count_above = np.sum(similarities >= threshold)
        percentage = count_above / len(similarities) * 100
        ax1.axvline(x=threshold, color=color, linestyle='--', alpha=0.7, 
                   label=f'阈值 {threshold}: {count_above}对 ({percentage:.1f}%)')
        ax2.axhline(y=threshold, color=color, linestyle='--', alpha=0.7)
    
    ax1.legend(loc='upper left', fontsize=9)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"分布图已保存到: {output_path}")
    plt.show()

def analyze_clusters(similarity_matrix: np.ndarray, entity_ids: List[str], threshold: float = 0.8):
    """分析实体聚类"""
    if len(entity_ids) < 2:
        print("实体数量不足，无法进行聚类分析")
        return
    
    from scipy.cluster.hierarchy import dendrogram, linkage
    from scipy.spatial.distance import squareform
    
    # 将相似度转换为距离
    distance_matrix = 1 - similarity_matrix
    
    # 进行层次聚类
    condensed_dist = squareform(distance_matrix)
    Z = linkage(condensed_dist, method='average')
    
    plt.figure(figsize=(12, 8))
    
    # 绘制树状图
    dendrogram(Z, labels=entity_ids, leaf_rotation=90, leaf_font_size=10)
    plt.title(f'实体层次聚类树状图 (阈值: {threshold})', fontsize=16)
    plt.xlabel('实体', fontsize=12)
    plt.ylabel('距离 (1 - 相似度)', fontsize=12)
    plt.axhline(y=1-threshold, color='r', linestyle='--', alpha=0.7, 
               label=f'切割阈值: {threshold}')
    plt.legend()
    plt.tight_layout()
    plt.savefig('entity_clusters.png', dpi=300, bbox_inches='tight')
    print("聚类图已保存到: entity_clusters.png")
    plt.show()
    
    # 分析聚类结果
    from scipy.cluster.hierarchy import fcluster
    clusters = fcluster(Z, t=1-threshold, criterion='distance')
    
    # 统计聚类信息
    unique_clusters = np.unique(clusters)
    print(f"\n聚类分析 (阈值={threshold}):")
    print(f"共形成 {len(unique_clusters)} 个聚类")
    
    for cluster_id in unique_clusters:
        cluster_indices = np.where(clusters == cluster_id)[0]
        cluster_entities = [entity_ids[i] for i in cluster_indices]
        print(f"\n聚类 {cluster_id} (包含 {len(cluster_entities)} 个实体):")
        for entity in cluster_entities:
            print(f"  - {entity}")

def main():
    """主函数"""
    library_path = "data/memory/test3/kg_data/entity_library.json"
    
    if not Path(library_path).exists():
        print(f"实体库文件不存在: {library_path}")
        return
    
    print(f"加载实体库: {library_path}")
    
    try:
        # 加载数据
        embeddings, aliases = load_entity_data(library_path)
        
        if not embeddings:
            print("实体库中没有嵌入向量数据")
            return
        
        print(f"找到 {len(embeddings)} 个有嵌入向量的实体")
        
        # 计算相似度矩阵
        similarity_matrix, entity_ids = compute_similarity_matrix(embeddings)
        
        if len(entity_ids) == 0:
            print("没有可计算相似度的实体")
            return
        
        print("\n实体列表:")
        for i, entity_id in enumerate(entity_ids):
            alias_str = ", ".join(aliases.get(entity_id, []))
            if alias_str:
                print(f"  {i:2d}. {entity_id} (别名: {alias_str})")
            else:
                print(f"  {i:2d}. {entity_id}")
        
        # 创建可视化
        print("\n创建可视化图表...")
        
        # 1. 热力图
        create_heatmap(similarity_matrix, entity_ids, "similarity_heatmap.png")
        
        # 2. 网络图 (阈值=0.8)
        create_similarity_network(similarity_matrix, entity_ids, threshold=0.8, output_path="similarity_network_0.8.png")
        
        # 3. 网络图 (阈值=0.7)
        create_similarity_network(similarity_matrix, entity_ids, threshold=0.7, output_path="similarity_network_0.7.png")
        
        # 4. 分布图
        create_similarity_distribution(similarity_matrix, "similarity_distribution.png")
        
        # 5. 聚类分析
        analyze_clusters(similarity_matrix, entity_ids, threshold=0.8)
        
        # 导出详细数据
        df_matrix = pd.DataFrame(similarity_matrix, index=entity_ids, columns=entity_ids)
        df_matrix.to_csv("detailed_similarity_matrix.csv")
        
        # 创建实体信息表
        entity_info = []
        for entity_id in entity_ids:
            info = {
                "实体ID": entity_id,
                "别名": ", ".join(aliases.get(entity_id, [])),
                "嵌入向量维度": len(embeddings[entity_id]) if entity_id in embeddings else 0
            }
            entity_info.append(info)
        
        df_info = pd.DataFrame(entity_info)
        df_info.to_csv("entity_info.csv", index=False, encoding='utf-8-sig')
        
        print("\n数据导出完成:")
        print("  - detailed_similarity_matrix.csv: 详细相似度矩阵")
        print("  - entity_info.csv: 实体信息表")
        print("  - entity_similarity_matrix.csv: 相似度矩阵 (简单版)")
        print("  - 多个可视化图表文件")
        
    except Exception as e:
        print(f"处理实体库时出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()