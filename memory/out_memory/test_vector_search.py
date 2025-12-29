#!/usr/bin/env python3
"""
向量搜索测试文件 - 用于召回"北京大学"相关的记忆
"""

import json
import numpy as np
import faiss
from pathlib import Path
import sys
from typing import List, Dict, Any, Tuple

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from load_model.OpenAIcall import get_embed_model

def load_vector_store(vector_dir: str = "data/memory/vectors/scenes") -> Tuple[faiss.Index, np.ndarray, List[Dict[str, Any]]]:
    """
    加载向量库
    
    Args:
        vector_dir: 向量库目录路径
        
    Returns:
        index: FAISS 索引
        embeddings: 嵌入向量数组
        metas: 元数据列表
    """
    vector_dir_path = Path(vector_dir)
    
    # 加载 FAISS 索引
    index_path = vector_dir_path / "index.faiss"
    if not index_path.exists():
        raise FileNotFoundError(f"FAISS 索引文件不存在: {index_path}")
    
    index = faiss.read_index(str(index_path))
    print(f"已加载 FAISS 索引，包含 {index.ntotal} 个向量")
    
    # 加载嵌入向量
    embeddings_path = vector_dir_path / "embeddings.npy"
    if not embeddings_path.exists():
        raise FileNotFoundError(f"嵌入向量文件不存在: {embeddings_path}")
    
    embeddings = np.load(str(embeddings_path))
    print(f"已加载嵌入向量，形状: {embeddings.shape}")
    
    # 加载元数据
    meta_path = vector_dir_path / "meta.jsonl"
    if not meta_path.exists():
        raise FileNotFoundError(f"元数据文件不存在: {meta_path}")
    
    metas = []
    with open(meta_path, 'r', encoding='utf-8') as f:
        for line in f:
            metas.append(json.loads(line.strip()))
    
    print(f"已加载 {len(metas)} 条元数据")
    
    return index, embeddings, metas

def search_by_query(query: str, index: faiss.Index, metas: List[Dict[str, Any]], 
                   embed_model, k: int = 5) -> List[Dict[str, Any]]:
    """
    通过查询文本搜索相似记忆
    
    Args:
        query: 查询文本
        index: FAISS 索引
        metas: 元数据列表
        embed_model: 嵌入模型函数
        k: 返回的最近邻数量
        
    Returns:
        搜索结果列表，每个元素包含元数据和相似度分数
    """
    # 生成查询文本的嵌入向量
    query_embedding = embed_model(query)
    query_vector = np.array([query_embedding], dtype=np.float32)
    
    # 搜索最近邻
    distances, indices = index.search(query_vector, k)
    
    results = []
    for i, (distance, idx) in enumerate(zip(distances[0], indices[0])):
        if idx < 0 or idx >= len(metas):
            continue
        
        meta = metas[idx]
        # 计算相似度分数（距离越小，相似度越高）
        similarity_score = 1.0 / (1.0 + distance)  # 简单的相似度转换
        
        results.append({
            "rank": i + 1,
            "similarity_score": float(similarity_score),
            "distance": float(distance),
            "meta": meta
        })
    
    return results

