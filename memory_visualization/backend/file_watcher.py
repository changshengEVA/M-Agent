#!/usr/bin/env python3
"""
文件监控器
监控data/memory目录的变化，实现实时更新
"""

import os
import time
import logging
import threading
from pathlib import Path
from typing import Callable, Optional, List
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger(__name__)

class MemoryFileHandler(FileSystemEventHandler):
    """处理文件系统事件"""
    
    def __init__(self, on_change_callback: Callable):
        self.on_change_callback = on_change_callback
        self.last_event_time = 0
        self.event_cooldown = 1.0  # 事件冷却时间（秒）
    
    def on_modified(self, event: FileSystemEvent):
        """文件修改事件"""
        if not event.is_directory:
            current_time = time.time()
            if current_time - self.last_event_time > self.event_cooldown:
                self.last_event_time = current_time
                logger.debug(f"文件修改: {event.src_path}")
                self.on_change_callback("modified", event.src_path)
    
    def on_created(self, event: FileSystemEvent):
        """文件创建事件"""
        if not event.is_directory:
            logger.debug(f"文件创建: {event.src_path}")
            self.on_change_callback("created", event.src_path)
    
    def on_deleted(self, event: FileSystemEvent):
        """文件删除事件"""
        if not event.is_directory:
            logger.debug(f"文件删除: {event.src_path}")
            self.on_change_callback("deleted", event.src_path)

class MemoryFileWatcher:
    """Memory文件监控器"""
    
    def __init__(self, data_dir: str, on_change_callback: Callable):
        """
        初始化文件监控器
        
        Args:
            data_dir: 要监控的数据目录
            on_change_callback: 文件变化时的回调函数，接收两个参数：
                                change_type: "modified", "created", "deleted"
                                file_path: 变化的文件路径
        """
        self.data_dir = Path(data_dir)
        self.on_change_callback = on_change_callback
        self.observer: Optional[Observer] = None
        self.event_handler: Optional[MemoryFileHandler] = None
        self.is_running = False
        self.watch_thread: Optional[threading.Thread] = None
        
        logger.info(f"初始化文件监控器，监控目录: {self.data_dir}")
    
    def start(self) -> bool:
        """启动文件监控"""
        try:
            if not self.data_dir.exists():
                logger.error(f"监控目录不存在: {self.data_dir}")
                return False
            
            # 创建观察者和事件处理器
            self.observer = Observer()
            self.event_handler = MemoryFileHandler(self.on_change_callback)
            
            # 递归监控整个目录
            self.observer.schedule(self.event_handler, str(self.data_dir), recursive=True)
            
            # 启动观察者
            self.observer.start()
            self.is_running = True
            
            logger.info(f"✅ 文件监控已启动，监控目录: {self.data_dir}")
            logger.info(f"监控器运行状态: {self.is_running}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 启动文件监控失败: {e}")
            self.is_running = False
            return False
    
    def stop(self):
        """停止文件监控"""
        if self.observer and self.is_running:
            try:
                self.observer.stop()
                self.observer.join()
                self.is_running = False
                logger.info("文件监控已停止")
            except Exception as e:
                logger.error(f"停止文件监控失败: {e}")
    
    def get_running_status(self) -> bool:
        """检查监控器是否正在运行"""
        return self.is_running
    
    def get_monitored_directories(self) -> List[str]:
        """获取正在监控的目录列表"""
        if self.observer:
            return [str(self.data_dir)]
        return []