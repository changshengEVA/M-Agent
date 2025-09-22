import os 
import json
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