def search_by_keyword(keyword: str, metas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    通过关键词在元数据中搜索（基于标签、内容等）
    
    Args:
        keyword: 关键词
        metas: 元数据列表
        
    Returns:
        匹配的元数据列表
    """
    keyword_lower = keyword.lower()
    results = []
    
    for meta in metas:
        # 检查标签
        tags = meta.get("tags", [])
        tag_matches = any(keyword_lower in tag.lower() for tag in tags)
        
        # 检查场景类型、内容类型等
        scene_type = meta.get("scene_type", "").lower()
        content_type = meta.get("content_type", "").lower()
        intent = meta.get("intent", "").lower()
        
        type_matches = (keyword_lower in scene_type or 
                       keyword_lower in content_type or 
                       keyword_lower in intent)
        
        if tag_matches or type_matches:
            results.append(meta)
    
    return results

def format_result(result: Dict[str, Any]) -> str:
    """
    格式化搜索结果用于输出
    
    Args:
        result: 搜索结果
        
    Returns:
        格式化后的字符串
    """
    meta = result["meta"]
    
    output_lines = [
        f"排名: #{result['rank']}",
        f"相似度分数: {result['similarity_score']:.4f} (距离: {result['distance']:.4f})",
        f"场景ID: {meta['scene_id']}",
        f"对话ID: {meta['source_dialogue']}",
        f"片段ID: {meta['episode_ids']}",
        f"用户ID: {meta['user_id']}",
        f"场景类型: {meta['scene_type']}",
        f"内容类型: {meta['content_type']}",
        f"意图: {meta.get('intent', 'N/A')}",
        f"标签: {', '.join(meta.get('tags', []))}",
        f"置信度: {meta.get('confidence', 1.0)}",
        f"文件路径: {meta['scene_path']}",
        "-" * 50
    ]
    
    return "\n".join(output_lines)

def main():
    """主函数：测试召回'北京大学'相关的记忆"""
    print("=" * 60)
    print("向量库记忆召回测试 - '北京大学'")
    print("=" * 60)
    
    try:
        # 1. 加载向量库
        print("\n1. 加载向量库...")
        index, embeddings, metas = load_vector_store()
        
        # 2. 加载嵌入模型
        print("\n2. 加载嵌入模型...")
        embed_model = get_embed_model()
        
        keyword_results = search_by_keyword("北京大学", metas)
        keyword_results.extend(search_by_keyword("Peking_University", metas))
        keyword_results.extend(search_by_keyword("peking_university", metas))
        
        # 4. 方法2：基于向量相似度的搜索
        print("\n4. 基于向量相似度搜索 '北京大学'...")
        query_texts = [
            "北京大学",
        ]
        
        all_vector_results = []
        for query in query_texts:
            print(f"\n查询: '{query}'")
            vector_results = search_by_query(query, index, metas, embed_model, k=6)
            
            for result in vector_results:
                all_vector_results.append((query, result))
                print(f"  排名 #{result['rank']}: 对话ID={result['meta']['source_dialogue']}, "
                      f"片段ID={result['meta']['episode_ids']}, "
                      f"相似度={result['similarity_score']:.4f}")
        
        # 5. 输出详细结果到文件
        print("\n5. 生成详细结果报告...")
        output_file = Path(__file__).parent / "peking_university_memory_results.txt"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("北京大学相关记忆召回结果\n")
            f.write("=" * 60 + "\n\n")
            
            # 关键词搜索结果
            f.write("一、关键词搜索结果（基于标签匹配）\n")
            f.write("-" * 50 + "\n")
            if keyword_results:
                for i, meta in enumerate(keyword_results, 1):
                    f.write(f"\n结果 #{i}:\n")
                    f.write(f"对话ID: {meta['source_dialogue']}\n")
                    f.write(f"片段ID: {meta['episode_ids']}\n")
                    f.write(f"场景ID: {meta['scene_id']}\n")
                    f.write(f"场景版本: {meta['scene_version']}\n")
                    f.write(f"用户ID: {meta['user_id']}\n")
                    f.write(f"标签: {', '.join(meta.get('tags', []))}\n")
                    f.write(f"文件路径: {meta['scene_path']}\n")
            else:
                f.write("未找到匹配的关键词结果\n")
            
            # 向量搜索结果
            f.write("\n\n二、向量相似度搜索结果\n")
            f.write("-" * 50 + "\n")
            
            # 按查询分组
            queries_dict = {}
            for query, result in all_vector_results:
                if query not in queries_dict:
                    queries_dict[query] = []
                queries_dict[query].append(result)
            
            for query, results in queries_dict.items():
                f.write(f"\n查询: '{query}'\n")
                f.write("-" * 30 + "\n")
                
                for result in results:
                    f.write(format_result(result) + "\n")
            
            # 总结
            f.write("\n\n三、总结\n")
            f.write("-" * 50 + "\n")
            
            unique_dialogues = set()
            unique_episodes = set()
            
            for meta in keyword_results:
                unique_dialogues.add(meta['source_dialogue'])
                unique_episodes.add(meta['episode_ids'])
            
            for _, result in all_vector_results:
                meta = result['meta']
                unique_dialogues.add(meta['source_dialogue'])
                unique_episodes.add(meta['episode_ids'])
            
            f.write(f"找到的唯一对话ID: {', '.join(sorted(unique_dialogues))}\n")
            f.write(f"找到的唯一片段ID: {', '.join(sorted(unique_episodes))}\n")
            f.write(f"总计: {len(unique_dialogues)} 个对话, {len(unique_episodes)} 个片段\n")
        
        print(f"\n详细结果已保存到: {output_file}")
        
        # 6. 控制台输出总结
        print("\n" + "=" * 60)
        print("测试完成！")
        print("=" * 60)
        
        unique_dialogues = set()
        unique_episodes = set()
        
        for meta in keyword_results:
            unique_dialogues.add(meta['source_dialogue'])
            unique_episodes.add(meta['episode_ids'])
        
        for _, result in all_vector_results:
            meta = result['meta']
            unique_dialogues.add(meta['source_dialogue'])
            unique_episodes.add(meta['episode_ids'])
        
        print(f"\n找到的'北京大学'相关记忆:")
        print(f"对话ID列表: {', '.join(sorted(unique_dialogues))}")
        print(f"片段ID列表: {', '.join(sorted(unique_episodes))}")
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())