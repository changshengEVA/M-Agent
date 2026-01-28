#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
来源管理模块

负责来源信息的合并和去重逻辑
"""

import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class SourceManager:
    """来源信息管理类"""
    
    @staticmethod
    def is_same_source(source1: Dict, source2: Dict) -> bool:
        """
        检查两个来源信息是否相同
        
        Args:
            source1: 第一个来源信息
            source2: 第二个来源信息
            
        Returns:
            如果相同返回True，否则返回False
        """
        # 比较关键字段：dialogue_id, episode_id, scene_id
        return (source1.get('dialogue_id') == source2.get('dialogue_id') and
                source1.get('episode_id') == source2.get('episode_id') and
                source1.get('scene_id') == source2.get('scene_id'))
    
    @staticmethod
    def merge_sources(sources_a: List[Dict], sources_b: List[Dict]) -> List[Dict]:
        """
        合并两个来源列表，避免重复
        
        Args:
            sources_a: 来源列表A
            sources_b: 来源列表B
            
        Returns:
            合并后的来源列表
        """
        merged_sources = sources_a.copy()
        
        for source_b in sources_b:
            # 检查是否已存在相同来源
            source_exists = False
            for source_a in merged_sources:
                if SourceManager.is_same_source(source_a, source_b):
                    source_exists = True
                    break
            
            if not source_exists:
                merged_sources.append(source_b)
        
        return merged_sources
    
    @staticmethod
    def add_source_if_not_exists(existing_sources: List[Dict], new_source: Dict) -> List[Dict]:
        """
        如果来源不存在则添加到列表中
        
        Args:
            existing_sources: 现有来源列表
            new_source: 新来源信息
            
        Returns:
            更新后的来源列表
        """
        # 检查是否已存在相同来源
        for source in existing_sources:
            if SourceManager.is_same_source(source, new_source):
                return existing_sources
        
        # 添加新来源
        existing_sources.append(new_source)
        return existing_sources
    
    @staticmethod
    def create_source_info(dialogue_id: str = None, episode_id: str = None, 
                          scene_id: str = None, generated_at: str = None) -> Dict:
        """
        创建标准化的来源信息字典
        
        Args:
            dialogue_id: 对话ID
            episode_id: 情节ID
            scene_id: 场景ID
            generated_at: 生成时间戳
            
        Returns:
            来源信息字典
        """
        source_info = {}
        
        if dialogue_id is not None:
            source_info['dialogue_id'] = dialogue_id
        if episode_id is not None:
            source_info['episode_id'] = episode_id
        if scene_id is not None:
            source_info['scene_id'] = scene_id
        if generated_at is not None:
            source_info['generated_at'] = generated_at
        
        return source_info
    
    @staticmethod
    def extract_source_from_kg_candidate(kg_candidate_json: Dict) -> Dict:
        """
        从kg_candidate JSON中提取来源信息
        
        Args:
            kg_candidate_json: kg_candidate JSON对象
            
        Returns:
            提取的来源信息
        """
        return {
            "dialogue_id": kg_candidate_json.get('dialogue_id'),
            "episode_id": kg_candidate_json.get('episode_id'),
            "generated_at": kg_candidate_json.get('generated_at')
        }
    
    @staticmethod
    def enhance_source_with_scene(source_info: Dict, scene_id: str = None) -> Dict:
        """
        使用场景ID增强来源信息
        
        Args:
            source_info: 基本来源信息
            scene_id: 场景ID
            
        Returns:
            增强后的来源信息
        """
        enhanced_source = source_info.copy()
        if scene_id is not None:
            enhanced_source['scene_id'] = scene_id
        return enhanced_source
    
    @staticmethod
    def validate_source(source_info: Dict) -> bool:
        """
        验证来源信息的有效性
        
        Args:
            source_info: 来源信息
            
        Returns:
            如果有效返回True，否则返回False
        """
        # 至少需要有一个标识字段
        required_fields = ['dialogue_id', 'episode_id', 'scene_id']
        for field in required_fields:
            if field in source_info and source_info[field]:
                return True
        
        # 如果没有找到任何标识字段，记录警告
        logger.warning(f"来源信息缺少标识字段: {source_info}")
        return False
    
    @staticmethod
    def get_source_key(source_info: Dict) -> str:
        """
        获取来源信息的唯一键
        
        Args:
            source_info: 来源信息
            
        Returns:
            唯一键字符串
        """
        # 使用关键字段组合生成唯一键
        dialogue_id = source_info.get('dialogue_id', '')
        episode_id = source_info.get('episode_id', '')
        scene_id = source_info.get('scene_id', '')
        
        return f"{dialogue_id}|{episode_id}|{scene_id}"
    
    @staticmethod
    def deduplicate_sources(sources: List[Dict]) -> List[Dict]:
        """
        去重来源列表
        
        Args:
            sources: 来源列表
            
        Returns:
            去重后的来源列表
        """
        seen_keys = set()
        deduplicated = []
        
        for source in sources:
            source_key = SourceManager.get_source_key(source)
            if source_key not in seen_keys:
                seen_keys.add(source_key)
                deduplicated.append(source)
        
        return deduplicated