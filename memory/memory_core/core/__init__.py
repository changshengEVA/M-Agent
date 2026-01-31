#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core 模块 - 知识图谱核心算子

提供对知识图谱进行整体内容或结构上改变的稳定操作。
包括实体结构级算子、关系结构级算子、实体内容级算子和结构维护级算子。
"""

from .repo_context import RepoContext
from .entity_ops import (
    merge_entities,
    delete_entity_and_edges,
    rename_entity,
    add_entity
)
from .relation_ops import (
    redirect_relations,
    delete_relations_of_entity
)
from .content_ops import (
    append_feature,
    append_attribute,
    move_features,
    move_attributes
)
from .integrity_ops import (
    remove_dangling_relations,
    assert_entity_exists
)
from .kg_base import KGBase

__all__ = [
    'RepoContext',
    'merge_entities',
    'delete_entity_and_edges',
    'rename_entity',
    'add_entity',
    'redirect_relations',
    'delete_relations_of_entity',
    'append_feature',
    'append_attribute',
    'move_features',
    'move_attributes',
    'remove_dangling_relations',
    'assert_entity_exists',
    'KGBase'
]