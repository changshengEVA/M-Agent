#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关系存储模块

负责关系的存储、查找和合并操作
"""

import json
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class RelationStorage:
    """关系存储管理类"""
    
    def __init__(self, relation_dir: Path):
        """
        初始化关系存储管理器
        
        Args:
            relation_dir: 关系文件目录路径
        """
        self.relation_dir = relation_dir
        self.relation_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"初始化关系存储，目录: {self.relation_dir}")
    
    def find_relation(self, subject: str, relation_type: str, obj: str) -> Optional[Tuple[Path, Dict]]:
        """
        查找指定关系
        
        Args:
            subject: 主体实体ID
            relation_type: 关系类型
            obj: 客体实体ID
            
        Returns:
            如果找到返回(文件路径, 关系数据)，否则返回None
        """
        if not self.relation_dir.exists():
            return None
        
        for relation_file in self.relation_dir.glob("*.json"):
            try:
                with open(relation_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                
                # 检查是否是相同的关系（相同的subject, relation, object）
                if (existing_data.get('subject') == subject and
                    existing_data.get('relation') == relation_type and
                    existing_data.get('object') == obj):
                    return (relation_file, existing_data)
            except Exception as e:
                logger.warning(f"读取关系文件失败 {relation_file}: {e}")
                continue
        
        return None
    
    def save_relation(self, relation_data: Dict, source_info: Optional[Dict] = None) -> Tuple[bool, Optional[Path]]:
        """
        保存关系到文件，检测并合并重复的关系
        
        Args:
            relation_data: 关系数据，包含 subject, relation, object, confidence, scene_id 等字段
            source_info: 基本来源信息（可选）
            
        Returns:
            (保存成功, 文件路径)
        """
        try:
            subject = relation_data.get('subject')
            relation_type = relation_data.get('relation')
            obj = relation_data.get('object')
            
            if not subject or not relation_type or not obj:
                logger.warning("关系数据缺少必要字段")
                return (False, None)
            
            # 创建关系的来源信息，包含 scene_id（如果存在）
            relation_source_info = source_info.copy() if source_info else {}
            scene_id = relation_data.get('scene_id')
            if scene_id is not None:
                relation_source_info['scene_id'] = scene_id
            
            # 首先检查是否已存在相同的关系
            existing_relation = self.find_relation(subject, relation_type, obj)
            
            if existing_relation:
                # 合并现有关系
                existing_file, existing_data = existing_relation
                logger.debug(f"找到重复关系，合并: {subject} -[{relation_type}]-> {obj}")
                
                # 确保 sources 字段存在
                if 'sources' not in existing_data:
                    existing_data['sources'] = []
                
                # 添加来源信息到现有关系数据
                if relation_source_info:
                    # 检查是否已存在相同来源（考虑dialogue_id, episode_id和scene_id）
                    source_found = False
                    for source in existing_data['sources']:
                        # 如果所有关键字段都匹配，则认为是相同来源
                        if (source.get('dialogue_id') == relation_source_info.get('dialogue_id') and
                            source.get('episode_id') == relation_source_info.get('episode_id') and
                            source.get('scene_id') == relation_source_info.get('scene_id')):
                            source_found = True
                            break
                    
                    if not source_found:
                        existing_data['sources'].append(relation_source_info)
                
                # 更新置信度（取最高值）
                new_confidence = relation_data.get('confidence')
                if new_confidence is not None:
                    existing_confidence = existing_data.get('confidence')
                    if existing_confidence is None or new_confidence > existing_confidence:
                        existing_data['confidence'] = new_confidence
                
                # 保存合并后的关系文件
                with open(existing_file, 'w', encoding='utf-8') as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=2)
                
                logger.debug(f"合并关系: {subject} -[{relation_type}]-> {obj} -> {existing_file}")
                return (True, existing_file)
            else:
                # 创建新的关系
                # 添加来源信息到关系数据
                if relation_source_info:
                    # 确保 sources 字段存在
                    if 'sources' not in relation_data:
                        relation_data['sources'] = []
                    
                    # 检查是否已存在相同来源（考虑dialogue_id, episode_id和scene_id）
                    source_found = False
                    for source in relation_data['sources']:
                        # 如果所有关键字段都匹配，则认为是相同来源
                        if (source.get('dialogue_id') == relation_source_info.get('dialogue_id') and
                            source.get('episode_id') == relation_source_info.get('episode_id') and
                            source.get('scene_id') == relation_source_info.get('scene_id')):
                            source_found = True
                            break
                    
                    if not source_found:
                        relation_data['sources'].append(relation_source_info)
                
                # 生成唯一的关系文件名
                relation_id = str(uuid.uuid4())
                relation_filename = f"{relation_id}.json"
                relation_file = self.relation_dir / relation_filename
                
                # 保存关系文件
                with open(relation_file, 'w', encoding='utf-8') as f:
                    json.dump(relation_data, f, ensure_ascii=False, indent=2)
                
                logger.debug(f"保存新关系: {subject} -[{relation_type}]-> {obj} -> {relation_file}")
                return (True, relation_file)
            
        except Exception as e:
            logger.error(f"保存关系失败: {e}")
            return (False, None)
    
    def update_relation_entities(self, old_entity_id: str, new_entity_id: str) -> Tuple[List[str], List[str]]:
        """
        更新关系中涉及的实体ID
        
        Args:
            old_entity_id: 旧的实体ID
            new_entity_id: 新的实体ID
            
        Returns:
            (更新的关系文件列表, 删除的关系文件列表)
        """
        updated_relations = []
        deleted_relations = []
        
        if not self.relation_dir.exists():
            return (updated_relations, deleted_relations)
        
        relation_files = list(self.relation_dir.glob("*.json"))
        
        for relation_file in relation_files:
            try:
                with open(relation_file, 'r', encoding='utf-8') as f:
                    relation_data = json.load(f)
                
                subject = relation_data.get('subject')
                obj = relation_data.get('object')
                relation_type = relation_data.get('relation')
                
                updated = False
                
                # 检查关系是否涉及旧实体ID
                if subject == old_entity_id or obj == old_entity_id:
                    # 更新subject或object
                    if subject == old_entity_id:
                        relation_data['subject'] = new_entity_id
                        updated = True
                    
                    if obj == old_entity_id:
                        relation_data['object'] = new_entity_id
                        updated = True
                    
                    if updated:
                        # 检查是否会产生重复关系（A->A）
                        if relation_data['subject'] == relation_data['object']:
                            # 删除自引用的关系
                            relation_file.unlink()
                            deleted_relations.append(str(relation_file.name))
                            continue
                        
                        # 检查是否已存在相同的关系（避免重复）
                        relation_exists = False
                        for other_file in relation_files:
                            if other_file == relation_file:
                                continue
                            
                            try:
                                with open(other_file, 'r', encoding='utf-8') as f2:
                                    other_data = json.load(f2)
                                
                                if (other_data.get('subject') == relation_data.get('subject') and
                                    other_data.get('relation') == relation_data.get('relation') and
                                    other_data.get('object') == relation_data.get('object')):
                                    # 合并来源信息
                                    if 'sources' in relation_data:
                                        sources_rel = other_data.get('sources', [])
                                        sources_new = relation_data.get('sources', [])
                                        # 简单的合并逻辑，实际应该使用SourceManager
                                        for source in sources_new:
                                            if source not in sources_rel:
                                                sources_rel.append(source)
                                        other_data['sources'] = sources_rel
                                    
                                    # 选择置信度更高的值
                                    other_conf = other_data.get('confidence', 0)
                                    new_conf = relation_data.get('confidence', 0)
                                    if new_conf > other_conf:
                                        other_data['confidence'] = new_conf
                                    
                                    # 保存更新后的关系
                                    with open(other_file, 'w', encoding='utf-8') as f2:
                                        json.dump(other_data, f2, ensure_ascii=False, indent=2)
                                    
                                    # 删除当前关系文件
                                    relation_file.unlink()
                                    relation_exists = True
                                    break
                            
                            except Exception:
                                continue
                        
                        if not relation_exists:
                            # 保存更新后的关系
                            with open(relation_file, 'w', encoding='utf-8') as f:
                                json.dump(relation_data, f, ensure_ascii=False, indent=2)
                            
                            updated_relations.append(str(relation_file.name))
            
            except Exception as e:
                logger.warning(f"处理关系文件 {relation_file} 时出错: {e}")
                continue
        
        return (updated_relations, deleted_relations)
    
    def get_all_relation_files(self) -> List[Path]:
        """
        获取所有关系文件
        
        Returns:
            关系文件路径列表
        """
        if not self.relation_dir.exists():
            return []
        
        return list(self.relation_dir.glob("*.json"))
    
    def get_relation_count(self) -> int:
        """
        获取关系数量
        
        Returns:
            关系文件数量
        """
        return len(self.get_all_relation_files())
    
    def load_relation(self, relation_file: Path) -> Optional[Dict]:
        """
        加载关系数据
        
        Args:
            relation_file: 关系文件路径
            
        Returns:
            关系数据字典，如果读取失败则返回None
        """
        try:
            with open(relation_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取关系文件失败 {relation_file}: {e}")
            return None
    
    def delete_relation_file(self, relation_file: Path) -> bool:
        """
        删除关系文件
        
        Args:
            relation_file: 关系文件路径
            
        Returns:
            删除成功返回True，否则返回False
        """
        try:
            if relation_file.exists():
                relation_file.unlink()
                logger.debug(f"删除关系文件: {relation_file}")
                return True
            return False
        except Exception as e:
            logger.error(f"删除关系文件失败 {relation_file}: {e}")
            return False