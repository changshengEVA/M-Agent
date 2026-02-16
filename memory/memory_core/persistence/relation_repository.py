#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关系仓库模块

负责直接对关系文件进行操作：查询、删除、读取、保存等
文件格式：relation/{relation_id}.json (relation_id就是文件名)
"""

import json
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

# 导入schemas中定义的类型
try:
    from ..schemas.kg_schemas import (
        RelationRecord, validate_relation_data
    )
except ImportError:
    # 用于测试环境
    from memory.memory_core.schemas.kg_schemas import (
        RelationRecord, validate_relation_data
    )

logger = logging.getLogger(__name__)


class RelationRepository:
    """关系仓库类"""
    
    def __init__(self, relation_dir: Path):
        """
        初始化关系仓库
        
        Args:
            relation_dir: 关系文件目录路径
        """
        self.relation_dir = relation_dir
        self.relation_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"初始化关系仓库，目录: {self.relation_dir}")
    
    def _get_relation_file_path(self, relation_id: str) -> Path:
        """
        获取关系文件路径
        
        Args:
            relation_id: 关系ID（文件名）
            
        Returns:
            关系文件路径
        """
        return self.relation_dir / f"{relation_id}.json"
    
    def save(self, relation_record: RelationRecord) -> bool:
        """
        接收一条关系信息json，将这条关系信息进行写入
        
        Args:
            relation_record: 关系记录
            
        Returns:
            保存成功返回True，否则返回False
        """
        try:
            # 验证关系记录格式
            if not validate_relation_data(relation_record):
                logger.warning("关系数据格式验证失败")
                return False
            
            # 检查必要字段
            subject = relation_record.get('subject')
            relation_type = relation_record.get('relation')
            obj = relation_record.get('object')
            
            if not subject or not relation_type or not obj:
                logger.warning("关系数据缺少必要字段: subject, relation, object")
                return False
            
            # 生成唯一的关系ID（如果未提供）
            relation_id = relation_record.get('id')
            if not relation_id:
                relation_id = str(uuid.uuid4())
                relation_record['id'] = relation_id
            
            # 获取关系文件路径
            relation_file = self._get_relation_file_path(relation_id)
            
            # 确保目录存在
            relation_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存关系文件
            with open(relation_file, 'w', encoding='utf-8') as f:
                json.dump(relation_record, f, ensure_ascii=False, indent=2)
            
            logger.info(f"保存关系: {subject} -[{relation_type}]-> {obj} -> {relation_file}")
            return True
            
        except Exception as e:
            logger.error(f"保存关系失败: {e}")
            return False
    
    def delete(self, relation_id: str) -> bool:
        """
        接收relation_id，删除这个关系文件
        
        Args:
            relation_id: 关系ID（文件名）
            
        Returns:
            删除成功返回True，否则返回False
        """
        try:
            relation_file = self._get_relation_file_path(relation_id)
            if relation_file.exists():
                relation_file.unlink()
                logger.info(f"删除关系文件: {relation_file}")
                return True
            logger.warning(f"关系文件不存在，无法删除: {relation_file}")
            return False
        except Exception as e:
            logger.error(f"删除关系文件失败 {relation_id}: {e}")
            return False
    
    def list_all(self) -> List[RelationRecord]:
        """
        读取返回所有的关系信息
        
        Returns:
            所有关系记录的列表
        """
        if not self.relation_dir.exists():
            return []
        
        all_relations = []
        for relation_file in self.relation_dir.glob("*.json"):
            try:
                with open(relation_file, 'r', encoding='utf-8') as f:
                    relation_data = json.load(f)
                
                # 验证数据格式
                if validate_relation_data(relation_data):
                    all_relations.append(relation_data)
                else:
                    logger.warning(f"关系数据格式验证失败: {relation_file}")
            except Exception as e:
                logger.warning(f"读取关系文件失败 {relation_file}: {e}")
                continue
        
        logger.debug(f"加载了 {len(all_relations)} 个关系记录")
        return all_relations
    
    def find_by_subject(self, entity_id: str) -> List[RelationRecord]:
        """
        返回该实体ID所有的出边（实体作为主语的关系）
        
        Args:
            entity_id: 实体ID
            
        Returns:
            该实体作为主语的所有关系记录
        """
        if not self.relation_dir.exists():
            return []
        
        relations = []
        for relation_file in self.relation_dir.glob("*.json"):
            try:
                with open(relation_file, 'r', encoding='utf-8') as f:
                    relation_data = json.load(f)
                
                if relation_data.get('subject') == entity_id and validate_relation_data(relation_data):
                    relations.append(relation_data)
            except Exception as e:
                logger.warning(f"读取关系文件失败 {relation_file}: {e}")
                continue
        
        logger.debug(f"找到实体 {entity_id} 的 {len(relations)} 个出边关系")
        return relations
    
    def find_by_object(self, entity_id: str) -> List[RelationRecord]:
        """
        返回该实体ID所有的入边（实体作为宾语的关系）
        
        Args:
            entity_id: 实体ID
            
        Returns:
            该实体作为宾语的所有关系记录
        """
        if not self.relation_dir.exists():
            return []
        
        relations = []
        for relation_file in self.relation_dir.glob("*.json"):
            try:
                with open(relation_file, 'r', encoding='utf-8') as f:
                    relation_data = json.load(f)
                
                if relation_data.get('object') == entity_id and validate_relation_data(relation_data):
                    relations.append(relation_data)
            except Exception as e:
                logger.warning(f"读取关系文件失败 {relation_file}: {e}")
                continue
        
        logger.debug(f"找到实体 {entity_id} 的 {len(relations)} 个入边关系")
        return relations
    
    def update_endpoint(self, old_entity_id: str, new_entity_id: str) -> Dict[str, Any]:
        """
        将实现重定向，将与old_entity_id相关的关系连接到new_entity_id上，并返回执行结果
        
        Args:
            old_entity_id: 旧的实体ID
            new_entity_id: 新的实体ID
            
        Returns:
            执行结果字典，包含更新的关系数量和详细信息
        """
        if not self.relation_dir.exists():
            return {
                "success": True,
                "message": "关系目录不存在，无需更新",
                "updated_count": 0,
                "deleted_count": 0,
                "updated_relations": [],
                "deleted_relations": []
            }
        
        updated_relations = []
        deleted_relations = []
        
        for relation_file in self.relation_dir.glob("*.json"):
            try:
                with open(relation_file, 'r', encoding='utf-8') as f:
                    relation_data = json.load(f)
                
                subject = relation_data.get('subject')
                obj = relation_data.get('object')
                relation_type = relation_data.get('relation')
                relation_id = relation_file.stem
                
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
                        # 检查是否会产生自引用关系（A->A）
                        if relation_data['subject'] == relation_data['object']:
                            # 删除自引用的关系
                            relation_file.unlink()
                            deleted_relations.append({
                                "relation_id": relation_id,
                                "relation_type": relation_type,
                                "reason": "自引用关系"
                            })
                            continue
                        
                        # 检查是否已存在相同的关系（避免重复）
                        relation_exists = False
                        for other_file in self.relation_dir.glob("*.json"):
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
                                        # 简单的合并逻辑
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
                                    deleted_relations.append({
                                        "relation_id": relation_id,
                                        "relation_type": relation_type,
                                        "reason": "合并到重复关系"
                                    })
                                    break
                            
                            except Exception:
                                continue
                        
                        if not relation_exists:
                            # 保存更新后的关系
                            with open(relation_file, 'w', encoding='utf-8') as f:
                                json.dump(relation_data, f, ensure_ascii=False, indent=2)
                            
                            updated_relations.append({
                                "relation_id": relation_id,
                                "relation_type": relation_type,
                                "old_subject": subject,
                                "old_object": obj,
                                "new_subject": relation_data.get('subject'),
                                "new_object": relation_data.get('object')
                            })
            
            except Exception as e:
                logger.warning(f"处理关系文件 {relation_file} 时出错: {e}")
                continue
        
        result = {
            "success": True,
            "message": f"成功更新实体端点: {old_entity_id} -> {new_entity_id}",
            "updated_count": len(updated_relations),
            "deleted_count": len(deleted_relations),
            "updated_relations": updated_relations,
            "deleted_relations": deleted_relations
        }
        
        logger.info(f"实体端点更新完成: {result}")
        return result
    
    def find_by_entities(
        self,
        entity1_id: str,
        entity2_id: str
    ) -> List[RelationRecord]:
        """
        查找两个实体之间的所有关系
        
        返回所有连接 entity1_id 和 entity2_id 的关系（双向），
        即 subject=entity1_id 且 object=entity2_id，或者
        subject=entity2_id 且 object=entity1_id。
        
        Args:
            entity1_id: 第一个实体ID
            entity2_id: 第二个实体ID
            
        Returns:
            两个实体之间的所有关系记录列表
        """
        if not self.relation_dir.exists():
            return []
        
        relations = []
        for relation_file in self.relation_dir.glob("*.json"):
            try:
                with open(relation_file, 'r', encoding='utf-8') as f:
                    relation_data = json.load(f)
                
                subject = relation_data.get('subject')
                obj = relation_data.get('object')
                
                # 检查是否匹配两个实体（不考虑方向）
                if ((subject == entity1_id and obj == entity2_id) or
                    (subject == entity2_id and obj == entity1_id)):
                    if validate_relation_data(relation_data):
                        relations.append(relation_data)
            except Exception as e:
                logger.warning(f"读取关系文件失败 {relation_file}: {e}")
                continue
        
        logger.debug(f"找到实体 {entity1_id} 和 {entity2_id} 之间的 {len(relations)} 个关系")
        return relations
    
    def delete_by_entities(
        self,
        entity1_id: str,
        entity2_id: str
    ) -> Dict[str, Any]:
        """
        删除两个实体之间的所有关系
        
        删除所有连接 entity1_id 和 entity2_id 的关系（双向）。
        
        Args:
            entity1_id: 第一个实体ID
            entity2_id: 第二个实体ID
            
        Returns:
            执行结果字典，包含删除的关系数量和详细信息
        """
        if not self.relation_dir.exists():
            return {
                "success": True,
                "message": "关系目录不存在，无需删除",
                "deleted_count": 0,
                "deleted_relations": []
            }
        
        deleted_relations = []
        failed_deletions = []
        
        for relation_file in self.relation_dir.glob("*.json"):
            try:
                with open(relation_file, 'r', encoding='utf-8') as f:
                    relation_data = json.load(f)
                
                subject = relation_data.get('subject')
                obj = relation_data.get('object')
                relation_id = relation_file.stem
                
                # 检查是否匹配两个实体（不考虑方向）
                if ((subject == entity1_id and obj == entity2_id) or
                    (subject == entity2_id and obj == entity1_id)):
                    # 删除关系文件
                    relation_file.unlink()
                    deleted_relations.append({
                        "relation_id": relation_id,
                        "subject": subject,
                        "relation": relation_data.get('relation'),
                        "object": obj
                    })
            except Exception as e:
                logger.warning(f"处理关系文件 {relation_file} 时出错: {e}")
                failed_deletions.append(relation_file.stem)
                continue
        
        result = {
            "success": True,
            "message": f"成功删除实体 {entity1_id} 和 {entity2_id} 之间的所有关系",
            "deleted_count": len(deleted_relations),
            "failed_count": len(failed_deletions),
            "deleted_relations": deleted_relations,
            "failed_relations": failed_deletions
        }
        
        logger.info(f"删除实体间关系完成: {result}")
        return result