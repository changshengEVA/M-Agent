from pathlib import Path
import json
import numpy as np
import faiss
import os,sys
from typing import List, Dict, Any, Tuple
# 添加项目根目录到 Python 路径，确保可以导入 load_model
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from load_model.OpenAIcall import get_embed_model

def scan_scenes(scene_root: Path) -> List[Tuple[Dict[str, Any], Path, str]]:
    """
    扫描 scene_root 目录下的所有用户目录，加载每个 scene 的 v1.0.json 文件。
    
    Args:
        scene_root: 场景根目录，例如 Path("data/memory/scenes/by_user")
    
    Returns:
        列表，每个元素为 (scene_data, scene_file_path, user_id)
    """
    scenes = []
    if not scene_root.exists():
        print(f"警告：目录 {scene_root} 不存在")
        return scenes
    
    for user_dir in scene_root.iterdir():
        if not user_dir.is_dir():
            continue
        user_id = user_dir.name
        for scene_dir in user_dir.iterdir():
            if not scene_dir.is_dir():
                continue
            scene_file = scene_dir / "v1.0.json"
            if scene_file.exists():
                try:
                    scene = json.loads(scene_file.read_text(encoding="utf-8"))
                    scenes.append((scene, scene_file, user_id))
                except Exception as e:
                    print(f"加载文件 {scene_file} 时出错: {e}")
    return scenes

def build_scene_embedding_text(scene: Dict[str, Any]) -> str:
    """
    根据场景数据构建用于嵌入的文本。
    
    Args:
        scene: 场景字典
    
    Returns:
        用于嵌入的文本字符串
    """
    parts = []
    parts.append(f"[Scene Diary]\n{scene['diary']}")
    parts.append(f"\n[Scene Type]\n{scene['scene_type']} / {scene['content_type']}")

    if scene.get("intent"):
        parts.append(f"\n[Intent]\n{scene['intent'].replace('_', ' ')}")

    if scene.get("tags"):
        parts.append(f"\n[Tags]\n{', '.join(scene['tags'])}")

    return "\n".join(parts)

def build_metadata(scene: Dict[str, Any], scene_file: Path, user_id: str) -> Dict[str, Any]:
    """
    为场景构建元数据。
    
    Args:
        scene: 场景字典
        scene_file: 场景文件路径
        user_id: 用户ID
    
    Returns:
        元数据字典
    """
    # 注意：原始代码中 scene['episodes'] 是一个字典，但实际文件显示是嵌套结构
    # 根据实际数据结构调整
    episodes = scene.get("source", {}).get("episodes", [])
    if episodes:
        first_episode = episodes[0]
        dialogue_id = first_episode.get("dialogue_id", "")
        episode_id = first_episode.get("episode_id", "")
        turn_span = first_episode.get("turn_span", [])
    else:
        # 回退到旧字段（如果存在）
        dialogue_id = scene.get("episodes", {}).get("dialogue_id", "")
        episode_id = scene.get("episodes", {}).get("episode_id", "")
        turn_span = scene.get("episodes", {}).get("turn_span", [])
    
    # 使用相对路径
    scene_path = str(scene_file.relative_to(Path.cwd())) if scene_file.is_relative_to(Path.cwd()) else str(scene_file)
    
    return {
        "vector_id": f"{scene['scene_id']}_{scene['scene_version']}",
        "scene_id": scene["scene_id"],
        "scene_version": scene["scene_version"],
        "scene_path": scene_path,
        "user_id": user_id,
        "source_dialogue": dialogue_id,
        "episode_ids": episode_id,
        "turn_span": turn_span,
        "scene_type": scene["scene_type"],
        "content_type": scene["content_type"],
        "intent": scene.get("intent"),
        "tags": scene.get("tags", []),
        "confidence": scene.get("confidence", 1.0)
    }

