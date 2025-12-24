"""
自然语言文本到知识图谱的转换模块
基于项目中的Neo4j配置和OpenAI方法实现
"""

import os
import sys
import json
import yaml
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 确保项目根目录和KG_data目录在Python路径中
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)  # f:/AI/M-Agent

# 添加项目根目录到sys.path（如果不在其中）
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 添加KG_data目录到sys.path（如果不在其中）
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# 导入项目中的现有模块
try:
    # 首先尝试相对导入（当作为包的一部分时）
    from .create_person_node import PersonNodeCreator
except ImportError:
    # 如果相对导入失败，尝试绝对导入（当直接运行时）
    from create_person_node import PersonNodeCreator

from load_model.OpenAIcall import get_llm
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class NLPToKGConverter:
    """
    将自然语言文本转换为知识图谱信息的转换器
    
    功能：
    1. 使用LLM解析文本，提取实体和关系
    2. 将提取的信息转换为Neo4j数据结构
    3. 创建相应的节点和关系
    """
    
    def __init__(self, 
                 neo4j_config_path: str = "./config/neo4j.yaml",
                 llm_temperature: float = 0.1,
                 use_openai: bool = True):
        """
        初始化转换器
        
        Args:
            neo4j_config_path: Neo4j配置文件路径
            llm_temperature: LLM温度参数
            use_openai: 是否使用OpenAI（True）或本地模型（False）
        """
        # 加载Neo4j配置
        self.neo4j_config = self._load_neo4j_config(neo4j_config_path)
        
        # 初始化Neo4j连接
        self.person_creator = PersonNodeCreator(
            uri=self.neo4j_config.get("url"),
            username=self.neo4j_config.get("user_name"),
            password=self.neo4j_config.get("password")
        )
        
        # 初始化LLM
        self.llm = self._init_llm(use_openai, llm_temperature)
        
        # 实体提取提示词模板
        self.entity_extraction_prompt = """请从以下文本中提取人物实体信息，并以JSON格式返回。
        文本：{text}
        
        请提取以下信息：
        1. 人物姓名（name）
        2. 出生日期（birth_date，格式：YYYY-MM-DD，如果无法确定具体日期，请使用"未知"）
        3. 性别（gender，可选值：'男'、'女'、'其他'）
        4. 国籍（nationality）
        5. 人物简介（biography，总结文本中关于该人物的描述）
        6. 其他属性（metadata，如职业、成就等）
        
        如果文本中包含多个人物，请为每个人物创建一个独立的JSON对象。
        
        返回格式示例：
        [
            {{
                "name": "张三",
                "birth_date": "1990-01-01",
                "gender": "男",
                "nationality": "中国",
                "biography": "张三是一位软件工程师...",
                "metadata": {{
                    "occupation": "软件工程师",
                    "achievements": ["开发了XX系统"]
                }}
            }}
        ]
        
        请只返回JSON格式，不要有其他文本。"""
        
        # 关系提取提示词模板
        self.relation_extraction_prompt = """请从以下文本中提取人物之间的关系，并以JSON格式返回。
        文本：{text}
        已识别的人物列表：{entities}
        
        请提取以下信息：
        1. 关系类型（relation_type，如：'同事'、'朋友'、'家人'、'师生'等）
        2. 关系描述（description）
        3. 起始人物（from_person）
        4. 目标人物（to_person）
        
        返回格式示例：
        [
            {{
                "relation_type": "同事",
                "description": "在同一家公司工作",
                "from_person": "张三",
                "to_person": "李四"
            }}
        ]
        
        请只返回JSON格式，不要有其他文本。"""
    
    def _load_neo4j_config(self, config_path: str) -> Dict:
        """加载Neo4j配置文件"""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            return config
        except Exception as e:
            logger.error(f"加载Neo4j配置失败: {e}")
            return {}
    
    def _init_llm(self, use_openai: bool, temperature: float):
        """初始化LLM"""
        if use_openai:
            # 使用OpenAI
            try:
                from llama_index.llms.openai import OpenAI
                API_SECRET_KEY = os.getenv("API_SECRET_KEY")
                BASE_URL = os.getenv("BASE_URL")
                return OpenAI(
                    api_key=API_SECRET_KEY,
                    api_base=BASE_URL,
                    temperature=temperature,
                    model="gpt-3.5-turbo"
                )
            except ImportError:
                logger.warning("无法导入OpenAI，使用备用方案")
                return get_llm(temperature)
        else:
            # 使用本地模型
            return get_llm(temperature)
    
    def extract_entities_from_text(self, text: str) -> List[Dict]:
        """
        从文本中提取实体信息
        
        Args:
            text: 自然语言文本
            
        Returns:
            实体信息列表
        """
        try:
            # 构建提示词
            prompt = self.entity_extraction_prompt.format(text=text)
            
            # 调用LLM
            if hasattr(self.llm, 'complete'):
                response = self.llm.complete(prompt)
                result_text = response.text
            else:
                # 使用函数式LLM
                result_text = self.llm(prompt)
            
            # 解析JSON响应
            entities = self._parse_json_response(result_text)
            
            logger.info(f"成功提取 {len(entities)} 个实体")
            return entities
            
        except Exception as e:
            logger.error(f"提取实体失败: {e}")
            return []
    
    def extract_relations_from_text(self, text: str, entities: List[Dict]) -> List[Dict]:
        """
        从文本中提取关系信息（暂不实现）
        
        Args:
            text: 自然语言文本
            entities: 已识别的实体列表
            
        Returns:
            关系信息列表（空列表）
        """
        logger.info("关系提取功能暂未实现，将在后续版本中添加")
        return []
    
    def _parse_json_response(self, response_text: str) -> List[Dict]:
        """解析LLM返回的JSON响应"""
        try:
            # 清理响应文本，移除可能的markdown代码块
            cleaned_text = response_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            
            # 解析JSON
            data = json.loads(cleaned_text)
            
            # 确保返回的是列表
            if isinstance(data, dict):
                return [data]
            elif isinstance(data, list):
                return data
            else:
                return []
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            logger.debug(f"原始响应: {response_text}")
            return []
        except Exception as e:
            logger.error(f"解析响应失败: {e}")
            return []
    
    def create_person_nodes(self, entities: List[Dict]) -> Dict:
        """
        创建Person节点
        
        Args:
            entities: 实体信息列表
            
        Returns:
            创建结果统计
        """
        results = {
            "total": len(entities),
            "success_count": 0,
            "failed_count": 0,
            "success_records": [],
            "failed_records": []
        }
        
        for entity in entities:
            try:
                # 准备数据
                name = entity.get("name", "")
                birth_date = entity.get("birth_date", "1900-01-01")
                gender = entity.get("gender", "其他")
                nationality = entity.get("nationality", "未知")
                biography = entity.get("biography", "")
                metadata = entity.get("metadata", {})
                
                # 如果出生日期是"未知"，使用默认值
                if birth_date == "未知" or not birth_date:
                    birth_date = "1900-01-01"
                
                # 创建节点
                result = self.person_creator.create_single_person(
                    name=name,
                    birth_date=birth_date,
                    gender=gender,
                    nationality=nationality,
                    biography=biography,
                    metadata=metadata
                )
                
                if result.get("success", False):
                    results["success_count"] += 1
                    results["success_records"].append({
                        "name": name,
                        "person_id": result.get("person_id"),
                        "message": result.get("message")
                    })
                else:
                    results["failed_count"] += 1
                    results["failed_records"].append({
                        "name": name,
                        "error": result.get("message", "未知错误")
                    })
                    
            except Exception as e:
                results["failed_count"] += 1
                results["failed_records"].append({
                    "name": entity.get("name", "未知"),
                    "error": str(e)
                })
        
        logger.info(f"节点创建完成: 成功 {results['success_count']} 个，失败 {results['failed_count']} 个")
        return results
    
    def process_text(self, text: str) -> Dict:
        """
        处理自然语言文本，提取实体和关系并创建知识图谱
        
        Args:
            text: 自然语言文本
            
        Returns:
            处理结果
        """
        logger.info("=" * 50)
        logger.info("开始处理文本...")
        logger.info(f"文本长度: {len(text)} 字符")
        logger.info("=" * 50)
        
        # 步骤1: 提取实体
        logger.info("步骤1: 提取实体...")
        entities = self.extract_entities_from_text(text)
        
        if not entities:
            logger.warning("未提取到任何实体")
            return {
                "success": False,
                "message": "未提取到任何实体",
                "entities": [],
                "relations": []
            }
        
        # 步骤2: 创建Person节点
        logger.info("步骤2: 创建Person节点...")
        node_results = self.create_person_nodes(entities)
        
        # 构建人物姓名到ID的映射
        person_name_to_id = {}
        for record in node_results["success_records"]:
            person_name_to_id[record["name"]] = record["person_id"]
        
        
        # 返回综合结果
        logger.info("文本处理完成")
        return {
            "success": True,
            "message": "文本处理完成",
            "statistics": {
                "entities_extracted": len(entities),
                "nodes_created": node_results["success_count"],
                "nodes_failed": node_results["failed_count"],
            },
            "entities": entities,
            "node_results": node_results,
        }


# 便捷函数
def text_to_kg(text: str, 
               neo4j_config_path: str = "./config/neo4j.yaml",
               use_openai: bool = True) -> Dict:
    """
    将自然语言文本转换为知识图谱的便捷函数
    
    Args:
        text: 自然语言文本
        neo4j_config_path: Neo4j配置文件路径
        use_openai: 是否使用OpenAI
        
    Returns:
        处理结果
    """
    converter = NLPToKGConverter(
        neo4j_config_path=neo4j_config_path,
        use_openai=use_openai
    )
    
    return converter.process_text(text)


if __name__ == "__main__":
    # 测试示例
    test_text = """
    张三是一位中国男性软件工程师，出生于1990年5月15日。他在北京工作，主要擅长Python和Java开发。
    李四是张三的同事，也是一位软件工程师，出生于1988年3月20日，女性，来自上海。
    他们一起合作开发了一个智能聊天机器人项目。
    """
    
    print("测试自然语言到知识图谱转换...")
    result = text_to_kg(test_text)
    
    print("\n" + "=" * 50)
    print("处理结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("=" * 50)