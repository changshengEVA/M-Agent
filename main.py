import os
from dotenv import load_dotenv
import json
load_dotenv()
from llama_index.core.llms import ChatMessage
from flipflop.utils import print_with_color
import argparse
parser = argparse.ArgumentParser(description='处理方法选择。')
parser.add_argument('--method', type=str, choices=['local', 'azure', 'openai'], required=True, help='选择处理方法,参照README。')
parser.add_argument('--memory', action='store_true', help='是否使用记忆')
parser.add_argument('--store', action='store_true', help='是否使用保存记忆')
parser.add_argument('--function', type=str, default = "Talker", choices=["Talker","Email"],help='the function you want to use')
parser.add_argument('--llm_model_path', type=str, default = "./checkpoints/chatglm3-6b", help='LLM模型权重文件路径')
parser.add_argument('--embed_model_path', type=str, default = "./checkpoints/bge-large-en-v1.5", help='Embed模型权重文件路径')
args = parser.parse_args()

##test
args.llm_model_path = "./checkpoints/chatglm3-6b"
args.embed_model_path = "./checkpoints/bge-large-en-v1.5"

if not os.path.exists("./data/memory"):
    print("未检测到记忆文件，正在创建初始记忆文件...")
    os.makedirs("./data/memory")

if not os.path.exists("./data/memory/dialog_history.json"):
    with open('./data/memory/dialog_history.json','w',encoding = 'utf-8') as f:
        json.dump([],f,ensure_ascii=False,indent=4)

if args.method == 'local':
    """
    this needs you put the llm model and embed model in the same folder as this file -> checkpoint and change the local.py.
    """
    from load_model.Localcall import get_llm,get_embed_model
    args.llm = get_llm(args.llm_model_path)
    args.embed_model = get_embed_model(args.embed_model_path)
elif args.method == 'azure':
    from load_model.Azurecall import get_llm,get_embed_model
    args.llm = get_llm(model_temperature = 0)
    args.embed_model = get_embed_model()
elif args.method == 'openai':
    from llama_index.llms.openai import OpenAI
    from llama_index.embeddings.openai import OpenAIEmbedding
    from load_model.Localcall import get_embed_model
    API_SECRET_KEY = os.getenv("API_SECRET_KEY").encode().decode('utf-8')
    BASE_URL = os.getenv("BASE_URL")
    args.llm = OpenAI(api_key = API_SECRET_KEY, api_base = BASE_URL, temperature=0.1, model="gpt-3.5-turbo")
    
    # 尝试使用不同的 OpenAI 嵌入模型
    embed_models_to_try = [
        "text-embedding-3-large",  # 这个模型可用
        "text-embedding-3-small",  # 原始模型
        "text-embedding-ada-002",  # 备选模型
    ]
    
    args.embed_model = None
    
    for model_name in embed_models_to_try:
        try:
            print(f"尝试使用嵌入模型: {model_name}")
            test_embed = OpenAIEmbedding(
                api_key=API_SECRET_KEY,
                api_base=BASE_URL,
                model=model_name
            )
            # 快速测试
            test_embed.get_query_embedding("test")
            args.embed_model = test_embed
            print(f"✓ 成功使用 OpenAI 嵌入模型: {model_name}")
            break
        except Exception as e:
            print(f"模型 {model_name} 不可用: {e}")
            continue
    
    # 如果所有 OpenAI 模型都失败，使用本地嵌入模型
    if args.embed_model is None:
        print("所有 OpenAI 嵌入模型都不可用，回退到本地嵌入模型")
        try:
            args.embed_model = get_embed_model("./checkpoints/bge-large-en-v1.5")
            print("使用本地嵌入模型")
        except Exception as e:
            print(f"本地嵌入模型也失败: {e}")
            raise

from llama_index.core import VectorStoreIndex
from llama_index.core import Document
import json
def pre_load():
    from llama_index.core import Settings
    Settings.llm = args.llm
    Settings.embed_model = args.embed_model
    with open('./data/memory/dialog_history.json','r',encoding = 'utf-8') as f:
        dialog_history = json.load(f)
    documents = [Document(text = t['history'].replace('\n', '')) for t in dialog_history]
    index = VectorStoreIndex.from_documents(documents,)
    index.storage_context.persist(persist_dir="./data/memory/index")
def main():
    if args.memory:
        try:
            pre_load()
        except:
            print_with_color("记忆加载失败，请检查文件格式是否为最新版本，将使用无记忆模式", "yellow")
            args.memory = False
    if args.function == "Email":
        from multiff import start_server
        start_server(args)
    elif args.function == "Talker":
        from observation_chat import start_chat
        start_chat(args.llm, args.embed_model, args.memory, args.store)
        
    #print('正在加载记忆，请稍等...')
    #pre_load()
    #print('记忆加载完成')
    pass

if __name__ == '__main__':
    main()

## Use command: python main.py --method openai

