import logging
from py2neo import Graph
from datetime import datetime
from typing import List, Dict, Optional
import yaml
import os

# 配置日志
logger = logging.getLogger(__name__)

class PersonNodeDeleter:
    def __init__(self, uri: str = None, username: str = None, password: str = None):
        """
        初始化Neo4j连接
        
        Args:
            uri: Neo4j数据库地址，如未提供则从配置文件读取
            username: 用户名，如未提供则从配置文件读取
            password: 密码，如未提供则从配置文件读取
        """
        try:
            # 如果未提供连接参数，则从配置文件读取
            if uri is None or username is None or password is None:
                config = self._load_config()
                uri = uri or config.get('url', 'neo4j://127.0.0.1:7687')
                username = username or config.get('user_name', 'neo4j')
                password = password or config.get('password', 'EVAnational0')
            
            self.graph = Graph(uri, auth=(username, password))
            logger.info(f"成功连接到Neo4j数据库: {uri}")
            
            # 测试连接
            self.graph.run("RETURN 1")
            logger.info("数据库连接测试成功")
            
        except Exception as e:
            logger.error(f"连接数据库失败: {e}")
            raise
    
    def _load_config(self) -> Dict:
        """从配置文件加载Neo4j配置"""
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'neo4j.yaml')
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            logger.info(f"从配置文件加载Neo4j配置: {config_path}")
            return config
        except Exception as e:
            logger.warning(f"无法加载配置文件 {config_path}: {e}")
            # 返回默认配置
            return {
                'url': 'neo4j://127.0.0.1:7687',
                'user_name': 'neo4j',
                'password': 'EVAnational0'
            }
    
    def delete_single_person(self, person_id: str, confirm: bool = True) -> Dict:
        """
        删除单个Person节点
        
        Args:
            person_id: 要删除的人员ID
            confirm: 是否在删除前确认节点存在（默认True）
            
        Returns:
            Dict: 删除结果信息
        """
        try:
            # 验证person_id
            if not person_id or not isinstance(person_id, str):
                return {
                    "success": False,
                    "message": "无效的person_id",
                    "person_id": person_id
                }
            
            # 如果确认模式开启，先检查节点是否存在
            if confirm:
                check_query = """
                    MATCH (p:Person {person_id: $person_id})
                    RETURN p.person_id as person_id, p.name as name
                """
                result = self.graph.run(check_query, person_id=person_id).data()
                
                if not result:
                    return {
                        "success": False,
                        "message": f"未找到person_id为 '{person_id}' 的节点",
                        "person_id": person_id
                    }
                
                node_info = result[0]
                logger.info(f"找到要删除的节点: ID={node_info['person_id']}, 姓名={node_info.get('name', '未知')}")
            
            # 执行删除操作
            delete_query = """
                MATCH (p:Person {person_id: $person_id})
                DETACH DELETE p
                RETURN count(p) as deleted_count
            """
            
            delete_result = self.graph.run(delete_query, person_id=person_id).data()
            deleted_count = delete_result[0]['deleted_count'] if delete_result else 0
            
            if deleted_count > 0:
                logger.info(f"✅ 成功删除Person节点: person_id={person_id}")
                return {
                    "success": True,
                    "message": f"成功删除person_id为 '{person_id}' 的节点",
                    "person_id": person_id,
                    "deleted_count": deleted_count,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            else:
                logger.warning(f"⚠️ 未删除任何节点: person_id={person_id}")
                return {
                    "success": False,
                    "message": f"未找到或无法删除person_id为 '{person_id}' 的节点",
                    "person_id": person_id,
                    "deleted_count": 0
                }
                
        except Exception as e:
            logger.error(f"删除单个节点时出错 (person_id={person_id}): {e}")
            return {
                "success": False,
                "message": f"删除失败: {str(e)}",
                "person_id": person_id,
                "error": str(e)
            }
    
    def delete_multiple_persons(self, person_ids: List[str], confirm: bool = True) -> Dict:
        """
        批量删除多个Person节点
        
        Args:
            person_ids: 要删除的人员ID列表
            confirm: 是否在删除前确认节点存在（默认True）
            
        Returns:
            Dict: 批量删除结果统计
        """
        try:
            # 验证输入
            if not person_ids:
                return {
                    "success": False,
                    "message": "人员ID列表为空",
                    "total": 0,
                    "deleted_count": 0,
                    "failed_count": 0
                }
            
            if not isinstance(person_ids, list):
                return {
                    "success": False,
                    "message": "person_ids必须是列表类型",
                    "total": 0,
                    "deleted_count": 0,
                    "failed_count": 0
                }
            
            total = len(person_ids)
            deleted_count = 0
            failed_count = 0
            failed_records = []
            
            logger.info(f"开始批量删除 {total} 个节点...")
            
            # 如果确认模式开启，先统计存在的节点
            if confirm:
                existing_ids = []
                for person_id in person_ids:
                    check_query = """
                        MATCH (p:Person {person_id: $person_id})
                        RETURN p.person_id as person_id
                    """
                    result = self.graph.run(check_query, person_id=person_id).data()
                    if result:
                        existing_ids.append(person_id)
                
                logger.info(f"找到 {len(existing_ids)}/{total} 个存在的节点")
                person_ids = existing_ids  # 只删除存在的节点
            
            # 批量删除
            for i, person_id in enumerate(person_ids):
                try:
                    result = self.delete_single_person(person_id, confirm=False)
                    
                    if result['success']:
                        deleted_count += 1
                        logger.debug(f"成功删除第 {i+1}/{len(person_ids)} 个节点: {person_id}")
                    else:
                        failed_count += 1
                        failed_records.append({
                            'person_id': person_id,
                            'error': result.get('message', '未知错误'),
                            'index': i
                        })
                        logger.warning(f"删除失败第 {i+1}/{len(person_ids)} 个节点: {person_id} - {result.get('message')}")
                        
                except Exception as e:
                    failed_count += 1
                    failed_records.append({
                        'person_id': person_id,
                        'error': str(e),
                        'index': i
                    })
                    logger.error(f"删除节点时异常 (person_id={person_id}): {e}")
            
            logger.info(f"\n📊 批量删除完成:")
            logger.info(f"   总计: {total} 个")
            logger.info(f"   成功: {deleted_count} 个")
            logger.info(f"   失败: {failed_count} 个")
            
            return {
                "success": True if deleted_count > 0 else False,
                "message": f"批量删除完成，成功 {deleted_count} 个，失败 {failed_count} 个",
                "total": total,
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "failed_records": failed_records,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
        except Exception as e:
            logger.error(f"批量删除节点时出错: {e}")
            return {
                "success": False,
                "message": f"批量删除失败: {str(e)}",
                "error": str(e),
                "total": len(person_ids) if person_ids else 0,
                "deleted_count": 0,
                "failed_count": len(person_ids) if person_ids else 0
            }
    
    def delete_all_persons(self, force_confirm: bool = False) -> Dict:
        """
        删除所有Person节点（危险操作）
        
        Args:
            force_confirm: 强制确认，如果为False则需要额外确认
            
        Returns:
            Dict: 删除结果
        """
        try:
            # 首先统计当前有多少Person节点
            count_query = "MATCH (p:Person) RETURN count(p) as total_count"
            count_result = self.graph.run(count_query).data()
            total_count = count_result[0]['total_count'] if count_result else 0
            
            if total_count == 0:
                logger.info("数据库中没有任何Person节点")
                return {
                    "success": True,
                    "message": "数据库中没有任何Person节点",
                    "deleted_count": 0,
                    "total_count": 0
                }
            
            logger.warning(f"⚠️ 警告: 即将删除所有 {total_count} 个Person节点")
            
            # 如果不是强制确认，需要额外确认
            if not force_confirm:
                # 这里可以添加额外的确认逻辑，比如返回需要用户确认的信息
                # 在实际应用中，可能需要用户输入确认码或进行二次确认
                logger.warning("此操作将永久删除所有Person节点，请谨慎操作！")
                # 返回需要确认的信息
                return {
                    "success": False,
                    "message": f"需要确认: 此操作将删除所有 {total_count} 个Person节点",
                    "requires_confirmation": True,
                    "total_count": total_count,
                    "confirmation_message": f"确认删除所有 {total_count} 个Person节点吗？"
                }
            
            # 执行删除所有节点的操作
            delete_query = "MATCH (p:Person) DETACH DELETE p RETURN count(p) as deleted_count"
            delete_result = self.graph.run(delete_query).data()
            deleted_count = delete_result[0]['deleted_count'] if delete_result else 0
            
            logger.info(f"✅ 成功删除所有Person节点: 共 {deleted_count} 个")
            
            return {
                "success": True,
                "message": f"成功删除所有 {deleted_count} 个Person节点",
                "deleted_count": deleted_count,
                "total_count": total_count,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
        except Exception as e:
            logger.error(f"删除所有节点时出错: {e}")
            return {
                "success": False,
                "message": f"删除所有节点失败: {str(e)}",
                "error": str(e)
            }
    
    def delete_by_name(self, name: str, exact_match: bool = True) -> Dict:
        """
        根据姓名删除Person节点
        
        Args:
            name: 要删除的人员姓名
            exact_match: 是否精确匹配（True为精确匹配，False为模糊匹配）
            
        Returns:
            Dict: 删除结果
        """
        try:
            if not name:
                return {
                    "success": False,
                    "message": "姓名为空",
                    "deleted_count": 0
                }
            
            if exact_match:
                # 精确匹配
                query = """
                    MATCH (p:Person {name: $name})
                    DETACH DELETE p
                    RETURN count(p) as deleted_count
                """
            else:
                # 模糊匹配（包含）
                query = """
                    MATCH (p:Person)
                    WHERE p.name CONTAINS $name
                    DETACH DELETE p
                    RETURN count(p) as deleted_count
                """
            
            result = self.graph.run(query, name=name).data()
            deleted_count = result[0]['deleted_count'] if result else 0
            
            if deleted_count > 0:
                match_type = "精确" if exact_match else "模糊"
                logger.info(f"✅ 根据姓名{match_type}匹配删除 {deleted_count} 个节点: name='{name}'")
                return {
                    "success": True,
                    "message": f"根据姓名{match_type}匹配成功删除 {deleted_count} 个节点",
                    "name": name,
                    "exact_match": exact_match,
                    "deleted_count": deleted_count,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            else:
                logger.warning(f"⚠️ 未找到匹配的节点: name='{name}' (exact_match={exact_match})")
                return {
                    "success": False,
                    "message": f"未找到匹配姓名为 '{name}' 的节点",
                    "name": name,
                    "exact_match": exact_match,
                    "deleted_count": 0
                }
                
        except Exception as e:
            logger.error(f"根据姓名删除节点时出错 (name={name}): {e}")
            return {
                "success": False,
                "message": f"根据姓名删除失败: {str(e)}",
                "name": name,
                "error": str(e)
            }
    
    def verify_connection(self) -> bool:
        """验证数据库连接是否正常"""
        try:
            result = self.graph.run("RETURN 'Neo4j Connection Test' AS test").data()
            return len(result) > 0
        except Exception:
            return False
    
    def get_person_count(self) -> Dict:
        """获取当前Person节点数量"""
        try:
            count_query = "MATCH (p:Person) RETURN count(p) as person_count"
            result = self.graph.run(count_query).data()
            person_count = result[0]['person_count'] if result else 0
            
            return {
                "success": True,
                "person_count": person_count,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "person_count": 0
            }


# 便捷函数
def delete_person(person_id: str, **kwargs) -> Dict:
    """
    删除单个Person节点的便捷函数
    
    Args:
        person_id: 要删除的人员ID
        **kwargs: 其他参数，包括：
            uri: 数据库URI
            username: 用户名
            password: 密码
            confirm: 是否确认（默认True）
            
    Returns:
        Dict: 删除结果
    """
    try:
        # 提取连接参数
        uri = kwargs.get('uri')
        username = kwargs.get('username')
        password = kwargs.get('password')
        confirm = kwargs.get('confirm', True)
        
        # 创建删除器
        deleter = PersonNodeDeleter(uri, username, password)
        
        # 删除节点
        result = deleter.delete_single_person(person_id, confirm)
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"删除失败: {str(e)}",
            "error": str(e)
        }


def delete_persons_batch(person_ids: List[str], **kwargs) -> Dict:
    """
    批量删除多个Person节点的便捷函数
    
    Args:
        person_ids: 要删除的人员ID列表
        **kwargs: 其他参数，包括：
            uri: 数据库URI
            username: 用户名
            password: 密码
            confirm: 是否确认（默认True）
            
    Returns:
        Dict: 批量删除结果
    """
    try:
        # 提取连接参数
        uri = kwargs.get('uri')
        username = kwargs.get('username')
        password = kwargs.get('password')
        confirm = kwargs.get('confirm', True)
        
        # 创建删除器
        deleter = PersonNodeDeleter(uri, username, password)
        
        # 批量删除
        result = deleter.delete_multiple_persons(person_ids, confirm)
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"批量删除失败: {str(e)}",
            "error": str(e)
        }


def delete_all_persons_safe(**kwargs) -> Dict:
    """
    安全删除所有Person节点的便捷函数（需要额外确认）
    
    Args:
        **kwargs: 其他参数，包括：
            uri: 数据库URI
            username: 用户名
            password: 密码
            force: 是否强制删除（默认False）
            
    Returns:
        Dict: 删除结果
    """
    try:
        # 提取连接参数
        uri = kwargs.get('uri')
        username = kwargs.get('username')
        password = kwargs.get('password')
        force = kwargs.get('force', False)
        
        # 创建删除器
        deleter = PersonNodeDeleter(uri, username, password)
        
        # 删除所有节点
        result = deleter.delete_all_persons(force_confirm=force)
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"删除所有节点失败: {str(e)}",
            "error": str(e)
        }


# 测试函数
if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=== Person节点删除功能测试 ===")

    try:
        # 创建删除器实例
        deleter = PersonNodeDeleter()

        # 测试连接
        if deleter.verify_connection():
            print("✅ 数据库连接正常")
            
            # 获取当前节点数量
            count_result = deleter.get_person_count()
            if count_result['success']:
                print(f"📊 当前Person节点数量: {count_result['person_count']}")
            else:
                print(f"⚠️ 无法获取节点数量: {count_result.get('error', '未知错误')}")
            
            # 演示删除功能（注释掉实际删除操作，避免误删）
            print("\n📝 删除功能演示（实际删除操作已注释）:")
            print("1. 删除单个节点: deleter.delete_single_person('example_id')")
            print("2. 批量删除节点: deleter.delete_multiple_persons(['id1', 'id2'])")
            print("3. 根据姓名删除: deleter.delete_by_name('张三')")
            print("4. 删除所有节点: deleter.delete_all_persons(force_confirm=True)")
            print("\n⚠️ 注意: 实际使用时请取消注释并谨慎操作")
            
        else:
            print("❌ 数据库连接失败，请检查Neo4j服务是否运行")
            
    except Exception as e:
        print(f"❌ 测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()