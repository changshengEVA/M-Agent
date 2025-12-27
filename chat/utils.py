import os
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from llama_index.core.llms import ChatMessage
load_dotenv()
LANGUAGE = os.getenv("LANGUAGE")

def store_the_history(history, llm, **kwargs):
    memory_o = ""
    for key, value in kwargs.items():
        content = f"{key}: {value}"
        memory_o += content + "\n"
        print(content)
    memory_o += "The following is the conversation history:\n" + history
    memory_o += f"\nThis is the basic memory of a conversation, please summarize the content in {LANGUAGE} taking changshengEVA's perspective, only talk about the key information.\n"
    print("Start to memory...")
    mem = str(llm.chat([ChatMessage(content=memory_o)])).split('assistant:')[-1]
    print("The history is storing, find the following args:")
    data_to_store = {
        'history': history,
        'kwargs': kwargs,
        'memory': mem
    }
    filename = "./data/memory/dialog_history.json"
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            existing_data = json.load(file)
            if not isinstance(existing_data, list):
                raise ValueError("Existing data is not a list")
    except (FileNotFoundError, ValueError):
        existing_data = []
    existing_data.append(data_to_store)
    json_data = json.dumps(existing_data, ensure_ascii=False, indent=4)
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(json_data)
    print("OK!! Data saved to", filename)
    
    # 同时保存为新格式
    try:
        store_dialogue_new_format(data_to_store)
    except Exception as e:
        print(f"警告: 保存新格式时出错: {e}")

def parse_history_text(history_text, base_time):
    """
    解析历史文本，将其转换为对话轮次列表
    
    Args:
        history_text: 原始对话文本，格式为 "ZQR:...\nchangshengEVA: ..."
        base_time: 对话开始时间字符串 "YYYY-MM-DD HH:MM:SS"
    
    Returns:
        list: 对话轮次列表，每个轮次包含 turn_id, speaker, text, timestamp
    """
    # 将base_time转换为datetime对象
    try:
        base_dt = datetime.strptime(base_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # 如果格式不同，尝试其他格式
        try:
            base_dt = datetime.fromisoformat(base_time.replace('Z', '+00:00'))
        except:
            base_dt = datetime.now()
    
    # 使用正则表达式分割对话轮次
    # 模式匹配 "ZQR:" 或 "changshengEVA:" 开头，直到下一个说话者或结尾
    pattern = r'(ZQR:|changshengEVA:)(.*?)(?=(?:\nZQR:|\nchangshengEVA:|$))'
    matches = re.findall(pattern, history_text, re.DOTALL)
    
    turns = []
    for i, (speaker_prefix, text) in enumerate(matches):
        # 清理说话者前缀
        speaker = speaker_prefix.rstrip(':')
        
        # 清理文本：去除首尾空白，包括换行符
        text = text.strip()
        
        # 为每个轮次生成时间戳（假设每个轮次间隔5秒）
        turn_dt = base_dt + timedelta(seconds=i * 5)
        timestamp = turn_dt.isoformat()
        
        turns.append({
            "turn_id": i,
            "speaker": speaker,
            "text": text,
            "timestamp": timestamp
        })
    
    return turns

def generate_dialogue_id(base_time):
    """
    根据基础时间生成对话ID
    
    Args:
        base_time: 时间字符串 "YYYY-MM-DD HH:MM:SS"
    
    Returns:
        str: 对话ID，格式为 "dlg_YYYY-MM-DD_HH-MM-SS"
    """
    try:
        dt = datetime.strptime(base_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # 如果格式不同，尝试其他格式
        try:
            dt = datetime.fromisoformat(base_time.replace('Z', '+00:00'))
        except:
            dt = datetime.now()
    
    return f"dlg_{dt.strftime('%Y-%m-%d_%H-%M-%S')}"

def store_dialogue_new_format(dialogue_data):
    """
    将对话数据保存为新格式（每个对话一个独立的JSON文件）
    
    Args:
        dialogue_data: 包含history, kwargs, memory的字典
    """
    history_text = dialogue_data.get("history", "")
    kwargs = dialogue_data.get("kwargs", {})
    base_time = kwargs.get("time", "")
    
    # 检查是否有实时记录的turns数据
    realtime_turns = kwargs.get("turns", [])
    
    if not base_time:
        # 如果没有时间，使用当前时间
        base_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 生成对话ID
    dialogue_id = generate_dialogue_id(base_time)
    
    # 如果有实时记录的turns，使用它们；否则解析历史文本
    # 使用实时记录的turns，添加turn_id
    turns = []
    for i, turn in enumerate(realtime_turns):
        turns.append({
            "turn_id": i,
            "speaker": turn.get("speaker", ""),
            "text": turn.get("text", ""),
            "timestamp": turn.get("timestamp", "")
        })
        
    # 确定开始和结束时间
    if turns:
        start_time = turns[0]["timestamp"]
        end_time = turns[-1]["timestamp"]
    else:
        start_time = base_time.replace(" ", "T") + ":00"
        end_time = start_time
    
    # 构建转换后的对话对象
    converted = {
        "dialogue_id": dialogue_id,
        "user_id": "ZQR",  # 从对话内容推断
        "participants": ["ZQR", "changshengEVA"],
        "meta": {
            "start_time": start_time,
            "end_time": end_time,
            "language": "zh",
            "platform": "web",
            "version": 1
        },
        "turns": turns
    }
    
    # 从对话ID或开始时间提取年月
    try:
        dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        year = dt.strftime("%Y")
        month = dt.strftime("%m")
    except:
        # 如果解析失败，使用当前年月
        dt = datetime.now()
        year = dt.strftime("%Y")
        month = dt.strftime("%m")
    
    # 构建输出路径
    user_id = "ZQR"
    output_dir = Path("./data/memory/dialogues") / "by_user" / user_id / f"{year}-{month}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 构建输出文件名
    output_file = output_dir / f"{dialogue_id}.json"
    
    # 保存为JSON文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)
    
    print(f"新格式对话已保存: {output_file}")

def read_mem():
    # 初始化一个空列表来存储memory值
    memory_list = []
    # 打开并读取JSON文件
    with open('./data/memory/dialog_history.json', 'r', encoding='utf-8') as file:
        # 加载JSON内容
        data = json.load(file)
        
        # 遍历数据列表
        for item in data:
            # 提取每个字典中的'memory'值
            memory_value = item.get('memory', None)
            
            # 如果'memory'值存在，则添加到列表中
            if memory_value:
                memory_list.append(memory_value)
    return memory_list

def store_mem(embed_model, llm):
    from llama_index.core import Document,Settings
    Settings.embed_model = embed_model
    Settings.llm = llm
    documents = [Document(text=intro) for intro in read_mem()]
    from llama_index.core import VectorStoreIndex
    index = VectorStoreIndex.from_documents(documents,)
    #print(index)
    index.storage_context.persist(persist_dir="./data/memory/index")
    print('index已保存...')