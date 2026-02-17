#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
迁移脚本：为现有实体添加UID字段

此脚本扫描实体目录，为所有没有UID的实体添加唯一的UID。
"""

import sys
import json
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memory.memory_core.persistence.entity_repository import EntityRepository

def migrate_entity_uids(entity_dir: Path, dry_run: bool = True):
    """
    迁移实体UID
    
    Args:
        entity_dir: 实体目录路径
        dry_run: 如果为True，只显示将要进行的更改，不实际修改文件
    """
    repo = EntityRepository(entity_dir)
    entity_ids = repo.list_ids()
    
    print(f"找到 {len(entity_ids)} 个实体")
    
    migrated = 0
    errors = 0
    
    for entity_id in entity_ids:
        success, entity_data = repo.load(entity_id)
        if not success:
            print(f"  错误: 无法加载实体 {entity_id}")
            errors += 1
            continue
        
        # 检查是否已有UID
        if 'uid' in entity_data:
            print(f"  实体 {entity_id}: 已有UID ({entity_data['uid']})")
            continue
        
        # 生成UID
        new_uid = str(uuid.uuid4())
        entity_data['uid'] = new_uid
        
        print(f"  实体 {entity_id}: 添加UID {new_uid}")
        
        if not dry_run:
            # 保存更新后的实体
            if repo.save(entity_data):
                migrated += 1
            else:
                print(f"  错误: 保存实体 {entity_id} 失败")
                errors += 1
    
    print(f"\n迁移统计:")
    print(f"  总实体数: {len(entity_ids)}")
    print(f"  已迁移: {migrated}")
    print(f"  错误: {errors}")
    
    if dry_run:
        print(f"\n注意: 这是模拟运行 (dry_run=True)。要实际应用更改，请使用 --apply 参数。")
    
    return migrated, errors

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='为实体添加UID字段')
    parser.add_argument('--entity-dir', type=str, default='data/memory/testrt/entities',
                       help='实体目录路径 (默认: data/memory/testrt/entities)')
    parser.add_argument('--apply', action='store_true',
                       help='实际应用更改 (默认是模拟运行)')
    
    args = parser.parse_args()
    
    entity_dir = Path(args.entity_dir)
    if not entity_dir.exists():
        print(f"错误: 实体目录不存在: {entity_dir}")
        sys.exit(1)
    
    print(f"实体目录: {entity_dir}")
    print(f"模式: {'实际迁移' if args.apply else '模拟运行'}")
    print("=" * 60)
    
    migrated, errors = migrate_entity_uids(entity_dir, dry_run=not args.apply)
    
    if errors > 0:
        sys.exit(1)

if __name__ == '__main__':
    main()