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
    args.embed_model = OpenAIEmbedding(api_key = API_SECRET_KEY, api_base = BASE_URL, model="text-embedding-3-small")
    # if the api embed is not working, use the local embed model.
    #args.embed_model = get_embed_model("./checkpoint/bge-large-en-v1.5")

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