def generate_embeddings(embedding_texts: List[str], embed_model) -> Tuple[np.ndarray, List[int]]:
    """
    使用嵌入模型为文本列表生成嵌入向量。
    
    Args:
        embedding_texts: 文本列表
        embed_model: 嵌入模型函数
    
    Returns:
        embeddings: 形状为 (n, d) 的 numpy 数组
        success_indices: 成功生成嵌入的原始索引列表
    """
    embeddings = []
    success_indices = []
    
    for i, text in enumerate(embedding_texts):
        try:
            embedding = embed_model(text)
            embeddings.append(embedding)
            success_indices.append(i)
            if (i + 1) % 10 == 0:
                print(f"已生成 {i + 1}/{len(embedding_texts)} 个嵌入")
        except Exception as e:
            print(f"为第 {i} 个文本生成嵌入时出错: {e}")
            # 跳过这个文本
            continue
    
    if not embeddings:
        raise ValueError("未能生成任何嵌入向量")
    
    return np.array(embeddings, dtype=np.float32), success_indices

def build_faiss_index(embeddings: np.ndarray) -> faiss.Index:
    """
    使用 FAISS 构建索引。
    
    Args:
        embeddings: 形状为 (n, d) 的嵌入向量
    
    Returns:
        FAISS 索引
    """
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)  # 使用 L2 距离
    index.add(embeddings)
    return index

def save_vector_store(index: faiss.Index, embeddings: np.ndarray, 
                      metas: List[Dict[str, Any]], output_dir: Path):
    """
    保存向量库到指定目录。
    
    Args:
        index: FAISS 索引
        embeddings: 嵌入向量数组
        metas: 元数据列表
        output_dir: 输出目录
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存 FAISS 索引
    index_path = output_dir / "index.faiss"
    faiss.write_index(index, str(index_path))
    print(f"FAISS 索引已保存到 {index_path}")
    
    # 保存嵌入向量
    embeddings_path = output_dir / "embeddings.npy"
    np.save(str(embeddings_path), embeddings)
    print(f"嵌入向量已保存到 {embeddings_path}")
    
    # 保存元数据为 JSONL
    meta_path = output_dir / "meta.jsonl"
    with open(meta_path, 'w', encoding='utf-8') as f:
        for meta in metas:
            f.write(json.dumps(meta, ensure_ascii=False) + '\n')
    print(f"元数据已保存到 {meta_path}")

def scan_and_build_vector(scene_root: str = "data/memory/scenes/by_user",
                          vector_output_dir: str = "data/memory/vectors/scenes",
                          overwrite: bool = False):
    """
    扫描所有 scene 文件并构建向量库。
    
    Args:
        scene_root: 场景根目录路径
        vector_output_dir: 向量输出目录路径
        overwrite: 是否覆盖已存在的向量库
    """
    scene_root_path = Path(scene_root)
    output_dir = Path(vector_output_dir)
    
    # 检查输出目录是否已存在
    if output_dir.exists() and not overwrite:
        print(f"向量库目录 {output_dir} 已存在，跳过构建（使用 overwrite=True 强制重建）")
        return
    
    # 扫描场景
    print("正在扫描场景文件...")
    scene_tuples = scan_scenes(scene_root_path)
    if not scene_tuples:
        print("未找到任何场景文件")
        return
    
    print(f"共找到 {len(scene_tuples)} 个场景")
    
    # 构建嵌入文本和元数据
    print("正在构建嵌入文本和元数据...")
    embedding_texts = []
    metas = []
    scenes_data = []
    
    for scene, scene_file, user_id in scene_tuples:
        scenes_data.append(scene)
        embedding_texts.append(build_scene_embedding_text(scene))
        metas.append(build_metadata(scene, scene_file, user_id))
    
    # 获取嵌入模型
    print("正在加载嵌入模型...")
    embed_model = get_embed_model()
    
    # 生成嵌入向量
    print("正在生成嵌入向量...")
    embeddings, success_indices = generate_embeddings(embedding_texts, embed_model)
    
    # 根据成功索引过滤元数据和场景数据
    filtered_metas = [metas[i] for i in success_indices]
    filtered_scenes = [scenes_data[i] for i in success_indices]
    
    print(f"成功生成 {len(embeddings)} 个嵌入向量（跳过了 {len(embedding_texts) - len(embeddings)} 个）")
    
    # 更新引用
    metas = filtered_metas
    scenes_data = filtered_scenes
    
    # 构建 FAISS 索引
    print("正在构建 FAISS 索引...")
    index = build_faiss_index(embeddings)
    
    # 保存向量库
    print("正在保存向量库...")
    save_vector_store(index, embeddings, metas, output_dir)
    
    print("向量库构建完成！")

if __name__ == "__main__":
    # 如果直接运行此脚本，执行构建
    scan_and_build_vector()