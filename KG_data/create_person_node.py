import logging
from py2neo import Graph, Node
from datetime import datetime
import uuid
import json
from typing import Optional, Dict, List

# 配置日志
logger = logging.getLogger(__name__)

class PersonNodeCreator:
    def __init__(self, uri: str = "bolt://localhost:7687",
                 username: str = "neo4j",
                 password: str = "password"):
        """
        初始化Neo4j连接
        
        Args:
            uri: Neo4j数据库地址 (默认: bolt://localhost:7687)
            username: 用户名 (默认: neo4j)
            password: 密码
        """
        try:
            self.graph = Graph(uri, auth=(username, password))
            logger.info(f"成功连接到Neo4j数据库: {uri}")
            
            # 测试连接
            self.graph.run("RETURN 1")
            logger.info("数据库连接测试成功")
            
            # 创建约束和索引
            self._setup_constraints()
            
        except Exception as e:
            logger.error(f"连接数据库失败: {e}")
            raise
    
    def _setup_constraints(self):
        """创建唯一约束和索引"""
        try:
            # 创建person_id的唯一约束
            self.graph.run("""
                CREATE CONSTRAINT IF NOT EXISTS FOR (p:Person)
                REQUIRE p.person_id IS UNIQUE
            """)
            logger.info("已创建/确认person_id唯一约束")
            
            # 为常用查询字段创建索引
            self.graph.run("CREATE INDEX IF NOT EXISTS FOR (p:Person) ON (p.name)")
            self.graph.run("CREATE INDEX IF NOT EXISTS FOR (p:Person) ON (p.nationality)")
            self.graph.run("CREATE INDEX IF NOT EXISTS FOR (p:Person) ON (p.gender)")
            logger.info("已创建/确认常用字段索引")
            
        except Exception as e:
            logger.error(f"设置约束时出错: {e}")
    
    def create_single_person(self, 
                            name: str,
                            birth_date: str,
                            gender: str,
                            nationality: str,
                            biography: str = "",
                            metadata: Optional[Dict] = None,
                            person_id: Optional[str] = None) -> Dict:
        """
        创建单个Person节点
        
        Args:
            name: 姓名
            birth_date: 出生日期 (格式: YYYY-MM-DD)
            gender: 性别 ['男','女','其他']
            nationality: 国籍
            biography: 人物简介
            metadata: 扩展字段 (字典格式)
            person_id: 人员ID，如未提供则自动生成UUID
            
        Returns:
            Dict: 创建成功的信息和节点属性
            
        Raises:
            ValueError: 当输入数据无效时
        """
        # 验证性别
        valid_genders = ['男', '女', '其他']
        if gender not in valid_genders:
            raise ValueError(f"性别必须是以下之一: {valid_genders}")
        
        # 验证并格式化出生日期
        try:
            datetime.strptime(birth_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("出生日期格式应为 YYYY-MM-DD")
        
        # 生成person_id（如果未提供）
        if person_id is None:
            person_id = str(uuid.uuid4())
        
        # 准备metadata字段
        if metadata is None:
            metadata = {}
        
        # 创建节点属性字典
        properties = {
            "person_id": person_id,
            "name": name,
            "birth_date": birth_date,
            "gender": gender,
            "nationality": nationality,
            "biography": biography,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metadata": json.dumps(metadata, ensure_ascii=False)
        }
        
        # 创建Neo4j节点
        person_node = Node("Person", **properties)
        self.graph.create(person_node)
        
        logger.info(f"✅ 成功创建Person节点:")
        logger.info(f"   ID: {person_id}")
        logger.info(f"   姓名: {name}")
        logger.info(f"   出生日期: {birth_date}")
        logger.info(f"   性别: {gender}")
        logger.info(f"   国籍: {nationality}")
        
        return {
            "success": True,
            "person_id": person_id,
            "message": f"成功创建人员 '{name}'",
            "properties": properties
        }
    
    def create_batch_persons(self, persons_list: List[Dict]) -> Dict:
        """
        批量创建多个Person节点
        
        Args:
            persons_list: Person数据列表，每个字典应包含：
                         name, birth_date, gender, nationality
                         （可选：biography, metadata, person_id）
        
        Returns:
            Dict: 批量创建结果统计
            
        Raises:
            ValueError: 当数据格式无效时
        """
        if not persons_list:
            return {"success": False, "message": "人员列表为空"}
        
        success_count = 0
        failed_count = 0
        failed_records = []
        
        for i, person_data in enumerate(persons_list):
            try:
                # 验证必需字段
                required_fields = ['name', 'birth_date', 'gender', 'nationality']
                for field in required_fields:
                    if field not in person_data:
                        raise ValueError(f"缺少必需字段: {field}")
                
                # 调用创建单个节点的方法
                result = self.create_single_person(
                    name=person_data['name'],
                    birth_date=person_data['birth_date'],
                    gender=person_data['gender'],
                    nationality=person_data['nationality'],
                    biography=person_data.get('biography', ''),
                    metadata=person_data.get('metadata'),
                    person_id=person_data.get('person_id')
                )
                
                if result['success']:
                    success_count += 1
                else:
                    failed_count += 1
                    failed_records.append({
                        'index': i,
                        'name': person_data['name'],
                        'error': result.get('message', '未知错误')
                    })
                    
            except Exception as e:
                failed_count += 1
                failed_records.append({
                    'index': i,
                    'name': person_data.get('name', '未知姓名'),
                    'error': str(e)
                })
        
        logger.info(f"\n📊 批量创建完成:")
        logger.info(f"   成功: {success_count} 个")
        logger.info(f"   失败: {failed_count} 个")
        
        return {
            "success": True,
            "total": len(persons_list),
            "success_count": success_count,
            "failed_count": failed_count,
            "failed_records": failed_records
        }
    
    def create_person_from_dict(self, data_dict: Dict) -> Dict:
        """
        从字典直接创建Person节点
        
        Args:
            data_dict: 包含所有Person属性的字典
        
        Returns:
            Dict: 创建结果
        """
        try:
            # 提取必需字段
            required_fields = ['name', 'birth_date', 'gender', 'nationality']
            for field in required_fields:
                if field not in data_dict:
                    raise ValueError(f"字典中缺少必需字段: {field}")
            
            # 提取可选字段
            biography = data_dict.get('biography', '')
            metadata = data_dict.get('metadata')
            person_id = data_dict.get('person_id')
            
            # 创建节点
            return self.create_single_person(
                name=data_dict['name'],
                birth_date=data_dict['birth_date'],
                gender=data_dict['gender'],
                nationality=data_dict['nationality'],
                biography=biography,
                metadata=metadata,
                person_id=person_id
            )
            
        except Exception as e:
            return {
                "success": False,
                "message": f"创建失败: {str(e)}",
                "error": str(e)
            }
    
    def verify_connection(self) -> bool:
        """验证数据库连接是否正常"""
        try:
            result = self.graph.run("RETURN 'Neo4j Connection Test' AS test").data()
            return len(result) > 0
        except Exception:
            return False
    
    def get_database_info(self) -> Dict:
        """获取数据库信息"""
        try:
            # 获取Person节点数量
            count_result = self.graph.run("MATCH (p:Person) RETURN count(p) as count").data()
            person_count = count_result[0]['count'] if count_result else 0
            
            # 获取Neo4j版本
            version_result = self.graph.run("CALL dbms.components() YIELD versions RETURN versions[0] as version").data()
            neo4j_version = version_result[0]['version'] if version_result else "未知"
            
            return {
                "connected": True,
                "person_count": person_count,
                "neo4j_version": neo4j_version,
                "constraints": "person_id唯一约束已启用"
            }
        except Exception as e:
            return {
                "connected": False,
                "error": str(e)
            }

# 快速创建单个节点的便捷函数
def quick_create_person(name: str, birth_date: str, gender: str, nationality: str, 
                       biography: str = "", **kwargs):
    """
    快速创建单个Person节点的便捷函数
    
    Args:
        name: 姓名
        birth_date: 出生日期
        gender: 性别
        nationality: 国籍
        biography: 简介
        **kwargs: 其他参数，包括：
            uri: 数据库URI
            username: 用户名
            password: 密码
            metadata: 扩展字段
    
    Returns:
        Dict: 创建结果
    """
    try:
        # 提取连接参数
        uri = kwargs.get('uri', 'neo4j://127.0.0.1:7687')
        username = kwargs.get('username', 'neo4j')
        password = kwargs.get('password', 'EVAnational0')
        
        # 提取metadata
        metadata = kwargs.get('metadata', {})
        
        # 创建连接器
        creator = PersonNodeCreator(uri, username, password)
        
        # 创建节点
        result = creator.create_single_person(
            name=name,
            birth_date=birth_date,
            gender=gender,
            nationality=nationality,
            biography=biography,
            metadata=metadata
        )
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"快速创建失败: {str(e)}",
            "error": str(e)
        }
