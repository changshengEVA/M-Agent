# Services Bank - 服务开发指南

## 概述

Services Bank 是 Memory Core 系统的服务层，采用事件驱动架构设计。所有服务都通过 EventBus 进行通信，实现松耦合、高内聚的系统设计。

## 架构设计

### 核心组件

1. **BaseService** (`base_service.py`) - 服务抽象基类
2. **EventBus** (`system/event_bus.py`) - 事件总线
3. **EventType** (`system/event_types.py`) - 事件类型定义
4. **MemoryCore** (`memory_system.py`) - 系统主管理类

### 事件驱动流程

```
KGBase (事件发布者)
    │
    ▼ 发布事件 (ENTITY_ADDED, ENTITY_MERGED, ...)
EventBus (事件总线)
    │
    ▼ 分发事件
BaseService (事件订阅者)
    │
    ▼ 处理事件
业务逻辑执行
```

## 开发新 Service 的步骤

### 1. 创建 Service 类

新 Service 必须继承 `BaseService` 抽象类：

```python
from memory.memory_core.services_bank.base_service import BaseService
from memory.memory_core.system.event_types import EventType
from typing import List

class MyNewService(BaseService):
    """我的新服务"""
    
    def __init__(self, **kwargs):
        # 初始化服务
        super().__init__()
        # 其他初始化代码
        pass
    
    def get_subscribed_events(self) -> List[str]:
        """返回监听的 EventType 列表"""
        return [
            EventType.ENTITY_ADDED,
            EventType.ENTITY_MERGED,
            # 添加其他感兴趣的事件类型
        ]
    
    def handle_event(self, event_type: str, payload: dict) -> None:
        """处理事件"""
        self._log_event_handling(event_type, payload)
        
        if event_type == EventType.ENTITY_ADDED:
            entity_id = payload.get("entity_id")
            self.on_entity_added(entity_id)
        elif event_type == EventType.ENTITY_MERGED:
            source_id = payload.get("source_id")
            target_id = payload.get("target_id")
            self.on_entity_merged(source_id, target_id)
        # 处理其他事件类型
    
    def on_entity_added(self, entity_id: str) -> None:
        """处理实体添加事件"""
        # 实现业务逻辑
        pass
    
    def on_entity_merged(self, source_id: str, target_id: str) -> None:
        """处理实体合并事件"""
        # 实现业务逻辑
        pass
```

### 2. 可用的事件类型

所有事件类型定义在 `EventType` 枚举中：

#### 实体类事件
- `ENTITY_ADDED` - 实体添加事件
  - Payload: `{"entity_id": "实体ID"}`
- `ENTITY_DELETED` - 实体删除事件
  - Payload: `{"entity_id": "实体ID"}`
- `ENTITY_MERGED` - 实体合并事件
  - Payload: `{"source_id": "源实体ID", "target_id": "目标实体ID"}`
- `ENTITY_RENAMED` - 实体重命名事件
  - Payload: `{"old_id": "旧实体ID", "new_id": "新实体ID"}`
- `ENTITY_UPDATED` - 实体更新事件（特征/属性变更）
  - Payload: `{"entity_id": "实体ID", "changes": "变更详情"}`

#### 关系类事件
- `RELATION_ADDED` - 关系添加事件
  - Payload: `{"relation_id": "关系ID", "source_id": "源实体ID", "target_id": "目标实体ID"}`
- `RELATION_DELETED` - 关系删除事件
  - Payload: `{"relation_id": "关系ID"}`
- `RELATIONS_REDIRECTED` - 关系重定向事件
  - Payload: `{"old_entity_id": "旧实体ID", "new_entity_id": "新实体ID", "updated_count": "更新数量"}`

#### 系统级事件
- `SYSTEM_INITIALIZED` - 系统初始化完成事件
  - Payload: `{}`

### 3. 注册 Service 到系统

Service 需要在 `MemoryCore` 中注册才能接收事件：

```python
# 创建服务实例
my_service = MyNewService(...)

# 注册服务到 MemoryCore
memory_core.register_service(my_service)
```

或者通过便捷函数创建默认服务：

```python
def create_default_my_service(**kwargs) -> MyNewService:
    """创建默认的 MyNewService 实例"""
    return MyNewService(**kwargs)
```

### 4. 服务目录结构

建议按以下结构组织服务代码：

