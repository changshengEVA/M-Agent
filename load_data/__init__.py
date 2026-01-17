"""
数据加载模块
每个文件对应一个数据源，提供 load_data 方法
"""

from .dialog_history_loader import load_dialog_history, load_dialogues

__all__ = ['load_dialog_history', 'load_dialogues']