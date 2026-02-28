import sys
import json
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 配置日志显示
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # 输出到控制台
    ]
)

# 设置特定模块的日志级别
logging.getLogger('memory.memory_core').setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger('memory.memory_core.services_bank.entity_resolution').setLevel(logging.WARNING)

print("=== 开始测试，日志已启用 ===")

from memory.memory_core.memory_system import MemoryCore
from load_model.OpenAIcall import get_llm
from load_model.BGEcall import get_embed_model

memory_core = MemoryCore(
    workflow_id="testrt",
    llm_func=get_llm(0.0),
    embed_func=get_embed_model(),
    llm_temperature=0.0,
    similarity_threshold=0.88,
    top_k=3,
    use_threshold=True
)

# #强制重新解析并执行
# memory_core.entity_resolution_service.entity_library.reset_all_resolution_flags()
# memory_core.run_entity_resolution_pass()


# memory_core.load_from_dialogue_path(Path("data/memory/testrt/kg_candidates"))
# 获取统计信息
kg_stats = memory_core.get_kg_stats()
print(f"  KG统计: {kg_stats}")

# 获取实体解析统计
er_stats = memory_core.get_entity_resolution_stats()
print(f"  实体解析统计: {er_stats}")

# 尝试解析实体
entity_return = memory_core.resolve_entity("Emi")
print(" entity_return:")
print(json.dumps(entity_return, ensure_ascii=False, indent=2))

# 尝试搜索特征和属性
property_return = memory_core.query_entity_property("30a96824-01b0-4cbe-8ba4-c8c6fc3645eb", "兴趣爱好")
print(" property_return:")
print(json.dumps(property_return, ensure_ascii=False, indent=2))