```
services_bank/
├── README.md                    # 本文档
├── base_service.py              # 服务基类
├── __init__.py                  # 包导出
└── {service_name}/              # 具体服务目录
    ├── __init__.py              # 服务模块导出
    ├── service.py               # 主服务类
    ├── models.py                # 数据模型（可选）
    ├── strategies.py            # 策略类（可选）
    ├── utils.py                 # 工具函数（可选）
    └── tests/                   # 测试目录（可选）
        └── test_service.py
```

## 示例：EntityResolutionService

参考 `entity_resolution/service.py` 作为完整示例：

### 关键实现点

1. **继承 BaseService**：
   ```python
   class EntityResolutionService(BaseService):
   ```

2. **定义订阅的事件**：
   ```python
   def get_subscribed_events(self):
       return [
           EventType.ENTITY_MERGED,
           EventType.ENTITY_ADDED,
           EventType.ENTITY_RENAMED,
       ]
   ```

3. **实现事件处理**：
   ```python
   def handle_event(self, event_type: str, payload: dict):
       if event_type == EventType.ENTITY_MERGED:
           source_id = payload.get("source_id")
           target_id = payload.get("target_id")
           self.on_entity_merged(source_id, target_id)
       # ... 其他事件处理
   ```

4. **实现具体业务方法**：
   ```python
   def on_entity_merged(self, source_id: str, target_id: str):
       # 更新 EntityLibrary
       # 处理合并逻辑
       pass
   ```

## 最佳实践

### 1. 错误处理

在事件处理方法中添加适当的错误处理：

```python
def handle_event(self, event_type: str, payload: dict):
    try:
        # 事件处理逻辑
        pass
    except Exception as e:
        logger.error(f"处理事件 {event_type} 时出错: {e}")
        # 可以选择重新抛出或记录错误
```

### 2. 日志记录

使用基类提供的 `_log_event_handling` 方法：

```python
def handle_event(self, event_type: str, payload: dict):
    self._log_event_handling(event_type, payload)
    # 处理逻辑
```

### 3. 性能考虑

- 避免在事件处理方法中执行耗时操作
- 考虑使用异步处理或队列处理大量事件
- 确保事件处理是幂等的（可重复执行）

### 4. 测试

为服务编写单元测试：

```python
def test_my_service_event_handling():
    # 创建服务实例
    service = MyNewService()
    
    # 模拟事件
    test_payload = {"entity_id": "test_entity"}
    service.handle_event(EventType.ENTITY_ADDED, test_payload)
    
    # 验证处理结果
    assert service.some_state == expected_value
```

## 向后兼容性

### 传统服务支持

系统支持两种服务注册方式：

1. **事件驱动服务**（推荐）：继承 `BaseService`，通过 `EventBus` 接收事件
2. **传统服务**：实现 `on_{event_name}` 方法，通过 `_notify_services` 接收通知

### 迁移指南

如果已有传统服务，可以按以下步骤迁移：

1. 让服务类继承 `BaseService`
2. 实现 `get_subscribed_events()` 方法
3. 实现 `handle_event()` 方法，将事件路由到现有的 `on_{event_name}` 方法
4. 更新 `MemoryCore.register_service()` 调用

## 常见问题

### Q1: 服务如何访问 KGBase？

A: 服务不应直接访问 KGBase。如果需要操作知识图谱，应该：
- 通过事件响应 KGBase 的操作
- 通过 MemoryCore 提供的接口进行操作
- 保持服务的独立性

### Q2: 多个服务监听同一事件怎么办？

A: EventBus 支持多订阅者模式，所有订阅了同一事件的服务都会收到通知。执行顺序不确定，服务之间不应有依赖关系。

### Q3: 如何添加新的事件类型？

A: 在 `system/event_types.py` 的 `EventType` 枚举中添加新的事件类型，并确保 KGBase 在相应操作中发布该事件。

### Q4: 服务如何持久化数据？

A: 服务可以有自己的数据存储机制（如文件、数据库），但应遵循项目的数据存储约定（如使用 `data/memory/{workflow_id}/` 目录）。

## 扩展阅读

- [EventBus 设计文档](../system/event_bus.py)
- [EventType 定义](../system/event_types.py)
- [MemoryCore 集成指南](../memory_system.py)
- [EntityResolutionService 示例](./entity_resolution/service.py)

---

*最后更新: 2026-02-19*