#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
事件总线（EventBus）机制

负责接收 KGBase 发布的事件，并分发给已注册的 Service。
"""

import logging
from collections import defaultdict
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class EventBus:
    """
    事件总线类
    
    维护事件类型到 Service 列表的映射，支持 Service 注册和事件发布。
    """
    
    def __init__(self):
        """
        初始化 EventBus
        
        创建订阅表：event_type -> [service_list]
        """
        self._subscribers = defaultdict(list)
        logger.debug("EventBus 初始化完成")
    
    def register(self, service) -> None:
        """
        注册 Service
        
        读取 Service 声明的监听事件，建立 event → service 映射。
        
        Args:
            service: 实现了 get_subscribed_events() 和 handle_event() 的 Service 实例
        """
        try:
            events = service.get_subscribed_events()
            if not events:
                logger.debug(f"Service {service.__class__.__name__} 未声明监听事件")
                return
            
            for event_type in events:
                self._subscribers[event_type].append(service)
                logger.debug(f"Service {service.__class__.__name__} 订阅事件: {event_type}")
            
            logger.info(f"Service {service.__class__.__name__} 注册成功，监听 {len(events)} 种事件")
        except Exception as e:
            logger.error(f"注册 Service {service.__class__.__name__} 时出错: {e}")
    
    def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        """
        发布事件
        
        查找所有订阅该事件的 Service，依次调用其 handle_event() 方法。
        
        Args:
            event_type: 事件类型字符串
            payload: 事件负载字典
        """
        subscribers = self._subscribers.get(event_type, [])
        if not subscribers:
            logger.debug(f"事件 {event_type} 无订阅者")
            return
        
        logger.info(f"发布事件 {event_type}，订阅者数量: {len(subscribers)}")
        
        for service in subscribers:
            try:
                service.handle_event(event_type, payload)
            except Exception as e:
                logger.error(f"Service {service.__class__.__name__} 处理事件 {event_type} 时出错: {e}")
                # 单个 Service 出错不能影响其他 Service，继续执行
    
    def get_subscriber_count(self, event_type: str) -> int:
        """
        获取指定事件的订阅者数量
        
        Args:
            event_type: 事件类型
            
        Returns:
            订阅者数量
        """
        return len(self._subscribers.get(event_type, []))
    
    def get_all_subscribed_events(self) -> List[str]:
        """
        获取所有已注册的事件类型
        
        Returns:
            事件类型列表
        """
        return list(self._subscribers.keys())