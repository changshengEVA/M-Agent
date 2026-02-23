#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关系仓库模块

负责直接对关系文件进行操作：查询、删除、读取、保存等。

当前存储协议（新）：
- 文件名：由 subject/object 对应实体的 UID 组合得到的 bucket_id
- 文件内容：{"relation": [RelationRecord, ...]}

向后兼容（旧）：
- 文件名：relation_id
- 文件内容：单条 RelationRecord
"""

import copy
import hashlib
import json
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

# 导入schemas中定义的类型
try:
    from ..schemas.kg_schemas import (
        RelationRecord, validate_relation_data
    )
    from .entity_repository import EntityRepository
except ImportError:
    # 用于测试环境
    from memory.memory_core.schemas.kg_schemas import (
        RelationRecord, validate_relation_data
    )
    from memory.memory_core.persistence.entity_repository import EntityRepository

logger = logging.getLogger(__name__)


class RelationRepository:
    """关系仓库类"""
    
    def __init__(self, relation_dir: Path, entity_repository: Optional[EntityRepository] = None):
        """
        初始化关系仓库
        
        Args:
            relation_dir: 关系文件目录路径
            entity_repository: 实体仓库，用于将 entity_id 映射到 uid
        """
        self.relation_dir = relation_dir
        self.entity_repository = entity_repository
        self.relation_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"初始化关系仓库，目录: {self.relation_dir}")
        self._migrate_legacy_storage_if_needed()
    
    def _get_relation_file_path(self, relation_id: str) -> Path:
        """
        获取关系文件路径
        
        Args:
            relation_id: 关系ID（文件名）
            
        Returns:
            关系文件路径
        """
        return self.relation_dir / f"{relation_id}.json"

    def _sanitize_entity_name(self, entity_id: str) -> str:
        """清理实体名称，使其适合作为文件名。"""
        sanitized = entity_id.strip()
        sanitized = sanitized.replace(' ', '_')
        for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
            sanitized = sanitized.replace(char, '_')
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        return sanitized

    def _resolve_entity_uid(self, entity_id: str) -> str:
        """
        将实体ID映射为UID；若无法获取UID则回退为实体ID本身。
        """
        if self.entity_repository is None:
            return entity_id

        try:
            success, entity_data = self.entity_repository.load(entity_id)
            if success and isinstance(entity_data, dict):
                uid = entity_data.get("uid")
                if isinstance(uid, str) and uid.strip():
                    return uid.strip()
        except Exception as e:
            logger.debug(f"解析实体UID失败，回退entity_id: {entity_id}, err={e}")

        return entity_id

    def _build_bucket_id(self, subject: str, obj: str) -> str:
        """
        构建实体对分桶 ID（基于UID，无向，按字典序稳定排序）。

        说明：
        - 使用无向分桶，A-B 与 B-A 写入同一个文件，便于聚合。
        - 仍然保留关系记录内部的 subject/object 方向信息。
        """
        left_uid = self._resolve_entity_uid(subject)
        right_uid = self._resolve_entity_uid(obj)
        left = self._sanitize_entity_name(left_uid)
        right = self._sanitize_entity_name(right_uid)
        pair = sorted([left, right])
        bucket_id = f"{pair[0]}__{pair[1]}"
        if len(bucket_id) <= 180:
            return bucket_id
        digest = hashlib.sha1(bucket_id.encode('utf-8')).hexdigest()[:12]
        return f"{pair[0][:80]}__{pair[1][:80]}__{digest}"

    def _bucket_file_path(self, subject: str, obj: str) -> Path:
        """根据 subject/object 获取分桶文件路径。"""
        return self._get_relation_file_path(self._build_bucket_id(subject, obj))

    def _load_relations_from_file(self, relation_file: Path) -> Tuple[List[RelationRecord], str]:
        """
        读取文件中的关系列表。

        Returns:
            (relations, mode)
            - mode='bucket': 新协议 {"relation": [...]}
            - mode='legacy': 旧协议 单条 RelationRecord
            - mode='invalid': 无法识别
        """
        try:
            with open(relation_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"读取关系文件失败 {relation_file}: {e}")
            return [], "invalid"

        if isinstance(data, dict) and isinstance(data.get("relation"), list):
            relations: List[RelationRecord] = []
            for idx, item in enumerate(data["relation"]):
                if isinstance(item, dict) and validate_relation_data(item):
                    rel = copy.deepcopy(item)
                    if not rel.get("id"):
                        rel["id"] = f"{relation_file.stem}__{idx}"
                    relations.append(rel)
                else:
                    logger.warning(f"关系分桶文件中存在无效记录，已跳过: {relation_file}")
            return relations, "bucket"

        if isinstance(data, dict) and validate_relation_data(data):
            rel = copy.deepcopy(data)
            if not rel.get("id"):
                rel["id"] = relation_file.stem
            return [rel], "legacy"

        logger.warning(f"关系数据格式验证失败: {relation_file}")
        return [], "invalid"

    def _write_bucket_file(self, relation_file: Path, relations: List[RelationRecord]) -> bool:
        """将关系列表写为新协议分桶文件。"""
        try:
            relation_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"relation": relations}
            with open(relation_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"写入关系分桶文件失败 {relation_file}: {e}")
            return False

    def _rewrite_all_records(self, records: List[RelationRecord]) -> bool:
        """基于全量 records 重写 relation 目录为新协议。"""
        try:
            if not self.relation_dir.exists():
                self.relation_dir.mkdir(parents=True, exist_ok=True)

            # 清空现有文件（旧协议+新协议统一重建）
            for relation_file in self.relation_dir.glob("*.json"):
                relation_file.unlink()

            # 按分桶写回
            buckets: Dict[str, List[RelationRecord]] = {}
            for rel in records:
                subject = rel.get("subject")
                obj = rel.get("object")
                if not subject or not obj:
                    continue
                bucket_id = self._build_bucket_id(subject, obj)
                buckets.setdefault(bucket_id, []).append(rel)

            for bucket_id, bucket_records in buckets.items():
                bucket_file = self._get_relation_file_path(bucket_id)
                if not self._write_bucket_file(bucket_file, bucket_records):
                    return False

            return True
        except Exception as e:
            logger.error(f"重写关系目录失败: {e}")
            return False

    def _migrate_legacy_storage_if_needed(self) -> None:
        """如果检测到旧协议或文件名与当前分桶规则不一致，则自动迁移。"""
        try:
            if not self.relation_dir.exists():
                return

            need_rewrite = False
            for relation_file in self.relation_dir.glob("*.json"):
                relations, mode = self._load_relations_from_file(relation_file)
                if mode == "legacy":
                    need_rewrite = True
                    break
                if mode == "bucket":
                    for rel in relations:
                        subject = rel.get("subject")
                        obj = rel.get("object")
                        if not subject or not obj:
                            continue
                        expected_bucket_id = self._build_bucket_id(subject, obj)
                        if relation_file.stem != expected_bucket_id:
                            need_rewrite = True
                            break
                    if need_rewrite:
                        break

            if not need_rewrite:
                return

            logger.info("检测到关系存储格式需要重写，开始迁移到当前分桶格式（UID）")
            all_relations = self.list_all()
            if self._rewrite_all_records(all_relations):
                logger.info(f"关系存储重写完成，共处理 {len(all_relations)} 条关系")
            else:
                logger.warning("关系存储重写失败，将继续使用兼容读取模式")
        except Exception as e:
            logger.warning(f"关系存储迁移检查失败: {e}")
    
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
            
            # 获取分桶文件路径（按实体对存储）
            relation_file = self._bucket_file_path(subject, obj)

            # 加载已有分桶关系
            bucket_relations: List[RelationRecord] = []
            if relation_file.exists():
                loaded, mode = self._load_relations_from_file(relation_file)
                if mode in ("bucket", "legacy"):
                    bucket_relations = loaded

            # 同ID覆盖，否则追加
            replaced = False
            for idx, item in enumerate(bucket_relations):
                if item.get("id") == relation_id:
                    bucket_relations[idx] = relation_record
                    replaced = True
                    break
            if not replaced:
                bucket_relations.append(relation_record)

            if not self._write_bucket_file(relation_file, bucket_relations):
                return False

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
            if not self.relation_dir.exists():
                return False

            for relation_file in self.relation_dir.glob("*.json"):
                relations, mode = self._load_relations_from_file(relation_file)
                if mode == "invalid":
                    continue

                keep_relations = [rel for rel in relations if rel.get("id") != relation_id]
                if len(keep_relations) == len(relations):
                    continue

                # 命中删除
                if mode == "legacy":
                    relation_file.unlink()
                    logger.info(f"删除旧协议关系文件: {relation_file}")
                    return True

                # 新协议：删到空则删文件，否则回写
                if not keep_relations:
                    relation_file.unlink()
                else:
                    if not self._write_bucket_file(relation_file, keep_relations):
                        return False
                logger.info(f"删除关系ID成功: {relation_id}")
                return True

            logger.warning(f"关系ID不存在，无法删除: {relation_id}")
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
        
        all_relations: List[RelationRecord] = []
        for relation_file in self.relation_dir.glob("*.json"):
            relations, mode = self._load_relations_from_file(relation_file)
            if mode == "invalid":
                continue
            all_relations.extend(relations)
        
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
        
        relations = [rel for rel in self.list_all() if rel.get('subject') == entity_id]
        
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
        
        relations = [rel for rel in self.list_all() if rel.get('object') == entity_id]
        
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

        all_relations = self.list_all()
        transformed: List[RelationRecord] = []

        for relation_data in all_relations:
            subject = relation_data.get('subject')
            obj = relation_data.get('object')
            relation_type = relation_data.get('relation')
            relation_id = relation_data.get('id')

            if not subject or not obj or not relation_type:
                continue

            original_subject = subject
            original_object = obj

            if subject == old_entity_id:
                relation_data['subject'] = new_entity_id
            if obj == old_entity_id:
                relation_data['object'] = new_entity_id

            # 自引用关系删除
            if relation_data.get('subject') == relation_data.get('object'):
                deleted_relations.append({
                    "relation_id": relation_id,
                    "relation_type": relation_type,
                    "reason": "自引用关系"
                })
                continue

            if original_subject != relation_data.get('subject') or original_object != relation_data.get('object'):
                updated_relations.append({
                    "relation_id": relation_id,
                    "relation_type": relation_type,
                    "old_subject": original_subject,
                    "old_object": original_object,
                    "new_subject": relation_data.get('subject'),
                    "new_object": relation_data.get('object')
                })

            transformed.append(relation_data)

        # 去重（subject, relation, object），并合并来源与置信度
        dedup_map: Dict[Tuple[str, str, str], RelationRecord] = {}
        for rel in transformed:
            key = (rel.get('subject', ''), rel.get('relation', ''), rel.get('object', ''))
            if key not in dedup_map:
                dedup_map[key] = rel
                continue

            existing = dedup_map[key]
            # 合并来源
            existing_sources = existing.get('sources', []) or []
            new_sources = rel.get('sources', []) or []
            for source in new_sources:
                if source not in existing_sources:
                    existing_sources.append(source)
            if existing_sources:
                existing['sources'] = existing_sources

            # 置信度取高
            old_conf = existing.get('confidence', 0)
            new_conf = rel.get('confidence', 0)
            if isinstance(old_conf, (int, float)) and isinstance(new_conf, (int, float)) and new_conf > old_conf:
                existing['confidence'] = new_conf

            deleted_relations.append({
                "relation_id": rel.get("id"),
                "relation_type": rel.get("relation"),
                "reason": "合并到重复关系"
            })

        final_relations = list(dedup_map.values())
        if not self._rewrite_all_records(final_relations):
            return {
                "success": False,
                "message": "重写关系文件失败",
                "updated_count": 0,
                "deleted_count": 0,
                "updated_relations": [],
                "deleted_relations": []
            }
        
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
        for relation_data in self.list_all():
            subject = relation_data.get('subject')
            obj = relation_data.get('object')
            if ((subject == entity1_id and obj == entity2_id) or
                (subject == entity2_id and obj == entity1_id)):
                relations.append(relation_data)
        
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

        all_relations = self.list_all()
        keep_relations: List[RelationRecord] = []

        for relation_data in all_relations:
            subject = relation_data.get('subject')
            obj = relation_data.get('object')
            relation_id = relation_data.get('id')

            if ((subject == entity1_id and obj == entity2_id) or
                (subject == entity2_id and obj == entity1_id)):
                deleted_relations.append({
                    "relation_id": relation_id,
                    "subject": subject,
                    "relation": relation_data.get('relation'),
                    "object": obj
                })
            else:
                keep_relations.append(relation_data)

        try:
            if not self._rewrite_all_records(keep_relations):
                failed_deletions = [item.get("relation_id") for item in deleted_relations if item.get("relation_id")]
                deleted_relations = []
        except Exception:
            failed_deletions = [item.get("relation_id") for item in deleted_relations if item.get("relation_id")]
            deleted_relations = []
        
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
