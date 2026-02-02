
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.memory_core.memory_system import MemoryCore
from load_model.OpenAIcall import get_embed_model,get_llm

memory_core = MemoryCore(
    workflow_id="test4",
    llm_func=get_llm(0.0),
    embed_func=get_embed_model(),
    llm_temperature=0.0,
    similarity_threshold=0.7,
    top_k=3,
    use_threshold=True
)
# 获取统计信息
kg_stats = memory_core.get_kg_stats()
print(f"  KG统计: {kg_stats}")
        
# 获取实体解析统计
er_stats = memory_core.get_entity_resolution_stats()
print(f"  实体解析统计: {er_stats}")

memory_core.load_from_dialogue_path(Path("data/memory/test4/kg_candidates"))