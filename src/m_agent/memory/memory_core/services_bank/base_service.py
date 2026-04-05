#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Service 基类

所有 Service 必须继承此类，并实现事件监听机制。
"""

import logging
from abc import ABC, abstractmethod
from typing import List

logger = logging.getLogger(__name__)


class BaseService(ABC):
    """
    Service 抽象基类
    
    提供事件监听和处理的基本接口。
    """
    
    @abstractmethod
    def get_subscribed_events(self) -> List[str]:
        """
        返回监听的 EventType 列表
        
        Returns:
            事件类型字符串列表，例如 ["ENTITY_ADDED", "ENTITY_MERGED"]
        """
        return []
    
    @abstractmethod
    def handle_event(self, event_type: str, payload: dict) -> None:
        """
        处理事件
        
        Args:
            event_type: 事件类型字符串
            payload: 事件负载字典
        """
        raise NotImplementedError
    
    def _log_event_handling(self, event_type: str, payload: dict) -> None:
        """
        记录事件处理日志（辅助方法）
        
        Args:
            event_type: 事件类型
            payload: 事件负载
        """
        logger.debug(f"{self.__class__.__name__} 处理事件: {event_type}, payload: {payload}")