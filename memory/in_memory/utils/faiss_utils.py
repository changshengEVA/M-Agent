#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAISS工具函数

包含长期记忆管理系统中与FAISS向量索引相关的工具函数。
用于对scene中的theme进行编码，并支持按照diary进行召回。
"""

import os
import json
import logging
import pickle
import numpy as np
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 尝试导入FAISS，如果失败则提供友好的错误信息
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("FAISS不可用，请安装faiss-cpu或faiss-gpu")


class FAISSIndexManager:
    """
    FAISS索引管理器
    
    管理scene的theme向量索引，支持按照diary进行召回。
    """
    
    def __init__(self, index_dir: Optional[Path] = None, dimension: int = 1536):
        """
        初始化FAISS索引管理器
        
        Args:
            index_dir: 索引目录路径，如果提供则尝试加载现有索引
            dimension: 向量维度，默认为1536（OpenAI text-embedding-ada-002）
        """
        if not FAISS_AVAILABLE:
            raise ImportError("FAISS不可用，请安装faiss-cpu或faiss-gpu")
        
        self.dimension = dimension
        self.index = None  # FAISS索引
        self.id_to_scene = {}  # 索引ID到scene信息的映射
        self.scene_to_id = {}  # scene_id到索引ID的映射
        self.next_id = 0  # 下一个可用的索引ID
        
        # 嵌入模型（延迟加载）
        self._embed_model = None
        
        if index_dir is not None:
            # 尝试加载现有索引，如果失败则创建新索引
            if not self.load_index(index_dir):
                logger.warning(f"无法加载现有索引，创建新索引: {index_dir}")
                self._create_empty_index()
        else:
            # 创建新的空索引
            self._create_empty_index()
    
    def _create_empty_index(self):
        """创建空的FAISS索引"""
        # 使用内积（余弦相似度）索引，需要归一化向量
        self.index = faiss.IndexFlatIP(self.dimension)
        logger.info(f"创建新的FAISS索引，维度: {self.dimension}")
    
    def _get_embed_model(self):
        """获取嵌入模型（延迟加载）"""
        if self._embed_model is None:
            try:
                from load_model.OpenAIcall import get_embed_model
                self._embed_model = get_embed_model()
                logger.info("加载OpenAI嵌入模型成功")
            except ImportError as e:
                logger.error(f"无法加载嵌入模型: {e}")
                raise
        return self._embed_model
    
    def _embed_text(self, text: str) -> np.ndarray:
        """
        将文本编码为向量
        
        Args:
            text: 输入文本
            
        Returns:
            归一化的向量（numpy数组）
        """
        if not text or not text.strip():
            # 返回零向量
            return np.zeros(self.dimension, dtype=np.float32)
        
        embed_model = self._get_embed_model()
        vector = embed_model(text)
        vector_np = np.array(vector, dtype=np.float32)
        
        # 归一化向量（用于余弦相似度）
        norm = np.linalg.norm(vector_np)
        if norm > 0:
            vector_np = vector_np / norm
        
        return vector_np
    
    def add_scene(self, scene_id: str, theme: str, diary: str, metadata: Optional[Dict] = None) -> bool:
        """
        添加scene到索引
        
        Args:
            scene_id: 场景ID
            theme: 主题文本（将被编码）
            diary: 日记文本（用于召回）
            metadata: 额外的元数据
            
        Returns:
            添加成功返回True，否则返回False
        """
        if scene_id in self.scene_to_id:
            logger.warning(f"scene_id已存在: {scene_id}")
            return False
        
        # 编码theme
        try:
            theme_vector = self._embed_text(theme)
        except Exception as e:
            logger.error(f"编码theme失败: {e}")
            return False
        
        # 添加到索引
        index_id = self.next_id
        self.index.add(theme_vector.reshape(1, -1))
        
        # 存储映射和元数据
        self.id_to_scene[index_id] = {
            "scene_id": scene_id,
            "theme": theme,
            "diary": diary,
            "metadata": metadata or {},
            "index_id": index_id
        }
        self.scene_to_id[scene_id] = index_id
        self.next_id += 1
        
        logger.debug(f"添加scene到索引: {scene_id}, 索引ID: {index_id}")
        return True
    
    def search_by_diary(self, diary: str, top_k: int = 5) -> List[Dict]:
        """
        按照diary进行召回
        
        Args:
            diary: 日记文本（查询文本）
            top_k: 返回结果的数量
            
        Returns:
            包含匹配scene信息的列表，按相似度降序排列
        """
        if self.index.ntotal == 0:
            logger.warning("索引为空，无法搜索")
            return []
        
        # 编码diary作为查询向量
        try:
            query_vector = self._embed_text(diary)
        except Exception as e:
            logger.error(f"编码diary失败: {e}")
            return []
        
        # 搜索最相似的theme
        query_vector = query_vector.reshape(1, -1)
        distances, indices = self.index.search(query_vector, min(top_k, self.index.ntotal))
        
        results = []
        for i, (distance, index_id) in enumerate(zip(distances[0], indices[0])):
            if index_id < 0 or index_id >= self.next_id:
                continue
            
            scene_info = self.id_to_scene.get(index_id)
            if scene_info:
                results.append({
                    "scene_id": scene_info["scene_id"],
                    "theme": scene_info["theme"],
                    "diary": scene_info["diary"],
                    "metadata": scene_info["metadata"],
                    "similarity": float(distance),  # 内积值（余弦相似度）
                    "rank": i + 1
                })
        
        logger.debug(f"diary召回结果: {len(results)} 个匹配")
        return results
    
    def search_by_theme(self, theme: str, top_k: int = 5) -> List[Dict]:
        """
        按照theme进行召回（直接使用theme查询）
        
        Args:
            theme: 主题文本（查询文本）
            top_k: 返回结果的数量
            
        Returns:
            包含匹配scene信息的列表，按相似度降序排列
        """
        return self.search_by_diary(theme, top_k)
    
    def remove_scene(self, scene_id: str) -> bool:
        """
        从索引中移除scene
        
        Args:
            scene_id: 场景ID
            
        Returns:
            移除成功返回True，否则返回False
        """
        if scene_id not in self.scene_to_id:
            logger.warning(f"scene_id不存在: {scene_id}")
            return False
        
        index_id = self.scene_to_id[scene_id]
        
        # FAISS不支持直接删除，我们需要重建索引
        # 这是一个简化实现：标记为删除，实际删除需要重建
        # 对于生产环境，应该使用支持删除的索引类型（如IndexIDMap）
        logger.warning(f"FAISS索引不支持直接删除，scene {scene_id} 被标记为无效")
        
        # 从映射中移除
        del self.id_to_scene[index_id]
        del self.scene_to_id[scene_id]
        
        # 注意：索引中的向量仍然存在，但不会被搜索到（因为映射已移除）
        # 更好的实现是使用faiss.IndexIDMap并实际删除
        return True
    
    def get_scene_count(self) -> int:
        """获取索引中的scene数量"""
        return len(self.scene_to_id)
    
    def save_index(self, index_dir: Path):
        """
        保存索引和元数据到磁盘
        
        Args:
            index_dir: 索引目录路径
        """
        try:
            # 确保目录存在
            index_dir.mkdir(parents=True, exist_ok=True)
            
            # 保存FAISS索引
            index_file = index_dir / "faiss_index.bin"
            faiss.write_index(self.index, str(index_file))
            
            # 保存元数据
            metadata_file = index_dir / "metadata.json"
            metadata = {
                "dimension": self.dimension,
                "next_id": self.next_id,
                "id_to_scene": self.id_to_scene,
                "scene_to_id": self.scene_to_id
            }
            
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            
            logger.info(f"保存FAISS索引到: {index_dir}")
            return True
            
        except Exception as e:
            logger.error(f"保存索引失败: {e}")
            return False
    
    def load_index(self, index_dir: Path) -> bool:
        """
        从磁盘加载索引和元数据
        
        Args:
            index_dir: 索引目录路径
            
        Returns:
            加载成功返回True，否则返回False
        """
        try:
            index_file = index_dir / "faiss_index.bin"
            metadata_file = index_dir / "metadata.json"
            
            if not index_file.exists() or not metadata_file.exists():
                logger.warning(f"索引文件不存在: {index_dir}")
                return False
            
            # 加载FAISS索引
            self.index = faiss.read_index(str(index_file))
            
            # 加载元数据
            with open(metadata_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            self.dimension = metadata.get("dimension", self.dimension)
            self.next_id = metadata.get("next_id", 0)
            self.id_to_scene = metadata.get("id_to_scene", {})
            self.scene_to_id = metadata.get("scene_to_id", {})
            
            # 转换索引ID为整数（JSON保存时键被转换为字符串）
            self.id_to_scene = {int(k): v for k, v in self.id_to_scene.items()}
            self.scene_to_id = {k: int(v) for k, v in self.scene_to_id.items()}
            
            logger.info(f"加载FAISS索引成功: {index_dir}, 包含 {self.get_scene_count()} 个scene")
            return True
            
        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """获取索引统计信息"""
        return {
            "scene_count": self.get_scene_count(),
            "dimension": self.dimension,
            "index_type": str(type(self.index)) if self.index else "None",
            "index_size": self.index.ntotal if self.index else 0
        }


def create_faiss_index_manager(memory_root: Path, memory_id: str = "default") -> FAISSIndexManager:
    """
    创建或加载FAISS索引管理器
    
    Args:
        memory_root: 记忆根目录
        memory_id: 记忆ID
        
    Returns:
        FAISSIndexManager实例
    """
    index_dir = memory_root / memory_id / "faiss_index"
    return FAISSIndexManager(index_dir)


def test_faiss_functionality():
    """测试FAISS功能"""
    if not FAISS_AVAILABLE:
        print("FAISS不可用，跳过测试")
        return
    
    print("测试FAISS功能...")
    
    # 创建临时目录
    import tempfile
    temp_dir = Path(tempfile.mkdtemp())
    
    try:
        # 创建索引管理器
        manager = FAISSIndexManager()
        
        # 添加测试数据
        test_scenes = [
            {"scene_id": "scene_00001", "theme": "人工智能与机器学习", "diary": "今天学习了深度神经网络的基本原理"},
            {"scene_id": "scene_00002", "theme": "自然语言处理", "diary": "讨论了Transformer模型在NLP中的应用"},
            {"scene_id": "scene_00003", "theme": "计算机视觉", "diary": "研究了图像分类和目标检测算法"},
        ]
        
        for scene in test_scenes:
            success = manager.add_scene(
                scene["scene_id"],
                scene["theme"],
                scene["diary"],
                {"source": "test"}
            )
            print(f"添加scene {scene['scene_id']}: {'成功' if success else '失败'}")
        
        # 测试搜索
        query = "深度学习模型"
        results = manager.search_by_diary(query, top_k=2)
        print(f"\n查询: '{query}'")
        print(f"找到 {len(results)} 个结果:")
        for result in results:
            print(f"  - {result['scene_id']}: {result['theme']} (相似度: {result['similarity']:.4f})")
        
        # 测试保存和加载
        save_success = manager.save_index(temp_dir)
        print(f"\n保存索引: {'成功' if save_success else '失败'}")
        
        if save_success:
            # 创建新的管理器并加载
            manager2 = FAISSIndexManager(temp_dir)
            print(f"加载索引后scene数量: {manager2.get_scene_count()}")
            
            # 再次搜索
            results2 = manager2.search_by_diary("神经网络", top_k=1)
            print(f"加载后搜索 '神经网络': {len(results2)} 个结果")
        
        # 打印统计信息
        stats = manager.get_stats()
        print(f"\n索引统计: {stats}")
        
    finally:
        # 清理临时目录
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n清理临时目录: {temp_dir}")


if __name__ == "__main__":
    test_faiss_functionality()